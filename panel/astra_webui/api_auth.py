# -*- coding: utf-8 -*-
"""Rutas de autenticación y sesiones."""

from __future__ import annotations

from . import config, httpd, logs, metrics, security, util


def public_user(user, include_token=True):
    payload = {
        "id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("display_name"),
        "role": user.get("role"),
        "enabled": user.get("enabled", True),
        "all_channels": user.get("all_channels", True),
        "allowed_channels": list(user.get("allowed_channels") or []),
        "max_connections": util.to_int(user.get("max_connections"), 0),
        "expires_at": util.to_int(user.get("expires_at"), 0),
        "created_at": util.to_int(user.get("created_at"), 0),
        "last_login": util.to_int(user.get("last_login"), 0),
    }
    if include_token:
        payload["stream_token"] = user.get("stream_token", "")
    return payload


def _set_session_cookie(app, response, sid, max_age):
    parts = [
        "%s=%s" % (security.SESSION_COOKIE, sid),
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=%d" % max_age,
    ]
    base = app.cfg.get("base_url") or ""
    if base.startswith("https://"):
        parts.append("Secure")
    response.headers["Set-Cookie"] = "; ".join(parts)


def register(app):
    router = app.router

    @router.add("POST", "/api/auth/login", public=True, csrf=False)
    def login(req):
        data = req.json()
        username = util.sanitize_text(data.get("username"), 32).lower()
        password = data.get("password") or ""
        if not username or not password:
            raise httpd.ApiError(400, "usuario y contraseña son obligatorios")

        key = "%s|%s" % (req.ip, username)
        allowed, retry = app.limiter.check(key)
        if not allowed:
            app.store.audit(username, req.ip, "auth.login", username, "blocked",
                            "bloqueado %ds" % retry)
            raise httpd.ApiError(429, "demasiados intentos fallidos. Prueba de nuevo en %d segundos" % retry)

        user, error = app.users.authenticate(username, password)
        if not user:
            app.limiter.fail(key)
            app.store.audit(username, req.ip, "auth.login", username, "fail", error or "")
            raise httpd.ApiError(401, error or "usuario o contraseña incorrectos")

        app.limiter.reset(key)
        app.users.mark_login(user.get("id"), user, req.ip)
        sid, session = app.sessions.create(user, req.ip, req.header("user-agent"))
        app.store.audit(user, req.ip, "auth.login", user.get("username"), "ok")

        hours = util.to_int(app.cfg.get("session_hours"), 12, minimum=1)
        response = httpd.json_response({
            "ok": True,
            "user": public_user(user),
            "csrf": session.get("csrf"),
            "version": _version(app),
            "hostname": metrics.hostname(),
            "server_time": util.now(),
        })
        _set_session_cookie(app, response, sid, hours * 3600)
        logs.get().info("acceso correcto de '%s' desde %s", user.get("username"), req.ip)
        return response

    @router.add("POST", "/api/auth/logout", public=True, csrf=False)
    def logout(req):
        sid = req.cookies().get(security.SESSION_COOKIE)
        if sid:
            session = app.sessions.get(sid, touch=False)
            app.sessions.destroy(sid)
            if session:
                app.store.audit(session.get("username"), req.ip, "auth.logout", session.get("username"), "ok")
        response = httpd.json_response({"ok": True})
        response.headers["Set-Cookie"] = "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" % security.SESSION_COOKIE
        return response

    @router.add("GET", "/api/auth/me")
    def me(req):
        return {
            "ok": True,
            "user": public_user(req.user),
            "csrf": (req.session or {}).get("csrf", ""),
            "version": _version(app),
            "hostname": metrics.hostname(),
            "server_time": util.now(),
            "ui": app.cfg.get("ui", {}),
            "stream": {
                "port": util.to_int(app.cfg.get("stream", {}).get("port"), 8001),
                "public_base": config.stream_base_url(app.cfg, req.host),
            },
            "service_control": app.service_available(),
            "capabilities": {
                "channels": True,
                "users": req.user.get("role") == "admin",
                "settings": req.user.get("role") == "admin",
                "dvb": True,
                "logs": True,
            },
        }

    @router.add("POST", "/api/auth/password")
    def change_password(req):
        data = req.json()
        current = data.get("current") or ""
        new = data.get("password") or ""
        if not security.verify_password(req.user.get("password"), current):
            app.store.audit(req.user, req.ip, "auth.password", req.user.get("username"), "fail",
                            "contraseña actual incorrecta")
            raise httpd.ApiError(403, "la contraseña actual no es correcta")
        _user, errors = app.users.change_password(req.user.get("id"), new, req.user, req.ip)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))
        app.store.audit(req.user, req.ip, "auth.password", req.user.get("username"), "ok")
        return {"ok": True, "message": "contraseña actualizada"}

    @router.add("GET", "/api/auth/sessions", min_role="admin")
    def list_sessions(req):
        return {"ok": True, "sessions": app.sessions.list_active()}

    @router.add("POST", "/api/auth/sessions/purge", min_role="admin")
    def purge_sessions(req):
        removed = app.sessions.purge()
        app.store.audit(req.user, req.ip, "auth.sessions.purge", "", "ok", "%d sesiones" % removed)
        return {"ok": True, "removed": removed}


def _version(app):
    try:
        from . import __version__  # noqa: PLC0415
        return __version__
    except Exception:  # noqa: BLE001
        return "1.0.0"
