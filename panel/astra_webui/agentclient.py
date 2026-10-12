# -*- coding: utf-8 -*-
"""Cliente HTTP del agente Lua (`http://127.0.0.1:9977`)."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from . import util


class AgentError(Exception):
    """El agente no responde o devuelve un error."""


class AgentClient:
    def __init__(self, cfg):
        agent = cfg.get("agent", {})
        self.base_url = str(agent.get("url") or "http://127.0.0.1:9977").rstrip("/")
        self.token_file = agent.get("token_file") or "/etc/astra-webui/agent.token"
        self.timeout = util.to_int(agent.get("timeout"), 4, minimum=1, maximum=60)
        self._token_cache = None
        self._token_mtime = None
        self.last_error = None
        self.last_ok = 0
        self.last_latency_ms = 0

    # -- token del agente --------------------------------------------------

    def token(self, force=False):
        try:
            mtime = os.stat(self.token_file).st_mtime
        except OSError:
            mtime = None
        if force or self._token_cache is None or mtime != self._token_mtime:
            text = util.read_text(self.token_file, "")
            token = util.sanitize_text(text, 256)
            self._token_cache = token or None
            self._token_mtime = mtime
        return self._token_cache

    # -- peticiones --------------------------------------------------------

    def request(self, method, path, body=None, timeout=None, raw=False):
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        token = self.token()
        if token:
            headers["X-Astra-Token"] = token
        if body is not None:
            data = util.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            headers["Content-Length"] = str(len(data))

        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                payload = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            self.last_error = "el agente respondió %s (%s)" % (exc.code, util.sanitize_text(detail, 200))
            self.last_ok = 0
            raise AgentError(self.last_error)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            self.last_error = "no se puede contactar con el agente en %s: %s" % (self.base_url, reason)
            self.last_ok = 0
            raise AgentError(self.last_error)

        self.last_ok = util.now()
        self.last_latency_ms = max(0, int((time.time() - started) * 1000))
        self.last_error = None

        if raw:
            return payload
        try:
            parsed = json.loads(payload) if payload.strip() else {}
        except ValueError as exc:
            raise AgentError("respuesta JSON inválida del agente: %s" % exc)
        return util.normalize_empty_objects(parsed)

    def available(self):
        try:
            self.request("GET", "/health", timeout=min(3, self.timeout))
            return True
        except AgentError:
            return False

    # -- atajos ------------------------------------------------------------

    def health(self):
        return self.request("GET", "/health")

    def status(self):
        return self.request("GET", "/status")

    def metrics(self):
        return self.request("GET", "/metrics")

    def reload(self):
        return self.request("POST", "/reload", {})

    def auth_reload(self):
        return self.request("POST", "/auth/reload", {})

    def channel_action(self, action, channel_id):
        if action not in ("start", "stop", "restart"):
            raise AgentError("acción desconocida: %s" % action)
        return self.request("POST", "/channel/%s" % action, {"id": channel_id})

    def psi(self, channel_id, input_index=None):
        path = "/channel/psi?id=" + urllib.parse.quote(str(channel_id))
        if input_index:
            path += "&input=" + urllib.parse.quote(str(input_index))
        return self.request("GET", path)

    def adapters(self):
        return self.request("GET", "/dvb/adapters")

    def scan_start(self, payload):
        return self.request("POST", "/dvb/scan", payload)

    def scan_status(self, scan_id):
        return self.request("GET", "/dvb/scan?id=" + urllib.parse.quote(scan_id or ""))

    def scan_stop(self, scan_id):
        return self.request("POST", "/dvb/scan/stop", {"id": scan_id or ""})

    def log_tail(self, lines=200):
        return self.request("GET", "/log/tail?lines=%d" % util.to_int(lines, 200, minimum=1, maximum=5000))

    def shutdown(self):
        return self.request("POST", "/exit", {})
