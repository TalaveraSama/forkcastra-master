-- Astra WebUI — Agente Lua
-- aw_auth.lua: tokens de consumo (auth.json) y control de conexiones.

local util = require("aw_util")
local jsonm = require("aw_json")

local M = {}

M.path = nil
M.content = nil
M.data = nil
M.error = "auth.json no cargado"
M.loaded_at = 0

local function normalize_channel_list(list)
    local out = {}
    if type(list) == "table" then
        for _, v in pairs(list) do
            if type(v) == "string" and v ~= "" then
                out[v] = true
            end
        end
    end
    return out
end

function M.set_path(path)
    M.path = path
end

function M.parse(text)
    local obj, err = jsonm.decode(text)
    if not obj then
        M.error = "auth.json inválido: " .. util.errstr(err)
        log.error("[astra-webui] " .. M.error)
        return nil, M.error
    end

    local tokens = {}
    if type(obj.tokens) == "table" then
        for tok, acc in pairs(obj.tokens) do
            if type(tok) == "string" and tok ~= "" and type(acc) == "table" then
                tokens[tok] = {
                    username = tostring(acc.username or ""),
                    display_name = tostring(acc.display_name or acc.username or ""),
                    enabled = acc.enabled ~= false,
                    channels = normalize_channel_list(acc.channels),
                    all_channels = acc.all_channels == true,
                    expires_at = util.to_int(acc.expires_at, 0),
                    max_connections = util.to_int(acc.max_connections, 0),
                    allow_public = acc.allow_public == true,
                }
            end
        end
    end

    M.data = {
        enabled = obj.enabled ~= false,
        tokens = tokens,
    }
    M.error = nil
    M.loaded_at = util.now()
    M.content = text
    return true
end

-- Recarga desde disco; devuelve (ok, cambiado, error)
function M.reload(force)
    if type(M.path) ~= "string" or M.path == "" then
        return false, false, "ruta de auth.json no configurada"
    end

    local text, err = util.read_file(M.path)
    if not text then
        M.error = "no se puede leer auth.json: " .. util.errstr(err)
        return false, false, M.error
    end

    if (not force) and M.content == text then
        return true, false
    end

    local ok, perr = M.parse(text)
    if not ok then
        return false, false, perr
    end
    return true, true
end

function M.count()
    if not M.data then return 0 end
    return util.count(M.data.tokens)
end

function M.by_token(token)
    if not M.data then return nil end
    return M.data.tokens[token]
end

-- Valida token + canal. Devuelve (cuenta, motivo_de_error)
function M.validate(token, channel_id)
    if not M.data then
        return nil, "servicio de autenticación no disponible"
    end
    if M.data.enabled == false then
        return nil, "el streaming está deshabilitado"
    end
    if type(token) ~= "string" or token == "" then
        return nil, "token no indicado"
    end

    local acc = M.data.tokens[token]
    if not acc then
        return nil, "token desconocido"
    end
    if acc.enabled == false then
        return nil, "token deshabilitado"
    end
    if acc.expires_at > 0 and util.now() > acc.expires_at then
        return nil, "token expirado"
    end

    if channel_id ~= nil then
        if not M.can_watch(acc, channel_id) then
            return nil, "sin permiso para ese canal"
        end
    end
    return acc
end

function M.can_watch(acc, channel_id)
    if not acc then return false end
    if acc.all_channels then return true end
    return acc.channels[channel_id] == true
end

return M
