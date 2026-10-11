# -*- coding: utf-8 -*-
"""Rutas DVB: adaptadores, satélites/transponders, escaneo y creación de canales."""

from __future__ import annotations

from . import agentclient, httpd, logs, util, validators

SAT_ID = r"(?P<sat_id>[A-Za-z0-9_\-]{3,32})"
TP_ID = r"(?P<tp_id>[A-Za-z0-9_\-]{3,32})"
SCAN_ID = r"(?P<scan_id>[A-Za-z0-9_\-]{3,64})"

UHF_MAX = 69
UHF_MIN = 21


def _cam_del_panel(app):
    """CAM configurado en Ajustes → Tarjeta / CAM, para que los canales nazcan con él.

    Si no hay CAM configurado se registran igual: el escaneo no depende de la tarjeta.
    """
    try:
        from . import api_cam  # noqa: PLC0415 (evita ciclos de importación)
        return api_cam._cam_guardado(app)
    except Exception:  # noqa: BLE001
        return None


def _uhf_frequency(channel):
    """Frecuencia central (MHz) del canal UHF indicado (Europa, 8 MHz)."""
    return int(474 + (channel - UHF_MIN) * 8)


def _expand_transponders(body):
    """Devuelve (lista_de_transponders, error)."""
    raw = body.get("transponders")
    if isinstance(raw, list) and raw:
        if len(raw) > 512:
            return None, "demasiados transponders (máximo 512)"
        return raw, None

    if util.sanitize_text(body.get("band"), 8).lower() in ("uhf", "t", "t2"):
        first = util.to_int(body.get("channel_first"), UHF_MIN, minimum=UHF_MIN, maximum=UHF_MAX)
        last = util.to_int(body.get("channel_last"), UHF_MAX, minimum=UHF_MIN, maximum=UHF_MAX)
        if last < first:
            first, last = last, first
        if last - first > 120:
            return None, "el rango de canales UHF es demasiado amplio"
        delivery = util.sanitize_text(body.get("delivery"), 8).upper() or "T2"
        out = []
        for number in range(first, last + 1):
            out.append({
                "type": "dvb",
                "delivery": delivery,
                "adapter": util.to_int(body.get("adapter"), 0, minimum=0, maximum=63),
                "device": util.to_int(body.get("device"), 0, minimum=0, maximum=63),
                "frequency": _uhf_frequency(number),
                "bandwidth": util.sanitize_text(body.get("bandwidth"), 8).upper() or "8MHZ",
                "guardinterval": util.sanitize_text(body.get("guardinterval"), 8).upper() or "AUTO",
                "transmitmode": util.sanitize_text(body.get("transmitmode"), 8).upper() or "AUTO",
                "hierarchy": "AUTO",
                "stream_id": util.to_int(body.get("stream_id"), 0, minimum=0, maximum=65535),
                "channel": number,
            })
        return out, None

    if body.get("frequency"):
        return [{
            "type": "dvb",
            "delivery": util.sanitize_text(body.get("delivery"), 8).upper() or "S2",
            "adapter": util.to_int(body.get("adapter"), 0, minimum=0, maximum=63),
            "device": util.to_int(body.get("device"), 0, minimum=0, maximum=63),
            "frequency": util.to_int(body.get("frequency"), 0),
            "polarization": util.sanitize_text(body.get("polarization"), 2).upper() or "H",
            "symbolrate": util.to_int(body.get("symbolrate"), 0),
            "lof1": util.to_int(body.get("lof1"), 9750),
            "lof2": util.to_int(body.get("lof2"), 10600),
            "slof": util.to_int(body.get("slof"), 11700),
            "diseqc": util.to_int(body.get("diseqc"), 0, minimum=0, maximum=4),
        }], None

    return None, "indica 'transponders', 'satellite_id', un rango UHF o una frecuencia"


def _canales_del_transponder(app, tp):
    """Canales cuyo sintonizador apunta a ese transponder (frecuencia+polarización+rate)."""
    if not isinstance(tp, dict):
        return []
    frecuencia = util.to_int(tp.get("frequency"), 0)
    polarizacion = util.sanitize_text(tp.get("polarization"), 2).upper()
    rate = util.to_int(tp.get("symbolrate"), 0)
    salida = []
    for canal in app.channels.list():
        for entrada in canal.get("inputs") or []:
            if not isinstance(entrada, dict):
                continue
            if util.sanitize_text(entrada.get("type"), 16).lower() != "dvb":
                continue
            if util.to_int(entrada.get("frequency"), 0) != frecuencia:
                continue
            if polarizacion and util.sanitize_text(entrada.get("polarization"), 2).upper() != polarizacion:
                continue
            if rate and util.to_int(entrada.get("symbolrate"), 0) != rate:
                continue
            salida.append(canal)
            break
    return salida


def _buscar_tp(app, sat_id, tp_id):
    """Busca el transponder por id dentro del satélite (DvbDB no tiene getter directo)."""
    try:
        for item in app.dvb.transponders(sat_id) or []:
            if isinstance(item, dict) and item.get("id") == tp_id:
                return item
    except Exception:  # noqa: BLE001
        return None
    return None


def _borrar_canales(app, canales, user, ip):
    """Borra los canales indicados y recarga el agente. Devuelve cuántos."""
    borrados = 0
    for canal in canales:
        try:
            if app.channels.delete(canal.get("id"), user, ip):
                borrados += 1
        except Exception:  # noqa: BLE001
            continue
    if borrados:
        try:
            from . import api_channels  # noqa: PLC0415
            api_channels._agent_apply(app, user, ip, "dvb.cascade", "", "%d canales" % borrados)
        except Exception:  # noqa: BLE001
            pass
    return borrados



def register(app):
    router = app.router

    @router.add("GET", "/api/dvb/adapters")
    def adapters(req):
        try:
            payload = app.agent.adapters()
        except agentclient.AgentError as exc:
            raise httpd.ApiError(502, str(exc))
        payload["ok"] = True
        payload["assigned"] = _count_dvb_channels(app)
        return payload

    @router.add("GET", "/api/dvb/transponders")
    def transponders(req):
        sat_id = util.sanitize_text(req.get("satellite_id"), 32) or None
        return {"ok": True, "transponders": app.dvb.transponders(sat_id),
                "satellites": app.dvb.satellites(), "assigned": _count_dvb_channels(app)}

    @router.add("GET", "/api/dvb/satellites")
    def satellites(req):
        return {"ok": True, "satellites": app.dvb.satellites(), "last_scan": app.dvb.last_scan(),
                "scans": app.dvb.scans()}

    @router.add("POST", "/api/dvb/satellites", min_role="operator")
    def create_satellite(req):
        satellite = app.dvb.create_satellite(req.json(), req.user, req.ip)
        return {"ok": True, "satellite": satellite}

    @router.add("PUT", r"/api/dvb/satellites/" + SAT_ID, min_role="operator")
    def update_satellite(req, sat_id=None):
        satellite = app.dvb.update_satellite(sat_id, req.json(), req.user, req.ip)
        if not satellite:
            raise httpd.ApiError(404, "satélite no encontrado")
        return {"ok": True, "satellite": satellite}

    @router.add("DELETE", r"/api/dvb/satellites/" + SAT_ID, min_role="operator")
    def delete_satellite(req, sat_id=None):
        # los canales de esos transponders se van con él: si no, quedan huérfanos
        transponders = app.dvb.transponders(sat_id) if hasattr(app.dvb, "transponders") else []
        canales = []
        for tp in transponders or []:
            canales.extend(_canales_del_transponder(app, tp))
        if not app.dvb.delete_satellite(sat_id, req.user, req.ip):
            raise httpd.ApiError(404, "satélite no encontrado")
        borrados = _borrar_canales(app, canales, req.user, req.ip)
        return {"ok": True, "deleted_channels": borrados}

    @router.add("POST", r"/api/dvb/satellites/" + SAT_ID + r"/transponders", min_role="operator")
    def add_transponders(req, sat_id=None):
        payload = req.json()
        transponders = payload.get("transponders")
        if not isinstance(transponders, list) or not transponders:
            raise httpd.ApiError(400, "se requiere la lista 'transponders'")
        if len(transponders) > 512:
            raise httpd.ApiError(400, "demasiados transponders (máximo 512)")
        added, errors = app.dvb.add_transponders(sat_id, transponders, req.user, req.ip)
        if not added and errors:
            raise httpd.ApiError(400, "; ".join(errors[:5]), {"errors": errors[:20]})
        return {"ok": True, "added": len(added), "transponders": added, "errors": errors[:20]}

    @router.add("DELETE", r"/api/dvb/satellites/" + SAT_ID + r"/transponders/" + TP_ID, min_role="operator")
    def delete_transponder(req, sat_id=None, tp_id=None):
        # igual que el satélite: se lleva sus canales por delante
        tp = _buscar_tp(app, sat_id, tp_id)
        canales = _canales_del_transponder(app, tp) if tp else []
        if not app.dvb.delete_transponder(sat_id, tp_id, req.user, req.ip):
            raise httpd.ApiError(404, "transponder no encontrado")
        borrados = _borrar_canales(app, canales, req.user, req.ip)
        return {"ok": True, "deleted_channels": borrados}

    # -- escaneo -----------------------------------------------------------

    @router.add("POST", "/api/dvb/scan", min_role="operator")
    def start_scan(req):
        body = req.json()
        sat_id = util.sanitize_text(body.get("satellite_id"), 32)

        if sat_id and not body.get("transponders"):
            transponders = app.dvb.transponders(sat_id)
            if not transponders:
                raise httpd.ApiError(400, "el satélite no tiene transponders guardados")
        else:
            transponders, error = _expand_transponders(body)
            if transponders is None:
                raise httpd.ApiError(400, error)

        payload = {
            "adapter": util.to_int(body.get("adapter"), 0, minimum=0, maximum=63),
            "device": util.to_int(body.get("device"), 0, minimum=0, maximum=63),
            "type": util.sanitize_text(body.get("delivery"), 8).upper() or "S2",
            "duration": util.to_int(body.get("duration"), 8, minimum=3, maximum=120),
            "transponders": transponders,
        }
        if len(payload["transponders"]) > 512:
            raise httpd.ApiError(400, "demasiados transponders (máximo 512)")
        try:
            result = app.agent.scan_start(payload)
        except agentclient.AgentError as exc:
            raise httpd.ApiError(502, str(exc))
        scan_id = result.get("scan_id")
        app._scan_cache[scan_id] = {
            "started": util.now(),
            "satellite_id": sat_id,
            "total": len(payload["transponders"]),
            "duration": payload["duration"],
            "remembered": False,
        }
        app.store.audit(req.user, req.ip, "dvb.scan.start", scan_id, "ok",
                        "%d transponders" % len(payload["transponders"]))
        return {"ok": True, "scan_id": scan_id, "total": len(payload["transponders"])}

    @router.add("GET", r"/api/dvb/scan/" + SCAN_ID)
    def scan_status(req, scan_id=None):
        try:
            payload = app.agent.scan_status(scan_id)
        except agentclient.AgentError as exc:
            raise httpd.ApiError(502, str(exc))
        scan = payload.get("scan") or {}
        meta = app._scan_cache.get(scan_id) or {}
        if scan.get("state") in ("done", "stopped") and meta and not meta.get("remembered"):
            meta["remembered"] = True
            record = {
                "id": scan_id,
                "started": util.to_int(scan.get("started"), 0),
                "finished": util.to_int(scan.get("finished"), 0),
                "adapter": util.to_int(scan.get("adapter"), 0),
                "device": util.to_int(scan.get("device"), 0),
                "total": util.to_int(scan.get("total"), 0),
                "state": scan.get("state"),
                "satellite_id": meta.get("satellite_id", ""),
                "services": scan.get("services") or [],
                "results": scan.get("results") or [],
            }
            app.dvb.remember_scan(record)
        payload["meta"] = meta
        payload["ok"] = True
        return payload

    @router.add("POST", r"/api/dvb/scan/" + SCAN_ID + r"/stop", min_role="operator")
    def stop_scan(req, scan_id=None):
        try:
            result = app.agent.scan_stop(scan_id)
        except agentclient.AgentError as exc:
            raise httpd.ApiError(502, str(exc))
        app.store.audit(req.user, req.ip, "dvb.scan.stop", scan_id, "ok")
        return {"ok": True, "result": result}

    @router.add("POST", "/api/dvb/scan/apply", min_role="operator")
    def apply_services(req):
        """Crea canales a partir de los servicios encontrados en un escaneo."""
        body = req.json()
        services = body.get("services")
        if not isinstance(services, list) or not services:
            raise httpd.ApiError(400, "selecciona al menos un servicio")
        if len(services) > 500:
            raise httpd.ApiError(400, "demasiados servicios seleccionados")

        adapter = util.to_int(body.get("adapter"), 0, minimum=0, maximum=63)
        device = util.to_int(body.get("device"), 0, minimum=0, maximum=63)
        group = util.sanitize_text(body.get("group"), 48) or "DVB"
        name_prefix = util.sanitize_text(body.get("name_prefix"), 48)

        channels = app.channels.list()
        created = []
        errors = []
        for index, service in enumerate(services):
            if not isinstance(service, dict):
                continue
            tp = service.get("transponder") or {}
            # El sistema de entrega viene en 'delivery' del transponder; 'type' puede ser
            # "dvb" (tipo de entrada, no de frontend) y hay que descartarlo.
            delivery = util.sanitize_text(tp.get("delivery"), 8).upper() \
                or util.sanitize_text(tp.get("type"), 8).upper() \
                or util.sanitize_text(body.get("delivery"), 8).upper() or "S2"
            delivery = delivery.replace("DVB-", "")
            if delivery in ("DVB", "DVB_INPUT", ""):
                delivery = "S2"
            if delivery not in validators.DELIVERY_SYSTEMS:
                delivery = "S2"
            pnr = util.to_int(service.get("pnr"), 0)
            name = util.sanitize_text(service.get("name"), 96)
            if not name:
                name = "%s %s" % (name_prefix or "Servicio", pnr or service.get("sid") or (index + 1))
            elif name_prefix:
                name = "%s %s" % (name_prefix, name)

            # El PID del ECM se guarda como información del servicio (notas), pero NO se
            # fija en la entrada: en decrypt.c, `if(mod->ecm_pid)` SALTA la lectura de la
            # PMT y con ella el descubrimiento del CAID, de modo que el CAM no encuentra
            # el handler del CAS ("ECM:0xNN size:0 Not Found"). Astra lo saca solo de la PMT.
            options = {"pnr": pnr}
            ecm_pid = util.to_int(service.get("ecm_pid"), 0, minimum=0, maximum=8191)
            caid = util.to_int(service.get("caid"), 0, minimum=0, maximum=65535)
            nota = []
            if caid:
                nota.append("CAID %04X" % caid)
            if ecm_pid:
                nota.append("ECM %d" % ecm_pid)

            # Softcam elegido en el diálogo de escaneo (Astra 5.1): se aplica a los canales
            # nuevos. Si no se elige ninguno, se usa el CAM por defecto del panel.
            cam = None
            softcam_id = util.sanitize_text(body.get("softcam_id"), 40)
            if softcam_id:
                try:
                    from . import api_cam  # noqa: PLC0415
                    cam = api_cam.cam_de_perfil(api_cam._buscar_softcam(app, softcam_id))
                except Exception:  # noqa: BLE001
                    cam = None
            if cam is None:
                cam = _cam_del_panel(app)
            if cam:
                options["cam"] = cam

            source = {
                "type": "dvb",
                "delivery": delivery,
                "adapter": adapter,
                "device": device,
                "frequency": util.to_int(tp.get("frequency"), 0),
                "polarization": util.sanitize_text(tp.get("polarization"), 2).upper() or "H",
                "symbolrate": util.to_int(tp.get("symbolrate"), 0),
                "fec": util.sanitize_text(tp.get("fec"), 8).upper() or "AUTO",
                "lof1": util.to_int(tp.get("lof1"), util.to_int(body.get("lof1"), 9750)),
                "lof2": util.to_int(tp.get("lof2"), util.to_int(body.get("lof2"), 10600)),
                "slof": util.to_int(tp.get("slof"), util.to_int(body.get("slof"), 11700)),
                "diseqc": util.to_int(tp.get("diseqc"), util.to_int(body.get("diseqc"), 0), minimum=0, maximum=4),
                "stream_id": util.to_int(body.get("stream_id"), 0, minimum=0, maximum=65535),
                "bandwidth": util.sanitize_text(body.get("bandwidth"), 8).upper() or "AUTO",
                "options": options,
            }
            payload = {
                "name": name,
                "group": group,
                "enabled": True,
                "tags": [delivery],
                "notes": " / ".join(nota),
                "inputs": [source],
                "outputs": [{"type": "http", "uri": "/" + util.slugify(name), "public": False}],
            }
            channel, errs, _warns = validators.validate_channel(payload, None, channels + created)
            if errs:
                errors.append("%s: %s" % (name, "; ".join(errs)))
                continue
            created.append(channel)

        if not created:
            raise httpd.ApiError(400, "no se pudo crear ningún canal: " + "; ".join(errors[:5]))

        channels.extend(created)
        app.channels.save_all(channels, req.user, req.ip)
        app.store.audit(req.user, req.ip, "dvb.scan.apply", group, "ok", "%d canales" % len(created))
        result, agent_error = app.reload_agent("canales desde escaneo DVB")
        return {"ok": True, "created": len(created), "channels": created,
                "errors": errors[:20], "agent": result, "agent_error": agent_error or ""}


def _count_dvb_channels(app):
    assigned = {}
    for channel in app.channels.list():
        for source in channel.get("inputs") or []:
            if source.get("type") == "dvb":
                key = "%s:%s" % (source.get("adapter"), source.get("device"))
                assigned[key] = assigned.get(key, 0) + 1
    return assigned
