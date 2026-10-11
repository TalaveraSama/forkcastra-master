-- Astra WebUI — Agente Lua
-- aw_control.lua: API de control local (127.0.0.1:9977) que consume el panel.

local util = require("aw_util")
local http = require("aw_http")
local logm = require("aw_log")

local M = {}

M.server = nil
M.token = nil
M.token_path = nil
M.started_at = util.now()
M.cfg = {
    addr = "127.0.0.1",
    port = 9977,
    token_file = "/etc/astra-webui/agent.token",
}
M.deps = { channels = nil, dvb = nil, auth = nil, stream = nil }

function M.setup(cfg)
    if type(cfg) == "table" then
        for k, v in pairs(cfg) do M.cfg[k] = v end
    end
end

function M.set_deps(deps)
    for k, v in pairs(deps or {}) do M.deps[k] = v end
end

----------------------------------------------------------------------------
-- token compartido con el panel
----------------------------------------------------------------------------

function M.load_token()
    local text = util.read_file(M.cfg.token_file)
    if not text then return nil, "no se puede leer " .. tostring(M.cfg.token_file) end
    local token = util.trim(text)
    if token == "" then return nil, "el fichero de token está vacío" end
    M.token = token
    return token
end

local function check_token(ctx)
    if not M.token then
        M.load_token()
    end
    if not M.token then
        return false, "el agente no tiene token configurado"
    end

    local got = ctx:get("x-astra-token")
    if got == M.token then return true end

    -- puede haber rotado: se reintenta una vez
    if M.load_token() and util.trim(ctx:get("x-astra-token") or "") == M.token then
        return true
    end
    return false, "token de agente inválido"
end

----------------------------------------------------------------------------
-- utilidades de respuesta
----------------------------------------------------------------------------

local function started_uptime()
    return util.now() - M.started_at
end

local function require_id(ctx)
    local body = ctx.body
    if type(body) == "string" and body ~= "" then
        local obj, err = ctx:body_json()
        if not obj then return nil, err end
        if type(obj.id) == "string" and obj.id ~= "" then return obj.id end
        return nil, "falta el campo 'id'"
    end
    if type(ctx.query.id) == "string" and ctx.query.id ~= "" then return ctx.query.id end
    return nil, "falta el campo 'id'"
end

----------------------------------------------------------------------------
-- rutas
----------------------------------------------------------------------------

function M.routes()
    local channels = M.deps.channels
    local dvb = M.deps.dvb

    local routes = {
        {
            method = "GET", pattern = "^/health$",
            handler = function(ctx)
                return 200, {
                    ok = true,
                    version = astra.version,
                    agent_version = "1.0.0",
                    pid = util.self_pid(),
                    rss_kb = util.self_rss_kb(),
                    uptime = started_uptime(),
                    running = channels.running_count(),
                    config_error = channels.config_error or "",
                }
            end,
        },
        {
            method = "GET", pattern = "^/status$",
            handler = function(ctx)
                local st = channels.status()
                st.ok = true
                st.pid = util.self_pid()
                st.rss_kb = util.self_rss_kb()
                st.uptime = started_uptime()
                st.agent_version = "1.0.0"
                st.streaming = true
                if M.deps.stream then
                    st.stream_requests = M.deps.stream.requests
                    st.stream_rejected = M.deps.stream.rejected
                    st.stream_port = M.deps.stream.cfg.port
                end
                if #(channels.failed or {}) > 0 then
                    st.failed = channels.failed
                end
                return 200, st
            end,
        },
        {
            method = "GET", pattern = "^/metrics$",
            handler = function(ctx)
                local list = {}
                for _, id in ipairs(channels.order) do
                    local meta = channels.meta[id] or {}
                    local st = channels.stats[id] or {}
                    list[#list + 1] = {
                        id = id,
                        name = meta.name or id,
                        enabled = meta.enabled ~= false,
                        running = channels.runtime[id] ~= nil,
                        on_air = st.on_air == true,
                        bitrate = util.to_int(st.bitrate, 0),
                        viewers = M.deps.stream and M.deps.stream.viewers_of(id) or 0,
                    }
                end
                return 200, {
                    ok = true,
                    uptime = started_uptime(),
                    bitrate_total = channels.bitrate_total(),
                    viewers = M.deps.stream and M.deps.stream.viewers_total() or 0,
                    channels = list,
                }
            end,
        },
        {
            method = "POST", pattern = "^/reload$",
            handler = function(ctx)
                local report, changed, err = channels.reload(true)
                local auth_ok = true
                local auth_err = nil
                if M.deps.auth then
                    local ok, _, aerr = M.deps.auth.reload(true)
                    auth_ok = ok
                    auth_err = aerr
                end

                if not report then
                    return 200, {
                        ok = false,
                        error = err or "no se pudo aplicar la configuración",
                        changed = changed,
                        added = 0, updated = 0, removed = 0,
                        running = channels.running_count(),
                        auth_ok = auth_ok,
                        auth_error = auth_err or "",
                    }
                end

                local failed = {}
                for _, f in ipairs(report.failed or {}) do failed[#failed + 1] = f end

                return 200, {
                    ok = true,
                    changed = changed == true,
                    added = util.to_int(report.added, 0),
                    updated = util.to_int(report.updated, 0),
                    removed = util.to_int(report.removed, 0),
                    running = util.to_int(report.running, 0),
                    failed = failed,
                    auth_ok = auth_ok,
                    auth_error = auth_err or "",
                }
            end,
        },
        {
            method = "POST", pattern = "^/auth/reload$",
            handler = function(ctx)
                if not M.deps.auth then return 500, { ok = false, error = "auth no disponible" } end
                local ok, changed, err = M.deps.auth.reload(true)
                return 200, {
                    ok = ok == true,
                    changed = changed == true,
                    tokens = M.deps.auth.count(),
                    error = err or "",
                }
            end,
        },
        {
            method = "GET", pattern = "^/channel/psi$",
            handler = function(ctx)
                local id = ctx.query.id
                if util.is_empty(id) then return 400, { ok = false, error = "falta el parámetro id" } end
                local psi = channels.psi_of(id, ctx.query.input)
                local st = channels.stats[id]
                return 200, {
                    ok = true,
                    id = id,
                    tables = psi or {},
                    stats = {
                        bitrate = st and util.to_int(st.bitrate, 0) or 0,
                        on_air = st and st.on_air == true or false,
                    },
                }
            end,
        },
        {
            method = "POST", pattern = "^/channel/start$",
            handler = function(ctx)
                local id, err = require_id(ctx)
                if not id then return 400, { ok = false, error = err } end
                if channels.runtime[id] then
                    return 200, { ok = true, id = id, running = channels.running_count(), note = "ya estaba en marcha" }
                end

                local ch = channels.config and util.find_by(channels.config.channels, "id", id) or nil
                if not ch then return 404, { ok = false, error = "canal desconocido: " .. tostring(id) } end
                if ch.enabled == false then
                    return 200, { ok = false, error = "el canal está deshabilitado en la configuración" }
                end

                local conf, info, berr = channels.build_channel_conf(ch)
                if not conf then return 400, { ok = false, error = berr } end
                local started, serr = channels.start(id, conf, channels.signature(ch), info)
                channels.meta[id] = channels.meta[id] or { id = id, name = ch.name, enabled = true }
                return 200, {
                    ok = started == true,
                    id = id,
                    running = channels.running_count(),
                    error = serr or "",
                }
            end,
        },
        {
            method = "POST", pattern = "^/channel/stop$",
            handler = function(ctx)
                local id, err = require_id(ctx)
                if not id then return 400, { ok = false, error = err } end
                if not channels.runtime[id] then
                    return 200, { ok = true, id = id, note = "el canal no estaba en marcha" }
                end
                local ok, serr = channels.stop(id, { keep_dvb = false })
                return 200, { ok = ok == true, id = id, error = serr or "" }
            end,
        },
        {
            method = "POST", pattern = "^/channel/restart$",
            handler = function(ctx)
                local id, err = require_id(ctx)
                if not id then return 400, { ok = false, error = err } end
                local ch = channels.config and util.find_by(channels.config.channels, "id", id) or nil
                if not ch then return 404, { ok = false, error = "canal desconocido: " .. tostring(id) } end

                local conf, info, berr = channels.build_channel_conf(ch)
                if not conf then return 400, { ok = false, error = berr } end
                local ok, rerr = channels.restart(id, conf, channels.signature(ch), info)
                return 200, { ok = ok == true, id = id, error = rerr or "" }
            end,
        },
        {
            method = "GET", pattern = "^/dvb/adapters$",
            handler = function(ctx)
                local list, err = dvb.adapters()
                if not list then return 500, { ok = false, error = err } end
                return 200, { ok = true, adapters = list }
            end,
        },
        {
            method = "POST", pattern = "^/dvb/scan$",
            handler = function(ctx)
                local body, berr = ctx:body_json()
                if not body then return 400, { ok = false, error = berr } end
                local scan_id, err = dvb.start_scan(body)
                if not scan_id then return 400, { ok = false, error = err } end
                return 200, { ok = true, scan_id = scan_id }
            end,
        },
        {
            method = "GET", pattern = "^/dvb/scan$",
            handler = function(ctx)
                return 200, dvb.status(ctx.query.id)
            end,
        },
        {
            method = "POST", pattern = "^/dvb/scan/stop$",
            handler = function(ctx)
                local body = ctx:body_json() or {}
                local ok, err = dvb.stop_scan(body.id)
                return 200, { ok = ok == true, error = err or "" }
            end,
        },
        {
            method = "GET", pattern = "^/log/tail$",
            handler = function(ctx)
                local lines = util.to_int(ctx.query.lines, 200)
                if lines < 1 then lines = 1 end
                if lines > 5000 then lines = 5000 end

                local list, err = logm.tail(nil, lines)
                if not list then return 500, { ok = false, error = err } end

                local parsed = {}
                for _, line in ipairs(list) do
                    parsed[#parsed + 1] = logm.parse_line(line)
                end
                return 200, { ok = true, path = tostring(logm.path or ""), lines = parsed }
            end,
        },
        {
            method = "POST", pattern = "^/exit$",
            handler = function(ctx)
                M.exit_timer = timer({
                    interval = 1,
                    callback = function() astra.exit() end,
                })
                log.info("[astra-webui] salida solicitada por el panel")
                return 200, { ok = true, message = "astra finalizará en un segundo" }
            end,
        },
    }

    return routes
end

----------------------------------------------------------------------------
-- arranque
----------------------------------------------------------------------------

function M.start()
    local token, err = M.load_token()
    if not token then
        log.error("[astra-webui] " .. tostring(err))
        log.error("[astra-webui] la API de control rechazará todas las peticiones hasta que exista el token")
    end

    local router = http.router(M.routes(), {
        max_body = 524288,
        on_close = nil,
    })

    M.server = http_server({
        addr = M.cfg.addr,
        port = M.cfg.port,
        callback = function(self, client, data)
            local ok, cerr = pcall(function()
                -- comprobación de token (también en la desconexión, que es inocua)
                if data ~= nil and type(data) == "table" then
                    local st = self:data(client)
                    local headers = http.parse_headers(data.headers)
                    local ctx = {
                        headers = headers,
                        get = function(_, name) return headers[name:lower()] end,
                    }
                    local allowed, reason = check_token(ctx)
                    if not allowed then
                        local path = util.path_of(tostring(data.uri or "/"))
                        log.warning(string.format("[astra-webui] petición rechazada (%s) %s %s",
                                                  tostring(reason), tostring(data.method), path))
                        return http.send_error(self, client, 401, reason)
                    end
                end
                router(self, client, data)
            end)
            if not ok then
                log.error("[astra-webui] error en la API de control: " .. util.errstr(cerr))
                pcall(http.send_error, self, client, 500, util.errstr(cerr))
            end
        end,
    })

    log.info(string.format("[astra-webui] API de control en http://%s:%d",
                           tostring(M.cfg.addr), M.cfg.port))
    return M.server
end

return M
