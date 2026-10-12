-- Astra WebUI — Agente Lua
-- aw_http.lua: ayuda para peticiones/respuestas y enrutador para `http_server`.
--
-- Notas sobre el módulo `http_server` del core (ver modules/http/server.c):
--   * callback(self, client, data)
--       - data == nil          → el cliente se desconectó (o error de lectura)
--       - data == tabla        → cabeceras completas (method, uri, version,
--                                addr, port, headers[])
--       - data == cadena       → trozo del cuerpo; la cadena vacía marca el fin
--   * `server:data(client)` devuelve una tabla persistente por conexión.
--   * `server:send(client, {...})` envía cabeceras de forma inmediata y el
--     cuerpo de forma DIFERIDA (evento de escritura). Por eso NUNCA se debe
--     llamar a `server:close(client)` justo después de un send con contenido:
--     el cuerpo se perdería. Las respuestas se dejan con Content-Length y el
--     socket se cierra solo cuando el cliente desconecta.

local util = require("aw_util")
local jsonm = require("aw_json")

local M = {}

M.STATUS = {
    [200] = "OK",
    [201] = "Created",
    [204] = "No Content",
    [301] = "Moved Permanently",
    [302] = "Found",
    [400] = "Bad Request",
    [401] = "Unauthorized",
    [403] = "Forbidden",
    [404] = "Not Found",
    [405] = "Method Not Allowed",
    [409] = "Conflict",
    [413] = "Payload Too Large",
    [415] = "Unsupported Media Type",
    [422] = "Unprocessable Entity",
    [429] = "Too Many Requests",
    [500] = "Internal Server Error",
    [502] = "Bad Gateway",
    [503] = "Service Unavailable",
}

function M.status_text(code)
    return M.STATUS[code] or "OK"
end

function M.parse_headers(list)
    local out = {}
    if type(list) ~= "table" then return out end
    for _, line in ipairs(list) do
        if type(line) == "string" then
            local k, v = line:match("^([%w%-_]+):%s*(.*)$")
            if k then
                out[k:lower()] = v
            end
        end
    end
    return out
end

-- Envía una respuesta cruda (cabeceras + cuerpo). No cierra el cliente.
-- `head_only` mantiene Content-Length pero omite el cuerpo (peticiones HEAD).
function M.send_raw(server, client, code, body, ctype, extra_headers, head_only)
    body = body or ""
    local length = #body
    if head_only then body = "" end
    local headers = {
        "Server: astra-webui",
        "Content-Type: " .. (ctype or "application/json; charset=utf-8"),
        "Content-Length: " .. length,
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
    }
    if type(extra_headers) == "table" then
        for _, h in ipairs(extra_headers) do
            headers[#headers + 1] = h
        end
    end

    local payload = {
        code = code,
        message = M.status_text(code),
        headers = headers,
    }
    if not head_only then
        payload.content = body
    end
    server:send(client, payload)
end

function M.send_json(server, client, code, obj, extra_headers, head_only)
    local body = jsonm.encode(obj)
    if not body then
        code = 500
        body = '{"ok":false,"error":"no se pudo codificar la respuesta"}'
    end
    M.send_raw(server, client, code, body, "application/json; charset=utf-8", extra_headers, head_only)
end

function M.send_error(server, client, code, message, head_only)
    M.send_json(server, client, code, { ok = false, error = message or M.status_text(code), code = code },
                nil, head_only)
end

-- Enrutador. `routes` es una lista de entradas:
--   { method = "GET"|"POST"|"*", pattern = "^/ruta/(%w+)$", handler = f(ctx, capturas...) }
-- El handler devuelve (code, obj) para responder JSON, o nil si ya respondió.
function M.router(routes, opts)
    opts = opts or {}
    local max_body = opts.max_body or 262144

    local function dispatch(server, client, st)
        local route = nil
        local captures = nil

        for _, r in ipairs(routes) do
            if r.method == "*" or r.method == st.method then
                local m = { st.path:match(r.pattern) }
                if m[1] ~= nil then
                    route = r
                    captures = m
                    break
                end
            end
        end

        if not route then
            return M.send_error(server, client, 404, "ruta no encontrada: " .. tostring(st.path))
        end

        local ctx = {
            method = st.method,
            path = st.path,
            uri = st.uri,
            query = st.query,
            headers = st.headers,
            body = st.body,
            addr = st.addr,
            port = st.port,
            server = server,
            client = client,
            state = st,
        }

        local head_only = (st.method == "HEAD")

        function ctx:json(code, obj)
            M.send_json(server, client, code, obj, nil, head_only)
            return nil
        end

        function ctx:error(code, message)
            M.send_error(server, client, code, message, head_only)
            return nil
        end

        function ctx:get(name)
            return self.headers[name:lower()]
        end

        function ctx:body_json()
            if type(self.body) ~= "string" or self.body == "" then return {} end
            local obj, err = jsonm.decode(self.body)
            if not obj then return nil, err end
            return obj
        end

        function ctx:param(name)
            return self.query[name]
        end

        local ok, code, obj = pcall(route.handler, ctx, table.unpack(captures))
        if not ok then
            log.error(string.format("[astra-webui] error en %s %s: %s",
                                    tostring(st.method), tostring(st.path), util.errstr(code)))
            return M.send_error(server, client, 500, util.errstr(code))
        end

        if code ~= nil then
            M.send_json(server, client, code, obj, nil, head_only)
        end
    end

    return function(self, client, data)
        local st = self:data(client)

        -- desconexión del cliente
        if data == nil then
            if st.client_state and type(opts.on_close) == "function" then
                pcall(opts.on_close, st.client_state)
            end
            st.client_state = nil
            st.method, st.uri, st.path, st.query, st.headers = nil, nil, nil, nil, nil
            st.body, st.buf, st.need, st.done, st.addr, st.port = nil, nil, nil, nil, nil, nil
            return
        end

        -- petición nueva (cabeceras completas)
        if type(data) == "table" then
            st.method = tostring(data.method or "GET")
            st.uri = tostring(data.uri or "/")
            st.path = util.path_of(st.uri)
            st.query = util.parse_query(st.uri)
            st.headers = M.parse_headers(data.headers)
            st.addr = data.addr
            st.port = data.port
            st.body = nil
            st.buf = nil
            st.need = nil
            st.done = false

            local len = util.to_int(st.headers["content-length"], 0) or 0
            if len > max_body then
                st.done = true
                return M.send_error(self, client, 413, "cuerpo de petición demasiado grande")
            end
            if len > 0 then
                st.need = len
                st.buf = {}
                return -- se espera al cuerpo
            end
            st.done = true
            return dispatch(self, client, st)
        end

        -- trozo del cuerpo
        if st.done then return end
        if not st.buf then return end

        if data == "" then
            st.done = true
            st.body = table.concat(st.buf)
            st.buf = nil
            return dispatch(self, client, st)
        end

        st.buf[#st.buf + 1] = data
    end
end

return M
