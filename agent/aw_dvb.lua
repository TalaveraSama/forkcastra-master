-- Astra WebUI — Agente Lua
-- aw_dvb.lua: listado de adaptadores y escaneo de transponders.
--
-- El escaneo crea un `dvb_input` temporal por transponder y un `analyze` que
-- recoge PAT/SDT/PMT/CAT. Al terminar cada transponder se sueltan las
-- referencias y se fuerza el recolector, igual que hace stream.lua en
-- kill_input().
--
-- Aviso: un adaptador sólo admite un lector a la vez. Si hay canales DVB
-- emitiendo desde el mismo adaptador, el escaneo fallará (frontend ocupado).

local util = require("aw_util")
local channels = require("aw_channels")

local M = {}

M.scan = nil
M.last_scan = nil
M.history = {}

local MAX_HISTORY = 10

local function fail(fmt, ...)
    local msg = select("#", ...) > 0 and string.format(fmt, ...) or fmt
    return nil, msg
end

----------------------------------------------------------------------------
-- adaptadores
----------------------------------------------------------------------------

function M.adapters()
    if type(dvbls) ~= "function" then
        return fail("el módulo dvbls no está disponible en este binario")
    end

    local ok, list = pcall(dvbls)
    if not ok then return fail("dvbls() falló: %s", util.errstr(list)) end
    if type(list) ~= "table" then return fail("dvbls() no devolvió una lista") end

    local out = {}
    for _, a in ipairs(list) do
        if type(a) == "table" then
            out[#out + 1] = {
                adapter = util.to_int(a.adapter, 0),
                device = util.to_int(a.device, 0),
                frontend = tostring(a.frontend or "unknown"),
                mac = tostring(a.mac or ""),
                busy = a.busy == true,
                error = tostring(a.error or ""),
            }
        end
    end
    return out
end

----------------------------------------------------------------------------
-- escaneo
----------------------------------------------------------------------------

local function reset_current(scan)
    scan.pat, scan.sdt, scan.pmt, scan.cat = nil, nil, nil, nil
    scan.bitrate, scan.on_air = 0, false
    scan.tp_error = ""
    scan.tp_started = util.now()
end

local function collect_services(scan)
    local names = {}
    if type(scan.sdt) == "table" and type(scan.sdt.services) == "table" then
        for _, svc in pairs(scan.sdt.services) do
            if type(svc) == "table" then
                local name, provider = "", ""
                if type(svc.descriptors) == "table" then
                    for _, d in pairs(svc.descriptors) do
                        if type(d) == "table" and d.type_name == "service" then
                            name = tostring(d.service_name or "")
                            provider = tostring(d.service_provider or "")
                        end
                    end
                end
                names[util.to_int(svc.sid, 0)] = { name = name, provider = provider }
            end
        end
    end

    local pat = {}
    if type(scan.pat) == "table" and type(scan.pat.programs) == "table" then
        for _, p in pairs(scan.pat.programs) do
            if type(p) == "table" then
                local pnr = util.to_int(p.pnr, 0)
                if pnr > 0 then pat[pnr] = util.to_int(p.pid, 0) end
            end
        end
    end

    local pnrs = {}
    for pnr, _ in pairs(pat) do pnrs[#pnrs + 1] = pnr end
    table.sort(pnrs)

    local services = {}
    for _, pnr in ipairs(pnrs) do
        local pmt = scan.pmt and scan.pmt[pnr] or nil
        local info = names[pnr]
        local scrambled, video, audio = false, 0, 0

        if type(pmt) == "table" then
            if type(pmt.descriptors) == "table" then
                for _, d in pairs(pmt.descriptors) do
                    if type(d) == "table" and d.type_name == "cas" then scrambled = true end
                end
            end
            if type(pmt.streams) == "table" then
                for _, s in pairs(pmt.streams) do
                    if type(s) == "table" then
                        local tn = tostring(s.type_name or "")
                        local pid = util.to_int(s.pid, 0)
                        if tn == "video" and video == 0 then
                            video = pid
                        elseif tn == "audio" and audio == 0 then
                            audio = pid
                        end
                    end
                end
            end
        end

        services[#services + 1] = {
            pnr = pnr,
            sid = pnr,
            pmt_pid = util.to_int(pat[pnr], 0),
            name = info and info.name or "",
            provider = info and info.provider or "",
            scrambled = scrambled,
            video_pid = video,
            audio_pid = audio,
        }
    end

    -- servicios anunciados en SDT que no aparecen en PAT
    for sid, info in pairs(names) do
        local found = false
        for _, s in ipairs(services) do
            if s.sid == sid then found = true break end
        end
        if not found and sid > 0 then
            services[#services + 1] = {
                pnr = 0, sid = sid, pmt_pid = 0,
                name = info.name or "", provider = info.provider or "",
                scrambled = false, video_pid = 0, audio_pid = 0,
            }
        end
    end

    return services
end

local function finish_current(scan)
    if not scan.current then return end

    local services = collect_services(scan)
    local result = {
        index = scan.index,
        type = scan.current.type,
        frequency = util.to_int(scan.current.frequency, 0),
        polarization = tostring(scan.current.polarization or ""),
        symbolrate = util.to_int(scan.current.symbolrate, 0),
        tsid = (type(scan.pat) == "table" and util.to_int(scan.pat.tsid, 0)) or 0,
        bitrate = util.to_int(scan.bitrate, 0),
        on_air = scan.on_air == true,
        services_count = #services,
        services = services,
        error = scan.tp_error or "",
    }

    scan.results[#scan.results + 1] = result
    for _, svc in ipairs(services) do
        scan.services[#scan.services + 1] = {
            pnr = svc.pnr, sid = svc.sid, pmt_pid = svc.pmt_pid,
            name = svc.name, provider = svc.provider, scrambled = svc.scrambled,
            video_pid = svc.video_pid, audio_pid = svc.audio_pid,
            transponder = {
                type = result.type,
                frequency = result.frequency,
                polarization = result.polarization,
                symbolrate = result.symbolrate,
                tsid = result.tsid,
            },
        }
    end

    log.info(string.format("[astra-webui] escaneo %s: TP %s %s %s → %d servicios (%d kbit/s)",
                           tostring(scan.id), tostring(result.frequency), tostring(result.polarization),
                           tostring(result.symbolrate), #services, result.bitrate))

    -- soltar el transponder actual
    scan.an = nil
    scan.dev = nil
    scan.current = nil
    collectgarbage("collect")
end

local function start_current(scan)
    local tp = scan.transponders[scan.index]
    if not tp then return end

    local opts, err = channels.dvb_options(tp)
    if not opts then
        scan.tp_error = err or "transponder inválido"
        scan.current = tp
        scan.tp_started = util.now() - scan.duration -- cierra de inmediato
        log.error("[astra-webui] transponder inválido en el escaneo: " .. tostring(err))
        return
    end

    scan.current = tp
    scan.tp_error = ""
    scan.pat, scan.sdt, scan.pmt, scan.cat = nil, nil, nil, nil
    scan.bitrate, scan.on_air = 0, false
    scan.tp_started = util.now()

    local ok, dev = pcall(dvb_input, opts)
    if not ok or dev == nil then
        scan.tp_error = "no se pudo abrir el adaptador: " .. util.errstr(dev)
        log.error("[astra-webui] " .. scan.tp_error)
        scan.dev, scan.an = nil, nil
        return
    end
    scan.dev = dev

    local ok2, an = pcall(analyze, {
        upstream = dev:stream(),
        name = "scan",
        callback = function(data)
            if type(data) ~= "table" or M.scan ~= scan then return end
            if data.error then
                scan.tp_error = tostring(data.error)
            end
            if data.psi == "pat" then
                scan.pat = util.deepcopy(data, 0)
            elseif data.psi == "sdt" then
                scan.sdt = util.deepcopy(data, 0)
            elseif data.psi == "pmt" then
                scan.pmt = scan.pmt or {}
                scan.pmt[util.to_int(data.pnr, 0)] = util.deepcopy(data, 0)
            elseif data.psi == "cat" then
                scan.cat = util.deepcopy(data, 0)
            elseif type(data.analyze) == "table" then
                local bitrate = 0
                for _, pid in pairs(data.analyze) do
                    if type(pid) == "table" then
                        bitrate = bitrate + (util.to_int(pid.bitrate, 0) or 0)
                    end
                end
                scan.bitrate = bitrate
                scan.on_air = data.on_air == true
            end
        end,
    })

    if not ok2 or an == nil then
        scan.tp_error = "no se pudo crear el analizador: " .. util.errstr(an)
        scan.dev, scan.an = nil, nil
        collectgarbage("collect")
        return
    end
    scan.an = an
end

local function finish_scan(scan, state)
    scan.state = state or "done"
    scan.finished = util.now()
    if scan.timer then
        pcall(scan.timer.close, scan.timer)
    end
    scan.timer = nil
    scan.an, scan.dev = nil, nil
    collectgarbage("collect")

    M.last_scan = scan
    M.history[#M.history + 1] = scan
    if #M.history > MAX_HISTORY then table.remove(M.history, 1) end

    log.info(string.format("[astra-webui] escaneo %s %s: %d transponders, %d servicios",
                           tostring(scan.id), tostring(scan.state), #scan.results, #scan.services))
end

function M.tick()
    local scan = M.scan
    if not scan or scan.state ~= "running" then return end

    local now = util.now()

    if scan.current then
        if now - scan.tp_started >= scan.duration then
            finish_current(scan)
            scan.index = scan.index + 1
            if scan.index > #scan.transponders then
                finish_scan(scan, "done")
                return
            end
        else
            return
        end
    end

    if scan.index <= #scan.transponders then
        start_current(scan)
    else
        finish_scan(scan, "done")
    end
end

function M.start_scan(req)
    if type(req) ~= "table" then return fail("petición inválida") end
    if M.scan and M.scan.state == "running" then
        return fail("ya hay un escaneo en curso (%s)", tostring(M.scan.id))
    end
    if type(req.transponders) ~= "table" or #req.transponders == 0 then
        return fail("se requiere la lista 'transponders'")
    end
    if #req.transponders > 512 then
        return fail("demasiados transponders (máximo 512)")
    end

    local duration = util.to_int(req.duration, 8)
    if duration < 3 then duration = 3 end
    if duration > 120 then duration = 120 end

    local scan = {
        id = util.new_id("scan_"),
        state = "running",
        adapter = util.to_int(req.adapter, 0),
        device = util.to_int(req.device, 0),
        dve_type = tostring(req.type or ""),
        duration = duration,
        transponders = req.transponders,
        index = 1,
        total = #req.transponders,
        services = {},
        results = {},
        started = util.now(),
        finished = 0,
        current = nil,
        dev = nil,
        an = nil,
        timer = nil,
        tp_error = "",
        bitrate = 0,
        on_air = false,
    }

    M.scan = scan
    scan.timer = timer({
        interval = 1,
        callback = function() M.tick() end,
    })

    M.tick()
    return scan.id
end

function M.stop_scan(id)
    local scan = M.scan
    if not scan or scan.state ~= "running" then return fail("no hay escaneo en curso") end
    if id and id ~= "" and id ~= scan.id then return fail("id de escaneo incorrecto") end

    if scan.timer then
        pcall(scan.timer.close, scan.timer)
    end
    if scan.current then
        finish_current(scan)
    end
    finish_scan(scan, "stopped")
    return true
end

function M.status(id)
    local scan = M.scan
    if (not scan) or (id and id ~= "" and id ~= scan.id) then
        scan = M.last_scan
    end
    if not scan then
        return { ok = true, scan = { state = "idle", id = "", total = 0, index = 0, services = {}, results = {} } }
    end

    local current = { frequency = 0, polarization = "", symbolrate = 0, type = "" }
    if type(scan.current) == "table" then
        current = {
            frequency = util.to_int(scan.current.frequency, 0),
            polarization = tostring(scan.current.polarization or ""),
            symbolrate = util.to_int(scan.current.symbolrate, 0),
            type = tostring(scan.current.type or ""),
        }
    end

    return {
        ok = true,
        scan = {
            id = scan.id,
            state = scan.state,
            adapter = util.to_int(scan.adapter, 0),
            device = util.to_int(scan.device, 0),
            duration = util.to_int(scan.duration, 8),
            index = util.to_int(scan.index, 0),
            total = util.to_int(scan.total, 0),
            started = util.to_int(scan.started, 0),
            finished = util.to_int(scan.finished, 0),
            current = current,
            bitrate = util.to_int(scan.bitrate, 0),
            on_air = scan.on_air == true,
            error = scan.tp_error or "",
            services = scan.services,
            results = scan.results,
        },
    }
end

return M
