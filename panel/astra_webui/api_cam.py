# -*- coding: utf-8 -*-
"""Rutas del CAM: estado de la tarjeta (OSCam) y configuración desde el panel.

La tarjeta física se lee con OSCam sobre un lector PC/SC y el agente consume sus claves
por newcamd. Aquí el panel enseña el estado real y permite configurar el CAM una sola vez
aplicándolo a todos los canales DVB:

    GET    /api/cam     estado (OSCam, tarjeta, canales que descifran)
    POST   /api/cam     guarda {host, port, user, pass, key} y lo aplica
    DELETE /api/cam     olvida la configuración

El estado de la tarjeta se lee del panel web de OSCam (por defecto http://127.0.0.1:8888,
sin autenticación y limitado a la red local). Si OSCam no está, se informa del error y el
resto del panel sigue funcionando igual.
"""

from __future__ import annotations

import os
import re
import socket
import time
import urllib.request

from . import httpd, util

OSCAM_DEFAULT = "http://127.0.0.1:8888"
MARCAS_TARJETA = ("CARDOK", "CARDNOK", "NEEDINIT", "NO CARD", "CARD ERROR", "ERROR")
SOFTCAMS_FILE = "softcams.json"


def _leer_json(ruta, default=None):
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            return util.loads(fh.read(), default if default is not None else {})
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _cam_path(app):
    """Fichero del CAM en el directorio de datos del panel (/var/lib/astra-webui).

    No se usa panel.json: /etc/astra-webui es de root y el panel corre como `astra`, así
    que sólo puede escribir en su directorio de datos.
    """
    try:
        base = os.path.dirname(app.store.path("channels"))
    except Exception:  # noqa: BLE001
        base = "/var/lib/astra-webui"
    return os.path.join(base or "/var/lib/astra-webui", "cam.json")


# ---------------------------------------------------------------------------
# Softcams con nombre (como en Astra 5.1): se eligen en el escaneo y por canal
# ---------------------------------------------------------------------------


def _softcams_path(app):
    return os.path.join(os.path.dirname(_cam_path(app)), SOFTCAMS_FILE)


def _softcams(app):
    datos = _leer_json(_softcams_path(app), {})
    lista = datos.get("profiles") if isinstance(datos, dict) else None
    return lista if isinstance(lista, list) else []


def _guardar_softcams(app, lista):
    util.write_atomic(_softcams_path(app), util.dumps({"profiles": lista}, indent=2), mode=0o640)


def _buscar_softcam(app, softcam_id):
    for perfil in _softcams(app):
        if isinstance(perfil, dict) and perfil.get("id") == softcam_id:
            return perfil
    return None


def _sin_secretos(perfil):
    copia = dict(perfil)
    copia["pass_set"] = bool(copia.pop("pass", None))
    copia["key_set"] = bool(copia.pop("key", None))
    return copia


def cam_de_perfil(perfil):
    """Bloque `cam` que entiende el agente, a partir de un softcam guardado."""
    if not isinstance(perfil, dict):
        return None
    cam = {
        "host": util.sanitize_text(perfil.get("host"), 64) or "127.0.0.1",
        "port": util.to_int(perfil.get("port"), 5000, minimum=1, maximum=65535),
        "user": util.sanitize_text(perfil.get("user"), 64),
    }
    if not cam["user"]:
        return None
    if perfil.get("pass"):
        cam["pass"] = str(perfil["pass"])[:64]
    if perfil.get("key"):
        cam["key"] = str(perfil["key"]).upper()[:64]
    if util.to_bool(perfil.get("each_input")):
        cam["each_input"] = 1
    if perfil.get("allow_emm") is not None and not util.to_bool(perfil.get("allow_emm")):
        cam["disable_emm"] = 1
    nombre = util.sanitize_text(perfil.get("name"), 48)
    if nombre:
        cam["softcam"] = nombre
    return cam


def _aplicar_a_canales(app, cam, user, ip):
    """Pone (o quita, con cam=None) el CAM en la entrada DVB de todos los canales."""
    aplicados, errores = 0, []
    for canal in app.channels.list():
        entradas = canal.get("inputs") or []
        tocado = False
        for entrada in entradas:
            if not isinstance(entrada, dict):
                continue
            if util.sanitize_text(entrada.get("type"), 16).lower() != "dvb":
                continue
            opciones = dict(entrada.get("options") or {})
            # OJO: nunca fijar ecm_pid (astra se saltaría la PMT y el CAID)
            opciones.pop("ecm_pid", None)
            if cam is None:
                opciones.pop("cam", None)
            else:
                opciones["cam"] = dict(cam)
            entrada["options"] = opciones
            tocado = True
        if not tocado:
            continue
        payload = {"name": canal.get("name"), "enabled": canal.get("enabled", True),
                   "inputs": entradas, "outputs": canal.get("outputs") or [],
                   "group": canal.get("group"), "tags": canal.get("tags") or []}
        _, errs, _ = app.channels.update(canal["id"], payload, user, ip)
        if errs:
            errores.append("%s: %s" % (canal.get("name"), "; ".join(errs)))
        else:
            aplicados += 1
    try:
        from . import api_channels  # noqa: PLC0415
        api_channels._agent_apply(app, user, ip, "cam.apply", "", "%d canales" % aplicados)
    except Exception:  # noqa: BLE001
        pass
    return aplicados, errores


def _clean_cam(raw, estricto=False):
    """Normaliza el bloque CAM (los mismos campos que el newcamd de OSCam)."""
    if not isinstance(raw, dict) or not raw:
        return None
    host = util.sanitize_text(raw.get("host"), 64) or "127.0.0.1"
    port = util.to_int(raw.get("port"), 5000, minimum=1, maximum=65535)
    user = util.sanitize_text(raw.get("user"), 64)
    if not user:
        if estricto:
            raise httpd.ApiError(400, "el CAM necesita al menos 'user'")
        return None
    cam = {"host": host, "port": port, "user": user}
    password = util.sanitize_text(raw.get("pass"), 64)
    if password:
        cam["pass"] = password
    key = util.sanitize_text(raw.get("key"), 64)
    if key:
        if not re.match(r"^[0-9A-Fa-f]{28,32}$", key):
            raise httpd.ApiError(400, "la clave DES del CAM debe ser hexadecimal de 28 o "
                                      "32 caracteres (14 o 16 bytes)")
        cam["key"] = key.upper()
    if util.to_bool(raw.get("disable_emm")):
        cam["disable_emm"] = 1
    # 'allow_emm' es la casilla de Astra («Allow EMM if allowed by the server»):
    # se traduce al 'disable_emm' que entiende el módulo newcamd del núcleo.
    if "allow_emm" in raw and not util.to_bool(raw.get("allow_emm")):
        cam["disable_emm"] = 1
    if util.to_bool(raw.get("each_input")):
        cam["each_input"] = 1
    # nombre del softcam elegido (para que el editor sepa cuál mostrar)
    nombre_perfil = util.sanitize_text(raw.get("softcam"), 48)
    if nombre_perfil:
        cam["softcam"] = nombre_perfil
    return cam


def _cam_guardado(app):
    guardado = _leer_json(_cam_path(app), {})
    cam = _clean_cam(guardado.get("newcamd") if isinstance(guardado.get("newcamd"), dict)
                     else guardado)
    if cam is None:
        # valor por defecto puesto por el administrador en panel.json (sólo lectura)
        seccion = app.cfg.get("cam") if isinstance(app.cfg.get("cam"), dict) else {}
        cam = _clean_cam(seccion.get("newcamd") if isinstance(seccion.get("newcamd"), dict)
                         else seccion)
    return cam


def _oscam_status(cfg):
    """Consulta el panel de OSCam: (datos, error)."""
    seccion = cfg.get("cam") if isinstance(cfg.get("cam"), dict) else {}
    base = util.sanitize_text(seccion.get("oscam_url"), 200) or OSCAM_DEFAULT
    info = {"url": base, "reachable": False, "card": "", "caid": "", "reader": ""}
    try:
        with urllib.request.urlopen(base + "/status.html", timeout=4) as respuesta:
            html = respuesta.read().decode("utf-8", "replace")
        info["reachable"] = True
    except Exception as exc:  # noqa: BLE001
        return info, str(exc)

    texto = re.sub(r"<[^>]*>", " ", html).replace("&nbsp;", " ")
    for marca in MARCAS_TARJETA:
        if marca.lower() in texto.lower():
            info["card"] = marca
            break
    caid = re.search(r"\b[0-9A-Fa-f]{4}@[0-9A-Fa-f]{6}\b", texto)
    if caid:
        info["caid"] = caid.group(0)
    lector = re.search(r"(OmniKey[^<\n]{0,40}|OMNIKEY[^<\n]{0,40}|tv_box[0-9a-z_]+)", texto)
    if lector:
        info["reader"] = lector.group(1).strip()[:48]
    return info, None


def _newcamd_open(cam):
    if not cam:
        return False
    try:
        with socket.create_connection((cam.get("host", "127.0.0.1"),
                                       int(cam.get("port", 0))), timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


def _quitar_de_canales(app, user, ip):
    """Borra el bloque CAM de la entrada de todos los canales (el «newcamd fantasma»)."""
    quitados = 0
    for canal in app.channels.list():
        entradas = canal.get("inputs") or []
        tocado = False
        for entrada in entradas:
            if isinstance(entrada, dict) and isinstance((entrada.get("options") or {}).get("cam"), dict):
                opciones = dict(entrada.get("options") or {})
                opciones.pop("cam", None)
                entrada["options"] = opciones
                tocado = True
        if not tocado:
            continue
        payload = {"name": canal.get("name"), "enabled": canal.get("enabled", True),
                   "inputs": entradas, "outputs": canal.get("outputs") or [],
                   "group": canal.get("group"), "tags": canal.get("tags") or []}
        _, errs, _ = app.channels.update(canal["id"], payload, user, ip)
        if not errs:
            quitados += 1
    try:
        from . import api_channels  # noqa: PLC0415
        api_channels._agent_apply(app, user, ip, "cam.clear", "", "%d canales" % quitados)
    except Exception:  # noqa: BLE001
        pass
    return quitados


def register(app):
    router = app.router

    def _resumen_canales():
        estado, error = app.agent_status(max_age=2.0)
        canales = (estado or {}).get("channels") or []
        con_cam, descifrando, sin_claves = 0, [], []
        for item in canales:
            canal = app.channels.get(item.get("id")) or {}
            # los desactivados no cuentan: su última medida se queda congelada
            if canal.get("enabled") is False:
                continue
            tiene = False
            for entrada in canal.get("inputs") or []:
                if isinstance(entrada, dict):
                    opciones = entrada.get("options")
                    if isinstance(opciones, dict) and isinstance(opciones.get("cam"), dict):
                        tiene = True
            if not tiene:
                continue
            con_cam += 1
            sc = (item.get("errors") or {}).get("sc") or 0
            (descifrando if sc < 100 else sin_claves).append(item.get("name"))
        return {"total": len(canales), "with_cam": con_cam, "decoding": len(descifrando),
                "decoding_names": descifrando[:40], "no_keys": len(sin_claves),
                "agent_error": error or ""}

    @router.add("GET", "/api/cam", min_role="admin")
    def cam_status(req):
        cam = _cam_guardado(app)
        estado, agent_error = app.agent_status(max_age=2.0)
        return {
            "ok": True,
            "config": {
                "host": (cam or {}).get("host", ""),
                "port": (cam or {}).get("port", 0),
                "user": (cam or {}).get("user", ""),
                "key_set": bool((cam or {}).get("key")),
                "each_input": util.to_bool((cam or {}).get("each_input")),
                "allow_emm": not util.to_bool((cam or {}).get("disable_emm")),
                "configured": cam is not None,
            },
            "newcamd": {"open": _newcamd_open(cam)},
            "channels": _resumen_canales(),
            "agent": {"ok": agent_error is None, "error": agent_error or "",
                      "version": (estado or {}).get("version", "")},
        }

    @router.add("POST", "/api/cam", min_role="admin")
    def cam_save(req):
        body = req.json()
        cam = _clean_cam(body, estricto=True)
        # se guarda en el directorio de datos del panel (no en /etc, que es de root)
        ruta = _cam_path(app)
        guardado = _leer_json(ruta, {})
        guardado.update(cam)
        try:
            util.write_atomic(ruta, util.dumps(guardado, indent=2), mode=0o640)
        except Exception as exc:  # noqa: BLE001
            raise httpd.ApiError(500, "no se pudo guardar la configuración del CAM: %s" % exc)
        app.store.audit(req.user, req.ip, "cam.save", "%s@%s:%s" % (
            cam.get("user"), cam.get("host"), cam.get("port")), "ok")

        aplicados, errores = 0, []
        if util.to_bool(body.get("apply", True)):
            aplicados, errores = _aplicar_a_canales(app, cam, req.user, req.ip)
        return {"ok": True, "applied": aplicados, "errors": errores[:10],
                "cam": {"host": cam.get("host"), "port": cam.get("port"),
                        "user": cam.get("user"), "key_set": bool(cam.get("key"))}}

    # -- softcams con nombre (como en Astra 5.1) ---------------------------

    @router.add("GET", "/api/softcams", min_role="admin")
    def softcams_list(req):
        perfiles = [_sin_secretos(p) for p in _softcams(app) if isinstance(p, dict)]
        defecto = _cam_guardado(app) or {}
        return {"ok": True, "softcams": perfiles, "default": defecto.get("softcam", "")}

    @router.add("POST", "/api/softcams", min_role="admin")
    def softcams_save(req):
        body = req.json()
        nombre = util.sanitize_text(body.get("name"), 48)
        if not nombre:
            raise httpd.ApiError(400, "el softcam necesita un nombre")
        usuario = util.sanitize_text(body.get("user"), 64)
        if not usuario:
            raise httpd.ApiError(400, "el softcam necesita el usuario del newcamd")
        clave = util.sanitize_text(body.get("key"), 64)
        if clave and not re.match(r"^[0-9A-Fa-f]{28,32}$", clave):
            raise httpd.ApiError(400, "la clave DES debe ser hexadecimal de 28 o 32 caracteres")
        identificador = util.sanitize_text(body.get("id"), 40) or util.slugify(nombre)[:40]
        if not identificador:
            identificador = "sc%d" % int(time.time())
        perfil = {
            "id": identificador,
            "name": nombre,
            "host": util.sanitize_text(body.get("host"), 64) or "127.0.0.1",
            "port": util.to_int(body.get("port"), 5000, minimum=1, maximum=65535),
            "user": usuario,
            "each_input": 1 if util.to_bool(body.get("each_input")) else 0,
            "allow_emm": 0 if body.get("allow_emm") is False else 1,
        }
        if body.get("pass"):
            perfil["pass"] = str(body["pass"])[:64]
        if clave:
            perfil["key"] = clave.upper()

        lista = [p for p in _softcams(app) if isinstance(p, dict)
                 and p.get("id") != identificador]
        # conservar contraseña/clave anteriores si no se envían de nuevo
        anterior = _buscar_softcam(app, identificador) or {}
        for campo in ("pass", "key"):
            if campo not in perfil and anterior.get(campo):
                perfil[campo] = anterior[campo]
        lista.append(perfil)
        _guardar_softcams(app, lista)
        app.store.audit(req.user, req.ip, "softcam.save", identificador, "ok")
        return {"ok": True, "softcam": _sin_secretos(perfil)}

    @router.add("GET", r"/api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})/full", min_role="admin")
    def softcams_full(req, softcam_id=None):
        perfil = _buscar_softcam(app, softcam_id)
        if not perfil:
            raise httpd.ApiError(404, "softcam no encontrado")
        return {"ok": True, "cam": cam_de_perfil(perfil),
                "softcam": _sin_secretos(perfil)}

    @router.add("POST", "/api/channels/softcam", min_role="admin")
    def channels_softcam(req):
        """Pone (o quita) el softcam en las entradas DVB de los canales indicados."""
        payload = req.json()
        ids = payload.get("ids")
        if not isinstance(ids, list) or not ids:
            raise httpd.ApiError(400, "se requiere la lista 'ids'")
        manual = payload.get("cam")
        softcam_id = util.sanitize_text(payload.get("softcam_id"), 32)
        quitar = (not manual) and (not softcam_id or softcam_id in ("none", "__none"))
        cam = None
        if isinstance(manual, dict) and manual:
            cam = {k: manual.get(k) for k in
                   ("host", "port", "user", "pass", "key", "disable_emm", "each_input")
                   if manual.get(k) is not None}
            if not cam or not cam.get("host"):
                raise httpd.ApiError(400, "el CAM manual necesita al menos 'host'")
        elif not quitar:
            perfil = _buscar_softcam(app, softcam_id)
            if not perfil:
                raise httpd.ApiError(404, "softcam no encontrado")
            cam = cam_de_perfil(perfil)
            if not cam:
                raise httpd.ApiError(400, "el softcam no tiene datos utilizables")

        campos = ("name", "group", "logo", "epg_id", "enabled", "tags", "notes",
                  "inputs", "outputs", "analyze")
        hechos, fallos = [], []
        for crudo in ids[:200]:
            cid = util.sanitize_text(crudo, 32)
            actual = app.channels.get(cid)
            if not actual:
                fallos.append("%s: no encontrado" % cid)
                continue
            nuevo = {k: actual.get(k) for k in campos}
            entradas = []
            tocado = False
            for entrada in (actual.get("inputs") or []):
                if not isinstance(entrada, dict):
                    entradas.append(entrada)
                    continue
                copia = dict(entrada)
                if util.sanitize_text(copia.get("type"), 16).lower() == "dvb":
                    opciones = dict(copia.get("options") or {})
                    opciones.pop("ecm_pid", None)  # nunca fijar ecm_pid
                    if cam is None:
                        opciones.pop("cam", None)
                    else:
                        opciones["cam"] = dict(cam)
                    copia["options"] = opciones
                    tocado = True
                entradas.append(copia)
            if not tocado:
                fallos.append("%s: no tiene entrada DVB" % cid)
                continue
            nuevo["inputs"] = entradas
            canal, errs, _warns = app.channels.update(cid, nuevo, req.user, req.ip)
            if errs or not canal:
                fallos.append("%s: %s" % (cid, "; ".join(errs or ["sin cambios"])))
                continue
            hechos.append(cid)
        app.store.audit(req.user, req.ip, "channels.softcam",
                        "%s -> %d canales" % (softcam_id or ("manual" if cam else "quitar"), len(hechos)), "ok")
        return {"ok": True, "updated": len(hechos), "ids": hechos,
                "softcam": softcam_id or ("manual" if cam else ""), "failed": fallos[:20]}

    @router.add("POST", r"/api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})/apply", min_role="admin")
    def softcams_apply(req, softcam_id=None):
        perfil = _buscar_softcam(app, softcam_id)
        if not perfil:
            raise httpd.ApiError(404, "softcam no encontrado")
        cam = cam_de_perfil(perfil)
        if not cam:
            raise httpd.ApiError(400, "el softcam no tiene datos utilizables")
        aplicados, errores = _aplicar_a_canales(app, cam, req.user, req.ip)
        app.store.audit(req.user, req.ip, "softcam.apply", softcam_id, "ok")
        return {"ok": True, "applied": aplicados, "errors": errores[:10]}

    @router.add("DELETE", r"/api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})", min_role="admin")
    def softcams_delete(req, softcam_id=None):
        lista = [p for p in _softcams(app) if isinstance(p, dict) and p.get("id") != softcam_id]
        if len(lista) == len(_softcams(app)):
            raise httpd.ApiError(404, "softcam no encontrado")
        _guardar_softcams(app, lista)
        app.store.audit(req.user, req.ip, "softcam.delete", softcam_id, "ok")
        return {"ok": True}

    @router.add("DELETE", "/api/cam", min_role="admin")
    def cam_clear(req):
        ruta = _cam_path(app)
        try:
            if os.path.exists(ruta):
                os.unlink(ruta)
        except Exception as exc:  # noqa: BLE001
            raise httpd.ApiError(500, "no se pudo borrar la configuración del CAM: %s" % exc)
        quitados = _quitar_de_canales(app, req.user, req.ip)
        app.store.audit(req.user, req.ip, "cam.clear", "", "ok")
        return {"ok": True, "cleared": quitados}
