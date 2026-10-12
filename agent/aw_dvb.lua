-- Astra WebUI — Agente Lua
-- aw_dvb.lua: listado de adaptadores y escaneo de transponders.
--
-- El escaneo NO se hace dentro del agente: cada transponder se sintoniza en un
-- PROCESO HIJO (`aw_scan.lua`). El motivo es que el módulo dvb del núcleo llama a
-- astra_abort() cuando el tune falla (sin señal, transponder equivocado, driver que
-- rechaza los parámetros): ese abort termina el proceso entero, así que si el escaneo
-- corriera aquí dentro se llevaría por delante el streaming y el panel se quedaría sin
-- agente. El hijo escribe su resultado en JSON y el agente lo va leyendo sin bloquearse.
--
-- Aviso: un adaptador sólo admite un lector a la vez. Si hay canales DVB
-- emitiendo desde el mismo adaptador, el escaneo fallará (frontend ocupado).

local util = require("aw_util")
local channels = require("aw_channels")
local jsonm = require("aw_json")

local M = {}

M.scan = nil
M.last_scan = nil
M.history = {}

local MAX_HISTORY = 10

-- Rutas del paquete (las fija deb_stage; se pueden sobrescribir en tests).
M.astra_bin = "/usr/bin/astra"
M.scan_script = "/usr/share/astra-webui/agent/aw_scan.lua"
-- Directorio de trabajo del escaneo. No se usa /tmp porque astra.service tiene
-- PrivateTmp (el agente tendría su propio /tmp, invisible desde fuera, y los
-- ficheros del escaneo no se podrían revisar). Debe estar en ReadWritePaths.
M.scan_dir = "/var/lib/astra-webui/scan"

local function fail(fmt, ...)
    local msg = select("#", ...) > 0 and string.format(fmt, ...) or fmt
    return nil, msg
end

----------------------------------------------------------------------------
-- adaptadores
----------------------------------------------------------------------------

function M.adapters()
    if type(dvbls) ~= "function" then
        return fail("el módulo dvbls no está disponible en este binario")
    end

    local ok, list = pcall(dvbls)
    if not ok then return fail("dvbls() falló: %s", util.errstr(list)) end
    if type(list) ~= "table" then return fail("dvbls() no devolvió una lista") end

    local out = {}
    for _, a in ipairs(list) do
        if type(a) == "table" then
            out[#out + 1] = {
                adapter = util.to_int(a.adapter, 0),
                device = util.to_int(a.device, 0),
                frontend = tostring(a.frontend or "unknown"),
                mac = tostring(a.mac or ""),
                busy = a.busy == true,
                error = tostring(a.error or ""),
            }
        end
    end
    return out
end

----------------------------------------------------------------------------
-- escaneo
----------------------------------------------------------------------------

local function reset_current(scan)
    scan.pat, scan.sdt, scan.pmt, scan.cat = nil, nil, nil, nil
    scan.bitrate, scan.on_air = 0, false
    scan.tp_error = ""
    scan.tp_started = util.now()
end

local function collect_services(scan)
    local names = {}
    if type(scan.sdt) == "table" and type(scan.sdt.services) == "table" then
        for _, svc in pairs(scan.sdt.services) do
            if type(svc) == "table" then
                local name, provider = "", ""
                if type(svc.descriptors) == "table" then
                    for _, d in pairs(svc.descriptors) do
                        if type(d) == "table" and d.type_name == "service" then
                            name = tostring(d.service_name or "")
                            provider = tostring(d.service_provider or "")
                        end
                    end
                end
                names[util.to_int(svc.sid, 0)] = { name = name, provider = provider }
            end
        end
    end

    local pat = {}
    if type(scan.pat) == "table" and type(scan.pat.programs) == "table" then
        for _, p in pairs(scan.pat.programs) do
            if type(p) == "table" then
                local pnr = util.to_int(p.pnr, 0)
                if pnr > 0 then pat[pnr] = util.to_int(p.pid, 0) end
            end
        end
    end

    local pnrs = {}
    for pnr, _ in pairs(pat) do pnrs[#pnrs + 1] = pnr end
    table.sort(pnrs)

    local services = {}
    for _, pnr in ipairs(pnrs) do
        local pmt = scan.pmt and scan.pmt[pnr] or nil
        local info = names[pnr]
        local scrambled, video, audio = false, 0, 0
        local ecm_pid, caid = 0, 0

        if type(pmt) == "table" then
            if type(pmt.descriptors) == "table" then
                for _, d in pairs(pmt.descriptors) do
                    if type(d) == "table" and d.type_name == "cas" then
                        scrambled = true
                        -- El PID del ECM (descriptor CA de la PMT) es lo que necesita el
                        -- módulo decrypt para pedir las claves al CAM.
                        local dpid = util.to_int(d.pid, 0)
                        if dpid > 0 and ecm_pid == 0 then ecm_pid = dpid end
                        local dcaid = util.to_int(d.caid, 0)
                        if dcaid > 0 and caid == 0 then caid = dcaid end
                    end
                end
            end
            if type(pmt.streams) == "table" then
                for _, s in pairs(pmt.streams) do
                    if type(s) == "table" then
                        -- El core emite type_name en MAYÚSCULAS ("VIDEO"/"AUDIO"): se
                        -- compara en minúsculas y, si no viene, se deduce del type_id.
                        local tn = tostring(s.type_name or ""):lower()
                        local type_id = util.to_int(s.type_id, 0)
                        if tn == "" then
                            if type_id == 0x01 or type_id == 0x02
                                    or type_id == 0x10 or type_id == 0x1B then
                                tn = "video"
                            elseif type_id == 0x03 or type_id == 0x04
                                    or type_id == 0x0F or type_id == 0x11 then
                                tn = "audio"
                            end
                        end
                        local pid = util.to_int(s.pid, 0)
                        if tn == "video" and video == 0 then
                            video = pid
                        elseif tn == "audio" and audio == 0 then
                            audio = pid
                        end
                    end
                end
            end
        end

        services[#services + 1] = {
            pnr = pnr,
            sid = pnr,
            pmt_pid = util.to_int(pat[pnr], 0),
            name = info and info.name or "",
            provider = info and info.provider or "",
            scrambled = scrambled,
            video_pid = video,
            audio_pid = audio,
            ecm_pid = ecm_pid,
            caid = caid,
        }
    end

    -- servicios anunciados en SDT que no aparecen en PAT
    for sid, info in pairs(names) do
        local found = false
        for _, s in ipairs(services) do
            if s.sid == sid then found = true break end
        end
        if not found and sid > 0 then
            services[#services + 1] = {
                pnr = 0, sid = sid, pmt_pid = 0,
                name = info.name or "", provider = info.provider or "",
                scrambled = false, video_pid = 0, audio_pid = 0,
            }
        end
    end

    return services
end

local function finish_current(scan)
    if not scan.current then return end

    local services = collect_services(scan)
    local result = {
        index = scan.index,
        type = scan.current.type,
        frequency = util.to_int(scan.current.frequency, 0),
        polarization = tostring(scan.current.polarization or ""),
        symbolrate = util.to_int(scan.current.symbolrate, 0),
        tsid = (type(scan.pat) == "table" and util.to_int(scan.pat.tsid, 0)) or 0,
        bitrate = util.to_int(scan.bitrate, 0),
        on_air = scan.on_air == true,
        services_count = #services,
        services = services,
        error = scan.tp_error or "",
    }

    scan.results[#scan.results + 1] = result
    for _, svc in ipairs(services) do
        scan.services[#scan.services + 1] = {
            pnr = svc.pnr, sid = svc.sid, pmt_pid = svc.pmt_pid,
            name = svc.name, provider = svc.provider, scrambled = svc.scrambled,
            video_pid = svc.video_pid, audio_pid = svc.audio_pid,
            ecm_pid = svc.ecm_pid, caid = svc.caid,
            transponder = {
                type = result.type,
                frequency = result.frequency,
                polarization = result.polarization,
                symbolrate = result.symbolrate,
                tsid = result.tsid,
            },
        }
    end

    log.info(string.format("[astra-webui] escaneo %s: TP %s %s %s → %d servicios (%d kbit/s)",
                           tostring(scan.id), tostring(result.frequency), tostring(result.polarization),
                           tostring(result.symbolrate), #services, result.bitrate))

    -- soltar el transponder actual (el proceso hijo ya terminó)
    scan.current = nil
    scan.child_pid = 0
    scan.out_file, scan.log_file, scan.pid_file = nil, nil, nil
    collectgarbage("collect")
end

local function read_number(path)
    local handle = io.open(path, "r")
    if not handle then return nil end
    local text = handle:read("*l")
    handle:close()
    return tonumber(text)
end


local function start_current(scan)
    local tp = scan.transponders[scan.index]
    if not tp then return end

    local opts, err = channels.dvb_options(tp)
    if not opts then
        scan.tp_error = err or "transponder inválido"
        scan.current = tp
        scan.tp_started = util.now() - scan.duration -- cierra de inmediato
        log.error("[astra-webui] transponder inválido en el escaneo: " .. tostring(err))
        return
    end

    scan.current = tp
    scan.tp_error = ""
    scan.pat, scan.sdt, scan.pmt, scan.cat = nil, nil, {}, nil
    scan.bitrate, scan.on_air = 0, false
    scan.tp_started = util.now()

    -- Resultado del hijo y su registro, en /tmp (el hijo hereda el mismo /tmp)
    scan.out_file = string.format("%s/%s-%d.json", M.scan_dir, tostring(scan.id), scan.index)
    scan.log_file = string.format("%s/%s-%d.log", M.scan_dir, tostring(scan.id), scan.index)
    scan.pid_file = string.format("%s/%s-%d.pid", M.scan_dir, tostring(scan.id), scan.index)
    scan.child_pid = 0

    os.execute(string.format("mkdir -p %s 2>/dev/null", M.scan_dir))
    os.remove(scan.out_file)
    os.remove(scan.pid_file)

    -- Argumentos clave=valor para el hijo (evita depender del json de entrada)
    local parts = { "out=" .. scan.out_file, "seconds=" .. tostring(scan.duration) }
    local keys = { "type", "delivery", "adapter", "device", "frequency", "polarization",
                   "symbolrate", "lof1", "lof2", "slof", "diseqc", "tone", "fec",
                   "rolloff", "stream_id", "bandwidth", "guardinterval", "transmitmode",
                   "hierarchy", "modulation", "budget" }
    for _, key in ipairs(keys) do
        local value = opts[key]
        if value ~= nil and value ~= "" then
            if type(value) == "boolean" then
                value = value and "1" or "0"
            end
            parts[#parts + 1] = string.format("%s=%s", key, tostring(value))
        end
    end

    -- `timeout -s KILL` garantiza que el hijo nunca se queda colgado con el adaptador
    local cmd = string.format(
        "nohup timeout -s KILL %d %s %s %s >>%s 2>&1 & echo $! > %s",
        scan.duration + 10, M.astra_bin, M.scan_script, table.concat(parts, " "),
        scan.log_file, scan.pid_file)

    local ok, why = os.execute(cmd)
    if ok == nil or ok == false then
        scan.tp_error = "no se pudo lanzar el escaneo: " .. tostring(why)
        log.error("[astra-webui] " .. scan.tp_error)
        return
    end

    scan.child_pid = util.to_int(read_number(scan.pid_file), 0)
    log.info(string.format("[astra-webui] escaneo %s: sintonizando %s %s %s (pid %s)",
                           tostring(scan.id), tostring(opts.frequency or ""),
                           tostring(opts.polarization or ""), tostring(opts.symbolrate or ""),
                           tostring(scan.child_pid)))
end

-- Lee el JSON que va escribiendo el hijo (nil si todavía no existe)
local function read_child_result(scan)
    if not scan.out_file then return nil end
    local handle = io.open(scan.out_file, "r")
    if not handle then return nil end
    local text = handle:read("*a")
    handle:close()
    if not text or text == "" then return nil end
    local data = jsonm.decode(text)
    if type(data) ~= "table" then return nil end
    return data
end

local function child_alive(scan)
    if not scan.child_pid or scan.child_pid <= 0 then return false end
    local ok = os.execute(string.format("kill -0 %d 2>/dev/null", scan.child_pid))
    return ok == true or ok == 0
end

local function kill_child(scan)
    if scan.child_pid and scan.child_pid > 0 then
        os.execute(string.format("kill -s KILL %d 2>/dev/null", scan.child_pid))
        scan.child_pid = 0
    end
end

-- Pasa los datos del hijo a la estructura del escaneo y cierra el transponder
local function apply_child_result(scan, data)
    if type(data.pat) == "table" then scan.pat = data.pat end
    if type(data.sdt) == "table" then scan.sdt = data.sdt end
    if type(data.pmt) == "table" then
        -- El hijo envía la lista de PMT; se indexa por pnr para el resto del escaneo.
        -- También se acepta el formato antiguo (claves = pnr).
        local pmt = {}
        for key, value in pairs(data.pmt) do
            if type(value) == "table" then
                local pnr = tonumber(value.pnr) or tonumber(key)
                if pnr and pnr > 0 then pmt[pnr] = value end
            end
        end
        scan.pmt = pmt
    end
    if type(data.cat) == "table" then scan.cat = data.cat end
    scan.bitrate = util.to_int(data.bitrate, 0)
    scan.on_air = data.on_air == true
    if type(data.error) == "string" and data.error ~= "" then
        scan.tp_error = data.error
    end
end


local function finish_scan(scan, state)
    scan.state = state or "done"
    scan.finished = util.now()
    if scan.timer then
        pcall(scan.timer.close, scan.timer)
    end
    scan.timer = nil
    kill_child(scan)
    collectgarbage("collect")

    M.last_scan = scan
    M.history[#M.history + 1] = scan
    if #M.history > MAX_HISTORY then table.remove(M.history, 1) end

    log.info(string.format("[astra-webui] escaneo %s %s: %d transponders, %d servicios",
                           tostring(scan.id), tostring(scan.state), #scan.results, #scan.services))
end

function M.tick()
    local scan = M.scan
    if not scan or scan.state ~= "running" then return end

    local now = util.now()
    local next_tp = false

    if scan.current then
        local data = read_child_result(scan)
        if type(data) == "table" and (data.state == "done" or data.state == "error") then
            -- el hijo terminó (bien o con error) y dejó su resultado
            apply_child_result(scan, data)
            if data.state == "error" then
                scan.tp_error = (type(data.error) == "string" and data.error ~= "")
                    and data.error or "el escaneo del transponder falló"
            end
            kill_child(scan)
            finish_current(scan)
            next_tp = true
        elseif not child_alive(scan) then
            -- el hijo murió sin escribir un estado final: lo normal es que el núcleo
            -- haya abortado al no poder sintonizar (sin señal o parámetros rechazados)
            if not scan.tp_error or scan.tp_error == "" then
                scan.tp_error = "no se pudo sintonizar (sin señal o el sintonizador " ..
                                "rechazó los parámetros)"
            end
            finish_current(scan)
            next_tp = true
        elseif now - scan.tp_started >= scan.duration + 12 then
            -- red de seguridad: el hijo no responde y `timeout` no lo mató
            kill_child(scan)
            if not scan.tp_error or scan.tp_error == "" then
                scan.tp_error = "el escaneo del transponder no terminó a tiempo"
            end
            finish_current(scan)
            next_tp = true
        end
    end

    if next_tp then
        scan.index = scan.index + 1
        if scan.index > #scan.transponders then
            finish_scan(scan, "done")
            return
        end
    end

    if not scan.current then
        if scan.index <= #scan.transponders then
            start_current(scan)
        else
            finish_scan(scan, "done")
        end
    end
end

function M.start_scan(req)
    if type(req) ~= "table" then return fail("petición inválida") end
    if M.scan and M.scan.state == "running" then
        return fail("ya hay un escaneo en curso (%s)", tostring(M.scan.id))
    end
    if type(req.transponders) ~= "table" or #req.transponders == 0 then
        return fail("se requiere la lista 'transponders'")
    end
    if #req.transponders > 512 then
        return fail("demasiados transponders (máximo 512)")
    end

    local duration = util.to_int(req.duration, 8)
    if duration < 3 then duration = 3 end
    if duration > 120 then duration = 120 end

    local scan = {
        id = util.new_id("scan_"),
        state = "running",
        adapter = util.to_int(req.adapter, 0),
        device = util.to_int(req.device, 0),
        dve_type = tostring(req.type or ""),
        duration = duration,
        transponders = req.transponders,
        index = 1,
        total = #req.transponders,
        services = {},
        results = {},
        started = util.now(),
        finished = 0,
        current = nil,
        child_pid = 0,
        out_file = nil,
        log_file = nil,
        pid_file = nil,
        timer = nil,
        tp_error = "",
        bitrate = 0,
        on_air = false,
    }

    M.scan = scan
    scan.timer = timer({
        interval = 1,
        callback = function() M.tick() end,
    })

    M.tick()
    return scan.id
end

function M.stop_scan(id)
    local scan = M.scan
    if not scan or scan.state ~= "running" then return fail("no hay escaneo en curso") end
    if id and id ~= "" and id ~= scan.id then return fail("id de escaneo incorrecto") end

    if scan.timer then
        pcall(scan.timer.close, scan.timer)
    end
    kill_child(scan)
    if scan.current then
        finish_current(scan)
    end
    scan.current = nil
    finish_scan(scan, "stopped")
    return true
end

function M.status(id)
    local scan = M.scan
    if (not scan) or (id and id ~= "" and id ~= scan.id) then
        scan = M.last_scan
    end
    if not scan then
        return { ok = true, scan = { state = "idle", id = "", total = 0, index = 0, services = {}, results = {} } }
    end

    local current = { frequency = 0, polarization = "", symbolrate = 0, type = "" }
    if type(scan.current) == "table" then
        current = {
            frequency = util.to_int(scan.current.frequency, 0),
            polarization = tostring(scan.current.polarization or ""),
            symbolrate = util.to_int(scan.current.symbolrate, 0),
            type = tostring(scan.current.type or ""),
        }
    end

    return {
        ok = true,
        scan = {
            id = scan.id,
            state = scan.state,
            adapter = util.to_int(scan.adapter, 0),
            device = util.to_int(scan.device, 0),
            duration = util.to_int(scan.duration, 8),
            index = util.to_int(scan.index, 0),
            total = util.to_int(scan.total, 0),
            started = util.to_int(scan.started, 0),
            finished = util.to_int(scan.finished, 0),
            current = current,
            bitrate = util.to_int(scan.bitrate, 0),
            on_air = scan.on_air == true,
            error = scan.tp_error or "",
            services = scan.services,
            results = scan.results,
        },
    }
end

return M
