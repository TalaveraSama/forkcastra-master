# -*- coding: utf-8 -*-
"""Rutas de canales, usuarios, tokens de streaming y listas M3U."""

from __future__ import annotations

from . import (agentclient, api_core, config, httpd, logs, m3u, tuner, util,
               validators)

CHANNEL_ID = r"(?P<channel_id>[A-Za-z0-9_\-]{3,32})"
USER_ID = r"(?P<user_id>[A-Za-z0-9_\-]{3,32})"
TOKEN = r"(?P<token>[A-Za-z0-9_\-]{8,64})"


def _agent_apply(app, user, ip, action, target="", detail=""):
    """Pide al agente que aplique los cambios y devuelve el resultado."""
    result, error = app.reload_agent("%s %s" % (action, target))
    app.store.audit(user, ip, action, target, "ok" if error is None else "degraded", detail)
    return result, error


def register(app):
    router = app.router

    # -- canales -----------------------------------------------------------

    @router.add("GET", "/api/tuners/all")
    def list_tuners_all(req):
        """Señal de todos los adaptadores presentes, los use un canal o no."""
        salida = {}
        for adapter in tuner.presentes() or tuner.de_canales(app.channels.list()):
            salida[str(adapter)] = tuner.leer(adapter)
        return {"ok": True, "tuners": salida}

    @router.add("GET", "/api/tuners")
    def list_tuners(req):
        """Señal del sintonizador (S, Q, BER, UNC) de los adaptadores en uso."""
        adaptadores = tuner.de_canales(app.channels.list())
        salida = {}
        for adapter in adaptadores:
            salida[str(adapter)] = tuner.leer(adapter)
        return {"ok": True, "tuners": salida}

    @router.add("GET", "/api/channels")
    def list_channels(req):
        agent_status, agent_error = app.agent_status(max_age=1.0)
        return {
            "ok": True,
            "channels": api_core.channels_with_state(app, agent_status),
            "agent": {"ok": agent_error is None, "error": agent_error or ""},
        }

    @router.add("GET", r"/api/channels/" + CHANNEL_ID)
    def get_channel(req, channel_id=None):
        channel = app.channels.get(channel_id)
        if not channel:
            raise httpd.ApiError(404, "canal no encontrado")
        agent_status, agent_error = app.agent_status(max_age=1.0)
        state = None
        for item in api_core.channels_with_state(app, agent_status):
            if item["id"] == channel_id:
                state = item
                break
        return {"ok": True, "channel": channel, "state": state,
                "agent": {"ok": agent_error is None, "error": agent_error or ""}}

    @router.add("POST", "/api/channels", min_role="operator")
    def create_channel(req):
        payload = req.json()
        channel, errors, warnings = app.channels.create(payload, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors), {"warnings": warnings})
        result, error = _agent_apply(app, req.user, req.ip, "channel.create", channel.get("id"),
                                     channel.get("name", ""))
        return {"ok": True, "channel": channel, "warnings": warnings,
                "agent": result, "agent_error": error or ""}

    @router.add("PUT", r"/api/channels/" + CHANNEL_ID, min_role="operator")
    def update_channel(req, channel_id=None):
        payload = req.json()
        channel, errors, warnings = app.channels.update(channel_id, payload, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors), {"warnings": warnings})
        result, error = _agent_apply(app, req.user, req.ip, "channel.update", channel_id,
                                     channel.get("name", ""))
        return {"ok": True, "channel": channel, "warnings": warnings,
                "agent": result, "agent_error": error or ""}

    @router.add("DELETE", r"/api/channels/" + CHANNEL_ID, min_role="operator")
    def delete_channel(req, channel_id=None):
        channel = app.channels.get(channel_id)
        if not channel:
            raise httpd.ApiError(404, "canal no encontrado")
        if not app.channels.delete(channel_id, req.user, req.ip):
            raise httpd.ApiError(404, "canal no encontrado")
        result, error = _agent_apply(app, req.user, req.ip, "channel.delete", channel_id,
                                     channel.get("name", ""))
        return {"ok": True, "agent": result, "agent_error": error or ""}

    @router.add("POST", "/api/channels/outputs", min_role="operator")
    def add_outputs(req):
        """Añade la misma salida (por ejemplo UDP) a varios canales a la vez."""
        payload = req.json()
        ids = payload.get("ids")
        output = payload.get("output")
        if not isinstance(ids, list) or not ids:
            raise httpd.ApiError(400, "se requiere la lista 'ids'")
        if not isinstance(output, dict) or not output.get("type"):
            raise httpd.ApiError(400, "se requiere 'output' con 'type'")
        campos = ("name", "group", "logo", "epg_id", "enabled", "tags", "notes",
                  "inputs", "outputs", "analyze")
        hechos = []
        fallos = []
        for crudo in ids[:200]:
            cid = util.sanitize_text(crudo, 32)
            actual = app.channels.get(cid)
            if not actual:
                fallos.append("%s: no encontrado" % cid)
                continue
            nuevo = {k: actual.get(k) for k in campos}
            nuevo["outputs"] = list(actual.get("outputs") or []) + [dict(output)]
            canal, errs, _warns = app.channels.update(cid, nuevo, req.user, req.ip)
            if errs or not canal:
                fallos.append("%s: %s" % (cid, "; ".join(errs or ["sin cambios"])))
                continue
            hechos.append(cid)
            _agent_apply(app, req.user, req.ip, "channel.update", cid, canal.get("name", ""))
        return {"ok": True, "updated": len(hechos), "ids": hechos, "failed": fallos[:20]}

    @router.add("POST", r"/api/channels" + r"/reorder", min_role="operator")
    def reorder_channels(req):
        payload = req.json()
        ids = payload.get("ids")
        if not isinstance(ids, list) or not ids:
            raise httpd.ApiError(400, "se requiere la lista 'ids'")
        channels = app.channels.reorder([util.sanitize_text(i, 32) for i in ids], req.user, req.ip)
        return {"ok": True, "channels": channels}

    @router.add("POST", r"/api/channels/import", min_role="operator")
    def import_channels(req):
        payload = req.json()
        text = payload.get("text") or ""
        fmt = util.sanitize_text(payload.get("format"), 8).lower() or "m3u"
        group = util.sanitize_text(payload.get("group"), 48) or "Importado"
        if fmt not in ("m3u", "m3u8"):
            raise httpd.ApiError(400, "formato no soportado (usa m3u)")
        if len(text) > 2 * 1024 * 1024:
            raise httpd.ApiError(413, "el fichero es demasiado grande")
        imported, errors = app.channels.import_m3u(text, group, req.user, req.ip)
        if not imported and errors:
            raise httpd.ApiError(400, "; ".join(errors[:5]), {"errors": errors[:20]})
        result, agent_error = _agent_apply(app, req.user, req.ip, "channel.import", group,
                                           "%d canales" % len(imported))
        return {"ok": True, "imported": len(imported), "channels": imported,
                "errors": errors[:20], "agent": result, "agent_error": agent_error or ""}

    @router.add("POST", r"/api/channels/" + CHANNEL_ID + r"/duplicate", min_role="operator")
    def duplicate_channel(req, channel_id=None):
        channel, errors, warnings = app.channels.duplicate(channel_id, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))
        result, error = _agent_apply(app, req.user, req.ip, "channel.duplicate", channel_id,
                                     channel.get("name", ""))
        return {"ok": True, "channel": channel, "warnings": warnings,
                "agent": result, "agent_error": error or ""}

    @router.add("POST", r"/api/channels/" + CHANNEL_ID + r"/action", min_role="operator")
    def channel_action(req, channel_id=None):
        payload = req.json()
        action = util.sanitize_text(payload.get("action"), 16).lower()
        channel = app.channels.get(channel_id)
        if not channel:
            raise httpd.ApiError(404, "canal no encontrado")
        if action not in ("start", "stop", "restart"):
            raise httpd.ApiError(400, "acción no válida (start, stop, restart)")

        if action == "restart":
            try:
                result = app.agent.channel_action("restart", channel_id)
            except agentclient.AgentError as exc:
                app.store.audit(req.user, req.ip, "channel.restart", channel_id, "fail", str(exc))
                raise httpd.ApiError(502, str(exc))
            app.store.audit(req.user, req.ip, "channel.restart", channel_id, "ok")
            return {"ok": True, "action": action, "agent": result}

        enabled = action == "start"
        updated, errors, _warnings = app.channels.set_enabled(channel_id, enabled, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))
        result, error = _agent_apply(app, req.user, req.ip, "channel.%s" % action, channel_id,
                                     channel.get("name", ""))
        return {"ok": True, "action": action, "channel": updated, "agent": result,
                "agent_error": error or ""}

    @router.add("GET", r"/api/channels/" + CHANNEL_ID + r"/psi", min_role="operator")
    def channel_psi(req, channel_id=None):
        try:
            payload = app.agent.psi(channel_id, req.get("input"))
        except agentclient.AgentError as exc:
            raise httpd.ApiError(502, str(exc))
        payload["ok"] = True
        return payload

    @router.add("GET", r"/api/channels/" + CHANNEL_ID + r"/clients")
    def channel_clients(req, channel_id=None):
        """Espectadores activos del canal (según el agente)."""
        agent_status, agent_error = app.agent_status(max_age=1.0)
        for item in (agent_status or {}).get("channels") or []:
            if item.get("id") == channel_id:
                return {"ok": True, "viewers": util.to_int(item.get("viewers"), 0),
                        "bitrate": util.to_int(item.get("bitrate"), 0)}
        return {"ok": True, "viewers": 0, "bitrate": 0,
                "agent": {"ok": agent_error is None, "error": agent_error or ""}}

    # -- usuarios ----------------------------------------------------------

    def _user_payload(app, user, host):
        payload = dict(user)
        payload.pop("password", None)
        payload["m3u_url"] = "%s/api/playlists/%s.m3u" % (config.panel_base_url(app.cfg, host),
                                                         user.get("stream_token", ""))
        payload["stream_url_template"] = "%s/live/%s/<id_canal>.ts" % (
            config.stream_base_url(app.cfg, host), user.get("stream_token", ""))
        return payload

    @router.add("GET", "/api/users", min_role="admin")
    def list_users(req):
        users = [_user_payload(app, u, req.host) for u in app.users.list()]
        users.sort(key=lambda u: (u.get("role") != "admin", u.get("username") or ""))
        return {"ok": True, "users": users, "sessions": app.sessions.count()}

    @router.add("POST", "/api/users", min_role="admin")
    def create_user(req):
        payload = req.json()
        password = payload.get("password") or ""
        user, errors = app.users.create(payload, password, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))
        app.store.audit(req.user, req.ip, "user.create", user.get("username"), "ok")
        return {"ok": True, "user": _user_payload(app, user, req.host)}

    @router.add("GET", r"/api/users/" + USER_ID, min_role="admin")
    def get_user(req, user_id=None):
        user = app.users.find(user_id=user_id)
        if not user:
            raise httpd.ApiError(404, "usuario no encontrado")
        return {"ok": True, "user": _user_payload(app, user, req.host)}

    @router.add("PUT", r"/api/users/" + USER_ID, min_role="admin")
    def update_user(req, user_id=None):
        payload = req.json()
        password = payload.get("password") or None
        user, errors = app.users.update(user_id, payload, password, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))
        app.store.audit(req.user, req.ip, "user.update", user.get("username"), "ok")
        return {"ok": True, "user": _user_payload(app, user, req.host)}

    @router.add("DELETE", r"/api/users/" + USER_ID, min_role="admin")
    def delete_user(req, user_id=None):
        if req.user.get("id") == user_id:
            raise httpd.ApiError(400, "no puedes borrar tu propio usuario")
        user = app.users.find(user_id=user_id)
        if not user:
            raise httpd.ApiError(404, "usuario no encontrado")
        ok, error = app.users.delete(user_id, req.user, req.ip)
        if not ok:
            raise httpd.ApiError(400, error or "no se pudo borrar")
        app.store.audit(req.user, req.ip, "user.delete", user.get("username"), "ok")
        return {"ok": True}

    @router.add("POST", r"/api/users/" + USER_ID + r"/token/rotate", min_role="admin")
    def rotate_token(req, user_id=None):
        user, error = app.users.rotate_token(user_id, req.user, req.ip)
        if error:
            raise httpd.ApiError(404, error)
        try:
            app.agent.auth_reload()
        except agentclient.AgentError as exc:
            logs.get().warning("no se pudo recargar auth.json en el agente: %s", exc)
        app.store.audit(req.user, req.ip, "user.token.rotate", user.get("username"), "ok")
        return {"ok": True, "user": _user_payload(app, user, req.host)}

    @router.add("POST", r"/api/users/" + USER_ID + r"/password", min_role="admin")
    def reset_password(req, user_id=None):
        payload = req.json()
        user, errors = app.users.change_password(user_id, payload.get("password") or "", req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))
        app.store.audit(req.user, req.ip, "user.password", user.get("username"), "ok")
        return {"ok": True}

    # -- listas M3U --------------------------------------------------------

    @router.add("GET", r"/api/playlists/" + TOKEN + r"\.m3u8?", public=True, csrf=False)
    def playlist(req, token=None):
        user = app.users.token_owner(token)
        if not user or not user.get("enabled", True):
            raise httpd.ApiError(404, "lista no encontrada")
        expires = util.to_int(user.get("expires_at"), 0)
        if expires and expires < util.now():
            raise httpd.ApiError(403, "el acceso de este token ha caducado")
        account = {
            "all_channels": user.get("all_channels", True),
            "channels": list(user.get("allowed_channels") or []),
        }
        body = m3u.playlist(app.channels.list(), token, account, app.cfg, req.host)
        return httpd.text_response(body, 200, "audio/x-mpegurl; charset=utf-8",
                                   {"Cache-Control": "no-store"})

    @router.add("GET", "/api/playlists/public.m3u8?", public=True, csrf=False)
    def public_playlist(req):
        channels = [c for c in app.channels.list() if c.get("enabled", True)]
        body = m3u.public_playlist(channels, app.cfg, req.host)
        return httpd.text_response(body, 200, "audio/x-mpegurl; charset=utf-8",
                                   {"Cache-Control": "no-store"})

    @router.add("GET", "/api/channels/export.m3u8?", min_role="operator")
    def export_m3u(req):
        token = util.sanitize_text(req.get("token"), 64) or (req.user.get("stream_token") or "")
        body = m3u.channels_to_m3u(app.channels.list(), app.cfg, req.host, token)
        return httpd.text_response(body, 200, "audio/x-mpegurl; charset=utf-8",
                                   {"Content-Disposition": 'attachment; filename="canales.m3u"'})

    @router.add("GET", "/api/enums")
    def enums(req):
        return {
            "ok": True,
            "input_types": list(validators.INPUT_TYPES),
            "output_types": list(validators.OUTPUT_TYPES),
            "delivery_systems": list(validators.DELIVERY_SYSTEMS),
            "polarizations": list(validators.POLARIZATIONS),
            "fec": list(validators.FEC_VALUES),
            "modulations": list(validators.MODULATIONS),
            "bandwidths": list(validators.BANDWIDTHS),
            "guard_intervals": list(validators.GUARD_INTERVALS),
            "transmit_modes": list(validators.TRANSMIT_MODES),
            "hierarchies": list(validators.HIERARCHIES),
            "rolloffs": list(validators.ROLLOFFS),
            "roles": list(validators.ROLES),
        }
