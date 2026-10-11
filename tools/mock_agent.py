#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente simulado para desarrollar/probar el panel sin astra ni DVB.

Implementa el mismo contrato que el agente Lua (docs/ARCHITECTURE.md, sección 4)
sobre `channels.json`, `auth.json` y `agent.token`.

    python3 tools/mock_agent.py --data-dir ./var --token-file ./var/agent.token
    python3 tools/mock_agent.py --token ""        # desactiva la comprobación

Endpoints: /health /status /metrics /reload /auth/reload /channel/{start,stop,
restart} /channel/psi /dvb/adapters /dvb/scan /dvb/scan/stop /log/tail /exit
Además sirve un TS de mentira en http://127.0.0.1:8001/live/<token>/<canal>.ts
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socketserver
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "started": int(time.time()),
    "channels": {},       # id -> dict de configuración
    "running": {},        # id -> True
    "viewers": {},        # id -> n
    "bitrate": {},        # id -> kbit/s
    "psi": {},
    "scans": {},
    "token": None,
    "log_file": "/var/log/astra/astra.log",
    "adapters": [
        {"adapter": 0, "device": 0, "frontend": "Montage M88DS3103", "mac": "00:11:22:33:44:55",
         "busy": False, "error": ""},
        {"adapter": 1, "device": 0, "frontend": "Afatech AF9033", "mac": "00:11:22:33:44:56",
         "busy": False, "error": ""},
    ],
}
LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def now():
    return int(time.time())


def channels_from_config(path):
    doc = read_json(path, {})
    channels = doc.get("channels") or []
    out = {}
    for channel in channels:
        if isinstance(channel, dict) and channel.get("id"):
            out[channel["id"]] = channel
    return out


def load_state(data_dir):
    STATE["channels"] = channels_from_config(os.path.join(data_dir, "channels.json"))
    with LOCK:
        for channel_id, channel in STATE["channels"].items():
            if channel.get("enabled", True) and channel_id not in STATE["running"]:
                STATE["running"][channel_id] = True
                STATE["bitrate"][channel_id] = random.randint(1500, 9000)
                STATE["viewers"][channel_id] = random.randint(0, 4)
        for channel_id in list(STATE["running"]):
            if channel_id not in STATE["channels"] or not STATE["channels"][channel_id].get("enabled", True):
                STATE["running"].pop(channel_id, None)


def simulate():
    with LOCK:
        for channel_id in list(STATE["running"]):
            base = STATE["bitrate"].get(channel_id, 3000)
            STATE["bitrate"][channel_id] = max(200, base + random.randint(-250, 250))
            if random.random() < 0.02:
                STATE["viewers"][channel_id] = max(0, STATE["viewers"].get(channel_id, 0) + random.choice([-1, 1]))


# ---------------------------------------------------------------------------
# respuestas
# ---------------------------------------------------------------------------

def status_payload():
    with LOCK:
        channels = []
        total_bitrate = 0
        total_viewers = 0
        for channel_id, channel in STATE["channels"].items():
            running = bool(STATE["running"].get(channel_id))
            bitrate = STATE["bitrate"].get(channel_id, 0) if running else 0
            viewers = STATE["viewers"].get(channel_id, 0) if running else 0
            total_bitrate += bitrate
            total_viewers += viewers
            inputs = []
            for index, source in enumerate(channel.get("inputs") or [], 1):
                inputs.append({
                    "index": index,
                    "type": source.get("type", ""),
                    "on_air": running and index == 1,
                    "bitrate": bitrate if index == 1 else 0,
                    "cc_error": random.randint(0, 3) if running else 0,
                    "sc_error": 0,
                    "pes_error": 0,
                    "error": "",
                })
            outputs = []
            for output in channel.get("outputs") or []:
                target = output.get("uri") or output.get("filename") or ""
                if output.get("type") == "udp":
                    target = "%s:%s" % (output.get("addr"), output.get("port"))
                outputs.append({"type": output.get("type", ""), "target": target,
                                "public": output.get("public", False)})
            channels.append({
                "id": channel_id,
                "name": channel.get("name", channel_id),
                "group": channel.get("group", "General"),
                "enabled": channel.get("enabled", True),
                "running": running,
                "on_air": running,
                "bitrate": bitrate,
                "viewers": viewers,
                "errors": {"cc": random.randint(0, 5) if running else 0, "sc": 0, "pes": 0},
                "started": STATE["started"],
                "inputs": inputs,
                "outputs": outputs,
            })
        return {
            "ok": True,
            "version": "4.0 dev:282 (mock)",
            "pid": os.getpid(),
            "uptime": now() - STATE["started"],
            "rss_kb": random.randint(20000, 60000),
            "config_error": "",
            "channels_total": len(STATE["channels"]),
            "channels_running": len(STATE["running"]),
            "dvb_transponders": len(STATE["adapters"]),
            "viewers": total_viewers,
            "bitrate_total": total_bitrate,
            "applied_at": STATE["started"],
            "channels": channels,
        }


def metrics_payload():
    payload = status_payload()
    return {
        "ok": True,
        "uptime": payload["uptime"],
        "bitrate_total": payload["bitrate_total"],
        "viewers": payload["viewers"],
        "channels": [{"id": c["id"], "name": c["name"], "enabled": c["enabled"],
                      "running": c["running"], "on_air": c["on_air"],
                      "bitrate": c["bitrate"], "viewers": c["viewers"]}
                     for c in payload["channels"]],
    }


def fake_services(transponder, index):
    base_pnr = 100 + index * 10
    return [
        {"pnr": base_pnr + 1, "sid": base_pnr + 1, "pmt_pid": 1000 + index,
         "name": "Servicio %d A" % (index + 1), "provider": "Proveedor de prueba",
         "scrambled": index % 2 == 0, "video_pid": 101, "audio_pid": 102,
         "transponder": {"type": transponder.get("delivery") or transponder.get("type") or "S2",
                         "frequency": transponder.get("frequency", 0),
                         "polarization": transponder.get("polarization", "H"),
                         "symbolrate": transponder.get("symbolrate", 0), "tsid": 1000 + index}},
        {"pnr": base_pnr + 2, "sid": base_pnr + 2, "pmt_pid": 1100 + index,
         "name": "Servicio %d B" % (index + 1), "provider": "Proveedor de prueba",
         "scrambled": False, "video_pid": 201, "audio_pid": 202,
         "transponder": {"type": transponder.get("delivery") or transponder.get("type") or "S2",
                         "frequency": transponder.get("frequency", 0),
                         "polarization": transponder.get("polarization", "H"),
                         "symbolrate": transponder.get("symbolrate", 0), "tsid": 1000 + index}},
    ]


def tick_scans():
    with LOCK:
        for scan in STATE["scans"].values():
            if scan["state"] != "running":
                continue
            elapsed = now() - scan["tp_started"]
            if elapsed >= scan["duration"]:
                tp = scan["transponders"][scan["index"] - 1]
                services = fake_services(tp, scan["index"] - 1)
                scan["results"].append({
                    "index": scan["index"], "type": tp.get("delivery") or tp.get("type") or "S2",
                    "frequency": tp.get("frequency", 0), "polarization": tp.get("polarization", "H"),
                    "symbolrate": tp.get("symbolrate", 0), "tsid": 1000 + scan["index"],
                    "bitrate": random.randint(20000, 45000), "on_air": True,
                    "services_count": len(services), "services": services, "error": "",
                })
                scan["services"].extend(services)
                scan["index"] += 1
                scan["tp_started"] = now()
                if scan["index"] > len(scan["transponders"]):
                    scan["state"] = "done"
                    scan["finished"] = now()


# ---------------------------------------------------------------------------
# servidor
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "astra-mock"

    def log_message(self, fmt, *args):  # noqa: A003
        if os.environ.get("MOCK_VERBOSE"):
            sys.stderr.write("[mock] " + (fmt % args) + "\n")

    # -- utilidades --------------------------------------------------------

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _error(self, code, message):
        self._json(code, {"ok": False, "error": message})

    def _check_token(self):
        if not STATE["token"]:
            return True
        return self.headers.get("X-Astra-Token") == STATE["token"]

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    # -- verbos ------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/live/"):
            return self._stream(path)

        if not self._check_token():
            return self._error(401, "token de agente inválido")

        tick_scans()

        # ---- rutas GET ----------------------------------------------------
        if method == "GET":
            if path == "/health":
                payload = status_payload()
                return self._json(200, {"ok": True, "version": payload["version"], "pid": payload["pid"],
                                        "uptime": payload["uptime"], "running": payload["channels_running"],
                                        "rss_kb": payload["rss_kb"], "config_error": ""})
            if path == "/status":
                return self._json(200, status_payload())
            if path == "/metrics":
                return self._json(200, metrics_payload())
            if path == "/log/tail":
                lines = int((params.get("lines") or ["100"])[0])
                return self._json(200, {"ok": True, "path": STATE["log_file"],
                                        "lines": self._log_lines(lines)})
            if path == "/channel/psi":
                channel_id = (params.get("id") or [""])[0]
                return self._json(200, {"ok": True, "id": channel_id,
                                        "tables": STATE["psi"].get(channel_id, []),
                                        "stats": {"bitrate": STATE["bitrate"].get(channel_id, 0),
                                                  "on_air": bool(STATE["running"].get(channel_id))}})
            if path == "/dvb/adapters":
                return self._json(200, {"ok": True, "adapters": STATE["adapters"]})
            if path == "/dvb/scan":
                scan_id = (params.get("id") or [""])[0]
                scan = STATE["scans"].get(scan_id) or (list(STATE["scans"].values())[-1]
                                                       if STATE["scans"] else None)
                if not scan:
                    return self._json(200, {"ok": True, "scan": {"state": "idle", "id": "", "total": 0,
                                                                 "index": 0, "services": [], "results": []}})
                current = scan["transponders"][min(scan["index"] - 1, scan["total"] - 1)]
                return self._json(200, {"ok": True, "scan": {
                    "id": scan["id"], "state": scan["state"], "adapter": scan["adapter"],
                    "device": scan["device"], "duration": scan["duration"], "index": scan["index"],
                    "total": scan["total"], "started": scan["started"], "finished": scan.get("finished", 0),
                    "current": {"frequency": current.get("frequency", 0),
                                "polarization": current.get("polarization", "H"),
                                "symbolrate": current.get("symbolrate", 0),
                                "type": current.get("delivery", "S2")},
                    "bitrate": random.randint(20000, 45000), "on_air": scan["state"] == "running",
                    "error": "", "services": scan["services"], "results": scan["results"]}})

        # ---- rutas POST ---------------------------------------------------
        body = self._body()

        if path == "/reload":
            before = set(STATE["channels"])
            load_state(self.server.data_dir)
            after = set(STATE["channels"])
            return self._json(200, {"ok": True, "changed": True,
                                    "added": len(after - before), "updated": 0,
                                    "removed": len(before - after),
                                    "running": len(STATE["running"]), "failed": [],
                                    "auth_ok": True, "auth_error": ""})
        if path == "/auth/reload":
            return self._json(200, {"ok": True, "changed": False, "tokens": 1, "error": ""})
        if path == "/channel/start":
            channel_id = body.get("id", "")
            with LOCK:
                STATE["running"][channel_id] = True
                STATE["bitrate"].setdefault(channel_id, random.randint(1500, 9000))
            return self._json(200, {"ok": True, "id": channel_id, "running": len(STATE["running"])})
        if path == "/channel/stop":
            channel_id = body.get("id", "")
            with LOCK:
                STATE["running"].pop(channel_id, None)
                STATE["viewers"][channel_id] = 0
            return self._json(200, {"ok": True, "id": channel_id, "running": len(STATE["running"])})
        if path == "/channel/restart":
            channel_id = body.get("id", "")
            with LOCK:
                STATE["running"][channel_id] = True
                STATE["bitrate"][channel_id] = random.randint(1500, 9000)
            return self._json(200, {"ok": True, "id": channel_id})
        if path == "/dvb/scan":
            transponders = body.get("transponders") or []
            if not transponders:
                return self._error(400, "se requiere la lista 'transponders'")
            scan_id = "scan_%06x" % random.randint(0, 0xFFFFFF)
            duration = max(1, min(3, int(body.get("duration") or 3)))
            STATE["scans"][scan_id] = {
                "id": scan_id, "state": "running",
                "adapter": int(body.get("adapter") or 0), "device": int(body.get("device") or 0),
                "duration": duration, "transponders": transponders, "index": 1,
                "total": len(transponders), "services": [], "results": [],
                "started": now(), "finished": 0, "tp_started": now(),
            }
            return self._json(200, {"ok": True, "scan_id": scan_id})
        if path == "/dvb/scan/stop":
            scan_id = body.get("id", "")
            scan = STATE["scans"].get(scan_id)
            if scan:
                scan["state"] = "stopped"
                scan["finished"] = now()
            return self._json(200, {"ok": True})
        if path == "/exit":
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return self._json(200, {"ok": True})

        return self._error(404, "ruta no encontrada: %s" % path)

    # -- log simulado ------------------------------------------------------

    def _log_lines(self, count):
        now_t = time.time()
        levels = ["INFO", "INFO", "INFO", "WARNING", "ERROR", "DEBUG"]
        templates = [
            "[Canal Uno] Bitrate: 5340Kbit/s",
            "[dvb_input 0:0] frontend locked",
            "[stream.lua] canal 'Canal Dos' arrancado",
            "failed to read a request",
            "[http_server 0.0.0.0:8001] client connected",
        ]
        lines = []
        for index in range(count):
            stamp = time.strftime("%b %d %H:%M:%S", time.localtime(now_t - (count - index) * 3))
            level = random.choice(levels)
            lines.append("%s: %s: %s" % (stamp, level, random.choice(templates)))
        return lines

    # -- streaming de mentira ---------------------------------------------

    def _stream(self, path):
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3 or parts[0] != "live":
            return self._error(404, "ruta no encontrada")
        token, channel = parts[1], parts[2]

        # igual que el agente real: el token se valida contra auth.json
        tokens = read_json(os.path.join(self.server.data_dir, "auth.json"), {}).get("tokens") or {}
        account = tokens.get(token)
        if not account:
            return self._error(403, "token desconocido")
        if account.get("enabled") is False:
            return self._error(403, "token deshabilitado")
        expires = int(account.get("expires_at") or 0)
        if expires and expires < now():
            return self._error(403, "token expirado")
        if not account.get("all_channels") and channel not in (account.get("channels") or []):
            return self._error(403, "sin permiso para ese canal")

        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        packet = bytes([0x47]) + bytes(random.getrandbits(8) for _ in range(187))
        try:
            for _ in range(40):
                self.wfile.write(packet * 20)
                self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, data_dir):
        self.data_dir = data_dir
        super().__init__(address, Handler)


def _simulate_loop():
    while True:
        try:
            simulate()
        except Exception:  # noqa: BLE001 - el simulador nunca debe morir
            pass
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Agente Astra simulado (desarrollo)")
    parser.add_argument("--data-dir", default="./var", help="directorio con channels.json y agent.token")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9977)
    parser.add_argument("--stream-port", type=int, default=8001,
                        help="puerto del servidor de streaming simulado (0 para no abrirlo)")
    parser.add_argument("--token", default=None, help="token esperado; vacío para no validar")
    parser.add_argument("--log-file", default=None, help="fichero de log a mostrar en /log/tail")
    args = parser.parse_args()

    if args.token is None:
        token_path = os.path.join(args.data_dir, "agent.token")
        try:
            with open(token_path, "r", encoding="utf-8") as handle:
                STATE["token"] = handle.read().strip()
        except OSError:
            STATE["token"] = None
    else:
        STATE["token"] = args.token or None

    if args.log_file:
        STATE["log_file"] = args.log_file
    load_state(args.data_dir)

    threading.Thread(target=_simulate_loop, daemon=True).start()

    if args.stream_port:
        stream_server = Server((args.listen, args.stream_port), args.data_dir)
        threading.Thread(target=stream_server.serve_forever, daemon=True).start()
        print("[mock] streaming simulado en http://%s:%d/live/<token>/<canal>.ts"
              % (args.listen, args.stream_port))

    server = Server((args.listen, args.port), args.data_dir)
    print("[mock] agente simulado en http://%s:%d (token=%s, data=%s)"
          % (args.listen, args.port, STATE["token"] or "libre", args.data_dir))
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
