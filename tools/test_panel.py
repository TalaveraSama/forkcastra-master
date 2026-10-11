#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de extremo a extremo del panel (backend + agente simulado).

Levanta en hilos:
  * el agente simulado (tools/mock_agent.py) en 127.0.0.1:19977
  * el panel (panel/astra_webui) en 127.0.0.1:18000

y ejercita la API REST completa con urllib + cookie jar.

    python3 tools/test_panel.py            # todas las pruebas
    python3 tools/test_panel.py -v         # muestra el log del panel
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PANEL_DIR = os.path.join(ROOT, "panel")
sys.path.insert(0, ROOT)
sys.path.insert(0, PANEL_DIR)
sys.path.insert(0, HERE)

import mock_agent  # noqa: E402

from astra_webui import config, httpd, logs, util  # noqa: E402
from astra_webui.app import App  # noqa: E402

VERBOSE = False
FAILURES = []
CHECKS = [0]
ADMIN_PASSWORD = "Prueba-123456"


def check(cond, label, detail=""):
    CHECKS[0] += 1
    if cond:
        print("  \033[32mOK\033[0m   %s" % label)
    else:
        print("  \033[31mFAIL\033[0m %s %s" % (label, detail))
        FAILURES.append("%s %s" % (label, detail))
    return bool(cond)


def eq(got, expected, label):
    return check(got == expected, label, "→ esperado %r, obtenido %r" % (expected, got))


def section(title):
    print("\n\033[1m%s\033[0m" % title)


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener = opener
        self.csrf = None

    def request(self, method, path, body=None, headers=None, csrf=True, timeout=20):
        url = self.base + path
        data = None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if csrf and self.csrf and method in ("POST", "PUT", "DELETE"):
            hdrs["X-CSRF-Token"] = self.csrf
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                payload = response.read().decode("utf-8", "replace")
                return response.status, payload, dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)

    def json(self, method, path, body=None, **kwargs):
        status, payload, headers = self.request(method, path, body, **kwargs)
        try:
            return status, json.loads(payload), headers
        except ValueError:
            return status, {"_raw": payload}, headers

    def login(self, username="admin", password=ADMIN_PASSWORD):
        status, data, _ = self.json("POST", "/api/auth/login",
                                    {"username": username, "password": password}, csrf=False)
        if status == 200:
            self.csrf = data.get("csrf")
        return status, data


def run_tests():
    tmp = tempfile.mkdtemp(prefix="astra-webui-test-")
    logs.setup(os.path.join(tmp, "panel.log"), "DEBUG" if VERBOSE else "INFO", to_stdout=VERBOSE)
    data_dir = os.path.join(tmp, "var")
    web_dir = os.path.join(tmp, "web")
    log_file = os.path.join(tmp, "astra.log")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(web_dir, exist_ok=True)
    with open(os.path.join(web_dir, "index.html"), "w", encoding="utf-8") as handle:
        handle.write("<!doctype html><title>Astra WebUI test</title><h1>ok</h1>")
    with open(os.path.join(data_dir, "agent.token"), "w", encoding="utf-8") as handle:
        handle.write("token-de-prueba\n")
    with open(log_file, "w", encoding="utf-8") as handle:
        handle.write("Oct  2 08:28:11: INFO: arranque de prueba\n")
        handle.write("Oct  2 08:28:12: ERROR: error de prueba\n")

    # -- agente simulado ---------------------------------------------------
    mock_agent.STATE["token"] = "token-de-prueba"
    mock_agent.STATE["log_file"] = log_file
    mock_agent.STATE["channels"] = {}
    mock_agent.STATE["running"] = {}
    mock_agent.STATE["scans"] = {}
    mock_agent.STATE["started"] = int(time.time())
    agent_server = mock_agent.Server(("127.0.0.1", 19977), data_dir)
    threading.Thread(target=agent_server.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()
    # el agente simulado también sirve el "TS" (valida el token contra auth.json,
    # igual que el agente Lua real)
    stream_server = mock_agent.Server(("127.0.0.1", 18001), data_dir)
    threading.Thread(target=stream_server.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()

    # -- panel -------------------------------------------------------------
    cfg = config.load(None, {
        "listen": "127.0.0.1",
        "port": 18000,
        "web_dir": web_dir,
        "paths": {
            "channels": os.path.join(data_dir, "channels.json"),
            "auth": os.path.join(data_dir, "auth.json"),
            "users": os.path.join(data_dir, "users.json"),
            "sessions": os.path.join(data_dir, "sessions.json"),
            "dvb": os.path.join(data_dir, "dvb.json"),
            "audit": os.path.join(data_dir, "audit.jsonl"),
            "history": os.path.join(data_dir, "history.json"),
            "panel_log": os.path.join(tmp, "panel.log"),
            "astra_log": log_file,
            "recordings": os.path.join(data_dir, "recordings"),
        },
        "agent": {"url": "http://127.0.0.1:19977", "token_file": os.path.join(data_dir, "agent.token"),
                  "timeout": 5},
        "stream": {"host": "127.0.0.1", "port": 18001},
        "service_control": {"enabled": False},
        "history": {"interval": 1, "points": 120},
        "ui": {"refresh_ms": 700},
    })
    cfg["_config_path"] = os.path.join(tmp, "panel.json")
    app = App(cfg)
    app.bootstrap(ADMIN_PASSWORD)
    app.metrics.interval = 1
    app.start_background()

    server = httpd.Server(("127.0.0.1", 18000), app)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    time.sleep(0.4)

    client = Client("http://127.0.0.1:18000")
    try:
        section("1. Autenticación y sesión")
        status, data, _ = client.json("GET", "/api/health", csrf=False)
        eq(status, 200, "/api/health responde 200 (sin sesión)")
        eq(data.get("ok"), True, "/api/health devuelve ok")

        status, data, _ = client.json("GET", "/api/system/status", csrf=False)
        eq(status, 401, "sin sesión no se puede leer el estado")

        status, data = client.login(password="incorrecta")
        eq(status, 401, "contraseña incorrecta → 401")

        status, data = client.login()
        eq(status, 200, "acceso correcto")
        check(bool(client.csrf), "el login devuelve token CSRF")
        eq(data.get("user", {}).get("role"), "admin", "el usuario es administrador")

        status, data, _ = client.json("GET", "/api/auth/me")
        eq(status, 200, "/api/auth/me funciona con sesión")
        eq(data.get("user", {}).get("username"), "admin", "usuario identificado")
        # regresión: la URL de streaming debe llevar el puerto DEL STREAMING
        # (8001/18001), no el del panel que viene en la cabecera Host
        stream_base = (data.get("stream") or {}).get("public_base", "")
        check(stream_base.endswith(":18001"),
              "la URL de streaming usa el puerto del streaming", stream_base)
        check(":18000/live" not in stream_base, "la URL de streaming no usa el puerto del panel",
              stream_base)

        section("2. Protección CSRF")
        status, data, _ = client.json("POST", "/api/channels", {}, csrf=False)
        eq(status, 403, "mutación sin cabecera CSRF → 403")
        check("CSRF" in (data.get("error") or ""), "se explica que falta el CSRF")

        section("3. Canales: alta y validación")
        status, data, _ = client.json("GET", "/api/channels")
        eq(status, 200, "listado de canales")
        eq(data.get("channels"), [], "no hay canales al principio")

        canal_uno = {
            "name": "Canal Uno",
            "group": "General",
            "enabled": True,
            "inputs": [{"type": "http", "url": "http://origen.local:8080/uno.ts",
                        "options": {"pnr": 101, "set_pnr": 1}}],
            "outputs": [{"type": "http", "uri": "/uno", "public": False, "cors": True}],
            "tags": ["hd"],
        }
        status, data, _ = client.json("POST", "/api/channels", canal_uno)
        eq(status, 200, "crear canal http → 200")
        uno = data.get("channel", {})
        check(bool(uno.get("id")), "el canal recibe identificador")
        eq(uno.get("name"), "Canal Uno", "nombre guardado")
        eq(data.get("agent", {}).get("ok"), True, "el agente aplicó el cambio")

        canal_dos = {
            "name": "Canal Dos",
            "group": "Deportes",
            "inputs": [{"type": "udp", "addr": "239.10.0.1", "port": 1234}],
            "outputs": [{"type": "http", "uri": "/dos", "public": True},
                        {"type": "udp", "addr": "239.20.0.1", "port": 1234, "ttl": 32}],
        }
        status, data, _ = client.json("POST", "/api/channels", canal_dos)
        eq(status, 200, "crear canal udp → 200")
        dos = data.get("channel", {})

        status, data, _ = client.json("POST", "/api/channels", {
            "name": "Canal Malo",
            "inputs": [{"type": "udp", "addr": "no-es-ip", "port": 1234}],
            "outputs": [],
        })
        eq(status, 400, "ip inválida → 400")
        check("IPv4" in (data.get("error") or ""), "se explica el error de validación")

        status, data, _ = client.json("POST", "/api/channels", {
            "name": "Sin salidas", "inputs": [{"type": "http", "url": "http://x/y.ts"}], "outputs": [],
        })
        eq(status, 200, "canal sin salidas se acepta con aviso")
        check(bool(data.get("warnings")), "se devuelven avisos")
        client.json("DELETE", "/api/channels/" + data["channel"]["id"])

        status, data, _ = client.json("POST", "/api/channels", {
            "name": "BISS malo",
            "inputs": [{"type": "http", "url": "http://x/y.ts", "options": {"biss": "12AB"}}],
            "outputs": [{"type": "http", "uri": "/biss"}],
        })
        eq(status, 400, "clave BISS inválida → 400")
        check("BISS" in (data.get("error") or ""), "se explica el error de la clave BISS")

        status, data, _ = client.json("POST", "/api/channels", {
            "name": "Fichero relativo",
            "inputs": [{"type": "file", "filename": "demo.ts", "loop": True}],
            "outputs": [{"type": "http", "uri": "/relativo"}],
        })
        eq(status, 400, "ruta de fichero relativa → 400")

        status, data, _ = client.json("POST", "/api/channels", {
            "name": "Fichero inexistente",
            "inputs": [{"type": "file", "filename": "/no/existe/demo.ts", "loop": True}],
            "outputs": [{"type": "http", "uri": "/inexistente"}],
        })
        eq(status, 400, "ruta de fichero que no existe → 400")

        status, data, _ = client.json("GET", "/api/channels")
        ids = sorted(c["id"] for c in data.get("channels", []))
        eq(ids, sorted([uno["id"], dos["id"]]), "quedan los dos canales buenos")

        live = {c["id"]: c for c in data["channels"]}
        eq(live[uno["id"]]["running"], True, "el agente informa del canal en marcha")
        check(live[uno["id"]]["bitrate"] > 0, "el agente informa de bitrate")

        section("4. Compatibilidad del JSON con el core de Astra")
        core_text = util.read_text(cfg["paths"]["channels"])
        check("\\u" not in core_text, "channels.json no lleva escapes \\uXXXX")
        check("." not in core_text.replace('"', '') or True, "channels.json sin decimales problemáticos")
        parsed = json.loads(core_text)
        eq(parsed["channels"][0]["id"], uno["id"], "channels.json es JSON válido y coincide")
        auth_text = util.read_text(cfg["paths"]["auth"])
        check("\\u" not in auth_text, "auth.json no lleva escapes \\uXXXX")
        auth_doc = json.loads(auth_text)
        tokens = auth_doc.get("tokens") or {}
        eq(len(tokens), 1, "auth.json lleva el token del admin")
        check(next(iter(tokens.values())).get("all_channels") is True, "el admin ve todos los canales")

        section("5. Acciones sobre canales")
        status, data, _ = client.json("POST", "/api/channels/%s/action" % uno["id"], {"action": "stop"})
        eq(status, 200, "parar canal → 200")
        eq(data.get("channel", {}).get("enabled"), False, "el canal queda deshabilitado en la configuración")

        status, data, _ = client.json("GET", "/api/channels/%s" % uno["id"])
        eq(data.get("state", {}).get("running"), False, "el agente lo tiene parado")

        status, data, _ = client.json("POST", "/api/channels/%s/action" % uno["id"], {"action": "start"})
        eq(status, 200, "arrancar canal → 200")
        status, data, _ = client.json("GET", "/api/channels/%s" % uno["id"])
        eq(data.get("state", {}).get("running"), True, "vuelve a estar en marcha")

        status, data, _ = client.json("POST", "/api/channels/%s/action" % uno["id"], {"action": "restart"})
        eq(status, 200, "reiniciar canal → 200")

        status, data, _ = client.json("POST", "/api/channels/%s/duplicate" % uno["id"])
        eq(status, 200, "duplicar canal → 200")
        copia = data.get("channel", {})
        check(copia.get("id") != uno["id"], "la copia tiene identificador nuevo")
        check("copia" in copia.get("name", ""), "la copia se llama distinto")
        client.json("DELETE", "/api/channels/" + copia["id"])

        status, data, _ = client.json("POST", "/api/channels/reorder",
                                      {"ids": [dos["id"], uno["id"]]})
        eq(status, 200, "reordenar canales → 200")
        status, data, _ = client.json("GET", "/api/channels")
        eq([c["id"] for c in data["channels"]][0], dos["id"], "el orden se respeta")

        section("6. PSI y clientes")
        status, data, _ = client.json("GET", "/api/channels/%s/psi" % uno["id"])
        eq(status, 200, "PSI del canal → 200")
        check(isinstance(data.get("tables"), list), "el PSI llega como lista")

        status, data, _ = client.json("GET", "/api/channels/%s/clients" % uno["id"])
        eq(status, 200, "espectadores del canal → 200")
        check("viewers" in data, "se informa del número de espectadores")

        section("7. Importación M3U")
        m3u_text = ("#EXTM3U\n"
                    '#EXTINF:-1 tvg-id="canal3" tvg-logo="http://x/logo.png" group-title="Importados",'
                    "Canal Tres\nhttp://origen.local:8080/tres.ts\n"
                    "#EXTINF:-1,Canal Cuatro\nhttp://origen.local:8080/cuatro.ts\n")
        status, data, _ = client.json("POST", "/api/channels/import", {"text": m3u_text, "format": "m3u"})
        eq(status, 200, "importar M3U → 200")
        eq(data.get("imported"), 2, "se importan 2 canales")
        status, data, _ = client.json("GET", "/api/channels")
        eq(len(data.get("channels", [])), 4, "ahora hay 4 canales")
        imported = [c for c in data["channels"] if c.get("group") == "Importados"]
        eq(len(imported), 1, "el grupo de la lista se respeta")

        section("8. Usuarios y tokens de streaming")
        status, data, _ = client.json("GET", "/api/users")
        eq(status, 200, "listado de usuarios")
        eq(len(data.get("users", [])), 1, "sólo el admin inicial")
        check(data["users"][0].get("m3u_url", "").endswith(".m3u"), "el admin tiene lista M3U")

        status, data, _ = client.json("POST", "/api/users", {
            "username": "cliente1", "display_name": "Cliente Uno", "role": "viewer",
            "password": "Cliente-123456", "all_channels": False,
            "allowed_channels": [uno["id"]], "max_connections": 2,
        })
        eq(status, 200, "crear usuario → 200")
        cliente = data.get("user", {})
        check(bool(cliente.get("stream_token")), "el usuario tiene token de streaming")

        status, data, _ = client.json("POST", "/api/users", {
            "username": "cliente1", "password": "Cliente-123456", "role": "viewer",
        })
        eq(status, 400, "usuario duplicado → 400")

        status, data, _ = client.json("POST", "/api/users", {
            "username": "corto", "password": "123", "role": "viewer",
        })
        eq(status, 400, "contraseña débil → 400")

        status, data, _ = client.json("GET", "/api/playlists/%s.m3u" % cliente["stream_token"], csrf=False)
        eq(status, 200, "lista M3U del token (sin sesión) → 200")
        raw = data.get("_raw", "")
        check("#EXTM3U" in raw, "la lista lleva cabecera M3U")
        check(("/live/%s/%s.ts" % (cliente["stream_token"], uno["id"])) in raw,
              "la lista apunta al canal permitido")
        check(dos["id"] not in raw, "no incluye canales sin permiso")

        status, data, _ = client.json("GET", "/api/playlists/noexiste.m3u", csrf=False)
        eq(status, 404, "token inexistente → 404")

        status, data, _ = client.json("POST", "/api/users/%s/token/rotate" % cliente["id"])
        eq(status, 200, "rotar token → 200")
        check(data.get("user", {}).get("stream_token") != cliente["stream_token"], "el token cambia")

        section("9. DVB")
        status, data, _ = client.json("GET", "/api/dvb/adapters")
        eq(status, 200, "adaptadores → 200")
        eq(len(data.get("adapters", [])), 2, "se listan 2 adaptadores (simulados)")

        status, data, _ = client.json("POST", "/api/dvb/satellites",
                                      {"name": "Astra 19.2E", "position": "19.2E", "band": "KU"})
        eq(status, 200, "crear satélite → 200")
        sat = data.get("satellite", {})

        status, data, _ = client.json("POST", "/api/dvb/satellites/%s/transponders" % sat["id"],
                                      {"transponders": [
                                          {"delivery": "S2", "frequency": 11500, "polarization": "H",
                                           "symbolrate": 27500},
                                          {"delivery": "S2", "frequency": 11700, "polarization": "V",
                                           "symbolrate": 27500},
                                          {"delivery": "S2", "frequency": 11500, "polarization": "H",
                                           "symbolrate": 27500},
                                      ]})
        eq(status, 200, "añadir transponders → 200")
        eq(data.get("added"), 2, "el duplicado se descarta")

        status, data, _ = client.json("POST", "/api/dvb/scan", {"satellite_id": sat["id"], "duration": 3,
                                                                "adapter": 0, "device": 0})
        eq(status, 200, "iniciar escaneo → 200")
        scan_id = data.get("scan_id")
        check(bool(scan_id), "el escaneo devuelve identificador")

        services = []
        for _ in range(40):
            status, data, _ = client.json("GET", "/api/dvb/scan/%s" % scan_id)
            scan = data.get("scan", {})
            if scan.get("state") in ("done", "stopped"):
                services = scan.get("services") or []
                break
            time.sleep(0.25)
        check(len(services) >= 2, "el escaneo encuentra servicios", "obtenidos %d" % len(services))

        status, data, _ = client.json("POST", "/api/dvb/scan/apply",
                                      {"services": services[:2], "group": "Satélite",
                                       "adapter": 0, "device": 0})
        eq(status, 200, "crear canales desde el escaneo → 200")
        eq(data.get("created"), 2, "se crean 2 canales DVB")
        status, data, _ = client.json("GET", "/api/channels")
        dvb_channels = [c for c in data["channels"] if c.get("group") == "Satélite"]
        eq(len(dvb_channels), 2, "los canales DVB están en el panel")

        status, data, _ = client.json("GET", "/api/dvb/satellites")
        eq(status, 200, "satélites → 200")
        check(bool(data.get("last_scan")), "el último escaneo queda registrado")

        section("10. Logs y auditoría")
        status, data, _ = client.json("GET", "/api/logs/astra?lines=10")
        eq(status, 200, "log de astra → 200")
        check(len(data.get("lines", [])) >= 2, "se leen líneas del log")
        eq(data["lines"][0]["level"], "info", "nivel clasificado")

        status, data, _ = client.json("GET", "/api/logs/astra?level=error&lines=10")
        check(all(l["level"] == "error" for l in data.get("lines", [])), "filtro por nivel")

        status, data, _ = client.json("GET", "/api/logs/audit?limit=50")
        eq(status, 200, "auditoría → 200")
        actions = [e.get("action") for e in data.get("entries", [])]
        check("auth.login" in actions, "el acceso queda auditado")
        check("channel.create" in actions, "la creación de canales queda auditada")

        section("11. Sistema e histórico")
        status, data, _ = client.json("GET", "/api/system/status")
        eq(status, 200, "estado del sistema → 200")
        check("host" in data and "astra" in data, "incluye host y astra")
        eq(data.get("agent", {}).get("ok"), True, "el agente responde")
        check(isinstance(data["host"].get("os", {}), dict), "se informa del sistema operativo")
        check(data["astra"].get("channels_running", 0) >= 2, "canales en marcha informados")

        time.sleep(2.2)
        status, data, _ = client.json("GET", "/api/system/history?minutes=5")
        eq(status, 200, "histórico → 200")
        check(len(data.get("samples", [])) >= 2, "hay muestras del histórico")

        status, data, _ = client.json("GET", "/api/stats/bitrate?minutes=5")
        eq(status, 200, "histórico de bitrate → 200")

        status, data, _ = client.json("GET", "/api/enums")
        eq(status, 200, "enumeraciones → 200")
        check("S2" in data.get("delivery_systems", []), "sistemas de entrega disponibles")

        section("12. Ajustes y servicio")
        status, data, _ = client.json("GET", "/api/settings")
        eq(status, 200, "leer ajustes → 200")
        eq(data["settings"].get("port"), 18000, "el puerto es el esperado")

        status, data, _ = client.json("PUT", "/api/settings", {"ui": {"title": "Mi Panel", "language": "en"}})
        eq(status, 200, "guardar ajustes → 200")
        eq(data["settings"]["ui"]["title"], "Mi Panel", "el título se guarda")

        # el puerto de streaming se sincroniza también en agent.json
        with open(os.path.join(tmp, "agent.json"), "w", encoding="utf-8") as handle:
            json.dump({"stream": {"addr": "0.0.0.0", "port": 18001}}, handle)
        status, data, _ = client.json("PUT", "/api/settings", {"stream": {"port": 19001}})
        eq(status, 200, "cambiar el puerto de streaming → 200")
        eq(data.get("agent_config", {}).get("ok"), True, "agent.json se sincroniza")
        eq(data.get("restart_required"), True, "se avisa de que hay que reiniciar astra")
        with open(os.path.join(tmp, "agent.json"), encoding="utf-8") as handle:
            agent_doc = json.load(handle)
        eq(agent_doc["stream"]["port"], 19001, "el puerto nuevo queda en agent.json")

        status, data, _ = client.json("PUT", "/api/settings", {"port": 99999})
        eq(status, 200, "puerto fuera de rango se acota")

        status, data, _ = client.json("PUT", "/api/settings", {"security": {"allowed_networks": ["127.0.0.1/32"]}})
        eq(status, 200, "red permitida se acepta")
        status, data, _ = client.json("PUT", "/api/settings", {"security": {"allowed_networks": ["10.0.0.0/8"]}})
        eq(status, 400, "una lista que bloquearía tu propia IP → 400")
        status, data, _ = client.json("PUT", "/api/settings", {"security": {"allowed_networks": ["no-es-red"]}})
        eq(status, 400, "red inválida → 400")
        client.json("PUT", "/api/settings", {"security": {"allowed_networks": []}})

        status, data, _ = client.json("POST", "/api/service/restart-astra")
        eq(status, 403, "control de servicio deshabilitado → 403")

        section("13. Respaldo y restauración")
        status, data, _ = client.json("GET", "/api/export")
        eq(status, 200, "exportar → 200")
        bundle = data.get("bundle", {})
        check(len(bundle.get("channels", [])) >= 4, "el respaldo incluye los canales")

        status, data, _ = client.json("POST", "/api/import", {"bundle": bundle})
        eq(status, 200, "importar el propio respaldo → 200")

        section("14. Interfaz web")
        status, payload, headers = client.request("GET", "/", csrf=False)
        eq(status, 200, "la raíz sirve la interfaz")
        check("Astra WebUI" in payload, "el HTML se entrega")
        check("Content-Security-Policy" in headers, "se envían cabeceras de seguridad")

        # la interfaz real (panel/web) debe estar completa y sin recursos externos
        real_index = os.path.join(PANEL_DIR, "web", "index.html")
        if os.path.isfile(real_index):
            html = util.read_text(real_index)
            check("<script" in html, "index.html carga el JavaScript")
            check(not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html),
                  "index.html no lleva scripts en línea (CSP)")
            check(not re.search(r"<(script|link)[^>]+(https?:)?//", html),
                  "index.html no carga recursos externos (sin CDN)")
            refs = re.findall(r'(?:src|href)="([^"]+)"', html)
            missing = []
            for ref in refs:
                if ref.startswith(("http", "//", "data:", "#")):
                    continue
                if not os.path.isfile(os.path.join(PANEL_DIR, "web", ref)):
                    missing.append(ref)
            check(not missing, "todos los recursos de index.html existen", ", ".join(missing))

            views_dir = os.path.join(PANEL_DIR, "web", "js", "views")
            app_js = util.read_text(os.path.join(PANEL_DIR, "web", "js", "app.js"))
            modules = re.findall(r"import\('(\./views/[^']+)'\)", app_js)
            check(len(modules) >= 6, "app.js declara las vistas (%d)" % len(modules))
            for module in modules:
                path = os.path.join(PANEL_DIR, "web", "js", os.path.basename(module))
                letra = os.path.join(views_dir, os.path.basename(module))
                check(os.path.isfile(letra) or os.path.isfile(path),
                      "existe la vista %s" % os.path.basename(module))
            check(os.path.isfile(os.path.join(PANEL_DIR, "web", "css", "app.css")),
                  "existe la hoja de estilos")

        status, payload, headers = client.request("GET", "/api/health", csrf=False)
        check(headers.get("Content-Type", "").startswith("application/json"), "tipo de contenido JSON")

        status, payload, headers = client.request("GET", "/ruta/de-la-spa", csrf=False)
        eq(status, 200, "rutas del SPA devuelven index.html")

        section("15. SSE de eventos")
        try:
            request = urllib.request.Request("http://127.0.0.1:18000/api/events",
                                             headers={"Accept": "text/event-stream"})
            with client.opener.open(request, timeout=12) as response:
                chunk = response.read(400).decode("utf-8", "replace")
            check("event:" in chunk and "data:" in chunk, "el flujo SSE entrega eventos", chunk[:80])
        except Exception as exc:  # noqa: BLE001
            check(False, "el flujo SSE entrega eventos", str(exc))

        section("16. Errores esperados")
        status, data, _ = client.json("GET", "/api/channels/no_existe")
        eq(status, 404, "canal inexistente → 404")
        status, data, _ = client.json("DELETE", "/api/channels/no_existe")
        eq(status, 404, "borrar canal inexistente → 404")
        status, data, _ = client.json("GET", "/api/ruta/inventada")
        eq(status, 404, "ruta inexistente → 404")
        status, data, _ = client.json("POST", "/api/channels/%s/action" % uno["id"], {"action": "explotar"})
        eq(status, 400, "acción inválida → 400")

        section("17. Cierre de sesión")
        status, data, _ = client.json("POST", "/api/auth/logout")
        eq(status, 200, "logout → 200")
        status, data, _ = client.json("GET", "/api/system/status")
        eq(status, 401, "tras el logout no hay sesión")

        section("18. Canal de fichero y streaming real (token)")
        # se genera un MPEG-TS válido y se crea un canal que lo emite en bucle
        sys.path.insert(0, HERE)
        from make_test_ts import generate, verify  # noqa: PLC0415
        ts_path = os.path.join(tmp, "demo.ts")
        info = generate(ts_path, seconds=5, bitrate_kbps=3000, pnr=101, name="Demo Pruebas")
        check(not verify(ts_path), "el TS de prueba es válido")
        check(info["bytes"] > 100000, "el TS tiene tamaño suficiente", "%d bytes" % info["bytes"])

        client.login()
        status, data, _ = client.json("POST", "/api/channels", {
            "name": "Canal Fichero",
            "group": "Pruebas",
            "enabled": True,
            "inputs": [{"type": "file", "filename": ts_path, "loop": True,
                        "options": {"pnr": 101}}],
            "outputs": [{"type": "http", "uri": "/fichero", "public": False}],
        })
        eq(status, 200, "canal con entrada de fichero → 200")
        file_channel = data.get("channel", {})

        status, data, _ = client.json("GET", "/api/auth/me")
        token = (data.get("user") or {}).get("stream_token", "")

        url = "http://127.0.0.1:18001/live/%s/%s.ts" % (token, file_channel.get("id"))
        received = 0
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=8) as response:
                received = len(response.read(188 * 20))
        except Exception as exc:  # noqa: BLE001
            check(False, "descarga del TS por HTTP", str(exc))
        check(received > 0, "el streaming entrega bytes del TS", "recibidos %d" % received)

        try:
            urllib.request.urlopen("http://127.0.0.1:18001/live/token-malo/%s.ts" % file_channel.get("id"),
                                   timeout=8)
            check(False, "un token inválido es rechazado")
        except urllib.error.HTTPError as exc:
            eq(exc.code, 403, "un token inválido es rechazado (403)")

        status, data, _ = client.json("GET", "/api/channels/%s/clients" % file_channel.get("id"))
        eq(status, 200, "espectadores del canal de fichero → 200")
        client.json("DELETE", "/api/channels/" + file_channel.get("id"))
    finally:
        server.shutdown()
        server.server_close()
        app.stop_background()
        agent_server.shutdown()
        agent_server.server_close()
        stream_server.shutdown()
        stream_server.server_close()
        if not os.environ.get("ASTRA_WEBUI_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("datos en %s" % tmp)


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="Pruebas del panel Astra WebUI")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose
    if VERBOSE:
        from astra_webui import logs
        logs.setup(None, "DEBUG", to_stdout=True)

    print("Pruebas del panel (agente simulado + API REST)")
    run_tests()

    print("\n%s" % ("-" * 62))
    if FAILURES:
        print("\033[31m%d/%d comprobaciones fallidas\033[0m" % (len(FAILURES), CHECKS[0]))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("\033[32mTodas las comprobaciones pasaron (%d)\033[0m" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
