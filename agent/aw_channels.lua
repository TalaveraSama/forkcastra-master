-- Astra WebUI — Agente Lua
-- aw_channels.lua: carga de channels.json, construcción de la configuración de
-- stream.lua, diff en caliente, estadísticas por canal y validaciones.
--
-- Cómo se conecta con stream.lua:
--   * make_channel() devuelve el channel_data, cuyo campo `tail` es el objeto
--     `transmit`. Con `cd.tail:stream()` se obtiene el stream (lightuserdata)
--     que se entrega al servidor de streaming propio (aw_stream) para servir
--     /live/<token>/<canal>.ts.  Así el reparto de TS con control de acceso no
--     depende del `output_list.http` interno de stream.lua (que es local).
--   * Las salidas de tipo http NO se pasan al core: son metadatos de la UI.
--
-- IMPORTANTE: los módulos del core abortan el proceso (astra_abort → abort())
-- cuando falta una opción obligatoria. Por eso TODO se valida aquí antes de
-- llamar a make_channel(): nombres, tipos, puertos, frecuencias, directorios...

local util = require("aw_util")
local jsonm = require("aw_json")

local M = {}

M.path = nil
M.content = nil
M.config = nil            -- última configuración aplicada (decodificada)
M.config_error = nil
M.failed = {}
M.runtime = {}            -- id -> { cd, sig, started, conf, name, info }
M.stats = {}              -- id -> { inputs, psi, viewers, bitrate, on_air, errors }
M.meta = {}               -- id -> datos para listas M3U / estado
M.order = {}              -- lista ordenada de ids
M.dvb_pool = {}           -- clave de transponder -> { instance, refs }
M.applied_at = 0
M.deps = { stream = nil }

local ALLOWED_OUTPUT_DIRS = { "/var/lib/astra-webui/", "/srv/", "/media/", "/mnt/" }

function M.set_deps(deps)
    for k, v in pairs(deps or {}) do M.deps[k] = v end
end

----------------------------------------------------------------------------
-- validación
----------------------------------------------------------------------------

local function fail(fmt, ...)
    local msg = select("#", ...) > 0 and string.format(fmt, ...) or fmt
    return nil, msg
end

local function valid_port(v)
    local n = util.to_int(v, 0)
    return n >= 1 and n <= 65535
end

local function valid_ipv4(addr)
    if type(addr) ~= "string" then return false end
    local a, b, c, d = addr:match("^(%d+)%.(%d+)%.(%d+)%.(%d+)$")
    if not a then return false end
    for _, part in ipairs({ a, b, c, d }) do
        if tonumber(part) > 255 then return false end
    end
    return true
end

local function valid_hostname(host)
    if type(host) ~= "string" or host == "" then return false end
    if host:find("[%s/]") then return false end
    return true
end

local function valid_filename(path)
    if type(path) ~= "string" or path:sub(1, 1) ~= "/" then return false end
    if path:find("%.%.") then return false end
    return true
end

local function path_allowed(path, dirs)
    for _, d in ipairs(dirs) do
        if path:sub(1, #d) == d then return true end
    end
    return false
end

-- http://usuario:clave@host:puerto/ruta
local function parse_http_url(url)
    if type(url) ~= "string" or url == "" then return fail("url vacía") end
    local scheme, rest = url:match("^(%a[%w+%.%-]*)://(.*)$")
    if not scheme then return fail("url sin esquema (http://…)") end
    if scheme ~= "http" then
        return fail("esquema no soportado: %s (el core sólo habla http sin TLS)", scheme)
    end

    local userinfo
    local at = rest:find("@", 1, true)
    local slash = rest:find("/", 1, true)
    if at and (not slash or at < slash) then
        userinfo = rest:sub(1, at - 1)
        rest = rest:sub(at + 1)
    end

    local hostport, path = rest:match("^([^/]*)(.*)$")
    local host, port = hostport:match("^([^:]+):(%d+)$")
    if not host then
        host, port = hostport, 80
    end
    if not valid_hostname(host) then return fail("host inválido en la url") end
    port = util.to_int(port, 80)
    if not valid_port(port) then return fail("puerto inválido en la url") end
    if path == "" then path = "/" end

    return { host = host, port = port, uri = path, userinfo = userinfo }
end

----------------------------------------------------------------------------
-- construcción de la configuración de stream.lua
----------------------------------------------------------------------------

----------------------------------------------------------------------------
-- CAM (servidor de tarjeta por newcamd: OSCam + lector PC/SC, por ejemplo)
----------------------------------------------------------------------------

-- Los módulos CAM deben quedar referenciados aquí: si el recolector de basura de Lua
-- los libera mientras el núcleo los usa, el proceso se va a segmentation fault
-- (el mismo problema que con el analizador del escaneo).
local CAM_MODULES = {}

local function cam_instance(opts, input_key)
    if type(opts) ~= "table" then return nil, "configuración CAM inválida" end
    local host = tostring(opts.host or "")
    local port = util.to_int(opts.port, 0)
    local user = tostring(opts.user or "")
    if host == "" or port <= 0 or user == "" then
        return nil, "el CAM necesita 'host', 'port' y 'user'"
    end
    if type(newcamd) ~= "function" and type(newcamd) ~= "table" then
        return nil, "este binario no incluye el módulo newcamd"
    end

    -- "Make new connection for each input" (Astra): cada entrada abre su propia conexión
    -- con el servidor CAM en lugar de compartir una única instancia.
    local key = string.format("%s:%d:%s", host, port, user)
    if util.to_bool(opts.each_input) and input_key and input_key ~= "" then
        key = key .. "#" .. tostring(input_key)
    end
    local mod = CAM_MODULES[key]
    if not mod then
        local ok, created = pcall(newcamd, {
            name = "astra-webui",
            host = host,
            port = port,
            user = user,
            pass = tostring(opts.pass or ""),
            key = tostring(opts.key or ""),
            disable_emm = util.to_int(opts.disable_emm, 0),
            timeout = util.to_int(opts.timeout, 0),
        })
        if not ok or created == nil then
            return nil, "no se pudo crear el CAM: " .. tostring(created)
        end
        mod = created
        CAM_MODULES[key] = mod
    end

    local ok2, cam_obj = pcall(function() return mod:cam() end)
    if not ok2 or cam_obj == nil then
        return nil, "el CAM no devolvió su interfaz: " .. tostring(cam_obj)
    end
    return cam_obj
end

local function apply_input_options(conf, opts, input_key)
    opts = opts or {}
    local pnr = util.to_int(opts.pnr, 0)
    if pnr and pnr > 0 then conf.pnr = pnr end
    local set_pnr = util.to_int(opts.set_pnr, 0)
    if set_pnr and set_pnr > 0 then conf.set_pnr = set_pnr end
    if opts.no_analyze == true then conf.no_analyze = true end
    if type(opts.biss) == "string" and opts.biss ~= "" then conf.biss = opts.biss end
    if type(opts.filter) == "string" and opts.filter ~= "" then
        conf.filter = util.split_words(opts.filter, ",")
    end
    if opts.filter_reverse == true then conf.filter_reverse = true end
    local ecm_pid = util.to_int(opts.ecm_pid, 0)
    if ecm_pid > 0 then
        conf.ecm_pid = ecm_pid
    elseif type(opts.ecm_pid) == "string" and opts.ecm_pid ~= "" then
        conf.ecm_pid = opts.ecm_pid
    end
    local ecm_swap = util.to_int(opts.ecm_swap_time, 0)
    if ecm_swap > 0 then conf.ecm_swap_time = ecm_swap end
    if type(opts.cas_data) == "string" and opts.cas_data ~= "" then
        conf.cas_data = opts.cas_data
    end
    -- Servidor de tarjeta (newcamd). La instancia del CAM va en 'cam'.
    if type(opts.cam) == "table" then
        local cam_obj, cam_err = cam_instance(opts.cam, input_key)
        if cam_obj then
            conf.cam = cam_obj
        else
            log.error("[astra-webui] CAM: " .. tostring(cam_err))
        end
    end
end

local function dvb_key(inp)
    return string.format("%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s",
        tostring(util.to_int(inp.adapter, 0)), tostring(util.to_int(inp.device, 0)),
        tostring(inp.delivery or inp.type or "S2"), tostring(util.to_int(inp.frequency, 0)),
        tostring(inp.polarization or ""), tostring(util.to_int(inp.symbolrate, 0)),
        tostring(util.to_int(inp.diseqc, 0)), tostring(util.to_int(inp.lof1, 9750)),
        tostring(util.to_int(inp.lof2, 10600)), tostring(util.to_int(inp.slof, 11700)),
        tostring(util.to_int(inp.stream_id, 0)))
end

local function build_dvb_options(inp)
    -- El sistema de entrega del frontend llega en 'delivery' (S, S2, T, T2, C).
    -- Se acepta 'type' como alias para las peticiones de escaneo, pero nunca el
    -- valor "dvb" (que es el tipo de entrada, no el tipo de frontend).
    local t = inp.delivery
    if type(t) ~= "string" or t == "" then
        t = inp.type
    end
    t = tostring(t or "S2"):upper()
    if t == "DVB" or t == "DVB_INPUT" then t = "S2" end
    t = t:gsub("^DVB%-", "")

    local adapter = util.to_int(inp.adapter, nil)
    if adapter == nil or adapter < 0 then return fail("dvb: 'adapter' es obligatorio") end

    local frequency = util.to_int(inp.frequency, nil)
    if frequency == nil or frequency <= 0 then return fail("dvb: 'frequency' debe ser > 0") end

    local opts = {
        adapter = adapter,
        device = util.to_int(inp.device, 0),
        type = t,
        frequency = frequency,
    }

    if t == "S" or t == "S2" then
        local pol = tostring(inp.polarization or "H"):upper():sub(1, 1)
        if pol ~= "H" and pol ~= "V" and pol ~= "R" and pol ~= "L" then
            return fail("dvb: 'polarization' debe ser H, V, R o L")
        end
        local srate = util.to_int(inp.symbolrate, 0)
        if srate <= 0 then return fail("dvb-S: 'symbolrate' es obligatorio") end

        opts.polarization = pol
        opts.symbolrate = srate
        opts.lof1 = util.to_int(inp.lof1, 9750)
        opts.lof2 = util.to_int(inp.lof2, 10600)
        opts.slof = util.to_int(inp.slof, 11700)
        if util.to_int(inp.diseqc, 0) > 0 then opts.diseqc = util.to_int(inp.diseqc, 0) end
        if util.to_int(inp.tone, 0) > 0 then opts.tone = util.to_int(inp.tone, 0) end
        if util.to_int(inp.lnb_sharing, 0) > 0 then opts.lnb_sharing = util.to_int(inp.lnb_sharing, 0) end
        -- budget != 0 → el módulo dvb abre un solo filtro del demux (el transporte
        -- completo). Es lo que permite sacar el mux entero (por ejemplo para ver todos
        -- los canales de un transponder en VLC).
        if util.to_int(inp.budget, 0) > 0 then opts.budget = util.to_int(inp.budget, 0) end
        if type(inp.fec) == "string" and inp.fec ~= "" and inp.fec:upper() ~= "AUTO" then
            opts.fec = inp.fec
        end
        -- 'rolloff' sólo si no es AUTO: el core interpreta la cadena y "AUTO" no es un
        -- valor suyo (lo deja en su valor por defecto, que es lo que queremos).
        if type(inp.rolloff) == "string" and inp.rolloff ~= ""
                and inp.rolloff:upper() ~= "AUTO" then
            opts.rolloff = inp.rolloff
        end
        -- OJO: 'modulation' NO se envía nunca. El core aborta el proceso si recibe una
        -- cadena que no reconoce (comprobado: "unknown type of the 'modulation': 8PSK"),
        -- y en DVB-S/S2 el driver detecta la modulación solo.
        if util.to_int(inp.stream_id, 0) > 0 then opts.stream_id = util.to_int(inp.stream_id, 0) end

    elseif t == "T" or t == "T2" then
        opts.bandwidth = (type(inp.bandwidth) == "string" and inp.bandwidth ~= "")
            and inp.bandwidth or "AUTO"
        opts.guardinterval = (type(inp.guardinterval) == "string" and inp.guardinterval ~= "")
            and inp.guardinterval or "AUTO"
        opts.transmitmode = (type(inp.transmitmode) == "string" and inp.transmitmode ~= "")
            and inp.transmitmode or "AUTO"
        opts.hierarchy = (type(inp.hierarchy) == "string" and inp.hierarchy ~= "")
            and inp.hierarchy or "AUTO"
        if util.to_int(inp.stream_id, 0) > 0 then opts.stream_id = util.to_int(inp.stream_id, 0) end

    elseif t == "C" then
        local srate = util.to_int(inp.symbolrate, 0)
        if srate <= 0 then return fail("dvb-C: 'symbolrate' es obligatorio") end
        opts.symbolrate = srate
        if type(inp.fec) == "string" and inp.fec ~= "" and inp.fec:upper() ~= "AUTO" then
            opts.fec = inp.fec
        end

    else
        return fail("dvb: tipo no soportado '%s' (usa S, S2, T, T2 o C)", tostring(inp.type))
    end

    if type(inp.modulation) == "string" and inp.modulation ~= "" then
        opts.modulation = inp.modulation
    end
    if util.to_int(inp.budget, 0) > 0 then opts.budget = util.to_int(inp.budget, 0) end
    if util.to_int(inp.buffer_size, 0) > 0 then opts.buffer_size = util.to_int(inp.buffer_size, 0) end

    return opts
end

-- Exportado para el escaneo DVB (aw_dvb.lua)
function M.dvb_options(inp)
    return build_dvb_options(inp)
end

local function acquire_dvb(inp)
    local key = dvb_key(inp)
    local slot = M.dvb_pool[key]
    if slot and slot.instance then
        slot.refs = slot.refs + 1
        return slot.instance, key
    end

    local opts, err = build_dvb_options(inp)
    if not opts then return nil, nil, err end

    local ok, instance = pcall(dvb_input, opts)
    if not ok or instance == nil then
        return nil, nil, "no se pudo abrir el adaptador DVB (" .. util.errstr(instance) .. ")"
    end

    M.dvb_pool[key] = { instance = instance, refs = 1 }
    log.info(string.format("[astra-webui] dvb_input creado adapter=%s device=%s type=%s freq=%s",
                           tostring(opts.adapter), tostring(opts.device), tostring(opts.type),
                           tostring(opts.frequency)))
    return instance, key
end

local function release_dvb(keys)
    if type(keys) ~= "table" then return end
    for _, key in ipairs(keys) do
        local slot = M.dvb_pool[key]
        if slot then
            slot.refs = slot.refs - 1
            if slot.refs <= 0 then
                M.dvb_pool[key] = nil
                log.info("[astra-webui] dvb_input liberado (" .. tostring(key) .. ")")
            end
        end
    end
    collectgarbage("collect")
end

-- Comprobaciones que evitan que el core aborte el proceso: por ejemplo la clave
-- BISS debe tener exactamente 16 caracteres hexadecimales (modules/softcam/decrypt.c
-- llama a astra_abort() en caso contrario).
local function check_input_options(conf, idx)
    if conf.biss then
        local key = tostring(conf.biss)
        if #key ~= 16 or not key:match("^%x+$") then
            return string.format("entrada %d: la clave BISS debe tener 16 dígitos hexadecimales", idx)
        end
    end
    if conf.ecm_pid ~= nil and not tostring(conf.ecm_pid):match("^%d+$") then
        return string.format("entrada %d: 'ecm_pid' debe ser numérico", idx)
    end
    return nil
end

local function build_input(ch, idx)
    local inp = ch.inputs[idx]
    -- identidad estable de esta entrada: 'Make new connection for each input'
    local input_key = string.format("%s:%s", tostring(ch.id or ch.name or "?"), tostring(idx))
    if type(inp) ~= "table" then return nil, nil, string.format("entrada %d inválida", idx) end

    local t = tostring(inp.type or ""):lower()
    local conf, dvb_keys

    if t == "http" then
        local url = util.trim(tostring(inp.url or ""))
        local parsed, err = parse_http_url(url)
        if not parsed then return nil, nil, string.format("entrada %d: %s", idx, err) end
        conf = {
            module_name = "http",
            host = parsed.host,
            port = parsed.port,
            uri = parsed.uri,
        }
        if parsed.userinfo and parsed.userinfo ~= "" then
            conf.auth = base64.encode(parsed.userinfo)
        end
        apply_input_options(conf, inp.options, input_key)

    elseif t == "udp" then
        if not valid_ipv4(inp.addr) then
            return nil, nil, string.format("entrada %d: 'addr' debe ser una IPv4 (p. ej. 239.0.0.1)", idx)
        end
        if not valid_port(inp.port) then
            return nil, nil, string.format("entrada %d: 'port' inválido", idx)
        end
        conf = {
            module_name = "udp",
            addr = inp.addr,
            port = util.to_int(inp.port, 1234),
        }
        if type(inp.localaddr) == "string" and inp.localaddr ~= "" then
            conf.localaddr = inp.localaddr
        end
        if inp.rtp == true then conf.rtp = true end
        if util.to_int(inp.socket_size, 0) > 0 then conf.socket_size = util.to_int(inp.socket_size, 0) end
        if util.to_int(inp.renew, 0) > 0 then conf.renew = util.to_int(inp.renew, 0) end
        apply_input_options(conf, inp.options, input_key)

    elseif t == "file" then
        if not valid_filename(inp.filename) then
            return nil, nil, string.format("entrada %d: 'filename' debe ser una ruta absoluta", idx)
        end
        local st = util.stat(inp.filename)
        if not st or st.type ~= "file" then
            return nil, nil, string.format("entrada %d: el fichero no existe (%s)", idx, tostring(inp.filename))
        end
        conf = { module_name = "file", filename = inp.filename }
        if inp.loop == true then conf.loop = true end
        apply_input_options(conf, inp.options, input_key)

    elseif t == "dvb" then
        local instance, key, err = acquire_dvb(inp)
        if not instance then return nil, nil, string.format("entrada %d: %s", idx, tostring(err)) end
        dvb_keys = { key }
        conf = { module_name = "dvb", _instance = instance }
        if inp.cam == true then conf.dvbcam = true end
        apply_input_options(conf, inp.options, input_key)

    else
        return nil, nil, string.format("entrada %d: tipo no soportado '%s'", idx, tostring(inp.type))
    end

    local opt_err = check_input_options(conf, idx)
    if opt_err then
        if dvb_keys then release_dvb(dvb_keys) end
        return nil, nil, opt_err
    end

    return conf, dvb_keys
end

-- Devuelve (conf o nil, meta o nil, error)
local function build_output(ch, idx)
    local out = ch.outputs[idx]
    if type(out) ~= "table" then return nil, nil, string.format("salida %d inválida", idx) end

    local t = tostring(out.type or ""):lower()

    if t == "http" then
        local uri = util.trim(tostring(out.uri or ""))
        if uri == "" then uri = "/" .. tostring(ch.id) end
        if uri:sub(1, 1) ~= "/" then uri = "/" .. uri end
        if uri:find("[%s]") then
            return nil, nil, string.format("salida %d: la uri no puede tener espacios", idx)
        end
        -- Las salidas HTTP no se entregan al core: las sirve aw_stream con tokens
        return nil, { uri = uri, public = out.public == true, cors = out.cors ~= false }, nil

    elseif t == "udp" then
        if not valid_ipv4(out.addr) then
            return nil, nil, string.format("salida %d: 'addr' debe ser una IPv4", idx)
        end
        if not valid_port(out.port) then
            return nil, nil, string.format("salida %d: 'port' inválido", idx)
        end
        local ttl = util.to_int(out.ttl, 32)
        if ttl <= 0 then ttl = 1 end
        local conf = {
            module_name = "udp",
            addr = out.addr,
            port = util.to_int(out.port, 1234),
            ttl = ttl,
        }
        if type(out.localaddr) == "string" and out.localaddr ~= "" then
            conf.localaddr = out.localaddr
        end
        if out.rtp == true then conf.rtp = true end
        if util.to_int(out.sync, 0) > 0 then conf.sync = util.to_int(out.sync, 0) end
        if util.to_int(out.socket_size, 0) > 0 then conf.socket_size = util.to_int(out.socket_size, 0) end
        return conf, nil, nil

    elseif t == "file" then
        if not valid_filename(out.filename) then
            return nil, nil, string.format("salida %d: 'filename' debe ser una ruta absoluta", idx)
        end
        if not path_allowed(out.filename, ALLOWED_OUTPUT_DIRS) then
            return nil, nil, string.format("salida %d: ruta de grabación no permitida (usa %s)",
                                           idx, table.concat(ALLOWED_OUTPUT_DIRS, ", "))
        end
        local dir = util.dirname(out.filename)
        if not util.dir_exists(dir) then
            return nil, nil, string.format("salida %d: el directorio no existe (%s)", idx, dir)
        end
        local conf = { module_name = "file", filename = out.filename }
        if out.m2ts == true then conf.m2ts = true end
        if util.to_int(out.buffer_size, 0) > 0 then conf.buffer_size = util.to_int(out.buffer_size, 0) end
        return conf, nil, nil

    else
        return nil, nil, string.format("salida %d: tipo no soportado '%s'", idx, tostring(out.type))
    end
end

-- Construye la tabla que espera make_channel() de stream.lua.
-- Devuelve (conf, info, err) con info = { dvb_keys = {...}, http = { {uri,public,cors}, ... } }
function M.build_channel_conf(ch)
    if type(ch) ~= "table" then return nil, nil, "canal inválido" end
    if util.is_empty(ch.id) then return nil, nil, "el canal no tiene 'id'" end
    if util.is_empty(ch.name) then
        return nil, nil, string.format("el canal '%s' no tiene 'name'", tostring(ch.id))
    end
    if type(ch.inputs) ~= "table" or #ch.inputs == 0 then
        return nil, nil, string.format("el canal '%s' no tiene entradas", tostring(ch.name))
    end

    local inputs, dvb_keys = {}, {}
    for i = 1, #ch.inputs do
        local conf, keys, err = build_input(ch, i)
        if not conf then
            release_dvb(dvb_keys)
            return nil, nil, err
        end
        inputs[#inputs + 1] = conf
        if keys then
            for _, k in ipairs(keys) do dvb_keys[#dvb_keys + 1] = k end
        end
    end

    local outputs, http_meta = {}, {}
    for i = 1, #(ch.outputs or {}) do
        local conf, meta, err = build_output(ch, i)
        if err then
            release_dvb(dvb_keys)
            return nil, nil, err
        end
        if conf then outputs[#outputs + 1] = conf end
        if meta then
            meta.cors = meta.cors ~= false
            http_meta[#http_meta + 1] = meta
        end
    end

    local conf = {
        id = ch.id,
        name = ch.name,
        group = ch.group,
        logo = ch.logo,
        epg_id = ch.epg_id,
        enable = true,
        input = inputs,
        output = outputs,
    }

    if type(ch.map) == "table" and #ch.map > 0 then
        conf.map = ch.map
    end

    return conf, { dvb_keys = dvb_keys, http = http_meta }, nil
end

----------------------------------------------------------------------------
-- arranque / parada
----------------------------------------------------------------------------

local function new_stats(id)
    return {
        id = id,
        inputs = {},
        psi = {},
        bitrate = 0,
        on_air = false,
        errors = { cc = 0, sc = 0, pes = 0 },
        started = util.now(),
    }
end

local function register_http_outputs(id, cd, info)
    if not M.deps.stream then return end
    M.deps.stream.unregister_channel(id)
    if type(info) ~= "table" or type(info.http) ~= "table" or #info.http == 0 then return end

    local ok, upstream = pcall(function() return cd.tail:stream() end)
    if not ok or upstream == nil then
        log.error("[astra-webui] no se pudo obtener el stream del canal " .. tostring(id))
        return
    end
    M.deps.stream.register_channel(id, upstream, {
        name = cd.config and cd.config.name or id,
        cors = true,
        uris = info.http,
    })
end

function M.start(id, conf, sig, info)
    if M.runtime[id] then return true end

    local ok, result = pcall(make_channel, conf)
    if not ok then
        release_dvb(info and info.dvb_keys)
        return fail("make_channel falló: %s", util.errstr(result))
    end
    if result == nil then
        release_dvb(info and info.dvb_keys)
        return fail("make_channel devolvió nil (canal deshabilitado)")
    end

    M.runtime[id] = {
        cd = result,
        sig = sig,
        started = util.now(),
        conf = conf,
        info = info or {},
        name = conf.name,
    }
    M.stats[id] = new_stats(id)
    register_http_outputs(id, result, info)
    return true
end

function M.stop(id, opts)
    opts = opts or {}
    local rt = M.runtime[id]
    if not rt then return true end

    if M.deps.stream then
        pcall(M.deps.stream.unregister_channel, id)
    end

    local ok, err = pcall(kill_channel, rt.cd)
    M.runtime[id] = nil
    M.stats[id] = nil

    if not opts.keep_dvb then
        release_dvb(rt.info and rt.info.dvb_keys)
    end

    if not ok then
        return fail("kill_channel falló: %s", util.errstr(err))
    end
    return true
end

function M.restart(id, conf, sig, info)
    local rt = M.runtime[id]
    local old_keys = (rt and rt.info and rt.info.dvb_keys) or {}

    local ok, err = M.stop(id, { keep_dvb = true })
    if not ok then
        release_dvb(info and info.dvb_keys)
        release_dvb(old_keys)
        return nil, err
    end

    local started, serr = M.start(id, conf, sig, info)
    if not started then
        release_dvb(old_keys)
        return nil, serr
    end

    local keep = {}
    for _, k in ipairs((info and info.dvb_keys) or {}) do keep[k] = true end
    local to_release = {}
    for _, k in ipairs(old_keys) do
        if not keep[k] then to_release[#to_release + 1] = k end
    end
    release_dvb(to_release)

    return true
end

----------------------------------------------------------------------------
-- diff / aplicación
----------------------------------------------------------------------------

function M.signature(ch)
    return util.canon({ inputs = ch.inputs or {}, outputs = ch.outputs or {}, enabled = ch.enabled ~= false })
end

function M.apply(data)
    if type(data) ~= "table" or type(data.channels) ~= "table" then
        return nil, "estructura inválida: falta el array 'channels'"
    end

    local report = { added = 0, updated = 0, removed = 0, running = 0, failed = {} }
    local wanted = {}

    for _, ch in ipairs(data.channels) do
        if type(ch) == "table" and not util.is_empty(ch.id) then
            wanted[ch.id] = ch
        end
    end

    -- 1) parar los canales que ya no están
    local to_remove = {}
    for id, _ in pairs(M.runtime) do
        if not wanted[id] then to_remove[#to_remove + 1] = id end
    end
    for _, id in ipairs(to_remove) do
        M.stop(id)
        report.removed = report.removed + 1
    end

    -- 2) metadatos para M3U y estado
    M.meta = {}
    M.order = {}
    for _, ch in ipairs(data.channels) do
        if type(ch) == "table" and not util.is_empty(ch.id) then
            M.meta[ch.id] = {
                id = ch.id,
                name = ch.name or ch.id,
                group = ch.group or "General",
                logo = ch.logo or "",
                epg_id = ch.epg_id or "",
                enabled = ch.enabled ~= false,
                tags = ch.tags or {},
            }
            M.order[#M.order + 1] = ch.id
        end
    end

    -- 3) arrancar / actualizar
    for _, ch in ipairs(data.channels) do
        if type(ch) == "table" and not util.is_empty(ch.id) then
            local id = ch.id
            local enabled = ch.enabled ~= false
            local rt = M.runtime[id]

            if not enabled then
                if rt then
                    M.stop(id)
                    report.removed = report.removed + 1
                end
            else
                local sig = M.signature(ch)
                if not rt then
                    local conf, info, err = M.build_channel_conf(ch)
                    if not conf then
                        report.failed[#report.failed + 1] = { id = id, name = ch.name, error = err }
                    else
                        local started, serr = M.start(id, conf, sig, info)
                        if started then
                            report.added = report.added + 1
                        else
                            report.failed[#report.failed + 1] = { id = id, name = ch.name, error = serr }
                        end
                    end
                elseif rt.sig ~= sig then
                    local conf, info, err = M.build_channel_conf(ch)
                    if not conf then
                        report.failed[#report.failed + 1] = { id = id, name = ch.name, error = err }
                    else
                        local ok, serr = M.restart(id, conf, sig, info)
                        if ok then
                            report.updated = report.updated + 1
                        else
                            report.failed[#report.failed + 1] = { id = id, name = ch.name, error = serr }
                        end
                    end
                else
                    rt.cd.config.name = ch.name
                    rt.name = ch.name
                end
            end
        end
    end

    for _, _ in pairs(M.runtime) do
        report.running = report.running + 1
    end

    M.failed = report.failed
    M.config = data
    M.config_error = nil
    M.applied_at = util.now()
    return report
end

-- Lee channels.json y aplica el diff. Devuelve (reporte, cambiado, error)
function M.reload(force)
    if type(M.path) ~= "string" or M.path == "" then
        return nil, false, "ruta de channels.json no configurada"
    end

    local text, rerr = util.read_file(M.path)
    if not text then
        M.config_error = "no se puede leer channels.json: " .. util.errstr(rerr)
        return nil, false, M.config_error
    end
    if (not force) and M.content == text then
        return nil, false, nil
    end

    local data, jerr = jsonm.decode(text)
    if not data then
        M.config_error = "channels.json inválido: " .. util.errstr(jerr)
        log.error("[astra-webui] " .. M.config_error)
        return nil, false, M.config_error
    end

    M.content = text
    local report, aerr = M.apply(data)
    if not report then
        M.config_error = aerr
        return nil, true, aerr
    end
    return report, true, nil
end

function M.running_count()
    local n = 0
    for _ in pairs(M.runtime) do n = n + 1 end
    return n
end

function M.total_count()
    local n = 0
    for _, ch in pairs(M.meta) do
        if ch.enabled then n = n + 1 end
    end
    return n
end

function M.bitrate_total()
    local total = 0
    for _, st in pairs(M.stats) do
        total = total + (st.bitrate or 0)
    end
    return total
end

function M.dvb_pool_size()
    local n = 0
    for _ in pairs(M.dvb_pool) do n = n + 1 end
    return n
end

----------------------------------------------------------------------------
-- estadísticas
----------------------------------------------------------------------------

-- Envoltorio del on_analyze global de stream.lua
function M.on_analyze(cd, input_id, data)
    if type(cd) ~= "table" or type(cd.config) ~= "table" then return end
    local id = cd.config.id
    if not id then return end

    local st = M.stats[id]
    if not st then
        st = new_stats(id)
        M.stats[id] = st
    end

    local item = st.inputs[input_id]
    if not item then
        item = { index = input_id, on_air = false, bitrate = 0, cc = 0, sc = 0, pes = 0, samples = 0 }
        st.inputs[input_id] = item
    end

    if type(data) ~= "table" then return end

    if data.error then
        item.error = tostring(data.error)
    end

    if type(data.analyze) == "table" then
        local bitrate, cc, sc, pes = 0, 0, 0, 0
        for _, pid in pairs(data.analyze) do
            if type(pid) == "table" then
                bitrate = bitrate + (util.to_int(pid.bitrate, 0) or 0)
                cc = cc + (util.to_int(pid.cc_error, 0) or 0)
                sc = sc + (util.to_int(pid.sc_error, 0) or 0)
                pes = pes + (util.to_int(pid.pes_error, 0) or 0)
            end
        end
        item.bitrate = bitrate
        item.cc = cc
        item.sc = sc
        item.pes = pes
        item.on_air = data.on_air == true
        item.error = nil
        item.samples = (item.samples or 0) + 1

        st.errors.cc = cc
        st.errors.sc = sc
        st.errors.pes = pes

        if input_id == 1 or item.on_air then
            st.bitrate = bitrate
            st.on_air = item.on_air
        end
    end

    if data.psi == "pat" or data.psi == "pmt" or data.psi == "sdt" or data.psi == "cat" then
        st.psi[input_id] = util.deepcopy(data, 0)
        st.psi_at = util.now()
    end
end

function M.update_viewers()
    for id, st in pairs(M.stats) do
        if M.deps.stream then
            st.viewers = M.deps.stream.viewers_of(id)
        end
    end
end

function M.status()
    M.update_viewers()

    local channels = {}
    for _, id in ipairs(M.order) do
        local meta = M.meta[id] or {}
        local rt = M.runtime[id]
        local st = M.stats[id] or new_stats(id)
        local ch = M.config and util.find_by(M.config.channels, "id", id) or nil

        local inputs = {}
        if ch and type(ch.inputs) == "table" then
            for i = 1, #ch.inputs do
                local src = ch.inputs[i] or {}
                local item = st.inputs[i] or {}
                inputs[#inputs + 1] = {
                    index = i,
                    type = tostring(src.type or ""),
                    on_air = item.on_air == true,
                    bitrate = util.to_int(item.bitrate, 0),
                    cc_error = util.to_int(item.cc, 0),
                    sc_error = util.to_int(item.sc, 0),
                    pes_error = util.to_int(item.pes, 0),
                    error = tostring(item.error or ""),
                }
            end
        end

        local outputs = {}
        for _, out in ipairs((ch and ch.outputs) or {}) do
            local target = out.uri or out.filename or ""
            if out.type == "udp" then
                target = tostring(out.addr or "") .. ":" .. tostring(out.port or "")
            end
            outputs[#outputs + 1] = {
                type = tostring(out.type or ""),
                target = target,
                public = out.public == true,
            }
        end

        channels[#channels + 1] = {
            id = id,
            name = meta.name or id,
            group = meta.group or "General",
            enabled = meta.enabled ~= false,
            running = rt ~= nil,
            on_air = st.on_air == true,
            bitrate = util.to_int(st.bitrate, 0),
            viewers = util.to_int(st.viewers, 0),
            errors = {
                cc = util.to_int(st.errors.cc, 0),
                sc = util.to_int(st.errors.sc, 0),
                pes = util.to_int(st.errors.pes, 0),
            },
            started = rt and rt.started or 0,
            inputs = inputs,
            outputs = outputs,
        }
    end

    return {
        ok = true,
        config_error = M.config_error or "",
        channels_total = M.total_count(),
        channels_running = M.running_count(),
        dvb_transponders = M.dvb_pool_size(),
        viewers = M.deps.stream and M.deps.stream.viewers_total() or 0,
        bitrate_total = M.bitrate_total(),
        applied_at = M.applied_at,
        channels = channels,
    }
end

function M.psi_of(channel_id, input_id)
    local st = M.stats[channel_id]
    if not st then return {} end
    local list = {}
    for i, psi in pairs(st.psi) do
        if (not input_id) or tostring(input_id) == tostring(i) then
            if type(psi) == "table" then
                local copy = util.deepcopy(psi, 0) or {}
                copy.input_index = i
                list[#list + 1] = copy
            end
        end
    end
    return list
end

function M.playlist_items()
    local items = {}
    for _, id in ipairs(M.order) do
        local meta = M.meta[id] or {}
        items[#items + 1] = {
            id = id,
            name = meta.name or id,
            group = meta.group or "General",
            logo = meta.logo or "",
            epg_id = meta.epg_id or "",
            enabled = meta.enabled ~= false,
        }
    end
    return items
end

return M
