-- Astra WebUI — Agente Lua
-- aw_json.lua: envoltorio del módulo `json` del core.
--
-- Limitaciones conocidas del json del core (Astra 4.0, modules/astra/json.c):
--   * `json.decode` NO entiende escapes \uXXXX (falla) → el panel escribe UTF-8
--     literal y sin caracteres de control.
--   * `json.decode` TRUNCA los decimales (1.5 → 1) → sólo se usan enteros.
--   * `json.encode` convierte una tabla vacía en `[]` → nunca se emiten objetos
--     vacíos en las respuestas (siempre llevan al menos `ok`).
--   * `json.encode` exige que el valor a codificar esté en la pila como tabla.

local M = {}

local core_json = json

function M.available()
    return type(core_json) == "table"
        and type(core_json.encode) == "function"
        and type(core_json.decode) == "function"
end

function M.decode(text)
    if not M.available() then return nil, "json module not available" end
    if type(text) ~= "string" or text == "" then return nil, "empty document" end

    local ok, res = pcall(core_json.decode, text)
    if not ok then return nil, tostring(res) end
    if type(res) ~= "table" then return nil, "no se pudo interpretar el JSON" end
    return res
end

function M.encode(value)
    if not M.available() then return nil end
    if type(value) ~= "table" then
        value = { ok = true, value = value }
    end
    local ok, res = pcall(core_json.encode, value)
    if not ok or type(res) ~= "string" then return nil end
    return res
end

return M
