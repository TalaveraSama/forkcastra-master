-- Astra WebUI — Agente Lua (script principal de astra)
--
-- Se arranca con:
--     /usr/bin/astra /usr/share/astra-webui/agent/agent.lua [config.json]
--
-- Responsabilidades:
--   * cargar scripts/stream.lua del core (modelo de canales)
--   * leer los ficheros de estado escritos por el panel (channels.json, auth.json)
--   * aplicar los cambios en caliente mediante make_channel/kill_channel
--   * exponer la API de control en 127.0.0.1:9977 (aw_control)
--   * servir el TS con tokens en 0.0.0.0:8001 (aw_stream)
--   * recoger estadísticas (bitrate, errores, PSI) de cada canal (aw_channels)
--   * escanear transponders DVB a petición del panel (aw_dvb)
--
-- Compatible con Lua 5.2.2 (el intérprete embebido en Astra 4.0).

local AGENT_VERSION = "1.0.0"

----------------------------------------------------------------------------
-- 1. localización de los módulos del agente
----------------------------------------------------------------------------

local function script_dir()
    local info = debug and debug.getinfo and debug.getinfo(1, "S") or nil
    local src = (info and info.source) or ""
    src = src:gsub("^@", "")
    local dir = src:match("^(.*)/[^/]*$")
    if not dir or dir == "" then return "." end
    return dir
end

local DIR = script_dir()
package.path = DIR .. "/?.lua;" .. package.path

local util = require("aw_util")
local jsonm = require("aw_json")
local logm = require("aw_log")

----------------------------------------------------------------------------
-- 2. configuración
----------------------------------------------------------------------------

local DEFAULTS = {
    data_dir = "/var/lib/astra-webui",
    channels_file = "/var/lib/astra-webui/channels.json",
    auth_file = "/var/lib/astra-webui/auth.json",
    streams_script = "/etc/astra/scripts-4.0/stream.lua",
    reload_interval = 3,
    auto_reload = true,
    log_psi_info = false,
    log = {
        filename = "/var/log/astra/astra.log",
        stdout = false,
        debug = false,
        syslog = "",
    },
    control = {
        addr = "127.0.0.1",
        port = 9977,
        token_file = "/etc/astra-webui/agent.token",
    },
    stream = {
        enabled = true,
        addr = "0.0.0.0",
        port = 8001,
        cors = true,
        public_base = "",
    },
}

local function merge(dst, src)
    if type(src) ~= "table" then return dst end
    for k, v in pairs(src) do
        if type(v) == "table" and type(dst[k]) == "table" then
            merge(dst[k], v)
        else
            dst[k] = v
        end
    end
    return dst
end

local function config_path()
    if type(argv) == "table" and type(argv[1]) == "string" and argv[1] ~= "" then
        return argv[1]
    end
    local env = os.getenv("ASTRA_WEBUI_AGENT_CONFIG")
    if type(env) == "string" and env ~= "" then return env end
    return "/etc/astra-webui/agent.json"
end

local cfg = util.deepcopy(DEFAULTS) or DEFAULTS

local CFG_PATH = config_path()
do
    local text = util.read_file(CFG_PATH)
    if text then
        local obj, err = jsonm.decode(text)
        if obj then
            merge(cfg, obj)
        else
            -- el log aún no está configurado: se avisa por stdout
            print("[astra-webui] agent.json inválido: " .. util.errstr(err))
        end
    end
end

----------------------------------------------------------------------------
-- 3. arranque
----------------------------------------------------------------------------

logm.setup(cfg.log)

log.info(string.format("[astra-webui] agente v%s iniciando (astra %s, lua %s)",
                       AGENT_VERSION, tostring(astra.version), tostring(_VERSION)))
log.info("[astra-webui] configuración: " .. tostring(CFG_PATH))

----------------------------------------------------------------------------
-- 4. cargar stream.lua del core
----------------------------------------------------------------------------

local function candidate_stream_paths()
    local list = {}
    if type(cfg.streams_script) == "string" and cfg.streams_script ~= "" then
        list[#list + 1] = cfg.streams_script
    end
    list[#list + 1] = "/etc/astra/scripts-4.0/stream.lua"
    list[#list + 1] = DIR .. "/stream.lua"
    list[#list + 1] = DIR .. "/../scripts/stream.lua"
    return list
end

local stream_loaded, stream_source, stream_err = false, nil, nil
for _, path in ipairs(candidate_stream_paths()) do
    if util.file_exists(path) then
        local ok, err = pcall(dofile, path)
        if ok then
            stream_loaded, stream_source = true, path
            break
        end
        stream_err = err
    end
end

if not stream_loaded then
    log.error("[astra-webui] no se pudo cargar stream.lua del core: " .. util.errstr(stream_err))
    log.error("[astra-webui] comprueba 'streams_script' en " .. tostring(CFG_PATH))
    astra.exit()
    return
end

log.info("[astra-webui] stream.lua cargado desde " .. tostring(stream_source))

----------------------------------------------------------------------------
-- 5. módulos del agente
----------------------------------------------------------------------------

local channels = require("aw_channels")
local auth = require("aw_auth")
local dvb = require("aw_dvb")
local stream = require("aw_stream")
local control = require("aw_control")

channels.path = cfg.channels_file
channels.set_deps({ stream = stream })
auth.set_path(cfg.auth_file)
stream.setup(cfg.stream)
stream.set_deps({ channels = channels, auth = auth })
control.setup(cfg.control)
control.set_deps({ channels = channels, dvb = dvb, auth = auth, stream = stream })

----------------------------------------------------------------------------
-- 6. enganchar on_analyze para recoger estadísticas
----------------------------------------------------------------------------

local original_on_analyze = on_analyze

on_analyze = function(channel_data, input_id, data)
    local ok, err = pcall(channels.on_analyze, channel_data, input_id, data)
    if not ok then
        log.error("[astra-webui] error recogiendo estadísticas: " .. util.errstr(err))
    end

    if cfg.log_psi_info == true and type(original_on_analyze) == "function" then
        local ok2, err2 = pcall(original_on_analyze, channel_data, input_id, data)
        if not ok2 then
            log.error("[astra-webui] error en el analizador de PSI: " .. util.errstr(err2))
        end
    elseif type(data) == "table" and data.error then
        local name = (channel_data and channel_data.config and channel_data.config.name) or "?"
        log.warning(string.format("[%s #%d] %s", tostring(name), tonumber(input_id) or 0, tostring(data.error)))
    end
end

----------------------------------------------------------------------------
-- 7. estado inicial
----------------------------------------------------------------------------

do
    local ok, changed, err = auth.reload(true)
    if not ok then
        log.warning("[astra-webui] auth.json: " .. util.errstr(err))
    else
        log.info(string.format("[astra-webui] auth.json cargado (%d tokens)", auth.count()))
    end
end

do
    local report, changed, err = channels.reload(true)
    if not report then
        log.warning("[astra-webui] channels.json: " .. util.errstr(err))
    else
        log.info(string.format("[astra-webui] canales aplicados: +%d ~%d -%d, en marcha %d",
                               report.added, report.updated, report.removed, report.running))
        for _, f in ipairs(report.failed or {}) do
            log.error(string.format("[astra-webui] canal '%s' no arrancado: %s",
                                    tostring(f.name or f.id), tostring(f.error)))
        end
    end
end

----------------------------------------------------------------------------
-- 8. servidores
----------------------------------------------------------------------------

stream.start()
control.start()

----------------------------------------------------------------------------
-- 9. vigilancia de ficheros (el panel escribe, el agente aplica)
----------------------------------------------------------------------------

local reload_interval = util.to_int(cfg.reload_interval, 3)
if reload_interval < 2 then reload_interval = 2 end
if reload_interval > 60 then reload_interval = 60 end

local watcher = timer({
    interval = reload_interval,
    callback = function()
        if cfg.auto_reload == true then
            local ok, err = pcall(function()
                local report, changed, rerr = channels.reload(false)
                if report and changed then
                    log.info(string.format("[astra-webui] configuración recargada: +%d ~%d -%d, en marcha %d",
                                           report.added, report.updated, report.removed, report.running))
                    for _, f in ipairs(report.failed or {}) do
                        log.error(string.format("[astra-webui] canal '%s' no arrancado: %s",
                                                tostring(f.name or f.id), tostring(f.error)))
                    end
                elseif rerr then
                    log.warning("[astra-webui] recarga: " .. tostring(rerr))
                end

                local aok, achanged, aerr = auth.reload(false)
                if achanged then
                    log.info(string.format("[astra-webui] auth.json recargado (%d tokens)", auth.count()))
                elseif not aok and aerr then
                    log.warning("[astra-webui] auth.json: " .. tostring(aerr))
                end
            end)
            if not ok then
                log.error("[astra-webui] error en la vigilancia de ficheros: " .. util.errstr(err))
            end
        end
    end,
})

-- El temporizador se guarda en la tabla del módulo de control: los locales del
-- chunk principal mueren al terminar el script y el GC destruiría el timer.
control.watcher = watcher
_G.astra_webui_watcher = watcher

log.info(string.format("[astra-webui] agente listo (recarga cada %ds, auto_reload=%s)",
                       reload_interval, tostring(cfg.auto_reload == true)))
