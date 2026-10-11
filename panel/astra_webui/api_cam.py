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
import urllib.request

from . import httpd, util

OSCAM_DEFAULT = "http://127.0.0.1:8888"
MARCAS_TARJETA = ("CARDOK", "CARDNOK", "NEEDINIT", "NO CARD", "CARD ERROR", "ERROR")


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
                    opciones["cam"] = dict(cam)
                    entrada["options"] = opciones
                    tocado = True
                if not tocado:
                    continue
                payload = {"name": canal.get("name"), "enabled": canal.get("enabled", True),
                           "inputs": entradas, "outputs": canal.get("outputs") or [],
                           "group": canal.get("group"), "tags": canal.get("tags") or []}
                _, errs, _ = app.channels.update(canal["id"], payload, req.user, req.ip)
                if errs:
                    errores.append("%s: %s" % (canal.get("name"), "; ".join(errs)))
                else:
                    aplicados += 1
            try:
                from . import api_channels  # noqa: PLC0415
                api_channels._agent_apply(app, req.user, req.ip, "cam.apply", "",
                                          "%d canales" % aplicados)
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "applied": aplicados, "errors": errores[:10],
                "cam": {"host": cam.get("host"), "port": cam.get("port"),
                        "user": cam.get("user"), "key_set": bool(cam.get("key"))}}

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
