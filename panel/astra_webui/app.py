# -*- coding: utf-8 -*-
"""Contexto de la aplicación: bases de datos, agente, métricas y rutas."""

from __future__ import annotations

import os
import subprocess
import threading

from . import (agentclient, config, httpd, logs, m3u, metrics, security, store,
               util, validators)


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.started_at = util.now()

        self.store = store.Store(cfg)
        self.channels = store.ChannelsDB(self.store)
        self.users = store.UsersDB(self.store)
        self.dvb = store.DvbDB(self.store)
        self.sessions = security.SessionManager(self.store, cfg.get("session_hours", 12))
        self.limiter = security.RateLimiter(
            util.to_int(cfg.get("security", {}).get("max_login_attempts"), 5),
            util.to_int(cfg.get("security", {}).get("login_window"), 300),
        )
        self.agent = agentclient.AgentClient(cfg)
        self.metrics = metrics.Metrics(cfg, self.agent)
        self.static = httpd.StaticFiles(cfg.get("web_dir") or ".")
        self.router = httpd.Router(self)

        self._agent_lock = threading.Lock()
        self._agent_cache = None
        self._agent_cache_at = 0
        self._agent_error = None
        self._agent_fetching = False
        self._scan_cache = {}

        from . import api_auth, api_cam, api_channels, api_core, api_dvb  # noqa: PLC0415 (evita ciclos)
        for module in (api_auth, api_core, api_channels, api_dvb, api_cam):
            module.register(self)

    # -- sesiones ----------------------------------------------------------

    def current_session(self, req):
        cookies = req.cookies()
        sid = cookies.get(security.SESSION_COOKIE)
        if not sid:
            return None
        return self.sessions.get(sid)

    # -- agente ------------------------------------------------------------

    def agent_status(self, max_age=1.5, force=False):
        """Estado del agente con caché corta (lo comparten API y SSE).

        Si ya hay una consulta en curso (p. ej. varias pestañas con SSE abierto)
        los demás hilos usan el valor anterior en vez de bloquearse esperando al
        agente, que puede tardar hasta `agent.timeout` segundos.
        """
        now = util.now()
        with self._agent_lock:
            fresh = self._agent_cache is not None and (now - self._agent_cache_at) <= max_age
            if (not force) and (fresh or self._agent_fetching):
                return self._agent_cache, self._agent_error
            self._agent_fetching = True

        try:
            status = self.agent.status()
            error = None
        except agentclient.AgentError as exc:
            status = None
            error = str(exc)

        with self._agent_lock:
            self._agent_cache = status
            self._agent_error = error
            self._agent_cache_at = util.now()
            self._agent_fetching = False
        return status, error

    def reload_agent(self, reason="cambios en la configuración"):
        """Pide al agente que recargue y aplica el diff en caliente."""
        try:
            result = self.agent.reload()
        except agentclient.AgentError as exc:
            self.invalidate_agent_cache()
            return {"ok": False, "error": str(exc), "agent": False}, str(exc)
        result["agent"] = True
        self.invalidate_agent_cache()
        logs.get().info("recarga del agente (%s): %s", reason,
                        util.dumps({k: v for k, v in result.items() if k != "failed"}))
        return result, None

    def invalidate_agent_cache(self):
        """Tras un cambio hay que releer el estado: si no, la UI muestra datos viejos."""
        with self._agent_lock:
            self._agent_cache_at = 0
            self._agent_cache = None

    # -- systemd -----------------------------------------------------------

    def service_available(self):
        service = self.cfg.get("service_control", {})
        if not service.get("enabled"):
            return False
        systemctl = service.get("systemctl") or "/usr/bin/systemctl"
        return os.path.exists(systemctl)

    def service_command(self, action, unit=None):
        service = self.cfg.get("service_control", {})
        if not service.get("enabled"):
            raise httpd.ApiError(403, "el control del servicio está deshabilitado en panel.json")
        if action not in ("start", "stop", "restart", "status"):
            raise httpd.ApiError(400, "acción no soportada: %s" % action)
        systemctl = service.get("systemctl") or "/usr/bin/systemctl"
        if not os.path.exists(systemctl):
            raise httpd.ApiError(503, "systemctl no está disponible en este sistema")
        # unidades que el panel puede controlar (lista blanca): astra y su entorno
        permitidas = service.get("allowed_units") or [
            "astra.service", "astra-webui.service", "oscam.service", "ncam.service",
            "pcscd.service", "pcscd.socket",
        ]
        if unit is None:
            unit = service.get("unit") or "astra.service"
        if unit not in permitidas:
            raise httpd.ApiError(400, "unidad no permitida: %s" % unit)

        if action == "status":
            base = [systemctl, "is-active", unit]
        else:
            base = [systemctl, action, unit]

        # 1) intento directo (funciona con la regla de polkit incluida en el paquete)
        # 2) sudo -n (funciona con /etc/sudoers.d/astra-webui)
        candidates = [base]
        sudo = service.get("sudo") or "/usr/bin/sudo"
        if os.geteuid() != 0 and os.path.exists(sudo):
            candidates.append([sudo, "-n"] + base)

        last_output = ""
        last_code = 0
        for command in candidates:
            logs.get().debug("ejecutando: %s", " ".join(command))
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                last_output = "no se pudo ejecutar %s: %s" % (command[0], exc)
                last_code = 1
                continue

            output = util.sanitize_text((completed.stdout or "") + (completed.stderr or ""), 512)
            if completed.returncode == 0:
                logs.get().info("systemctl %s %s → ok", action, unit)
                return {"ok": True, "action": action, "unit": unit, "output": output}
            last_output = output
            last_code = completed.returncode

        hint = (" Comprueba que existe la regla de polkit "
                "/usr/share/polkit-1/rules.d/50-astra-webui.rules o el fichero "
                "/etc/sudoers.d/astra-webui, y que el usuario 'astra' puede gestionar %s." % unit)
        raise httpd.ApiError(500, "systemctl %s %s falló (%d): %s%s"
                             % (action, unit, last_code, last_output or "sin salida", hint))

    # -- arranque ----------------------------------------------------------

    def bootstrap(self, default_admin_password=None):
        """Crea el usuario admin inicial si no hay ninguno y sincroniza auth.json."""
        created = None
        if self.users.count() == 0:
            password = default_admin_password or util.random_secret(16)
            payload = {
                "username": "admin",
                "display_name": "Administrador",
                "role": "admin",
                "enabled": True,
                "all_channels": True,
                "max_connections": 0,
            }
            user, errors = self.users.create(payload, password=password, user="system")
            if errors:
                logs.get().error("no se pudo crear el administrador inicial: %s", "; ".join(errors))
            else:
                created = {"username": "admin", "password": password}
                logs.get().warning("usuario 'admin' creado con contraseña inicial: %s", password)
        self.users.sync_auth()
        return created

    def start_background(self):
        self.metrics.start()

    def stop_background(self):
        self.metrics.stop()
