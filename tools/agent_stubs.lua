-- Astra WebUI — stubs del core para pruebas del agente (tools/lua_selftest.py)
--
-- Sustituye los globales que el core (Astra 4.0) inyecta en Lua:
--   log, astra, utils, base64, json, http_server, timer, http_request, transmit,
--   analyze, channel, decrypt, udp_input/output, file_input/output, dvb_input, dvbls
--
-- Las funciones TEST_* las inyecta Python antes de cargar este fichero.

local SERVERS = {}
local TIMERS = {}
local MODULES = {}
local ANALYZERS = {}
local DVB_INPUTS = {}

_G.SERVERS = SERVERS
_G.TIMERS = TIMERS
_G.MODULE_CALLS = MODULES
_G.ANALYZERS = ANALYZERS
_G.DVB_INPUTS = DVB_INPUTS
_G.TEST_EXIT_CALLED = false

----------------------------------------------------------------------------
-- log
----------------------------------------------------------------------------

local function logf(level, msg)
    TEST_LOG(level, tostring(msg))
end

log = {
    set = function(opts) TEST_LOG_SET(opts) end,
    error = function(msg) logf("ERROR", msg) end,
    warning = function(msg) logf("WARNING", msg) end,
    info = function(msg) logf("INFO", msg) end,
    debug = function(msg) logf("DEBUG", msg) end,
}

----------------------------------------------------------------------------
-- astra
----------------------------------------------------------------------------

astra = {
    version = "4.0 dev:282 (stub)",
    debug = false,
    exit = function() _G.TEST_EXIT_CALLED = true end,
    abort = function() error("astra_abort() en el código bajo prueba", 2) end,
}

----------------------------------------------------------------------------
-- utils / base64 / hashes / json
----------------------------------------------------------------------------

utils = {
    hostname = function() return "testhost" end,
    stat = function(path) return TEST_STAT(path) end,
    readdir = function(path) return function() return nil end end,
}

base64 = {
    encode = function(s) return TEST_B64_ENCODE(s) end,
    decode = function(s) return TEST_B64_DECODE(s) end,
}

md5 = function(s) return TEST_MD5(s) end
sha1 = function(s) return TEST_SHA1(s) end
str2hex = function(s) return s end

json = {
    encode = function(t) return TEST_JSON_ENCODE(t) end,
    decode = function(s) return TEST_JSON_DECODE(s) end,
}

----------------------------------------------------------------------------
-- http_server
----------------------------------------------------------------------------

function http_server(opts)
    local obj = {
        addr = opts.addr,
        port = opts.port,
        callback = opts.callback,
        kind = "http_server",
        clients = {},
    }

    obj.send = function(self, client, data)
        client.responses[#client.responses + 1] = data
        return 0
    end

    obj.close = function(self, client)
        client.closed = true
    end

    obj.data = function(self, client)
        if not client.data then client.data = {} end
        return client.data
    end

    obj.port = function(self) return self.port end

    SERVERS[#SERVERS + 1] = obj
    return obj
end

----------------------------------------------------------------------------
-- timer
----------------------------------------------------------------------------

function timer(opts)
    local t = {
        interval = opts.interval,
        callback = opts.callback,
        closed = false,
        kind = "timer",
    }
    t.close = function(self) self.closed = true end
    TIMERS[#TIMERS + 1] = t
    return t
end

-- Ejecuta un tick de todos los temporizadores vivos
function TEST_TICK_TIMERS()
    local fired = 0
    for _, t in ipairs(TIMERS) do
        if not t.closed then
            t.callback(t)
            fired = fired + 1
        end
    end
    return fired
end

----------------------------------------------------------------------------
-- módulos de streaming
----------------------------------------------------------------------------

local function module_stub(name, opts)
    local obj = {
        _name = name,
        _opts = opts,
        _closed = false,
        kind = name,
    }
    obj.stream = function(self) return self end
    obj.set_upstream = function(self, upstream) self._upstream = upstream end
    obj.ca_set_pnr = function(self, pnr, on)
        self._cam_pnr = pnr
        self._cam_on = on
        return true
    end
    obj.close = function(self) self._closed = true end
    obj.status = function(self) return { size = 0 } end
    MODULES[#MODULES + 1] = obj
    return obj
end

function transmit(opts) return module_stub("transmit", opts) end
function channel(opts) return module_stub("channel", opts) end
function decrypt(opts) return module_stub("decrypt", opts) end
function mixaudio(opts) return module_stub("mixaudio", opts) end
function udp_input(opts) return module_stub("udp_input", opts) end
function udp_output(opts) return module_stub("udp_output", opts) end
function file_input(opts) return module_stub("file_input", opts) end
function file_output(opts) return module_stub("file_output", opts) end

function http_request(opts)
    local obj = module_stub("http_request", opts)
    obj._opts = opts
    return obj
end

function analyze(opts)
    local obj = module_stub("analyze", opts)
    obj._callback = opts.callback
    obj.feed = function(self, data)
        if self._callback then self._callback(data) end
    end
    ANALYZERS[#ANALYZERS + 1] = obj
    return obj
end

function dvb_input(opts)
    local obj = module_stub("dvb_input", opts)
    obj._opts = opts
    DVB_INPUTS[#DVB_INPUTS + 1] = obj
    return obj
end

function dvbls()
    return TEST_DVBLS()
end

function ddci(opts) return module_stub("ddci", opts) end
function asi_input(opts) return module_stub("asi_input", opts) end

----------------------------------------------------------------------------
-- os.execute: simula el proceso hijo del escaneo DVB (aw_scan.lua)
--
-- El escaneo real se ejecuta en un proceso aparte para que un fallo de sintonía no
-- tumbe astra. Aquí se sustituye os.execute por un simulador que:
--   * acepta `mkdir -p`, `kill -0` y `kill -s KILL`;
--   * cuando ve el comando de aw_scan.lua, escribe el fichero de resultado (y el del
--     pid) como haría el hijo, tomando el contenido de SCAN_CHILD_RESULTS[n].
-- La prueba rellena SCAN_CHILD_RESULTS y puede poner SCAN_CHILD_ALIVE = false para
-- simular un hijo que muere sin escribir nada (tune rechazado por el driver).
----------------------------------------------------------------------------

local real_execute = os.execute

SCAN_CHILD_RESULTS = _G.SCAN_CHILD_RESULTS or {}
SCAN_CHILD_ALIVE = true
SCAN_CHILD_CALLS = 0
SCAN_CHILD_COMMANDS = {}

os.execute = function(cmd)
    if type(cmd) ~= "string" then return real_execute(cmd) end

    if cmd:find("mkdir", 1, true) then return true, "exit", 0 end
    if cmd:find("kill -0", 1, true) then
        if SCAN_CHILD_ALIVE then return true, "exit", 0 end
        return nil, "exit", 1
    end
    if cmd:find("kill -s KILL", 1, true) or cmd:find("kill ", 1, true) then
        return true, "exit", 0
    end

    if cmd:find("aw_scan.lua", 1, true) then
        SCAN_CHILD_CALLS = SCAN_CHILD_CALLS + 1
        SCAN_CHILD_COMMANDS[#SCAN_CHILD_COMMANDS + 1] = cmd

        local out = cmd:match("out=([^%s]+)")
        local pid = cmd:match("echo %$! > ([^%s]+)")

        if pid then
            local handle = io.open(pid, "w")
            if handle then handle:write("4242") handle:close() end
        end

        local payload = SCAN_CHILD_RESULTS[SCAN_CHILD_CALLS]
        if out and payload then
            local handle = io.open(out, "w")
            if handle then
                handle:write(json.encode(payload))
                handle:close()
            end
        end
        return true, "exit", 0
    end

    return real_execute(cmd)
end
