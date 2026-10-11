-- Astra WebUI — Agente Lua
-- aw_stream.lua: servidor de streaming con tokens (/live/<token>/<canal>.ts),
-- registro de las salidas HTTP de cada canal y contador de espectadores.

local util = require("aw_util")
local http = require("aw_http")

local M = {}

M.cfg = {
    enabled = true,
    addr = "0.0.0.0",
    port = 8001,
    cors = true,
    public_base = "",
}

M.server = nil
M.by_uri = {}      -- uri configurada  -> slot
M.by_channel = {}  -- channel_id       -> slot (primera salida http)
M.viewers = {}     -- channel_id       -> nº de espectadores
M.conn = {}        -- token            -> nº de conexiones
M.deps = { channels = nil, auth = nil }
M.requests = 0
M.rejected = 0

function M.setup(cfg)
    if type(cfg) == "table" then
        for k, v in pairs(cfg) do
            M.cfg[k] = v
        end
    end
end

function M.set_deps(deps)
    for k, v in pairs(deps or {}) do M.deps[k] = v end
end

local function normalize_uri(uri)
    uri = util.trim(uri or "")
    if uri == "" then uri = "/" end
    if uri:sub(1, 1) ~= "/" then uri = "/" .. uri end
    return uri
end

local function safe_header_value(s)
    if type(s) ~= "string" then return "" end
    return (s:gsub("[\r\n]", " "))
end

----------------------------------------------------------------------------
-- Registro de canales emitiendo
--   opts = { name = "Canal", cors = true,
--            uris = { {uri="/canal1", public=false, cors=true}, ... } }
-- El acceso canónico es /live/<token>/<channel_id>.ts; cada uri configurada en
-- la salida HTTP del canal se sirve además como ruta directa (pública si el
-- operador la marcó como pública).
----------------------------------------------------------------------------

function M.register_channel(channel_id, upstream, opts)
    opts = opts or {}
    if type(channel_id) ~= "string" or channel_id == "" then return nil end
    if upstream == nil then return nil end

    local name = opts.name or channel_id
    M.by_channel[channel_id] = {
        channel_id = channel_id,
        name = name,
        upstream = upstream,
        cors = opts.cors ~= false,
        public = false,
    }

    for _, u in ipairs(opts.uris or {}) do
        local uri = normalize_uri(u.uri)
        M.by_uri[uri] = {
            uri = uri,
            channel_id = channel_id,
            name = name,
            upstream = upstream,
            cors = u.cors ~= false,
            public = u.public == true,
        }
    end

    return true
end

function M.unregister_channel(channel_id)
    M.by_channel[channel_id] = nil
    for uri, slot in pairs(M.by_uri) do
        if slot.channel_id == channel_id then
            M.by_uri[uri] = nil
        end
    end
end

function M.slot_for_channel(channel_id)
    return M.by_channel[channel_id]
end

----------------------------------------------------------------------------
-- espectadores
----------------------------------------------------------------------------

function M.add_viewer(channel_id, token)
    M.viewers[channel_id] = (M.viewers[channel_id] or 0) + 1
    if token then
        M.conn[token] = (M.conn[token] or 0) + 1
    end
end

function M.remove_viewer(channel_id, token)
    if channel_id and M.viewers[channel_id] then
        local n = M.viewers[channel_id] - 1
        M.viewers[channel_id] = (n > 0) and n or nil
    end
    if token and M.conn[token] then
        local n = M.conn[token] - 1
        M.conn[token] = (n > 0) and n or nil
    end
end

function M.viewers_total()
    local total = 0
    for _, n in pairs(M.viewers) do total = total + n end
    return total
end

function M.viewers_of(channel_id)
    return M.viewers[channel_id] or 0
end

function M.connections_of(token)
    return M.conn[token] or 0
end

----------------------------------------------------------------------------
-- respuestas de streaming
----------------------------------------------------------------------------

local function cors_headers(slot)
    local headers = {
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Methods: GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers: *",
        "Access-Control-Max-Age: 600",
    }
    if slot and slot.cors == false then
        return { "Access-Control-Allow-Origin: *", "Access-Control-Allow-Methods: GET, HEAD, OPTIONS" }
    end
    return headers
end

local function send_playlist(self, client, body)
    http.send_raw(self, client, 200, body, "audio/x-mpegurl; charset=utf-8",
                  { "Access-Control-Allow-Origin: *" })
end

local function base_url(ctx_host)
    if type(M.cfg.public_base) == "string" and M.cfg.public_base ~= "" then
        return (M.cfg.public_base:gsub("/+$", ""))
    end
    local host = safe_header_value(ctx_host or "")
    if host == "" then
        host = util.hostname() .. ":" .. tostring(M.cfg.port)
    end
    return "http://" .. host
end

local function build_playlist(token, acc, host)
    local root = base_url(host)
    local items = {}
    if M.deps.channels and M.deps.channels.playlist_items then
        items = M.deps.channels.playlist_items()
    end

    local lines = { "#EXTM3U" }
    for _, ch in ipairs(items) do
        local allowed = true
        if acc then
            allowed = M.deps.auth.can_watch(acc, ch.id)
        end
        if ch.enabled and allowed then
            lines[#lines + 1] = string.format(
                '#EXTINF:-1 tvg-id="%s" tvg-logo="%s" group-title="%s",%s',
                safe_header_value(ch.epg_id or ""),
                safe_header_value(ch.logo or ""),
                safe_header_value(ch.group or "General"),
                safe_header_value(ch.name or ch.id))
            lines[#lines + 1] = root .. "/live/" .. token .. "/" .. ch.id .. ".ts"
        end
    end
    return table.concat(lines, "\n") .. "\n"
end

local function serve_stream(self, client, st, slot, acc, token)
    if not slot.upstream then
        M.rejected = M.rejected + 1
        return http.send_error(self, client, 404, "el canal no está emitiendo")
    end

    if acc and acc.max_connections and acc.max_connections > 0 and token then
        if M.connections_of(token) >= acc.max_connections then
            M.rejected = M.rejected + 1
            return http.send_error(self, client, 429, "se alcanzó el límite de conexiones del token")
        end
    end

    st.client_state = { channel_id = slot.channel_id, token = token, counted = true }
    M.add_viewer(slot.channel_id, token)
    M.requests = M.requests + 1

    local headers = {
        "Server: astra-webui",
        "Content-Type: video/mp2t",
        "Cache-Control: no-cache, no-store",
        "Pragma: no-cache",
    }
    if slot.name and slot.name ~= "" then
        headers[#headers + 1] = "icy-name: " .. safe_header_value(slot.name)
    end
    if slot.cors ~= false then
        headers[#headers + 1] = "Access-Control-Allow-Origin: *"
    end

    self:send(client, {
        code = 200,
        message = "OK",
        headers = headers,
        upstream = slot.upstream,
    })
end

local function release_state(st)
    if st and st.client_state and st.client_state.counted then
        M.remove_viewer(st.client_state.channel_id, st.client_state.token)
        st.client_state = nil
    end
end

----------------------------------------------------------------------------
-- callback del servidor
----------------------------------------------------------------------------

function M.serve(self, client, data)
    local st = self:data(client)

    if data == nil then
        release_state(st)
        st.method, st.path = nil, nil
        return
    end

    if type(data) ~= "table" then
        return -- los reproductores no envían cuerpo
    end

    local uri = tostring(data.uri or "/")
    local path = util.path_of(uri)
    st.path = path
    st.headers = http.parse_headers(data.headers)
    st.state = nil

    if data.method == "OPTIONS" then
        return http.send_raw(self, client, 204, " ", "text/plain", cors_headers(nil))
    end

    local token, channel_id

    -- info de la cuenta: /live/<token>
    token = path:match("^/live/([^/]+)/?$")
    if token then
        local acc, err = M.deps.auth.validate(token)
        if not acc then
            M.rejected = M.rejected + 1
            return http.send_error(self, client, 403, err or "token inválido")
        end
        return http.send_json(self, client, 200, {
            ok = true,
            username = acc.username,
            display_name = acc.display_name,
            max_connections = acc.max_connections,
            expires_at = acc.expires_at,
            active_connections = M.connections_of(token),
            channels = M.deps.channels.playlist_items and M.deps.channels.playlist_items() or {},
        }, cors_headers(nil))
    end

    -- lista M3U
    token = path:match("^/live/([^/]+)/playlist%.m3u8?$")
    if token then
        local acc, err = M.deps.auth.validate(token)
        if not acc then
            M.rejected = M.rejected + 1
            return http.send_error(self, client, 403, err or "token inválido")
        end
        return send_playlist(self, client, build_playlist(token, acc, st.headers and st.headers["host"] or nil))
    end

    -- streaming: /live/<token>/<canal>.ts  (o /live/<token>/<canal>/index.ts)
    token, channel_id = path:match("^/live/([^/]+)/([^/]+)%.ts$")
    if not token then
        token, channel_id = path:match("^/live/([^/]+)/([^/]+)/index%.ts$")
    end

    if token then
        local acc, err = M.deps.auth.validate(token, channel_id)
        if not acc then
            M.rejected = M.rejected + 1
            log.info(string.format("[astra-webui] acceso denegado (%s) uri=%s addr=%s",
                                   tostring(err), path, tostring(data.addr)))
            return http.send_error(self, client, 403, err or "acceso denegado")
        end
        local slot = M.by_channel[channel_id]
        if not slot then
            M.rejected = M.rejected + 1
            return http.send_error(self, client, 404, "canal no disponible")
        end
        return serve_stream(self, client, st, slot, acc, token)
    end

    -- salida directa pública (uri configurada en el canal)
    local slot = M.by_uri[path]
    if slot then
        if not slot.public then
            M.rejected = M.rejected + 1
            return http.send_error(self, client, 403, "esta salida requiere token (/live/<token>/" ..
                                   tostring(slot.channel_id) .. ".ts)")
        end
        return serve_stream(self, client, st, slot, nil, nil)
    end

    M.rejected = M.rejected + 1
    return http.send_error(self, client, 404, "ruta no encontrada")
end

----------------------------------------------------------------------------
-- arranque
----------------------------------------------------------------------------

function M.start()
    if M.cfg.enabled == false then
        log.info("[astra-webui] servidor de streaming deshabilitado por configuración")
        return nil
    end

    M.server = http_server({
        addr = M.cfg.addr,
        port = M.cfg.port,
        callback = function(self, client, data)
            local ok, err = pcall(M.serve, self, client, data)
            if not ok then
                log.error("[astra-webui] error en el servidor de streaming: " .. util.errstr(err))
                pcall(release_state, self:data(client))
            end
        end,
    })

    log.info(string.format("[astra-webui] streaming en http://%s:%d/live/<token>/<canal>.ts",
                           tostring(M.cfg.addr), M.cfg.port))
    return M.server
end

return M
