# -*- coding: utf-8 -*-
"""OSCam integrado en el panel: estado de la tarjeta y control del servicio.

El panel ya usa OSCam para descifrar (newcamd). Aquí se añade lo que faltaba: verlo y
controlarlo desde dentro.

    GET  /api/oscam                    estado del servicio, de la tarjeta y del enlace
    POST /api/oscam/{start|stop|restart}

El estado de la tarjeta se lee del panel web de OSCam (por defecto `http://127.0.0.1:8888`,
acceso limitado a la red local). El control del servicio va por systemctl con la lista
blanca de unidades del panel.
"""

from __future__ import annotations

import socket
import urllib.request

from . import api_cam, httpd, util

UNIDAD = "oscam.service"
UNIDADES_ALTERNATIVAS = ("oscam.service", "ncam.service")


def _servicio_activo(app, unidad):
    try:
        resultado = app.service_command("status", unidad)
        return True, resultado.get("output", "")
    except httpd.ApiError as exc:
        return False, str(exc)


def _webif(cfg):
    """Estado de la tarjeta desde el panel web de OSCam (CARDOK/CARDNOK)."""
    return api_cam._oscam_status(cfg)


def _newcamd_abierto(cam):
    if not cam:
        return False
    try:
        with socket.create_connection((cam.get("host", "127.0.0.1"), int(cam.get("port", 0))),
                                      timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


def register(app):
    router = app.router

    def _detalle():
        """Estado completo que consume la vista de Ajustes."""
        servicio = {"unit": UNIDAD, "active": False, "output": ""}
        for unidad in UNIDADES_ALTERNATIVAS:
            activo, salida = _servicio_activo(app, unidad)
            if activo:
                servicio = {"unit": unidad, "active": True, "output": salida}
                break
            servicio["output"] = salida

        oscam, error = _webif(app.cfg)
        cam = api_cam._cam_guardado(app)
        return {
            "ok": True,
            "service": servicio,
            "oscam": oscam,
            "error": error or "",
            "newcamd": {
                "host": (cam or {}).get("host", ""),
                "port": (cam or {}).get("port", 0),
                "user": (cam or {}).get("user", ""),
                "open": _newcamd_abierto(cam),
            },
            "webif": oscam.get("url", "") if isinstance(oscam, dict) else "",
            "units": ["astra.service", "astra-webui.service", "oscam.service", "pcscd.service"],
        }

    @router.add("GET", "/api/oscam", min_role="admin")
    def oscam_estado(req):
        return _detalle()

    @router.add("POST", r"/api/oscam/(?P<action>start|stop|restart)", min_role="admin")
    def oscam_accion(req, action=None):
        ultimo = None
        for unidad in UNIDADES_ALTERNATIVAS:
            try:
                ultimo = app.service_command(action, unidad)
                app.store.audit(req.user, req.ip, "oscam.%s" % action, unidad, "ok")
                return ultimo
            except httpd.ApiError as exc:
                error = str(exc)
        # si ninguna unidad existe, se explica el comando exacto
        raise httpd.ApiError(500, "%s. Ejecuta a mano: sudo systemctl %s %s" % (
            error, action, UNIDAD))
