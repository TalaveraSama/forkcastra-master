-- Astra WebUI — Agente Lua
-- aw_log.lua: configuración del log del core y lectura del fichero de log.

local util = require("aw_util")

local M = {}

M.path = nil

function M.setup(cfg)
    cfg = cfg or {}
    M.path = cfg.filename

    local opts = {
        stdout = cfg.stdout == true,
        debug = cfg.debug == true,
    }
    if type(cfg.filename) == "string" and cfg.filename ~= "" then
        opts.filename = cfg.filename
    end
    -- syslog sólo en plataformas no Windows; el core ignora la opción si no existe
    if type(cfg.syslog) == "string" and cfg.syslog ~= "" then
        opts.syslog = cfg.syslog
    end

    local ok, err = pcall(log.set, opts)
    if not ok then
        -- Nunca abortar por el log: se sigue con lo que haya
        pcall(log.set, { stdout = true })
        log.error("[astra-webui] no se pudo configurar el log: " .. util.errstr(err))
    end
    return true
end

-- Devuelve las últimas `max_lines` líneas del fichero de log (máx. ~256 KiB)
function M.tail(path, max_lines, max_bytes)
    path = path or M.path
    max_lines = max_lines or 200
    max_bytes = max_bytes or 262144

    if type(path) ~= "string" or path == "" then
        return nil, "ruta de log no configurada"
    end

    local f, err = io.open(path, "rb")
    if not f then return nil, err or "no se puede abrir el log" end

    local size = f:seek("end") or 0
    local start = size - max_bytes
    if start < 0 then start = 0 end
    f:seek("set", start)

    local data = f:read("*a") or ""
    f:close()

    local raw = util.split_list(data, "\n")
    if start > 0 and #raw > 0 then
        table.remove(raw, 1) -- primera línea incompleta
    end

    local first = #raw - max_lines + 1
    if first < 1 then first = 1 end

    local out = {}
    for i = first, #raw do
        local line = raw[i]
        if line ~= "" then
            out[#out + 1] = line:gsub("\r$", "")
        end
    end
    return out
end

-- Clasifica una línea del core: "Oct  2 08:28:11: INFO: mensaje"
function M.parse_line(line)
    local ts, level, message = line:match("^(%a+ +%d+ +%d+:%d+:%d+): ([A-Z]+): (.*)$")
    if level then
        return { timestamp = ts, level = level:lower(), message = message, raw = line }
    end
    return { timestamp = "", level = "other", message = line, raw = line }
end

return M
