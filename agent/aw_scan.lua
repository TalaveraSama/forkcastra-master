-- Astra WebUI — Agente Lua
-- aw_scan.lua: escaneo de UN transponder en un proceso aparte.
--
-- POR QUÉ EXISTE ESTE FICHERO
-- El módulo `dvb` del núcleo llama a astra_abort() cuando el sintonizador rechaza el
-- tune (FE_SET_PROPERTY DTV_TUNE failed, típico cuando no hay señal, el transponder no
-- es el correcto o el driver no acepta los parámetros). Ese abort termina el proceso
-- ENTERO: si el escaneo corriera dentro del agente se llevaría por delante el streaming
-- y el panel se quedaría sin agente («El agente Astra no responde»).
--
-- Por eso el escaneo se lanza como proceso hijo: si aborta, sólo muere el escaneo y el
-- agente lo informa como «no se pudo sintonizar» y sigue con el siguiente transponder.
--
-- Uso (lo construye aw_dvb.lua):
--   astra aw_scan.lua out=/tmp/x.json seconds=8 adapter=0 device=0 delivery=S2 \
--         frequency=12000 symbolrate=27500 polarization=H lof1=9750 lof2=10600 slof=11700
--
-- Escribe en `out` un JSON compatible con el `json` del core (sin decimales ni \uXXXX):
--   { "state": "starting"|"running"|"done"|"error", "ok": true|false, "error": "",
--     "bitrate": 0, "on_air": false, "pat": {...}, "sdt": {...}, "pmt": {...}, "cat": {...} }

do
    local dir = "/usr/share/astra-webui/agent"
    package.path = dir .. "/?.lua;" .. package.path
end

local jsonm = require("aw_json")

local out = "/tmp/astra-webui-scan.json"
local seconds = 8
local opts = { type = "dvb" }

local NUMERIC = {
    adapter = true, device = true, frequency = true, symbolrate = true,
    lof1 = true, lof2 = true, slof = true, diseqc = true, tone = true,
    stream_id = true, lnb_sharing = true,
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

local state = {
    state = "starting",
    ok = false,
    error = "",
    bitrate = 0,
    on_air = false,
}

local function write_state()
    local handle = io.open(out, "w")
    if not handle then return end
    local text = jsonm.encode(state)
    if text then handle:write(text) end
    handle:close()
end

local function finish(st, err)
    state.state = st
    state.ok = (st == "done")
    if err then state.error = tostring(err) end
    write_state()
    os.exit(0)
end

write_state()

-- Los módulos del core son TABLAS invocables (metatabla __call), no funciones:
-- hay que aceptar los dos casos antes de llamarlos.
local dvb_module = dvb_input
if type(dvb_module) ~= "function" and type(dvb_module) ~= "table" then
    finish("error", "este binario no incluye el módulo dvb")
end

-- A partir de aquí, si el tune falla el núcleo aborta ESTE proceso (y sólo este).
local ok, dev = pcall(dvb_module, opts)
if not ok or dev == nil then
    finish("error", "no se pudo abrir el sintonizador: " .. tostring(dev))
end

state.state = "running"
write_state()

local analyzer_ok, an = pcall(analyze, {
    upstream = dev:stream(),
    name = "scan",
    callback = function(data)
        if type(data) ~= "table" then return end
        if data.error then state.error = tostring(data.error) end

        if data.psi == "pat" then
            state.pat = data
        elseif data.psi == "sdt" then
            state.sdt = data
        elseif data.psi == "cat" then
            state.cat = data
        elseif data.psi == "pmt" then
            state.pmt = state.pmt or {}
            state.pmt[tonumber(data.pnr) or 0] = data
            write_state()
            -- con PAT + SDT + una PMT ya hay suficiente para el panel
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
        end
        write_state()
    end,
})

if not analyzer_ok or an == nil then
    finish("error", "no se pudo crear el analizador: " .. tostring(an))
end

-- El temporizador del núcleo repite; basta con cerrar la primera vez.
local closed = false
timer({
    interval = math.max(1, seconds) * 1000,
    callback = function()
        if closed then return end
        closed = true
        finish("done")
    end,
})

astra.start()
