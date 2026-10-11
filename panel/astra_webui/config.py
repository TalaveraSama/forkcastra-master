# -*- coding: utf-8 -*-
"""Configuración del panel (`/etc/astra-webui/panel.json`)."""

from __future__ import annotations

import copy
import os
import re

from . import util

DEFAULTS = {
    "listen": "0.0.0.0",
    "port": 8000,
    "base_url": "",
    "session_hours": 12,
    "stream": {
        "host": "",
        "port": 8001,
        "public_base": "",
    },
    "agent": {
        "url": "http://127.0.0.1:9977",
        "token_file": "/etc/astra-webui/agent.token",
        "timeout": 4,
    },
    "paths": {
        "astra_bin": "/usr/bin/astra",
        "astra_log": "/var/log/astra/astra.log",
        "panel_log": "/var/log/astra-webui/panel.log",
        "channels": "/var/lib/astra-webui/channels.json",
        "auth": "/var/lib/astra-webui/auth.json",
        "users": "/var/lib/astra-webui/users.json",
        "sessions": "/var/lib/astra-webui/sessions.json",
        "dvb": "/var/lib/astra-webui/dvb.json",
        "audit": "/var/lib/astra-webui/audit.jsonl",
        "history": "/var/lib/astra-webui/history.json",
        "recordings": "/var/lib/astra-webui/recordings",
        "media": "/srv/media",
    },
    "service_control": {
        "enabled": True,
        "unit": "astra.service",
        "sudo": "/usr/bin/sudo",
        "systemctl": "/usr/bin/systemctl",
    },
    "security": {
        "max_login_attempts": 5,
        "login_window": 300,
        "trust_proxy": False,
        "allowed_networks": [],
    },
    "ui": {
        "title": "Astra WebUI",
        "language": "es",
        "refresh_ms": 2000,
        "theme": "dark",
    },
    "history": {
        "points": 720,
        "interval": 5,
    },
    "web_dir": "/usr/share/astra-webui/web",
    "dev": False,
}

# Directorios a los que se permite escribir salidas de tipo fichero
ALLOWED_OUTPUT_ROOTS = (
    "/var/lib/astra-webui/",
    "/srv/",
    "/media/",
    "/mnt/",
)


def deep_merge(base, extra):
    """Fusiona `extra` sobre una copia de `base` (recursivo en diccionarios)."""
    result = copy.deepcopy(base)
    if not isinstance(extra, dict):
        return result
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load(path=None, overrides=None):
    """Carga la configuración: fichero + variables de entorno + overrides."""
    cfg = copy.deepcopy(DEFAULTS)

    path = path or os.environ.get("ASTRA_WEBUI_CONFIG") or "/etc/astra-webui/panel.json"
    text = util.read_text(path)
    if text:
        try:
            cfg = deep_merge(cfg, util.loads(text, {}))
        except ValueError as exc:
            raise SystemExit("panel.json inválido (%s): %s" % (path, exc))

    env_map = {
        "ASTRA_WEBUI_LISTEN": ("listen", str),
        "ASTRA_WEBUI_PORT": ("port", int),
        "ASTRA_WEBUI_AGENT_URL": ("agent.url", str),
        "ASTRA_WEBUI_WEB_DIR": ("web_dir", str),
        "ASTRA_WEBUI_DEV": ("dev", bool),
    }
    for env_name, (key, caster) in env_map.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        if caster is int:
            value = util.to_int(raw, cfg.get("port", 8000))
        elif caster is bool:
            value = util.to_bool(raw)
        else:
            value = raw
        set_path(cfg, key, value)

    if overrides:
        cfg = deep_merge(cfg, overrides)

    normalize(cfg)
    cfg["_config_path"] = path
    return cfg


def get_path(cfg, dotted, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_path(cfg, dotted, value):
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return cfg


def normalize(cfg):
    cfg["port"] = util.to_int(cfg.get("port"), 8000, minimum=1, maximum=65535)
    cfg["session_hours"] = util.to_int(cfg.get("session_hours"), 12, minimum=1, maximum=720)

    stream = cfg.setdefault("stream", {})
    stream["port"] = util.to_int(stream.get("port"), 8001, minimum=1, maximum=65535)

    agent = cfg.setdefault("agent", {})
    agent["timeout"] = util.to_int(agent.get("timeout"), 4, minimum=1, maximum=60)

    security = cfg.setdefault("security", {})
    security["max_login_attempts"] = util.to_int(security.get("max_login_attempts"), 5, minimum=1, maximum=100)
    security["login_window"] = util.to_int(security.get("login_window"), 300, minimum=30, maximum=86400)
    networks = security.get("allowed_networks")
    if not isinstance(networks, list):
        security["allowed_networks"] = []

    history = cfg.setdefault("history", {})
    history["points"] = util.to_int(history.get("points"), 720, minimum=60, maximum=20000)
    history["interval"] = util.to_int(history.get("interval"), 5, minimum=1, maximum=300)

    ui = cfg.setdefault("ui", {})
    ui["refresh_ms"] = util.to_int(ui.get("refresh_ms"), 2000, minimum=500, maximum=60000)

    return cfg


def save(cfg, path=None):
    """Guarda la configuración (descarta las claves internas)."""
    path = path or cfg.get("_config_path") or "/etc/astra-webui/panel.json"
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    util.write_atomic(path, util.dumps(clean, indent=2), mode=0o640)
    return path


def agent_config_path(cfg):
    directory = os.path.dirname(os.path.abspath(cfg.get("_config_path") or "/etc/astra-webui/panel.json"))
    return os.path.join(directory, "agent.json")


def save_agent_stream(cfg):
    """Sincroniza el puerto de streaming en agent.json.

    El panel y el agente usan ficheros distintos: si el operador cambia el
    puerto desde la interfaz hay que reflejarlo en los dos o el núcleo seguiría
    escuchando en el antiguo. Devuelve (ok, ruta, error).
    """
    path = agent_config_path(cfg)
    text = util.read_text(path)
    if text is None:
        return False, path, "no existe %s" % path
    try:
        agent_cfg = util.loads(text, {}) or {}
    except ValueError as exc:
        return False, path, "agent.json inválido: %s" % exc

    stream = agent_cfg.setdefault("stream", {})
    port = util.to_int((cfg.get("stream") or {}).get("port"), 8001)
    if stream.get("port") == port:
        return True, path, None
    stream["port"] = port
    stream.setdefault("addr", "0.0.0.0")
    try:
        util.write_atomic(path, util.dumps(agent_cfg, indent=2), mode=0o640)
    except OSError as exc:
        return False, path, str(exc)
    return True, path, None


def stream_base_url(cfg, request_host=None):
    """URL pública del servidor de streaming (para listas M3U y enlaces).

    Ojo: el `Host` de una petición al panel trae el puerto DEL PANEL (8000), así
    que cuando el host se deduce de la petición hay que descartar ese puerto y
    usar siempre `stream.port`; si no, las listas M3U apuntarían al panel y el
    reproductor recibiría un 404.
    """
    stream = cfg.get("stream", {})
    public = util.sanitize_text(stream.get("public_base") or "", 256)
    if public:
        return public.rstrip("/")

    host = util.sanitize_text(stream.get("host") or "", 128)
    if host:
        # el operador fija el host a mano: se respeta tal cual (puede traer puerto)
        if re.search(r":\d+$", host):
            return "http://" + host
        port = util.to_int(stream.get("port"), 8001)
        return "http://" + host + ("" if port == 80 else ":%d" % port)

    host = util.sanitize_text(request_host or "", 128)
    host = re.sub(r":\d+$", "", host) or "127.0.0.1"
    port = util.to_int(stream.get("port"), 8001)
    return "http://" + host + ("" if port == 80 else ":%d" % port)


def panel_base_url(cfg, request_host=None):
    public = util.sanitize_text(cfg.get("base_url") or "", 256)
    if public:
        return public.rstrip("/")
    host = util.sanitize_text(request_host or "", 128) or "127.0.0.1"
    if ":" not in host and cfg.get("port") not in (80, None):
        host = "%s:%d" % (host, util.to_int(cfg.get("port"), 8000))
    return "http://" + host
