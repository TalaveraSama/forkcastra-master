#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Demo y verificación end-to-end del panel contra un astra en marcha.

Hace el recorrido completo de un operador, sin tocar ficheros a mano:

  1. entra en el panel (login + CSRF),
  2. genera un MPEG-TS de prueba (`tools/make_test_ts.py`),
  3. crea un canal con entrada `file` en bucle y salida HTTP,
  4. espera a que el agente lo ponga en emisión (bitrate > 0),
  5. descarga bytes reales del TS por `/live/<token>/<canal>.ts`,
  6. lee las tablas PSI capturadas por el analizador,
  7. (opcional) borra el canal de prueba.

Funciona igual contra el núcleo real (Linux/WSL) y contra el agente simulado:

    python3 tools/astra_demo.py --password <clave-admin>
    python3 tools/astra_demo.py --panel http://127.0.0.1:8000 --password X --cleanup

Códigos de salida: 0 todo correcto, 1 alguna comprobación falló, 2 error de uso.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from make_test_ts import generate, verify  # noqa: E402
except ImportError:  # pragma: no cover
    generate = None


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def request(self, method, path, body=None, csrf=True, timeout=30, raw=False):
        url = path if path.startswith("http") else self.base + path
        data = None
        headers = {"Accept": "application/json, text/plain, */*"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf and method in ("POST", "PUT", "DELETE"):
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                payload = response.read()
                if raw:
                    return response.status, payload
                return response.status, json.loads(payload.decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if raw:
                return exc.code, detail.encode()
            try:
                return exc.code, json.loads(detail or "{}")
            except ValueError:
                return exc.code, {"ok": False, "error": detail[:200]}
        except (urllib.error.URLError, OSError) as exc:
            return 0, {"ok": False, "error": str(exc)}

    def login(self, username, password):
        status, data = self.request("POST", "/api/auth/login",
                                    {"username": username, "password": password}, csrf=False)
        if status == 200:
            self.csrf = data.get("csrf", "")
        return status, data


class Report:
    def __init__(self):
        self.ok = 0
        self.fail = 0

    def check(self, condition, label, detail=""):
        if condition:
            self.ok += 1
            print("  \033[32mOK\033[0m   %s" % label)
        else:
            self.fail += 1
            print("  \033[31mFALLO\033[0m %s %s" % (label, detail))
        return bool(condition)

    def info(self, label):
        print("  \033[36m··\033[0m   %s" % label)

    def section(self, title):
        print("\n\033[1m%s\033[0m" % title)


def parse_psi_services(tables):
    """Nombres de servicio y programas detectados en las tablas PSI capturadas."""
    services, programs, pids = [], [], []
    for table in tables:
        if not isinstance(table, dict):
            continue
        if table.get("psi") == "sdt":
            for service in table.get("services") or []:
                name = ""
                for descriptor in service.get("descriptors") or []:
                    if descriptor.get("type_name") == "service":
                        name = descriptor.get("service_name") or ""
                services.append(name or ("sid %s" % service.get("sid")))
        elif table.get("psi") == "pat":
            for program in table.get("programs") or []:
                programs.append("pnr %s (PID %s)" % (program.get("pnr"), program.get("pid")))
        elif table.get("psi") == "pmt":
            for stream in table.get("streams") or []:
                pids.append("%s PID %s" % (stream.get("type_name"), stream.get("pid")))
    return services, programs, pids


def main():
    parser = argparse.ArgumentParser(description="Demo end-to-end de Astra WebUI")
    parser.add_argument("--panel", default="http://127.0.0.1:8000", help="URL del panel")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--ts", default="/var/lib/astra-webui/demo.ts",
                        help="ruta del TS de prueba (debe poder leerla el usuario astra)")
    parser.add_argument("--pnr", type=int, default=101, help="program number del TS y del canal")
    parser.add_argument("--name", default="Canal Demo")
    parser.add_argument("--group", default="Demo")
    parser.add_argument("--stream-base", default="",
                        help="URL base del streaming (por defecto, la que informa el panel)")
    parser.add_argument("--timeout", type=int, default=30, help="espera máxima por canal (s)")
    parser.add_argument("--cleanup", action="store_true", help="borrar el canal al terminar")
    args = parser.parse_args()

    report = Report()
    client = Client(args.panel)

    report.section("1. Acceso al panel")
    status, data = client.login(args.user, args.password)
    if not report.check(status == 200, "login en %s" % args.panel,
                        data.get("error", "HTTP %s" % status)):
        print("\n  comprobaciones: %d OK, %d FAIL" % (report.ok, report.fail))
        return 1
    report.check(bool(client.csrf), "token CSRF recibido")
    status, me = client.request("GET", "/api/auth/me")
    user = me.get("user") or {}
    token = user.get("stream_token", "")
    report.check(bool(token), "token de streaming del usuario")
    if args.stream_base:
        stream_base = args.stream_base.rstrip("/")
    else:
        stream_base = ((me.get("stream") or {}).get("public_base") or "").rstrip("/")
    if not stream_base:
        stream_base = "http://127.0.0.1:%d" % ((me.get("stream") or {}).get("port") or 8001)
    report.info("streaming en %s, token %s…" % (stream_base, token[:8]))

    report.section("2. MPEG-TS de prueba")
    if generate is None:
        report.check(False, "generador de TS disponible", "no se encontró tools/make_test_ts.py")
        return 1
    ts_path = args.ts
    if os.name != "posix":
        # fuera de Linux la ruta por defecto (/var/lib/...) se resolvería contra
        # la raíz de la unidad: se usa un directorio temporal
        import tempfile  # noqa: PLC0415
        ts_path = os.path.join(tempfile.gettempdir(), "astra-demo.ts")
        report.info("sistema no POSIX: TS de prueba en %s" % ts_path)
    try:
        os.makedirs(os.path.dirname(ts_path) or ".", exist_ok=True)
    except OSError:
        import tempfile  # noqa: PLC0415
        ts_path = os.path.join(tempfile.gettempdir(), "astra-demo.ts")
        report.info("no se puede escribir en %s; se usa %s" % (args.ts, ts_path))
    try:
        info = generate(ts_path, seconds=30, bitrate_kbps=4000, pnr=args.pnr, name=args.name)
    except OSError as exc:
        report.check(False, "escribir el TS de prueba en %s" % ts_path, str(exc))
        print("       (usa --ts con una ruta escribible por el panel y legible por el agente)")
        return 1
    report.check(os.path.isfile(ts_path), "TS generado en %s" % ts_path)
    report.check(not verify(ts_path), "el TS es estructuralmente válido")
    report.info("%d bytes, %d paquetes" % (info["bytes"], info["packets"]))

    report.section("3. Alta del canal")
    channel_id = None
    status, data = client.request("POST", "/api/channels", {
        "name": args.name,
        "group": args.group,
        "enabled": True,
        "tags": ["demo", "test"],
        "inputs": [{"type": "file", "filename": ts_path, "loop": True,
                    "options": {"pnr": args.pnr, "set_pnr": 0}}],
        "outputs": [{"type": "http", "uri": "/demo", "public": False, "cors": True}],
    })
    if status == 200:
        channel_id = (data.get("channel") or {}).get("id")
    elif status == 409 or "ya existe otro canal" in str(data.get("error", "")):
        # ya existía de una ejecución anterior: se reutiliza
        status, listing = client.request("GET", "/api/channels")
        for item in (listing.get("channels") or []):
            if item.get("name") == args.name:
                channel_id = item.get("id")
                break
        report.info("el canal '%s' ya existía (%s)" % (args.name, channel_id))
    report.check(bool(channel_id), "canal creado/recuperado",
                 data.get("error", "HTTP %s" % status))
    if not channel_id:
        return 1
    if data.get("agent_error"):
        report.info("aviso del agente: %s" % data["agent_error"])
    for warning in data.get("warnings") or []:
        report.info("aviso: %s" % warning)

    report.section("4. Emisión (agente real o simulado)")
    deadline = time.time() + args.timeout
    state = {}
    while time.time() < deadline:
        status, detail = client.request("GET", "/api/channels/%s" % channel_id)
        state = detail.get("state") or {}
        if state.get("running") and state.get("bitrate", 0) > 0:
            break
        time.sleep(1)
    report.check(bool(state.get("running")), "el canal está en marcha")
    report.check(state.get("bitrate", 0) > 0, "el analizador mide bitrate",
                 "bitrate=%s" % state.get("bitrate"))
    report.check(state.get("on_air") is True, "el flujo está en emisión (on_air)",
                 "on_air=%s" % state.get("on_air"))
    inputs = state.get("inputs") or []
    if inputs:
        report.check(inputs[0].get("on_air") is True, "la entrada 1 recibe datos",
                     "on_air=%s" % inputs[0].get("on_air"))
    report.info("bitrate %s kbit/s, errores cc=%s sc=%s pes=%s"
                % (state.get("bitrate"), (state.get("errors") or {}).get("cc"),
                   (state.get("errors") or {}).get("sc"), (state.get("errors") or {}).get("pes")))

    report.section("5. Streaming con token")
    url = "%s/live/%s/%s.ts" % (stream_base, token, channel_id)
    report.info("descargando %s" % url)
    received = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "astra-demo"})
        with urllib.request.urlopen(request, timeout=8) as response:
            deadline = time.time() + 6
            while time.time() < deadline:
                chunk = response.read(188 * 50)
                if not chunk:
                    break
                received += len(chunk)
                if received >= 188 * 500:  # ~94 KB
                    break
    except (urllib.error.URLError, OSError, ValueError) as exc:
        report.info("la descarga terminó: %s" % exc)
    report.check(received > 0, "se reciben bytes del TS por HTTP", "recibidos %d" % received)
    report.check(received >= 188 * 10, "el flujo es continuo", "recibidos %d bytes" % received)

    status, rejected = client.request("GET", "%s/live/tokeninvalido/%s.ts" % (stream_base, channel_id),
                                      csrf=False, raw=True)
    report.check(status == 403, "un token inválido es rechazado (403)", "HTTP %s" % status)

    report.section("6. Analizador PSI")
    tables = []
    deadline = time.time() + 15
    while time.time() < deadline:
        status, psi = client.request("GET", "/api/channels/%s/psi" % channel_id)
        tables = psi.get("tables") or []
        if tables:
            break
        time.sleep(1)
    if tables:
        services, programs, pids = parse_psi_services(tables)
        report.check(True, "el agente entrega tablas PSI (%d)" % len(tables))
        if services:
            report.info("servicios en la SDT: %s" % ", ".join(services))
            report.check(any(args.name in s for s in services),
                         "el nombre del servicio llega por SDT", ", ".join(services))
        if programs:
            report.info("programas en la PAT: %s" % ", ".join(programs))
        if pids:
            report.info("PIDs en la PMT: %s" % ", ".join(pids))
    else:
        report.info("todavía no hay tablas PSI (con el agente simulado es lo normal)")

    report.section("7. Resumen")
    status, listing = client.request("GET", "/api/channels")
    report.info("canales en el panel: %d" % len(listing.get("channels") or []))
    report.info("URL del canal: %s" % url)
    report.info("consola: abre %s e inicia sesión con %s" % (args.panel, args.user))

    if args.cleanup:
        status, _ = client.request("DELETE", "/api/channels/%s" % channel_id)
        report.check(status == 200, "canal de prueba borrado")
    else:
        report.info("el canal de prueba se conserva (usa --cleanup para borrarlo)")

    print("\n  comprobaciones: %d OK, %d FAIL" % (report.ok, report.fail))
    return 1 if report.fail else 0


if __name__ == "__main__":
    sys.exit(main())
