# -*- coding: utf-8 -*-
"""Rutas de sistema: estado, histórico, eventos SSE, logs, ajustes y respaldo."""

from __future__ import annotations

import json
import time

from . import config, httpd, logtail, logs, m3u, metrics, util, validators


def _version():
    try:
        from . import __version__  # noqa: PLC0415
        return __version__
    except Exception:  # noqa: BLE001
        return "1.0.0"


def _sse(payload, event="message"):
    return "event: %s\ndata: %s\n\n" % (event, util.dumps(payload))


def register(app):
    router = app.router
    cfg = app.cfg

    # -- estado ------------------------------------------------------------

    @router.add("GET", "/api/health", public=True, csrf=False)
    def health(req):
        agent_status, agent_error = app.agent_status(max_age=2.0)
        return {
            "ok": True,
            "name": "astra-webui",
            "version": _version(),
            "uptime": util.now() - app.started_at,
            "agent": {"ok": agent_error is None, "error": agent_error or ""},
            "astra": {"running": bool(agent_status)},
        }

    @router.add("GET", "/api/system/status")
    def system_status(req):
        agent_status, agent_error = app.agent_status(max_age=1.0)
        payload = app.metrics.system_status(app.agent, agent_status, agent_error)
        payload["panel"] = {
            "version": _version(),
            "uptime": util.now() - app.started_at,
            "sessions": app.sessions.count(),
            "channels": len(app.channels.list()),
            "users": app.users.count(),
            "service_control": app.service_available(),
        }
        payload["channels"] = channels_with_state(app, agent_status)
        return payload

    @router.add("GET", "/api/system/history")
    def system_history(req):
        minutes = req.get_int("minutes", 60, minimum=1, maximum=10080)
        samples = app.metrics.history(minutes)
        return {
            "ok": True,
            "interval": app.metrics.interval,
            "samples": samples,
        }

    @router.add("GET", "/api/stats/bitrate")
    def bitrate_history(req):
        minutes = req.get_int("minutes", 60, minimum=1, maximum=10080)
        samples = app.metrics.history(minutes)
        series = {}
        for sample in samples:
            for channel_id, value in (sample.get("bitrates") or {}).items():
                series.setdefault(channel_id, []).append({"t": sample.get("t"), "v": value})
        return {"ok": True, "series": series}

    # -- eventos en vivo (SSE) --------------------------------------------

    @router.add("GET", "/api/events")
    def events(req):
        refresh = util.to_int(cfg.get("ui", {}).get("refresh_ms"), 2000, minimum=500, maximum=60000)

        def generator():
            last_ping = time.time()
            try:
                while True:
                    agent_status, agent_error = app.agent_status(max_age=max(1.0, refresh / 1000.0))
                    payload = {
                        "t": util.now(),
                        "astra": {
                            "running": bool(agent_status),
                            "bitrate_total": util.to_int((agent_status or {}).get("bitrate_total"), 0),
                            "viewers": util.to_int((agent_status or {}).get("viewers"), 0),
                            "channels_running": util.to_int((agent_status or {}).get("channels_running"), 0),
                            "channels_total": util.to_int((agent_status or {}).get("channels_total"), 0),
                            "pid": util.to_int((agent_status or {}).get("pid"), 0),
                            "uptime": util.to_int((agent_status or {}).get("uptime"), 0),
                        },
                        "agent": {"ok": agent_error is None, "error": agent_error or ""},
                        "channels": channels_with_state(app, agent_status),
                    }
                    latest = app.metrics.latest()
                    if latest:
                        payload["host"] = {"cpu": latest.get("cpu", 0), "mem": latest.get("mem", 0),
                                           "net_rx": latest.get("net_rx", 0), "net_tx": latest.get("net_tx", 0)}
                    yield _sse(payload, "status")

                    if time.time() - last_ping > 15:
                        last_ping = time.time()
                        yield ": keep-alive\n\n"
                    time.sleep(max(0.5, refresh / 1000.0))
            except GeneratorExit:  # el cliente se desconectó
                return
            except Exception as exc:  # noqa: BLE001
                logs.get().debug("SSE de eventos finalizado: %s", exc)
                return

        return httpd.SSE(generator())

    # -- logs --------------------------------------------------------------

    def _log_payload(source, path, req):
        entries = logtail.read(path, lines=req.get_int("lines", 200, minimum=1, maximum=5000),
                               level=req.get("level", "all"), query=req.get("q", ""), source=source)
        return {
            "ok": True,
            "source": source,
            "path": path,
            "lines": entries,
            "stats": logtail.stats(path),
            "available": True,
        }

    @router.add("GET", "/api/logs/astra")
    def logs_astra(req):
        return _log_payload("astra", cfg.get("paths", {}).get("astra_log"), req)

    @router.add("GET", "/api/logs/panel")
    def logs_panel(req):
        return _log_payload("panel", cfg.get("paths", {}).get("panel_log"), req)

    @router.add("GET", "/api/logs/astra/stream")
    def logs_astra_stream(req):
        path = cfg.get("paths", {}).get("astra_log")
        follower = logtail.Follower(path, source="astra", level=req.get("level", "all"),
                                    query=req.get("q", ""), start_at_end=True)

        def generator():
            idle = 0
            try:
                while True:
                    entries = follower.poll()
                    if entries:
                        idle = 0
                        yield _sse({"ok": True, "lines": entries}, "log")
                    else:
                        idle += 1
                        if idle >= 20:
                            idle = 0
                            yield ": keep-alive\n\n"
                    if follower.expired():
                        yield _sse({"ok": True, "lines": [], "closed": True}, "log")
                        return
                    time.sleep(0.7)
            except GeneratorExit:
                return
            except Exception as exc:  # noqa: BLE001
                logs.get().debug("SSE de log finalizado: %s", exc)
                return

        return httpd.SSE(generator())

    @router.add("GET", "/api/logs/audit")
    def logs_audit(req):
        limit = req.get_int("limit", 200, minimum=1, maximum=5000)
        return {"ok": True, "entries": app.store.audit_tail(limit)}

    # -- ajustes -----------------------------------------------------------

    @router.add("GET", "/api/settings", min_role="admin")
    def get_settings(req):
        clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
        return {"ok": True, "settings": clean,
                "service_available": app.service_available(),
                "config_path": cfg.get("_config_path", "")}

    @router.add("PUT", "/api/settings", min_role="admin")
    def put_settings(req):
        payload = req.json()
        updated, errors = validators.sanitize_settings(payload, cfg)
        if errors:
            raise httpd.ApiError(400, "; ".join(errors))

        # Evita que un administrador se deje fuera con una lista de redes
        networks = (updated.get("security") or {}).get("allowed_networks") or []
        if networks and not httpd.ip_allowed(req.ip, networks):
            raise httpd.ApiError(400, "esa lista de redes bloquearía tu propia IP (%s): "
                                      "añádela o deja la lista vacía" % req.ip)

        merged = config.deep_merge(cfg, updated)
        config.normalize(merged)
        merged["_config_path"] = cfg.get("_config_path")
        config.save(merged)
        cfg.clear()
        cfg.update(merged)

        agent_ok, agent_path, agent_error = config.save_agent_stream(cfg)
        app.store.audit(req.user, req.ip, "settings.update", "panel.json", "ok",
                        "" if agent_ok else "agent.json: %s" % agent_error)
        restart_hint = any(key in payload for key in ("listen", "port", "stream"))
        return {
            "ok": True,
            "settings": {k: v for k, v in cfg.items() if not k.startswith("_")},
            "restart_required": bool(restart_hint),
            "agent_config": {"ok": agent_ok, "path": agent_path, "error": agent_error or ""},
        }

    # -- servicio ----------------------------------------------------------

    @router.add("POST", r"/api/service/(?P<action>restart|start|stop|status)-astra", min_role="admin")
    def service_action(req, action=None):
        action = action or req.params.get("action")
        result = app.service_command(action)
        app.store.audit(req.user, req.ip, "service.%s" % action, cfg.get("service_control", {}).get("unit"),
                        "ok", result.get("output", ""))
        return result

    # -- respaldo / restauración ------------------------------------------

    @router.add("GET", "/api/export", min_role="admin")
    def export_config(req):
        bundle = {
            "version": 1,
            "exported_at": util.now(),
            "hostname": metrics.hostname(),
            "channels": app.channels.list(),
            "users": app.users.list(),
            "dvb": app.dvb.satellites(),
            "settings": {k: v for k, v in cfg.items() if not k.startswith("_")},
        }
        app.store.audit(req.user, req.ip, "config.export", "bundle", "ok",
                        "%d canales" % len(bundle["channels"]))
        return {"ok": True, "bundle": bundle}

    @router.add("POST", "/api/import", min_role="admin")
    def import_config(req):
        payload = req.json()
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else payload
        if not isinstance(bundle, dict):
            raise httpd.ApiError(400, "formato de respaldo inválido")

        summary = {"channels": 0, "users": 0, "satellites": 0}

        if isinstance(bundle.get("channels"), list):
            channels = []
            errors = []
            for raw in bundle["channels"]:
                channel, errs, _warns = validators.validate_channel(raw, None, channels)
                if errs:
                    errors.append("%s: %s" % (raw.get("name") or raw.get("id"), "; ".join(errs)))
                else:
                    channels.append(channel)
            if errors and not channels:
                raise httpd.ApiError(400, "ningún canal válido en el respaldo: " + "; ".join(errors[:5]))
            app.channels.save_all(channels, req.user, req.ip)
            summary["channels"] = len(channels)

        if isinstance(bundle.get("dvb"), list):
            doc = {"version": 1, "satellites": bundle["dvb"], "scans": app.dvb.scans(),
                   "last_scan": app.dvb.last_scan()}
            app.store.save("dvb", doc)
            summary["satellites"] = len(bundle["dvb"])

        if util.to_bool(payload.get("import_users")) and isinstance(bundle.get("users"), list):
            existing = {u.get("username") for u in app.users.list()}
            users = app.users.list()
            for raw in bundle["users"]:
                username = util.sanitize_text((raw or {}).get("username"), 32).lower()
                if not username or username in existing:
                    continue
                clean, errs = validators.validate_user(raw, None, users)
                if errs:
                    continue
                clean["password"] = raw.get("password")
                if not isinstance(clean.get("password"), dict):
                    continue  # sin hash no se puede restaurar
                users.append(clean)
                existing.add(username)
                summary["users"] += 1
            app.users.save_all(users, req.user, req.ip)

        app.store.audit(req.user, req.ip, "config.import", "bundle", "ok", util.dumps(summary))
        result, error = app.reload_agent("importación de respaldo")
        return {"ok": True, "summary": summary, "agent": result, "agent_error": error or ""}


# ---------------------------------------------------------------------------
# ayudas compartidas con api_channels
# ---------------------------------------------------------------------------

def channels_with_state(app, agent_status):
    """Fusiona la configuración (panel) con el estado (agente)."""
    live = {}
    for item in (agent_status or {}).get("channels") or []:
        if isinstance(item, dict) and item.get("id"):
            live[item["id"]] = item

    out = []
    for channel in app.channels.list():
        state = live.get(channel.get("id")) or {}
        out.append({
            "id": channel.get("id"),
            "name": channel.get("name"),
            "group": channel.get("group"),
            "logo": channel.get("logo"),
            "epg_id": channel.get("epg_id"),
            "enabled": channel.get("enabled", True),
            "tags": channel.get("tags") or [],
            "running": bool(state.get("running")),
            "on_air": bool(state.get("on_air")),
            "bitrate": util.to_int(state.get("bitrate"), 0),
            "viewers": util.to_int(state.get("viewers"), 0),
            "errors": state.get("errors") or {"cc": 0, "sc": 0, "pes": 0},
            "inputs": state.get("inputs") or [],
            "outputs": state.get("outputs") or [],
            "inputs_config": channel.get("inputs") or [],
            "outputs_config": channel.get("outputs") or [],
            "updated_at": util.to_int(channel.get("updated_at"), 0),
        })
    return out
