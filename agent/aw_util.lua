-- Astra WebUI — Agente Lua
-- aw_util.lua: utilidades comunes (sin dependencias del core salvo `utils`)
--
-- Compatibilidad: Lua 5.2.2 (el intérprete embebido en Astra 4.0).
-- No usar operadores bit a bit, ni `//`, ni `utf8`, ni `goto`.

local M = {}

local _counter = 0
local _json_mod = nil

----------------------------------------------------------------------------
-- cadenas
----------------------------------------------------------------------------

function M.trim(s)
    if type(s) ~= "string" then return "" end
    return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

function M.is_empty(s)
    return type(s) ~= "string" or M.trim(s) == ""
end

-- Divide conservando campos vacíos (para rutas de fichero, por ejemplo)
function M.split_list(s, sep)
    local out = {}
    if type(s) ~= "string" then return out end
    local start = 1
    while true do
        local i = s:find(sep, start, true)
        if not i then
            out[#out + 1] = s:sub(start)
            break
        end
        out[#out + 1] = s:sub(start, i - 1)
        start = i + #sep
    end
    return out
end

-- Divide descartando campos vacíos
function M.split_words(s, sep)
    local out = {}
    if type(s) ~= "string" then return out end
    for part in s:gmatch("([^" .. sep .. "]+)") do
        out[#out + 1] = part
    end
    return out
end

function M.startswith(s, prefix)
    return type(s) == "string" and s:sub(1, #prefix) == prefix
end

function M.url_decode(s)
    if type(s) ~= "string" then return "" end
    s = s:gsub("+", " ")
    s = s:gsub("%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end)
    return s
end

function M.url_encode(s)
    if type(s) ~= "string" then s = tostring(s) end
    return (s:gsub("([^%w%-%._~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end))
end

function M.path_of(uri)
    if type(uri) ~= "string" then return "/" end
    local i = uri:find("?", 1, true)
    if i then return uri:sub(1, i - 1) end
    return uri
end

function M.parse_query(uri)
    local q = {}
    if type(uri) ~= "string" then return q end
    local i = uri:find("?", 1, true)
    if not i then return q end
    for _, pair in ipairs(M.split_list(uri:sub(i + 1), "&")) do
        if pair ~= "" then
            local eq = pair:find("=", 1, true)
            if eq then
                q[M.url_decode(pair:sub(1, eq - 1))] = M.url_decode(pair:sub(eq + 1))
            else
                q[M.url_decode(pair)] = ""
            end
        end
    end
    return q
end

----------------------------------------------------------------------------
-- números / booleanos
----------------------------------------------------------------------------

function M.to_int(v, default)
    if type(v) == "number" then return math.floor(v) end
    if type(v) == "string" then
        local n = tonumber(M.trim(v))
        if n then return math.floor(n) end
    end
    if type(v) == "boolean" then return v and 1 or 0 end
    return default
end

function M.to_num(v, default)
    if type(v) == "number" then return v end
    if type(v) == "string" then
        local n = tonumber(M.trim(v))
        if n then return n end
    end
    return default
end

function M.to_bool(v)
    if v == nil then return nil end
    if type(v) == "boolean" then return v end
    if type(v) == "number" then return v ~= 0 end
    if type(v) == "string" then
        local s = M.trim(v):lower()
        if s == "false" or s == "0" or s == "no" or s == "off" or s == "" then return false end
        return true
    end
    return true
end

function M.round(n)
    if type(n) ~= "number" then return 0 end
    return math.floor(n + 0.5)
end

----------------------------------------------------------------------------
-- tablas
----------------------------------------------------------------------------

function M.count(t)
    local n = 0
    if type(t) ~= "table" then return 0 end
    for _ in pairs(t) do n = n + 1 end
    return n
end

function M.is_empty_table(t)
    return type(t) ~= "table" or next(t) == nil
end

function M.deepcopy(v, depth)
    depth = depth or 0
    if depth > 12 then return nil end
    if type(v) ~= "table" then return v end
    local out = {}
    for k, val in pairs(v) do
        if type(k) ~= "userdata" and type(k) ~= "function" then
            out[k] = M.deepcopy(val, depth + 1)
        end
    end
    return out
end

-- Serialización determinista: se usa para firmar la configuración de un canal
-- y detectar si hay que reiniciarlo. Las claves con prefijo "_" se ignoran.
function M.canon(v)
    local t = type(v)
    if t == "table" then
        local keys = {}
        for k, _ in pairs(v) do
            if type(k) ~= "userdata" and type(k) ~= "function" then
                local ks = tostring(k)
                if ks:sub(1, 1) ~= "_" then
                    keys[#keys + 1] = k
                end
            end
        end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        local parts = {}
        for _, k in ipairs(keys) do
            parts[#parts + 1] = tostring(k) .. "=" .. M.canon(v[k])
        end
        return "{" .. table.concat(parts, ",") .. "}"
    elseif t == "string" then
        return '"' .. v .. '"'
    elseif t == "boolean" then
        return v and "true" or "false"
    elseif t == "nil" then
        return "nil"
    elseif t == "number" then
        return tostring(v)
    end
    return tostring(v)
end

function M.array_remove(t, value)
    for i = 1, #t do
        if t[i] == value then
            table.remove(t, i)
            return true
        end
    end
    return false
end

function M.find_by(t, key, value)
    if type(t) ~= "table" then return nil end
    for _, item in pairs(t) do
        if type(item) == "table" and item[key] == value then return item end
    end
    return nil
end

----------------------------------------------------------------------------
-- ficheros
----------------------------------------------------------------------------

function M.read_file(path)
    if type(path) ~= "string" then return nil, "bad path" end
    local f, err = io.open(path, "rb")
    if not f then return nil, err or "cannot open" end
    local data = f:read("*a")
    f:close()
    if not data then return nil, "read error" end
    return data
end

function M.write_file(path, data)
    local f, err = io.open(path, "wb")
    if not f then return nil, err or "cannot open" end
    local ok = f:write(data)
    f:close()
    if not ok then return nil, "write error" end
    return true
end

function M.write_file_atomic(path, data)
    local tmp = path .. ".tmp." .. tostring(os.time())
    local ok, err = M.write_file(tmp, data)
    if not ok then return nil, err end
    local rok, rerr = os.rename(tmp, path)
    if not rok then
        os.remove(tmp)
        return nil, rerr or "rename failed"
    end
    return true
end

function M.dirname(path)
    if type(path) ~= "string" then return "" end
    local i = path:match("^(.*)/[^/]*$")
    if not i or i == "" then return "/" end
    return i
end

function M.basename(path)
    if type(path) ~= "string" then return "" end
    return (path:match("([^/]*)$")) or path
end

function M.stat(path)
    if type(utils) ~= "table" or type(utils.stat) ~= "function" then return nil end
    local ok, st = pcall(utils.stat, path)
    if not ok or type(st) ~= "table" then return nil end
    if st.error then return nil end
    return st
end

function M.file_exists(path)
    local st = M.stat(path)
    return st ~= nil and st.type == "file"
end

function M.dir_exists(path)
    local st = M.stat(path)
    return st ~= nil and st.type == "directory"
end

----------------------------------------------------------------------------
-- varios
----------------------------------------------------------------------------

function M.new_id(prefix)
    _counter = (_counter + 1) % 0x10000
    local t = math.floor(os.time()) % 0x100000000
    local r = math.random(0, 0xFFFF)
    return string.format("%s%08x%04x%04x", prefix or "", t, r, _counter)
end

function M.hostname()
    if type(utils) == "table" and type(utils.hostname) == "function" then
        local ok, h = pcall(utils.hostname)
        if ok and type(h) == "string" and h ~= "" then return h end
    end
    return "localhost"
end

function M.now()
    return math.floor(os.time())
end

-- PID del proceso astra (leído de /proc/self/stat)
function M.self_pid()
    local f = io.open("/proc/self/stat", "rb")
    if not f then return 0 end
    local line = f:read("*l") or ""
    f:close()
    local pid = line:match("^(%d+)")
    return tonumber(pid) or 0
end

-- Memoria residente del proceso en KiB (VmRSS)
function M.self_rss_kb()
    local f = io.open("/proc/self/status", "rb")
    if not f then return 0 end
    local data = f:read("*a") or ""
    f:close()
    local kb = data:match("VmRSS:%s*(%d+)")
    return tonumber(kb) or 0
end

function M.set_json(mod)
    _json_mod = mod
end

-- Devuelve un texto legible de un error cualquiera
function M.errstr(err)
    if err == nil then return "unknown error" end
    if type(err) == "string" then return err end
    return tostring(err)
end

return M
