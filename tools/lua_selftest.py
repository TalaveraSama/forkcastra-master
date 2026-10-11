#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Banco de pruebas del agente Lua de Astra WebUI.

Carga el `stream.lua` REAL del fork y los stubs de los módulos del core
(tools/agent_stubs.lua) usando `lupa`, arranca el agente completo
(agent/agent.lua) y lanza peticiones HTTP sintéticas contra sus dos servidores
(control en 127.0.0.1:9977 y streaming en 0.0.0.0:8001).

No necesita astra, ni DVB, ni ser root. Es la forma de validar el agente en
desarrollo:

    python tools/lua_selftest.py            # ejecuta todas las pruebas
    python tools/lua_selftest.py -v         # muestra también los logs

Requiere: pip install lupa
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

try:
    import lupa
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    print("ERROR: falta el paquete 'lupa'  →  pip install lupa", file=sys.stderr)
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGENT_DIR = os.path.join(ROOT, "agent")


def find_fork_source():
    """Localiza el código del fork: junto al panel (repo/tree) o en la propia raíz.

    En este repositorio de desarrollo el código de Astra vive en `repo/tree`; si el
    panel se instala DENTRO del fork (el caso de la entrega para GitHub), los
    fuentes están en la raíz del proyecto.
    """
    candidates = [
        os.path.join(ROOT, "repo", "tree"),
        ROOT,
        os.path.dirname(ROOT),
    ]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "version.h")):
            return candidate
    return candidates[0]


FORK_SOURCE = find_fork_source()
STREAM_LUA = os.path.join(FORK_SOURCE, "scripts", "stream.lua")
STUBS_LUA = os.path.join(HERE, "agent_stubs.lua")

VERBOSE = False
LOGS = []

FAILURES = []
CHECKS = [0]


# ---------------------------------------------------------------------------
# utilidades de conversión Lua <-> Python
# ---------------------------------------------------------------------------

def lua_is_table(v):
    return lupa.lua_type(v) == "table"


def lua_to_py(v, depth=0):
    """Convierte una tabla Lua en dict/list de Python (mimetiza el core)."""
    if depth > 24:
        return None
    if not lua_is_table(v):
        return v

    items = list(v.items())
    if not items:
        return []  # el json.encode del core emite [] para tablas vacías

    numeric = [k for k, _ in items if isinstance(k, int)]
    other = [k for k, _ in items if not isinstance(k, int)]

    if not other:
        numeric.sort()
        if numeric == list(range(1, len(numeric) + 1)):
            return [lua_to_py(v[i], depth + 1) for i in numeric]
        return {str(k): lua_to_py(val, depth + 1) for k, val in items}

    return {str(k): lua_to_py(val, depth + 1) for k, val in items}


def py_to_lua(L, obj):
    if isinstance(obj, dict):
        t = L.table()
        for k, val in obj.items():
            t[k] = py_to_lua(L, val)
        return t
    if isinstance(obj, (list, tuple)):
        t = L.table()
        for i, val in enumerate(obj, 1):
            t[i] = py_to_lua(L, val)
        return t
    return obj


def truncate_floats(obj):
    if isinstance(obj, float):
        return int(obj)  # el json.decode del core trunca los decimales
    if isinstance(obj, dict):
        return {k: truncate_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [truncate_floats(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# aserciones
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# entorno
# ---------------------------------------------------------------------------

class Harness:
    def __init__(self, tmpdir):
        self.tmp = tmpdir
        self.L = LuaRuntime(unpack_returned_tuples=True)
        self.logs = []
        self.astr_log_file = os.path.join(tmpdir, "astra.log")

        g = self.L.globals()
        g.TEST_LOG = self._log
        g.TEST_LOG_SET = lambda opts: self.logs.append(("SET", str(lua_to_py(opts))))
        g.TEST_STAT = self._stat
        g.TEST_B64_ENCODE = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")
        g.TEST_B64_DECODE = lambda s: base64.b64decode(s.encode("ascii")).decode("utf-8", "replace")
        g.TEST_MD5 = lambda s: hashlib.md5(s.encode("utf-8")).hexdigest()
        g.TEST_SHA1 = lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest()
        g.TEST_DVBLS = lambda: py_to_lua(self.L, [
            {"adapter": 0, "device": 0, "frontend": "Montage M88DS3103", "mac": "00:11:22:33:44:55",
             "busy": False},
            {"adapter": 1, "device": 0, "frontend": "Afatech AF9033", "mac": "00:11:22:33:44:56",
             "busy": False},
        ])
        g.TEST_JSON_ENCODE = self._json_encode
        g.TEST_JSON_DECODE = self._json_decode

        self.L.execute(open(STUBS_LUA, encoding="utf-8").read())
        self.L.execute(open(STREAM_LUA, encoding="utf-8").read())
        self.L.execute('package.path = %s' % json.dumps(AGENT_DIR + "/?.lua") + ' .. ";" .. package.path')

    # -- callbacks inyectados -------------------------------------------------

    def _log(self, level, msg):
        self.logs.append((level, msg))
        if VERBOSE:
            print("      [lua %s] %s" % (level, msg))

    def _stat(self, path):
        if os.path.isdir(path):
            return py_to_lua(self.L, {"type": "directory", "uid": 0, "gid": 0, "size": 0})
        if os.path.isfile(path):
            return py_to_lua(self.L, {"type": "file", "uid": 0, "gid": 0, "size": os.path.getsize(path)})
        return py_to_lua(self.L, {"type": "unknown", "uid": 0, "gid": 0,
                                  "size": 0, "error": "No such file or directory"})

    def _json_encode(self, tbl):
        obj = lua_to_py(tbl)
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    def _json_decode(self, text):
        if "\\u" in text:
            raise ValueError("el json del core no soporta escapes \\uXXXX")
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("JSON inválido: %s" % exc)
        obj = truncate_floats(obj)
        if obj is None:
            return None
        return py_to_lua(self.L, obj)

    # -- ficheros de estado ---------------------------------------------------

    def write_json(self, name, obj):
        path = os.path.join(self.tmp, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return path

    def read_file(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    # -- arranque del agente --------------------------------------------------

    def start_agent(self):
        cfg = {
            "data_dir": self.tmp,
            "channels_file": os.path.join(self.tmp, "channels.json"),
            "auth_file": os.path.join(self.tmp, "auth.json"),
            "streams_script": STREAM_LUA,
            "reload_interval": 2,
            "auto_reload": True,
            "log_psi_info": False,
            "log": {"filename": self.astr_log_file, "stdout": False, "debug": False, "syslog": ""},
            "control": {"addr": "127.0.0.1", "port": 9977,
                        "token_file": os.path.join(self.tmp, "agent.token")},
            "stream": {"enabled": True, "addr": "0.0.0.0", "port": 8001, "cors": True, "public_base": ""},
        }
        cfg_path = self.write_json("agent.json", cfg)
        with open(os.path.join(self.tmp, "agent.token"), "w", encoding="utf-8") as fh:
            fh.write("s3cr3t-token\n")

        self.L.globals().argv = py_to_lua(self.L, [cfg_path])
        code = open(os.path.join(AGENT_DIR, "agent.lua"), encoding="utf-8").read()
        self.L.execute(code)

        servers = self.L.globals().SERVERS
        self.stream_server = servers[1]
        self.control_server = servers[2]
        return self.stream_server is not None and self.control_server is not None

    # -- peticiones sintéticas ------------------------------------------------

    def request(self, server, method, uri, headers=None, body=None, client=None):
        """Mimetiza el callback del módulo http_server del core."""
        L = self.L
        if client is None:
            client = L.table()
            client["responses"] = L.table()
            client["data"] = L.table()

        hdrs = list(headers or [])
        if body is not None and not any(h.lower().startswith("content-length") for h in hdrs):
            hdrs.append("Content-Length: %d" % len(body.encode("utf-8")))

        req = L.table()
        req["method"] = method
        req["uri"] = uri
        hlist = L.table()
        for i, h in enumerate(hdrs, 1):
            hlist[i] = h
        req["headers"] = hlist
        req["addr"] = "127.0.0.1"
        req["port"] = 41000

        server.callback(server, client, req)

        if body is not None:
            server.callback(server, client, body)
            server.callback(server, client, "")
        else:
            # el core entrega el resto del buffer (normalmente vacío) como cadena
            server.callback(server, client, "")

        return client

    def responses(self, client):
        out = []
        resp_table = client["responses"]
        idx = 1
        while True:
            r = resp_table[idx]
            if r is None:
                break
            out.append(lua_to_py(r))
            idx += 1
        return out

    def last_response(self, client):
        rs = self.responses(client)
        return rs[-1] if rs else None

    def body_of(self, resp):
        if not resp:
            return None
        content = resp.get("content")
        if isinstance(content, str) and content:
            return json.loads(content)
        return None

    def api(self, method, uri, body=None, token="s3cr3t-token", headers=None, raw=False):
        hdrs = list(headers or [])
        if token is not None:
            hdrs.append("X-Astra-Token: %s" % token)
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        client = self.request(self.control_server, method, uri, hdrs, payload)
        resp = self.last_response(client)
        if raw:
            return resp
        return resp, self.body_of(resp)

    def stream_get(self, uri, headers=None, client=None):
        client = self.request(self.stream_server, "GET", uri, headers or [], None, client=client)
        return client, self.last_response(client)

    def tick(self):
        return self.L.eval("TEST_TICK_TIMERS()")

    def lua(self, expr):
        return self.L.eval(expr)

    def lua_exec(self, code):
        return self.L.execute(code)


# ---------------------------------------------------------------------------
# datos de prueba
# ---------------------------------------------------------------------------

def make_channels():
    return {
        "version": 1,
        "updated_at": int(time.time()),
        "channels": [
            {
                "id": "ch_uno",
                "name": "Canal Uno",
                "group": "General",
                "logo": "", "epg_id": "uno.example", "enabled": True, "tags": ["hd"],
                "inputs": [{"type": "http", "url": "http://origen.local:8080/uno.ts",
                            "options": {"pnr": 101, "set_pnr": 1}}],
                "outputs": [{"type": "http", "uri": "/uno", "public": False, "cors": True}],
            },
            {
                "id": "ch_dos",
                "name": "Canal Dos",
                "group": "General", "logo": "", "epg_id": "", "enabled": True, "tags": [],
                "inputs": [{"type": "udp", "addr": "239.10.0.1", "port": 1234, "localaddr": "", "rtp": False,
                            "options": {"pnr": 0}}],
                "outputs": [
                    {"type": "http", "uri": "/dos", "public": True, "cors": True},
                    {"type": "udp", "addr": "239.20.0.1", "port": 1234, "ttl": 32},
                ],
            },
        ],
    }


def make_auth(token="tok1234567890abcdef"):
    return {
        "version": 1,
        "enabled": True,
        "tokens": {
            token: {
                "username": "cliente1",
                "display_name": "Cliente Uno",
                "enabled": True,
                "channels": ["ch_uno"],
                "all_channels": False,
                "expires_at": 0,
                "max_connections": 1,
                "allow_public": False,
            },
            "tokall": {
                "username": "revendedor",
                "display_name": "Revendedor",
                "enabled": True,
                "channels": [],
                "all_channels": True,
                "expires_at": 0,
                "max_connections": 0,
                "allow_public": True,
            },
        },
    }


TOKEN = "tok1234567890abcdef"


# ---------------------------------------------------------------------------
# pruebas
# ---------------------------------------------------------------------------

def run_tests(h):
    section("1. Arranque del agente con stream.lua real")
    h.write_json("channels.json", make_channels())
    h.write_json("auth.json", make_auth())
    with open(h.astr_log_file, "w", encoding="utf-8") as fh:
        fh.write("Oct  2 08:28:11: INFO: arranque de prueba\n")
        fh.write("Oct  2 08:28:12: ERROR: error de prueba\n")
        fh.write("Oct  2 08:28:13: WARNING: aviso de prueba\n")

    ok = h.start_agent()
    check(ok, "el agente creó los dos servidores (streaming + control)")
    eq(h.lua("TEST_EXIT_CALLED"), False, "el agente no se ha detenido en el arranque")
    eq(h.lua('require("aw_channels").running_count()'), 2, "dos canales en marcha tras el arranque")
    eq(h.lua('require("aw_channels").dvb_pool_size()'), 0, "sin transponders DVB reservados")

    section("2. API de control: autenticación")
    resp, data = h.api("GET", "/health")
    eq(resp["code"], 200, "/health responde 200")
    eq(data.get("ok"), True, "/health devuelve ok")
    eq(data.get("running"), 2, "/health informa 2 canales")

    resp, data = h.api("GET", "/status", token="token-malo")
    eq(resp["code"], 401, "/status con token inválido → 401")

    resp, _ = h.api("GET", "/ruta/inexistente")
    eq(resp["code"], 404, "ruta inexistente → 404")

    section("3. API de control: estado")
    resp, data = h.api("GET", "/status")
    eq(resp["code"], 200, "/status responde 200")
    eq(data.get("ok"), True, "/status ok")
    eq(data.get("channels_running"), 2, "/status: 2 canales en marcha")
    ids = [c["id"] for c in data.get("channels", [])]
    eq(sorted(ids), ["ch_dos", "ch_uno"], "/status devuelve los dos canales")
    uno = [c for c in data["channels"] if c["id"] == "ch_uno"][0]
    eq(uno["name"], "Canal Uno", "nombre del canal")
    eq(uno["enabled"], True, "canal habilitado")
    eq(uno["running"], True, "canal en marcha")
    eq(len(uno["inputs"]), 1, "un input en el canal")
    eq(uno["inputs"][0]["type"], "http", "tipo de input http")

    # el json del core no admite objetos vacíos: comprobar que no hay "[]" donde
    # se espera un objeto
    check(isinstance(uno["errors"], dict), "el objeto 'errors' llega como objeto")

    section("4. Streaming con tokens")
    client, resp = h.stream_get("/live/%s/ch_uno.ts" % TOKEN)
    eq(resp["code"], 200, "token válido con permiso → 200")
    check(resp.get("upstream") is not None, "la respuesta adjunta el stream (upstream)")
    eq(h.lua('require("aw_stream").viewers_of("ch_uno")'), 1, "se cuenta 1 espectador")

    _, resp = h.stream_get("/live/noexiste/ch_uno.ts")
    eq(resp["code"], 403, "token desconocido → 403")

    _, resp = h.stream_get("/live/%s/ch_dos.ts" % TOKEN)
    eq(resp["code"], 403, "token sin permiso sobre el canal → 403")

    _, resp = h.stream_get("/live/tokall/ch_inexistente.ts")
    eq(resp["code"], 404, "canal inexistente → 404")

    # desconexión: libera el espectador
    h.request(h.stream_server, "GET", "/live/x", [], None, client=client)
    h.stream_server.callback(h.stream_server, client, None)
    eq(h.lua('require("aw_stream").viewers_of("ch_uno")'), 0,
       "al desconectar el cliente se libera el espectador")

    section("5. Salidas públicas y listas M3U")
    _, resp = h.stream_get("/dos")
    eq(resp["code"], 200, "salida marcada como pública → 200 sin token")
    _, resp = h.stream_get("/uno")
    eq(resp["code"], 403, "salida no pública sin token → 403")

    _, resp = h.stream_get("/live/%s/playlist.m3u" % TOKEN)
    body = resp.get("content") or ""
    check("#EXTM3U" in body, "la lista M3U lleva cabecera")
    check("/live/%s/ch_uno.ts" % TOKEN in body, "la lista M3U incluye el canal permitido")
    check("ch_dos.ts" not in body, "la lista M3U no incluye canales sin permiso")

    _, resp = h.stream_get("/live/tokall/playlist.m3u8")
    body = resp.get("content") or ""
    check("ch_dos.ts" in body, "el token con all_channels ve todos los canales")

    section("6. Recarga en caliente (diff)")
    cfg = make_channels()
    cfg["channels"].append({
        "id": "ch_tres",
        "name": "Canal Tres",
        "group": "Deportes", "logo": "", "epg_id": "", "enabled": True, "tags": [],
        "inputs": [{"type": "http", "url": "http://origen.local:8080/tres.ts", "options": {}}],
        "outputs": [{"type": "http", "uri": "/tres", "public": False}],
    })
    cfg["channels"][0]["name"] = "Canal Uno HD"
    h.write_json("channels.json", cfg)

    resp, data = h.api("POST", "/reload", body={})
    eq(resp["code"], 200, "/reload responde 200")
    eq(data.get("ok"), True, "/reload ok")
    eq(data.get("added"), 1, "/reload añade 1 canal")
    eq(data.get("running"), 3, "/reload deja 3 canales en marcha")

    resp, data = h.api("GET", "/status")
    uno = [c for c in data["channels"] if c["id"] == "ch_uno"][0]
    eq(uno["name"], "Canal Uno HD", "el cambio de nombre se aplica en caliente")

    section("7. Canales inválidos (no deben tumbar el proceso)")
    cfg = make_channels()
    cfg["channels"].append({
        "id": "ch_malo",
        "name": "Canal Malo", "enabled": True,
        "inputs": [{"type": "udp", "addr": "no-es-una-ip", "port": 1234}],
        "outputs": [],
    })
    cfg["channels"].append({
        "id": "ch_sin_input",
        "name": "Sin Entrada", "enabled": True,
        "inputs": [], "outputs": [],
    })
    cfg["channels"].append({
        "id": "ch_biss_malo",
        "name": "BISS Malo", "enabled": True,
        "inputs": [{"type": "http", "url": "http://origen.local/x.ts",
                    "options": {"biss": "1234"}}],
        "outputs": [],
    })
    h.write_json("channels.json", cfg)
    resp, data = h.api("POST", "/reload", body={})
    eq(data.get("running"), 2, "siguen en marcha sólo los canales válidos")
    failed_ids = sorted(f["id"] for f in data.get("failed", []))
    eq(failed_ids, ["ch_biss_malo", "ch_malo", "ch_sin_input"], "se informa de los canales rechazados")
    biss_error = [f["error"] for f in data["failed"] if f["id"] == "ch_biss_malo"][0]
    check("BISS" in biss_error, "se explica el error de la clave BISS", biss_error)

    resp, data = h.api("GET", "/status")
    check(data.get("failed") is None or "ch_malo" in str(data.get("failed")),
          "/status expone los fallos de configuración")

    section("8. Activar/desactivar y acciones por canal")
    cfg = make_channels()
    cfg["channels"][1]["enabled"] = False
    h.write_json("channels.json", cfg)
    _, data = h.api("POST", "/reload", body={})
    eq(data.get("running"), 1, "deshabilitar un canal lo detiene")

    resp, data = h.api("POST", "/channel/start", body={"id": "ch_dos"})
    eq(data.get("ok"), False, "no se puede arrancar un canal deshabilitado")
    check("deshabilitado" in (data.get("error") or ""), "se explica el motivo")

    resp, data = h.api("POST", "/channel/stop", body={"id": "ch_uno"})
    eq(data.get("ok"), True, "parada manual de un canal")
    eq(h.lua('require("aw_channels").running_count()'), 0, "ya no queda nada en marcha")

    resp, data = h.api("POST", "/channel/restart", body={"id": "ch_uno"})
    eq(data.get("ok"), True, "reinicio manual de un canal")
    eq(h.lua('require("aw_channels").running_count()'), 1, "el canal vuelve a estar en marcha")

    resp, data = h.api("POST", "/channel/restart", body={"id": "no_existe"})
    eq(resp["code"], 404, "reiniciar un canal inexistente → 404")

    section("9. Entradas DVB")
    cfg = make_channels()
    cfg["channels"].append({
        "id": "ch_dvb",
        "name": "Canal DVB",
        "group": "Satélite", "enabled": True,
        "inputs": [{
            "type": "dvb", "delivery": "S2", "adapter": 0, "device": 0,
            "frequency": 11500, "polarization": "H", "symbolrate": 27500,
            "lof1": 9750, "lof2": 10600, "slof": 11700,
            "options": {"pnr": 101},
        }],
        "outputs": [{"type": "http", "uri": "/dvb", "public": False}],
    })
    h.write_json("channels.json", cfg)
    _, data = h.api("POST", "/reload", body={})
    eq(data.get("failed"), [], "el canal DVB se acepta")
    eq(h.lua("#DVB_INPUTS"), 1, "se creó un dvb_input")
    eq(h.lua("DVB_INPUTS[1]._opts.adapter"), 0, "adapter correcto")
    eq(h.lua("DVB_INPUTS[1]._opts.type"), "S2", "tipo de frontend correcto")
    eq(h.lua("DVB_INPUTS[1]._opts.lof1"), 9750, "LNB lof1 correcto")

    # un segundo canal en el mismo transponder comparte la instancia
    cfg["channels"].append({
        "id": "ch_dvb2",
        "name": "Canal DVB 2", "enabled": True,
        "inputs": [{
            "type": "dvb", "delivery": "S2", "adapter": 0, "device": 0,
            "frequency": 11500, "polarization": "H", "symbolrate": 27500,
            "lof1": 9750, "lof2": 10600, "slof": 11700, "options": {"pnr": 102},
        }],
        "outputs": [{"type": "http", "uri": "/dvb2", "public": False}],
    })
    h.write_json("channels.json", cfg)
    h.api("POST", "/reload", body={})
    eq(h.lua("#DVB_INPUTS"), 1, "dos canales del mismo transponder comparten un solo dvb_input")
    eq(h.lua('require("aw_channels").dvb_pool_size()'), 1, "el pool mantiene un transponder")

    section("10. Adaptadores y escaneo DVB")
    resp, data = h.api("GET", "/dvb/adapters")
    eq(resp["code"], 200, "/dvb/adapters responde 200")
    eq(len(data.get("adapters", [])), 2, "se listan los 2 adaptadores")
    eq(data["adapters"][0]["frontend"], "Montage M88DS3103", "nombre del frontend")

    resp, data = h.api("POST", "/dvb/scan", body={
        "adapter": 0, "device": 0, "duration": 3,
        "transponders": [
            {"type": "S2", "adapter": 0, "device": 0, "frequency": 11500, "polarization": "H",
             "symbolrate": 27500, "lof1": 9750, "lof2": 10600, "slof": 11700},
            {"type": "S2", "adapter": 0, "device": 0, "frequency": 11700, "polarization": "V",
             "symbolrate": 27500, "lof1": 9750, "lof2": 10600, "slof": 11700},
        ],
    })
    eq(resp["code"], 200, "el escaneo arranca")
    scan_id = data.get("scan_id")
    check(bool(scan_id), "el escaneo devuelve id")

    # alimentar PSI del primer transponder
    h.lua_exec("""
        local a = ANALYZERS[#ANALYZERS]
        a:feed({ psi = "pat", tsid = 1001, crc32 = 1, programs = {
            { pid = 100, pnr = 101 }, { pid = 200, pnr = 102 } } })
        a:feed({ psi = "pmt", pnr = 101, pid = 100, pcr = 101, crc32 = 2,
                 descriptors = { { type_name = "cas", caid = 0x0B00, pid = 500 } },
                 streams = { { pid = 101, type_name = "video", type_id = 0x02 },
                             { pid = 102, type_name = "audio", type_id = 0x03 } } })
        a:feed({ psi = "pmt", pnr = 102, pid = 200, pcr = 201, crc32 = 3,
                 descriptors = {},
                 streams = { { pid = 201, type_name = "video", type_id = 0x02 } } })
        a:feed({ psi = "sdt", tsid = 1001, crc32 = 4, services = {
            { sid = 101, descriptors = { { type_name = "service", service_name = "Canal Uno",
                                           service_provider = "Proveedor" } } },
            { sid = 102, descriptors = { { type_name = "service", service_name = "Canal Dos",
                                           service_provider = "Proveedor" } } } } })
        a:feed({ analyze = { { pid = 101, bitrate = 4000, cc_error = 0, sc_error = 0, pes_error = 0 } },
                 on_air = true })
    """)

    resp, data = h.api("GET", "/dvb/scan?id=%s" % scan_id)
    eq(resp["code"], 200, "/dvb/scan responde 200")
    scan = data.get("scan", {})
    eq(scan.get("state"), "running", "el escaneo está en curso")
    first = scan.get("results", [])
    check(isinstance(first, list), "'results' es una lista")

    # forzar el fin del transponder actual y avanzar
    h.lua_exec('require("aw_dvb").scan.tp_started = os.time() - 100')
    h.tick()
    resp, data = h.api("GET", "/dvb/scan?id=%s" % scan_id)
    scan = data.get("scan", {})
    eq(scan.get("index"), 2, "el escaneo pasa al segundo transponder")
    eq(len(scan.get("results", [])), 1, "se guardó el resultado del primer transponder")
    services = scan["results"][0]["services"]
    eq(len(services), 2, "se detectaron 2 servicios")
    names = sorted(s["name"] for s in services)
    eq(names, ["Canal Dos", "Canal Uno"], "nombres de servicio desde SDT")
    uno_svc = [s for s in services if s["name"] == "Canal Uno"][0]
    eq(uno_svc["scrambled"], True, "se detecta el cifrado (descriptor CAS)")
    eq(uno_svc["video_pid"], 101, "PID de vídeo del PMT")

    # terminar el escaneo (con PSI del segundo transponder)
    h.lua_exec("""
        local a = ANALYZERS[#ANALYZERS]
        a:feed({ psi = "pat", tsid = 2002, crc32 = 5, programs = { { pid = 300, pnr = 201 } } })
        a:feed({ psi = "sdt", tsid = 2002, crc32 = 6, services = {
            { sid = 201, descriptors = { { type_name = "service", service_name = "Canal Tres",
                                           service_provider = "Proveedor" } } } } })
    """)
    h.lua_exec('require("aw_dvb").scan.tp_started = os.time() - 100')
    h.tick()
    resp, data = h.api("GET", "/dvb/scan?id=%s" % scan_id)
    scan = data.get("scan", {})
    eq(scan.get("state"), "done", "el escaneo finaliza")
    eq(len(scan.get("results", [])), 2, "se guardan los dos transponders")
    eq(len(scan.get("services", [])), 3, "el total acumula los servicios de ambos transponders")
    eq(sorted(s["name"] for s in scan["services"]),
       ["Canal Dos", "Canal Tres", "Canal Uno"], "nombres acumulados de los servicios")

    section("11. PSI de un canal en marcha")
    # alimentar el analizador del canal ch_uno (el último creado con ese nombre)
    idx = h.lua('''
        (function()
            local n = 0
            for i, a in ipairs(ANALYZERS) do
                if a._opts and a._opts.name == "Canal Uno" then n = i end
            end
            return n
        end)()
    ''')
    check(idx and idx > 0, "existe el analizador del canal 'Canal Uno'")
    if idx:
        h.lua_exec("""
            local a = ANALYZERS[%d]
            a:feed({ psi = "sdt", tsid = 5, crc32 = 9, services = {
                { sid = 101, descriptors = { { type_name = "service", service_name = "Canal Uno",
                                               service_provider = "Prov" } } } } })
            a:feed({ analyze = { { pid = 101, bitrate = 5340, cc_error = 1, sc_error = 0, pes_error = 0 } },
                     on_air = true })
        """ % idx)

    resp, data = h.api("GET", "/channel/psi?id=ch_uno")
    eq(resp["code"], 200, "/channel/psi responde 200")
    check(len(data.get("tables", [])) >= 1, "se devuelven las tablas PSI capturadas")
    check(any(t.get("psi") == "sdt" for t in data["tables"]), "la tabla SDT está presente")

    resp, data = h.api("GET", "/status")
    uno = [c for c in data["channels"] if c["id"] == "ch_uno"][0]
    eq(uno["bitrate"], 5340, "el bitrate agregado del canal es correcto")
    eq(uno["errors"]["cc"], 1, "se contabilizan los errores de continuidad")

    section("12. Logs")
    resp, data = h.api("GET", "/log/tail?lines=10")
    if os.environ.get("ASTRA_WEBUI_DEBUG"):
        print("      DEBUG raw:", resp.get("content"))
    eq(resp["code"], 200, "/log/tail responde 200")
    lines = data.get("lines", [])
    eq(len(lines), 3, "se leen las 3 líneas del fichero de log")
    eq(lines[0]["level"], "info", "se clasifica el nivel INFO")
    eq(lines[1]["level"], "error", "se clasifica el nivel ERROR")
    eq(lines[2]["level"], "warning", "se clasifica el nivel WARNING")
    check("error de prueba" in lines[1]["message"], "se extrae el mensaje")
    eq(lines[1]["timestamp"], "Oct  2 08:28:12", "se extrae la marca de tiempo")

    section("13. Vigilancia de ficheros (auto reload)")
    cfg = make_channels()
    h.write_json("channels.json", cfg)
    h.tick()
    eq(h.lua('require("aw_channels").running_count()'), 2, "el vigilante aplica los cambios solo")

    section("14. auth.json: recarga y caducidad")
    auth = make_auth()
    auth["tokens"][TOKEN]["expires_at"] = int(time.time()) - 10
    h.write_json("auth.json", auth)
    resp, data = h.api("POST", "/auth/reload", body={})
    eq(data.get("ok"), True, "/auth/reload ok")
    eq(data.get("tokens"), 2, "se cuentan los tokens")
    _, resp = h.stream_get("/live/%s/ch_uno.ts" % TOKEN)
    eq(resp["code"], 403, "un token caducado no puede ver el canal")

    auth = make_auth()
    auth["enabled"] = False
    h.write_json("auth.json", auth)
    h.api("POST", "/auth/reload", body={})
    _, resp = h.stream_get("/live/tokall/ch_uno.ts")
    eq(resp["code"], 403, "con el streaming deshabilitado no se sirve nada")

    section("15. channels.json corrupto no tumba el servicio")
    with open(os.path.join(h.tmp, "channels.json"), "w", encoding="utf-8") as fh:
        fh.write('{"channels": [ {"id": "x", ')
    resp, data = h.api("POST", "/reload", body={})
    eq(data.get("ok"), False, "/reload informa del fallo de lectura")
    eq(h.lua('require("aw_channels").running_count()'), 2, "los canales siguen en marcha")

    section("16. Métricas")
    resp, data = h.api("GET", "/metrics")
    eq(resp["code"], 200, "/metrics responde 200")
    eq(len(data.get("channels", [])), 2, "/metrics lista los canales")
    check("bitrate_total" in data, "/metrics incluye el bitrate total")

    section("17. Compatibilidad del JSON con el core")
    raw = h.lua('require("aw_json").encode({ ok = true, vacio = {}, lista = {}, n = 3, s = "áé" })')
    check("\\u" not in raw, "el agente no emite escapes \\uXXXX")
    check('"vacio":[]' in raw, "las tablas vacías se codifican como [] (igual que el core)")
    check("áé" in raw, "las cadenas UTF-8 salen literales")

    section("18. Integración: canales generados por el panel Python")
    integration(h)


def integration(h):
    """Escribe channels.json/auth.json con el código real del panel y comprueba
    que el agente Lua los acepta (prueba de la costura panel ↔ agente)."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "panel"))
        from astra_webui import security, store, util as putil, validators  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        check(False, "el paquete del panel es importable", str(exc))
        return

    media = os.path.join(h.tmp, "loop.ts")
    with open(media, "wb") as handle:
        handle.write(b"\x47" + b"\x00" * 187)

    channels = []
    errors = []
    specs = [
        {"name": "Panel HTTP", "group": "Panel", "enabled": True,
         "inputs": [{"type": "http", "url": "http://origen.local:8080/panel.ts",
                     "options": {"pnr": 200, "set_pnr": 1}}],
         "outputs": [{"type": "http", "uri": "/panel-http", "public": False},
                     {"type": "udp", "addr": "239.30.0.1", "port": 4321, "ttl": 16}]},
        {"name": "Panel UDP", "group": "Panel", "enabled": True,
         "inputs": [{"type": "udp", "addr": "239.1.9.9", "port": 5000, "rtp": True}],
         "outputs": [{"type": "http", "uri": "/panel-udp", "public": True}]},
        {"name": "Panel Fichero", "group": "Panel", "enabled": True,
         "inputs": [{"type": "file", "filename": media, "loop": True}],
         "outputs": [{"type": "http", "uri": "/panel-file", "public": False}]},
        {"name": "Panel DVB", "group": "Panel", "enabled": True,
         "inputs": [{"type": "dvb", "delivery": "S2", "adapter": 0, "device": 0,
                     "frequency": 12070, "polarization": "V", "symbolrate": 27500,
                     "lof1": 9750, "lof2": 10600, "slof": 11700, "options": {"pnr": 300}}],
         "outputs": [{"type": "http", "uri": "/panel-dvb", "public": False}]},
        {"name": "Panel BISS", "group": "Panel", "enabled": True,
         "inputs": [{"type": "http", "url": "http://origen.local/biss.ts",
                     "options": {"pnr": 400, "biss": "1122330044556600"}}],
         "outputs": [{"type": "http", "uri": "/panel-biss", "public": False}]},
    ]

    if os.name != "posix":
        # las rutas de fichero deben ser absolutas POSIX: en Windows no aplica
        specs = [spec for spec in specs if spec["name"] != "Panel Fichero"]
    expected_names = sorted(spec["name"] for spec in specs)

    for spec in specs:
        channel, errs, _warns = validators.validate_channel(
            spec, None, channels, allowed_roots=[h.tmp + os.sep])
        if errs:
            errors.append("%s: %s" % (spec["name"], "; ".join(errs)))
        else:
            channels.append(channel)

    check(not errors, "el panel valida los canales de prueba", "; ".join(errors))
    eq(len(channels), len(specs), "se crean %d canales desde el panel" % len(specs))

    document = {"version": 1, "updated_at": putil.now(), "channels": channels}
    text = putil.dumps_core(document)  # lanza si hay \\uXXXX o decimales
    with open(os.path.join(h.tmp, "channels.json"), "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    check("\\u" not in text, "el panel escribe JSON sin escapes \\uXXXX")
    check(len(text) > 500, "el fichero de canales tiene contenido")

    # usuarios + auth.json (lo que valida el agente para el streaming)
    users = [{
        "id": "u_test01", "username": "cliente", "display_name": "Cliente de prueba",
        "role": "viewer", "enabled": True, "all_channels": True, "allowed_channels": [],
        "max_connections": 3, "expires_at": 0, "created_at": putil.now(), "last_login": 0,
        "stream_token": "f" * 32, "password": security.hash_password("Cliente-123456"),
        "failed_logins": 0,
    }]
    tmp_root = os.path.join(h.tmp, "paneldata")
    os.makedirs(tmp_root, exist_ok=True)
    db = store.Store({
        "paths": {
            "channels": os.path.join(h.tmp, "channels.json"),
            "auth": os.path.join(h.tmp, "auth.json"),
            "users": os.path.join(tmp_root, "users.json"),
            "sessions": os.path.join(tmp_root, "sessions.json"),
            "dvb": os.path.join(tmp_root, "dvb.json"),
            "history": os.path.join(tmp_root, "history.json"),
            "audit": os.path.join(tmp_root, "audit.jsonl"),
            "panel_log": os.path.join(tmp_root, "panel.log"),
        }
    })
    users_db = store.UsersDB(db)
    users_db.save_all(users, user="system")
    auth_text = putil.read_text(os.path.join(h.tmp, "auth.json"))
    check("\\u" not in auth_text, "el panel escribe auth.json sin escapes \\uXXXX")
    check("f" * 32 in auth_text, "auth.json incluye el token del usuario")

    # el agente aplica lo que escribió el panel
    resp, data = h.api("POST", "/reload", body={})
    eq(resp["code"], 200, "el agente recarga el fichero del panel")
    eq(data.get("failed"), [], "ningún canal generado por el panel es rechazado")
    eq(data.get("running"), len(specs), "los %d canales del panel arrancan" % len(specs))
    eq(h.lua("#DVB_INPUTS >= 1 and true or false"), True, "se creó el dvb_input del canal DVB")

    resp, data = h.api("GET", "/status")
    names = sorted(c["name"] for c in data["channels"])
    eq(names, expected_names, "el agente conoce todos los canales del panel")

    # streaming con el token creado por el panel
    token = "f" * 32
    channel_http = [c for c in data["channels"] if c["name"] == "Panel HTTP"][0]
    _, resp = h.stream_get("/live/%s/%s.ts" % (token, channel_http["id"]))
    eq(resp["code"], 200, "un cliente con all_channels ve el canal (token del panel)")
    require_upstream = resp.get("upstream") is not None
    check(require_upstream, "el TS se adjunta al cliente (upstream)")

    _, resp = h.stream_get("/live/%s/playlist.m3u" % token)
    body = resp.get("content") or ""
    check("#EXTM3U" in body, "la lista M3U del token del panel se genera")
    check(("/live/%s/%s.ts" % (token, channel_http["id"])) in body,
          "la lista M3U apunta al canal del panel")

    _, resp = h.stream_get("/live/%s/%s.ts" % ("0" * 32, channel_http["id"]))
    eq(resp["code"], 403, "un token que no existe en auth.json es rechazado")

    # una salida pública generada por el panel se sirve sin token
    public = [c for c in data["channels"] if c["name"] == "Panel UDP"][0]
    _, resp = h.stream_get("/panel-udp")
    eq(resp["code"], 200, "la salida pública del panel se sirve sin token")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="Pruebas del agente Lua de Astra WebUI")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostrar los logs del agente")
    ap.add_argument("--keep", action="store_true", help="no borrar el directorio temporal")
    args = ap.parse_args()
    VERBOSE = args.verbose

    for path in (STREAM_LUA, STUBS_LUA, os.path.join(AGENT_DIR, "agent.lua")):
        if not os.path.exists(path):
            print("ERROR: no existe %s" % path, file=sys.stderr)
            return 2

    tmp = tempfile.mkdtemp(prefix="astra-webui-selftest-")
    print("Directorio de trabajo: %s" % tmp)
    try:
        h = Harness(tmp)
        run_tests(h)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        FAILURES.append("excepción no controlada: %s" % exc)
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s" % ("-" * 62))
    total = CHECKS[0]
    if FAILURES:
        print("\033[31m%d/%d comprobaciones fallidas\033[0m" % (len(FAILURES), total))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("\033[32mTodas las comprobaciones pasaron (%d)\033[0m" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
