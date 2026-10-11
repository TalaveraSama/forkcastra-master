-- Astra WebUI — Agente Lua
-- aw_scan.lua: escaneo de UN transponder en un proceso aparte.
--
-- POR QUÉ EXISTE ESTE FICHERO
-- El módulo `dvb` del núcleo llama a astra_abort() cuando el sintonizador rechaza el
-- tune (FE_SET_PROPERTY DTV_TUNE failed, típico cuando no hay señal o el transponder no
-- es el correcto). Ese abort termina el proceso ENTERO: si el escaneo corriera dentro
-- del agente se llevaría por delante el streaming y el panel se quedaría sin agente.
--
-- Por eso el escaneo se lanza como proceso hijo: si aborta, sólo muere el escaneo y el
-- agente lo informa como «no se pudo sintonizar» y sigue con el siguiente transponder.
--
-- CLAVES APRENDIDAS EN EL HARDWARE REAL (TBS 6904se) — no quitar ninguna:
--   1. `budget = 1`: abre UN filtro del demux (el transporte completo). Sin esto el
--      módulo dvb espera a que alguien pida PID a PID y no llega ni un paquete.
--   2. Un CONSUMIDOR (`transmit`) que tire de la cadena, igual que hace stream.lua.
--      Sin consumidor el demux tampoco entrega datos al analizador.
--   3. El dispositivo y el analizador en variables GLOBALES + GC parado: si son
--      locales, el recolector de Lua los libera mientras el hilo del dvb sigue
--      enviando paquetes → segmentation fault (use-after-free comprobado con gdb).
--   4. El temporizador del núcleo va en SEGUNDOS (no milisegundos).
--   5. El JSON se escribe sólo si se pudo codificar (si no, se pierde lo anterior).
--   6. Sólo se envían al agente los campos que necesita, ya normalizados: las tablas
--      PSI crudas del core no siempre se pueden codificar y rompían el fichero.
--
-- Uso (lo construye aw_dvb.lua):
--   astra aw_scan.lua out=/tmp/x.json seconds=8 adapter=0 device=0 delivery=S2 \
--         frequency=10970 symbolrate=27900 polarization=V lof1=9750 lof2=10600 slof=11700

do
    local dir = "/usr/share/astra-webui/agent"
    package.path = dir .. "/?.lua;" .. package.path
end

local jsonm = require("aw_json")

local out = "/tmp/astra-webui-scan.json"
local seconds = 8
local opts = {}

local NUMERIC = {
    adapter = true, device = true, frequency = true, symbolrate = true,
    lof1 = true, lof2 = true, slof = true, diseqc = true, tone = true,
    stream_id = true, lnb_sharing = true, budget = true, buffer_size = true,
}

-- El núcleo expone los argumentos en la tabla global `argv` (no en `arg`).
local args = (type(argv) == "table" and argv) or (type(arg) == "table" and arg) or {}

for _, item in ipairs(args) do
    local key, value = tostring(item):match("^([%w_]+)=(.*)$")
    if key == "out" then
        out = value
    elseif key == "seconds" then
        seconds = tonumber(value) or seconds
    elseif key and value ~= "" then
        if NUMERIC[key] then
            opts[key] = tonumber(value) or 0
        else
            opts[key] = value
        end
    end
end

-- Red de seguridad: sin 'type' el core no sabe qué frontend configurar.
if type(opts.type) ~= "string" or opts.type == "" or opts.type:lower() == "dvb" then
    opts.type = "S2"
end

-- Clave 1: filtro de transporte completo.
if not opts.budget or opts.budget < 1 then
    opts.budget = 1
end

local state = {
    state = "starting",
    ok = false,
    error = "",
    bitrate = 0,
    on_air = false,
}

-- Clave 5: nunca truncar el fichero si la codificación falla.
local function write_state()
    local text = jsonm.encode(state)
    if not text then return end
    local tmp = out .. ".tmp"
    local handle = io.open(tmp, "w")
    if not handle then return end
    handle:write(text)
    handle:close()
    os.remove(out)
    os.rename(tmp, out)
end

local function finish(st, err)
    state.state = st
    state.ok = (st == "done")
    if err then state.error = tostring(err) end
    write_state()
    pcall(collectgarbage, "restart")
    os.exit(0)
end

write_state()

-- Los módulos del core son TABLAS invocables (metatabla __call), no funciones.
local dvb_module = dvb_input
if type(dvb_module) ~= "function" and type(dvb_module) ~= "table" then
    finish("error", "este binario no incluye el módulo dvb")
end

-- A partir de aquí, si el tune falla el núcleo aborta ESTE proceso (y sólo este).
local ok, dev = pcall(dvb_module, opts)
if not ok or dev == nil then
    finish("error", "no se pudo abrir el sintonizador: " .. tostring(dev))
end

-- Clave 3: referencias GLOBALES (si no, el GC libera el módulo y el núcleo revienta).
SCAN_DEV = dev
collectgarbage("stop")

state.state = "running"
write_state()

----------------------------------------------------------------------------
-- Normalización de las tablas PSI (clave 6): sólo lo que usa el agente
----------------------------------------------------------------------------

local function num(value) return tonumber(value) or 0 end
local function text(value) return tostring(value or "") end

local function clean_pat(data)
    local result = { psi = "pat", tsid = num(data.tsid), programs = {} }
    if type(data.programs) == "table" then
        for _, p in pairs(data.programs) do
            if type(p) == "table" then
                result.programs[#result.programs + 1] = { pnr = num(p.pnr), pid = num(p.pid) }
            end
        end
    end
    return result
end

local function clean_sdt(data)
    local result = { psi = "sdt", tsid = num(data.tsid), services = {} }
    if type(data.services) == "table" then
        for _, s in pairs(data.services) do
            if type(s) == "table" then
                local svc = { sid = num(s.sid), descriptors = {} }
                if type(s.descriptors) == "table" then
                    for _, d in pairs(s.descriptors) do
                        if type(d) == "table" and d.type_name == "service" then
                            svc.descriptors[#svc.descriptors + 1] = {
                                type_name = "service",
                                service_name = text(d.service_name),
                                service_provider = text(d.service_provider),
                            }
                        end
                    end
                end
                result.services[#result.services + 1] = svc
            end
        end
    end
    return result
end

local function clean_pmt(data)
    local result = {
        psi = "pmt", pnr = num(data.pnr), pid = num(data.pid), crc32 = num(data.crc32),
        descriptors = {}, streams = {},
    }
    if type(data.descriptors) == "table" then
        for _, d in pairs(data.descriptors) do
            if type(d) == "table" and d.type_name == "cas" then
                result.descriptors[#result.descriptors + 1] = { type_name = "cas" }
            end
        end
    end
    if type(data.streams) == "table" then
        for _, s in pairs(data.streams) do
            if type(s) == "table" then
                result.streams[#result.streams + 1] = {
                    pid = num(s.pid), type_name = text(s.type_name), type_id = num(s.type_id),
                }
            end
        end
    end
    return result
end

local function clean_cat(data)
    return { psi = "cat", pid = num(data.pid), crc32 = num(data.crc32) }
end

----------------------------------------------------------------------------
-- Analizador
----------------------------------------------------------------------------

local analyzer_ok, an = pcall(analyze, {
    upstream = dev:stream(),
    name = "scan",
    callback = function(data)
        if type(data) ~= "table" then return end
        if data.error then state.error = tostring(data.error) end

        if data.psi == "pat" then
            state.pat = clean_pat(data)
            write_state()
        elseif data.psi == "sdt" then
            state.sdt = clean_sdt(data)
            write_state()
        elseif data.psi == "cat" then
            state.cat = clean_cat(data)
            write_state()
        elseif data.psi == "pmt" then
            state.pmt = state.pmt or {}
            state.pmt[#state.pmt + 1] = clean_pmt(data)
            write_state()
            -- con PAT + SDT + alguna PMT ya hay bastante para el panel
            if state.pat and state.sdt then finish("done") end
            return
        elseif type(data.analyze) == "table" then
            local total = 0
            for _, pid in pairs(data.analyze) do
                if type(pid) == "table" then
                    total = total + (tonumber(pid.bitrate) or 0)
                end
            end
            state.bitrate = total
            state.on_air = data.on_air == true
            write_state()
        end
    end,
})

if not analyzer_ok or an == nil then
    finish("error", "no se pudo crear el analizador: " .. tostring(an))
end

-- Clave 3 (bis)
SCAN_AN = an

-- Clave 2: consumidor que tira de la cadena.
SCAN_TX = transmit({})
local tx_ok, tx_err = pcall(function() SCAN_TX:set_upstream(dev:stream()) end)
if not tx_ok then
    state.error = "no se pudo conectar el consumidor del escaneo: " .. tostring(tx_err)
    write_state()
end

-- Clave 4: el intervalo va en SEGUNDOS. El temporizador se repite: cerrar la 1ª vez.
local closed = false
timer({
    interval = math.max(1, seconds),
    callback = function()
        if closed then return end
        closed = true
        finish("done")
    end,
})

-- OJO: aquí NO se llama a astra.start(): no existe en el core y provoca un PANIC que
-- mata al hijo. Al terminar el script, astra sigue con su bucle de eventos.
