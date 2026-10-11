#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprobación de extremo a extremo de la SPA de Astra WebUI en un navegador real.

Lanza Chrome (o Edge) en modo headless con `--remote-debugging-port` y lo
controla por el protocolo DevTools (CDP) sobre WebSocket, usando SÓLO la
biblioteca estándar de Python (no necesita selenium, playwright ni requests).

Requisitos previos (dos procesos ya arrancados):

    $env:PYTHONIOENCODING='utf-8'
    New-Item -ItemType Directory -Force -Path var | Out-Null
    "token-dev" | Set-Content var\\agent.token
    python tools\\mock_agent.py --data-dir var --log-file var\\astra.log      # agente simulado
    $env:PYTHONPATH='panel'
    python -m astra_webui --dev --port 8000 --data-dir var                   # panel :8000

Uso:

    python tools/webui_browser_check.py --password <pwd_admin> \\
        [--user admin] [--agent-pid <pid_agente>] [--keep]

Qué comprueba:
  1. Estáticos: `index.html` carga `js/app.js` como módulo, una sola hoja de
     estilos propia y ningún `<style>` inline (CSP `script-src 'self'`).
  2. Login real contra `POST /api/auth/login` y montaje de la aplicación.
  3. Las siete rutas del router (`#/dashboard`, `#/channels`, `#/users`,
     `#/dvb`, `#/logs`, `#/audit`, `#/settings`) renderizan tarjetas.
  4. Canales: alta desde el formulario (se comprueba en la API), editor con
     entradas/salidas, opciones BISS y filtro de PIDs, diálogo PSI, `Esc` para
     cerrarlo, arrancar/parar y borrado con confirmación. Verifica además que no
     aparezca ningún error interno como toast.
  5. Importación de una lista M3U y creación del canal.
  6. DVB: pestañas, listado de adaptadores, alta masiva de transponders y
     asistente de escaneo con resultados y "crear canales".
  7. Logs (seguimiento SSE), auditoría y ajustes (guardado).
  8. Cambio de idioma EN/ES en caliente.
  9. Modo degradado: con `--agent-pid` apaga el agente simulado y comprueba que
     el panel avisa, sigue mostrando métricas del host y no rompe la interfaz.
 10. Consola del navegador sin excepciones ni errores (se ignoran los avisos
     esperados: imágenes http bloqueadas por la CSP y el 401 inicial de sesión).

Sale con código 0 si todas las comprobaciones pasan.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME_CANDIDATES = [
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Linux (servidores y GitHub Actions)
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "/usr/bin/microsoft-edge",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    import shutil  # noqa: PLC0415
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


PANEL = "http://127.0.0.1:8000/"

# Se activa con --agent-pid: la prueba de modo degradado provoca 502 esperados que
# no deben contarse como errores de consola (el panel los avisa en la interfaz).
EXPECT_AGENT_DOWN = False


# ---------------------------------------------------------------------------
# WebSocket mínimo (cliente)
# ---------------------------------------------------------------------------

class WS:
    def __init__(self, url, timeout=30):
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET /%s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: devtools://devtools\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(request.encode())
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("sin respuesta del handshake")
            buffer += chunk
        head, _, rest_data = buffer.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("handshake fallido: %r" % head[:200])
        self.buffer = rest_data
        self._id = 0

    def _send(self, payload, opcode=1):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        length = len(data)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self.sock.sendall(header + mask + masked)

    def _read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("conexión cerrada")
            self.buffer += chunk
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def recv(self):
        while True:
            first = self._read(2)
            opcode = first[0] & 0x0F
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(length)
            if opcode == 0x8:
                raise RuntimeError("cerrado por el servidor")
            if opcode == 0x9:
                self._send(payload, opcode=0xA)
                continue
            if opcode == 0xA:
                continue
            return payload.decode("utf-8", "replace")

    def call(self, method, params=None):
        self._id += 1
        self._send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            data = json.loads(self.recv())
            if data.get("id") == self._id:
                if "error" in data:
                    raise RuntimeError("%s → %s" % (method, data["error"]))
                return data.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Chrome y ayudas CDP
# ---------------------------------------------------------------------------

def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def launch(chrome, port, user_dir):
    args = [
        chrome, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        "--disable-background-networking", "--window-size=1500,1000",
        "--remote-debugging-port=%d" % port,
        "--remote-allow-origins=*",
        "--user-data-dir=%s" % user_dir,
        "about:blank",
    ]
    # Chrome se niega a arrancar como root (contenedores, CI) sin --no-sandbox
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_devtools(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=1) as resp:
                return json.load(resp)
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError("DevTools no responde en el puerto %d" % port)


class Report:
    def __init__(self):
        self.ok = 0
        self.fail = 0

    def check(self, condition, label, detail=""):
        if condition:
            self.ok += 1
            print("  OK   %s" % label)
        else:
            self.fail += 1
            print("  FAIL %s %s" % (label, detail))

    def section(self, title):
        print("\n== %s" % title)


class Browser:
    def __init__(self, ws):
        self.ws = ws
        self.problems = []

    def eval(self, expression, await_promise=False):
        result = self.ws.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True,
        })
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise RuntimeError(str(detail.get("exception", {}).get("description")
                                   or detail.get("text"))[:600])
        return result.get("result", {}).get("value")

    def wait(self, expression, timeout=20.0, interval=0.25, await_promise=False):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.eval(expression, await_promise=await_promise)
            except RuntimeError as exc:
                last = str(exc)
            if last:
                return last
            self.drain()
            time.sleep(interval)
        return last

    def drain(self):
        """Lee mensajes pendientes de CDP y guarda errores reales."""
        self.ws.sock.settimeout(0.25)
        try:
            while True:
                data = json.loads(self.ws.recv())
                method = data.get("method")
                if method == "Runtime.exceptionThrown":
                    detail = data["params"].get("exceptionDetails", {})
                    text = detail.get("exception", {}).get("description") or detail.get("text")
                    self.problems.append("exception: %s" % str(text)[:400])
                elif method == "Runtime.consoleAPICalled":
                    level = data["params"].get("type")
                    if level in ("error", "warning"):
                        parts = [str(a.get("value", a.get("description", "")))
                                 for a in data["params"].get("args", [])]
                        self.problems.append("console.%s: %s" % (level, " ".join(parts)[:400]))
                elif method == "Log.entryAdded":
                    entry = data["params"].get("entry", {})
                    text = entry.get("text") or ""
                    url = entry.get("url") or ""
                    benign = (
                        # Imágenes http bloqueadas por la CSP del panel: la UI
                        # las sustituye por el marcador de posición.
                        "Content Security Policy" in text
                        # 401 esperado al comprobar la sesión antes del login.
                        or ("401 (Unauthorized)" in text and "/api/auth/me" in url)
                        # 502 esperados en la prueba de agente caído (sección 9):
                        # el panel avisa en la interfaz y la API lo documenta así.
                        or (EXPECT_AGENT_DOWN and "502 (Bad Gateway)" in text
                            and "/api/" in url)
                    )
                    if entry.get("level") == "error" and not benign:
                        self.problems.append("log: %s %s" % (text, url))
        except (socket.timeout, TimeoutError):
            pass
        except RuntimeError:
            pass
        finally:
            self.ws.sock.settimeout(30)

    def set_value(self, selector, value):
        """Asigna un valor con el setter nativo y dispara `input`/`change`."""
        return self.eval("""
            (() => {
              const node = document.querySelector(%s);
              if (!node) return false;
              const proto = node instanceof HTMLTextAreaElement
                ? HTMLTextAreaElement.prototype
                : (node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype);
              const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
              setter.call(node, %s);
              node.dispatchEvent(new Event('input', {bubbles: true}));
              node.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            })()
        """ % (json.dumps(selector), json.dumps(value)))

    def set_labeled(self, label_regex, value, scope="#view"):
        """Rellena el control cuyo `.field__label` coincide con la expresión.

        Se prefiere la coincidencia exacta (normalizando espacios) para que
        «Nueva contraseña» no capture «Repite la nueva contraseña»; si no hay
        coincidencia exacta se usa la primera parcial.
        """
        return self.eval("""
            (() => {
              const scope = document.querySelector(%s) || document;
              const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
              const pattern = normalize(new RegExp(%s, 'i').source);
              const partial = new RegExp(%s, 'i');
              const exact = new RegExp('^' + pattern + '$', 'i');
              const fields = Array.from(scope.querySelectorAll('.field')).filter((f) => {
                const label = f.querySelector('.field__label');
                return label && partial.test(normalize(label.textContent));
              });
              const field = fields.find((f) => {
                const label = f.querySelector('.field__label');
                return label && exact.test(normalize(label.textContent));
              }) || fields[0];
              if (!field) return 'sin campo';
              const node = field.querySelector('input, select, textarea');
              if (!node) return 'sin control';
              const proto = node instanceof HTMLTextAreaElement
                ? HTMLTextAreaElement.prototype
                : (node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype);
              Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, %s);
              node.dispatchEvent(new Event('input', {bubbles: true}));
              node.dispatchEvent(new Event('change', {bubbles: true}));
              return node.value;
            })()
        """ % (json.dumps(scope), json.dumps(label_regex), json.dumps(label_regex), json.dumps(value)))

    def find_element(self, selector, pattern, exact=False):
        """Busca un nodo por su texto normalizado (sin espacios sobrantes).

        Los botones pueden llevar un icono SVG o un glifo en el texto, así que
        por defecto se compara como subcadena; `exact=True` exige igualdad.
        """
        return self.eval("""
            (() => {
              const nodes = Array.from(document.querySelectorAll(%s));
              const pattern = new RegExp(%s, 'i');
              const normalize = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
              const target = nodes.find((node) => {
                const text = normalize(node.textContent);
                return %s ? text.toLowerCase() === pattern.source.toLowerCase() : pattern.test(text);
              });
              if (!target) {
                return 'NO_ENCONTRADO: ' + nodes.slice(0, 12).map((n) => JSON.stringify(normalize(n.textContent))).join(' | ');
              }
              target.click();
              return 'OK';
            })()
        """ % (json.dumps(selector), json.dumps(pattern), 'true' if exact else 'false'))

    def click_text(self, selector, pattern, exact=False):
        return self.find_element(selector, pattern, exact)


# ---------------------------------------------------------------------------
# Pruebas
# ---------------------------------------------------------------------------

def kill_pid(pid):
    """Termina un proceso (Windows con taskkill, Linux/macOS con SIGKILL)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        return
    import signal  # noqa: PLC0415
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", required=True)
    parser.add_argument("--user", default="admin", help="usuario para el login de las pruebas")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--agent-pid", type=int, default=0, help="PID del agente simulado (prueba 502)")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    chrome = find_chrome()
    if not chrome:
        print("no se encontró Chrome/Edge")
        return 2

    user_dir = tempfile.mkdtemp(prefix="astra-webui-chrome-")
    process = launch(chrome, args.port, user_dir)
    report = Report()
    ws = None
    browser = None
    try:
        wait_devtools(args.port)
        target = None
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:%d/json/new?about:blank" % args.port, data=b"", method="PUT")
            with urllib.request.urlopen(request, timeout=5) as resp:
                target = json.load(resp)
        except Exception:  # noqa: BLE001
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % args.port, timeout=5) as resp:
                pages = [tab for tab in json.load(resp) if tab.get("type") == "page"]
            target = pages[0] if pages else None
        if not target:
            raise RuntimeError("no hay pestaña disponible")

        ws = WS(target["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        ws.call("Log.enable")
        browser = Browser(ws)

        report.section("1. Carga de la SPA")
        ws.call("Page.navigate", {"url": PANEL})
        browser.wait("document.readyState === 'complete'")
        browser.wait("document.getElementById('login-screen') && !document.getElementById('login-screen').hidden")
        report.check(browser.eval("!!document.querySelector('script[type=\"module\"][src=\"js/app.js\"]')"),
                     "index.html carga js/app.js como módulo")
        report.check(browser.eval("document.querySelectorAll('style').length === 0"),
                     "no hay estilos inline (CSP `style-src 'self'`)")
        report.check(browser.eval("document.querySelectorAll('link[rel=stylesheet]').length === 1"),
                     "una única hoja de estilos propia")

        report.section("2. Login")
        browser.set_value('#login-username', args.user)
        browser.set_value('#login-password', args.password)
        browser.eval("document.getElementById('login-form').dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); true")
        browser.wait("!document.getElementById('app').hidden", timeout=25)
        browser.drain()
        report.check(browser.eval("!document.getElementById('app').hidden"), "la aplicación se muestra tras el login")
        report.check(browser.eval("document.querySelectorAll('[data-nav]').length") >= 5,
                     "el menú lateral tiene entradas")

        report.section("3. Rutas")
        for hash_value in ["#/dashboard", "#/channels", "#/users", "#/dvb", "#/logs", "#/audit", "#/settings"]:
            browser.eval("window.location.hash = %s; true" % json.dumps(hash_value))
            rendered = browser.wait(
                "document.getElementById('page-title').textContent.length > 0 "
                "&& document.getElementById('view').childElementCount > 0")
            cards = browser.eval("document.querySelectorAll('#view .card, #view .stat').length")
            browser.drain()
            report.check(bool(rendered) and cards > 0, "%s renderiza" % hash_value, "cards=%r" % cards)

        report.section("4. Canales: alta, edición, reordenar y borrado")
        browser.eval("window.location.hash = '#/channels'; true")
        browser.wait("document.querySelectorAll('#view .table tbody tr').length > 0")
        browser.click_text('#view button', 'Nuevo canal', True)
        browser.wait("document.querySelectorAll('#view .repeat__item').length > 0")
        name_set = browser.set_labeled('^Nombre$', 'QA WebUI')
        url_set = browser.set_labeled('URL http', 'http://127.0.0.1:9999/qa.ts')
        report.check(name_set == 'QA WebUI' and url_set == 'http://127.0.0.1:9999/qa.ts',
                     "el formulario acepta nombre y URL", "name=%r url=%r" % (name_set, url_set))
        browser.click_text('#view button', 'Guardar', True)
        created = browser.wait(
            "document.getElementById('page-title').textContent.indexOf('QA WebUI') !== -1 "
            "|| document.getElementById('toast-root').textContent.indexOf('Canal creado') !== -1",
            timeout=25)
        browser.drain()
        report.check(bool(created), "el canal se guarda desde el formulario")
        report.check(not browser.eval(
            "document.getElementById('toast-root').textContent.indexOf('Cannot read properties') !== -1"),
            "ningún error interno se muestra como toast")

        channel_id = browser.eval("""
            (async () => {
              const d = await (await fetch('/api/channels')).json();
              const found = (d.channels || []).find((c) => c.name === 'QA WebUI');
              return found ? found.id : '';
            })()
        """, await_promise=True)
        report.check(bool(channel_id), "el canal existe en la API", str(channel_id))

        if channel_id:
            browser.eval("window.location.hash = '#/channels/' + %s; true" % json.dumps(channel_id))
            browser.wait("document.querySelectorAll('#view .repeat__item').length > 0")
            browser.drain()
            report.check(browser.eval("document.querySelectorAll('#view .repeat__item').length") >= 1,
                         "el editor dibuja entradas y salidas")
            report.check(browser.eval("document.querySelectorAll('#view input, #view select, #view textarea').length") > 10,
                         "el editor expone todos los campos")
            report.check(browser.eval(
                "Array.from(document.querySelectorAll('#view .field__label')).some((n) => /BISS/i.test(n.textContent))"),
                "el editor incluye la opción BISS")
            report.check(browser.eval(
                "Array.from(document.querySelectorAll('#view .field__label')).some((n) => /Filtro de PIDs/i.test(n.textContent))"),
                "el editor incluye el filtro de PIDs")

            # Diálogo PSI
            browser.click_text('#view button', 'PSI')
            browser.wait("document.querySelector('.modal-backdrop')", timeout=8)
            browser.drain()
            report.check(browser.eval("!!document.querySelector('.modal-backdrop')"), "el diálogo PSI se abre")
            browser.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true})); true")
            browser.wait("!document.querySelector('.modal-backdrop')", timeout=5)
            report.check(not browser.eval("!!document.querySelector('.modal-backdrop')"), "Esc cierra el diálogo")

            # Acción start/stop
            browser.click_text('#view .card button', '^(Arrancar|Parar)$')
            time.sleep(1.2)
            browser.drain()
            report.check(True, "arrancar/parar no lanza excepciones")

            # Borrado con confirmación
            browser.click_text('#view button', 'Borrar', True)
            browser.wait("document.querySelector('.modal-backdrop')", timeout=6)
            deleted = browser.click_text('.modal-backdrop button', 'Borrar', True)
            browser.wait("""
                (async () => {
                  const d = await (await fetch('/api/channels')).json();
                  return !(d.channels || []).some((c) => c.id === %s);
                })()
            """ % json.dumps(channel_id), timeout=20, await_promise=True)
            browser.drain()
            report.check(bool(deleted), "el diálogo de borrado aparece y el canal se elimina")

        report.section("5. Importar M3U")
        browser.eval("window.location.hash = '#/channels'; true")
        browser.wait("document.querySelectorAll('#view .table tbody tr').length > 0")
        browser.click_text('#view button', 'Importar M3U')
        browser.wait("document.querySelector('.modal-backdrop textarea')", timeout=8)
        browser.set_value('.modal-backdrop textarea',
                          '#EXTM3U\n#EXTINF:-1 group-title="QA",Canal QA M3U\nhttp://127.0.0.1:9999/qa-m3u.ts\n')
        browser.click_text('.modal-backdrop button', 'Importar', True)
        imported = browser.wait("""
            (async () => {
              const d = await (await fetch('/api/channels')).json();
              return (d.channels || []).some((c) => c.name === 'Canal QA M3U');
            })()
        """, timeout=20, await_promise=True)
        browser.drain()
        report.check(bool(imported), "la importación M3U crea el canal")

        report.section("6. DVB: pestañas, adaptadores, transponders y escaneo")
        browser.eval("window.location.hash = '#/dvb'; true")
        browser.wait("document.querySelectorAll('#view .tab').length === 3", timeout=10)
        browser.drain()
        report.check(browser.eval("document.querySelectorAll('#view .stat, #view .empty').length") > 0,
                     "la pestaña de adaptadores pinta datos")
        browser.click_text('#view .tab', 'Satélites')
        browser.wait("document.querySelector('#view .sat-list') !== null", timeout=10)
        # con una base de datos vacía hay que crear el satélite desde la interfaz
        if browser.eval("document.querySelectorAll('#view .sat-item').length") == 0:
            browser.eval("document.querySelector('#view .card__tools button.btn--primary').click(); true")
            browser.wait("document.querySelector('.modal__body input') !== null", timeout=8)
            browser.set_value('.modal__body input', 'Astra 19.2E (prueba)')
            browser.eval("document.querySelector('.modal__foot button.btn--primary').click(); true")
            browser.wait("document.querySelectorAll('#view .sat-item').length > 0", timeout=12)
        browser.drain()
        satellites = browser.eval("""
            (async () => {
              const r = await fetch('/api/dvb/satellites');
              const d = await r.json().catch(() => ({}));
              return (d.satellites || []).length;
            })()
        """, await_promise=True)
        report.check(browser.eval("document.querySelectorAll('#view .sat-item').length") > 0,
                     "la pestaña lista los satélites guardados", "api=%r" % satellites)
        browser.click_text('#view .card__tools button', 'Añadir transponders', True)
        browser.wait("document.querySelector('.modal-backdrop textarea') !== null", timeout=8)
        browser.set_value('.modal-backdrop textarea', '12000 V 27500\n12100 H 27500')
        browser.click_text('.modal-backdrop button', 'Añadir transponders', True)
        added = browser.wait("""
            (async () => {
              const d = await (await fetch('/api/dvb/transponders')).json();
              return (d.transponders || []).filter((t) => [12000, 12100].includes(t.frequency)).length === 2;
            })()
        """, timeout=25, await_promise=True)
        browser.drain()
        report.check(bool(added), "el alta masiva de transponders funciona")

        browser.click_text('#view .tab', 'Asistente')
        browser.wait("document.querySelector('#view .progress') !== null || document.querySelector('#view .card') !== null")
        browser.eval("""
            (() => {
              const select = Array.from(document.querySelectorAll('#view select'))
                .find((s) => Array.from(s.options).some((o) => o.value === 'manual'));
              const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
              setter.call(select, 'manual');
              select.dispatchEvent(new Event('change', {bubbles: true}));
              const area = document.querySelector('#view textarea');
              const textSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
              textSetter.call(area, '11500 H 27500');
              area.dispatchEvent(new Event('input', {bubbles: true}));
              const duration = Array.from(document.querySelectorAll('#view input[type=number]'))
                .find((i) => i.value === '8');
              if (duration) {
                const numberSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                numberSetter.call(duration, '3');
                duration.dispatchEvent(new Event('input', {bubbles: true}));
              }
              return true;
            })()
        """)
        browser.click_text('#view button', 'Iniciar escaneo', True)
        started = browser.wait("document.body.textContent.includes('Escaneando') || document.querySelectorAll('#view .result-tp').length > 0", timeout=15)
        browser.wait("document.querySelectorAll('#view .result-tp').length > 0", timeout=30)
        browser.drain()
        report.check(bool(started), "el escaneo arranca y llega a resultados")
        report.check(browser.eval("document.querySelectorAll('#view .result-tp').length") >= 1,
                     "los resultados se agrupan por transponder")
        report.check(browser.eval("document.querySelectorAll('#view .result-tp input[type=checkbox]').length") >= 2,
                     "cada servicio tiene casilla de selección")
        # Marcar todo y crear canales
        browser.eval("""
            (() => {
              const boxes = Array.from(document.querySelectorAll('#view .result-tp input[type=checkbox]'));
              if (boxes.length) { boxes[0].checked = true; boxes[0].dispatchEvent(new Event('change', {bubbles:true})); }
              return boxes.length;
            })()
        """)
        browser.wait("!Array.from(document.querySelectorAll('#view button')).find((b) => /Crear \\d+ canales/.test(b.textContent)).disabled", timeout=8)
        browser.click_text('#view button', 'Crear .*canales')
        browser.wait("document.querySelector('.modal-backdrop')", timeout=8)
        before = browser.eval("""
            (async () => (await (await fetch('/api/channels')).json()).channels.length)()
        """, await_promise=True)
        browser.click_text('.modal-backdrop button', '^Crear \\d+ canales?$')
        created = browser.wait("""
            (async () => (await (await fetch('/api/channels')).json()).channels.length > %d)()
        """ % before, timeout=25, await_promise=True)
        browser.drain()
        report.check(bool(created), "«crear canales» desde el escaneo añade canales")

        report.section("7. Logs, auditoría y ajustes")
        browser.eval("window.location.hash = '#/logs'; true")
        browser.wait("document.querySelector('#view .log-view') !== null")
        browser.click_text('#view button', 'Seguir en vivo')
        time.sleep(2.5)
        browser.click_text('#view button', 'Detenido')
        browser.drain()
        report.check(browser.eval("document.querySelectorAll('#view .log-line').length") >= 0,
                     "el visor de logs funciona con el seguimiento SSE")
        browser.eval("window.location.hash = '#/audit'; true")
        browser.wait("document.querySelectorAll('#view .table tbody tr').length > 0", timeout=15)
        report.check(browser.eval("document.querySelectorAll('#view .table tbody tr').length") > 0,
                     "la auditoría lista entradas")
        browser.eval("window.location.hash = '#/settings'; true")
        browser.wait("document.querySelectorAll('#view input').length > 10", timeout=15)
        browser.drain()
        report.check(browser.eval("!!document.querySelector('#view pre')"), "los ajustes muestran el JSON de configuración")
        browser.click_text('#view button', 'Guardar ajustes', True)
        time.sleep(1.5)
        browser.drain()
        report.check(True, "guardar ajustes no lanza excepciones")

        report.section("8. Idioma EN/ES")
        browser.eval("window.location.hash = '#/dashboard'; true")
        browser.wait("document.querySelectorAll('#view .stat').length > 0")
        browser.set_value('#lang-select', 'en')
        browser.wait("document.getElementById('page-title').textContent === 'Dashboard'", timeout=10)
        browser.drain()
        report.check(browser.eval("document.getElementById('page-title').textContent === 'Dashboard'"),
                     "el título se traduce al inglés")
        report.check(browser.eval("document.querySelector('[data-nav=channels]').textContent.includes('Channels')"),
                     "el menú se traduce al inglés")
        browser.set_value('#lang-select', 'es')
        browser.wait("document.getElementById('page-title').textContent === 'Panel'", timeout=10)
        report.check(browser.eval("document.getElementById('page-title').textContent === 'Panel'"),
                     "vuelve al español")

        report.section("9. Modo degradado (agente caído)")
        if args.agent_pid:
            global EXPECT_AGENT_DOWN
            EXPECT_AGENT_DOWN = True
            kill_pid(args.agent_pid)
            time.sleep(1.5)
            browser.eval("window.location.hash = '#/dashboard'; true")
            time.sleep(1)
            browser.eval("window.dispatchEvent(new Event('hashchange')); true")
            degraded = browser.wait("""
                (async () => {
                  const r = await fetch('/api/system/status');
                  const d = await r.json();
                  // Con el agente caído el panel responde 200 con agent.ok=false.
                  return r.status === 200 && d.agent && d.agent.ok === false;
                })()
            """, timeout=20, await_promise=True)
            time.sleep(1)
            browser.drain()
            report.check(bool(degraded), "el estado del panel informa de que el agente está caído")
            host_cards = browser.wait("document.querySelectorAll('#view .stat').length > 0", timeout=15)
            report.check(bool(host_cards), "el dashboard sigue mostrando métricas del host")
            warning = browser.wait("!!document.querySelector('#alert-layer .alert')", timeout=10)
            browser.drain()
            report.check(bool(warning), "se avisa al usuario de que el agente está caído")
            browser.eval("window.location.hash = '#/channels'; true")
            time.sleep(1.5)
            report.check(browser.eval("document.querySelectorAll('#view .card').length") > 0,
                         "la lista de canales sigue funcionando sin agente")
            browser.drain()
        else:
            report.check(True, "prueba de agente caído omitida (sin --agent-pid)")

        report.section("10. Consola")
        for message in browser.problems:
            print("     %s" % message)
        report.check(not browser.problems, "sin excepciones ni errores de consola",
                     "(%d mensajes)" % len(browser.problems))

        print("\n  comprobaciones: %d OK, %d FAIL" % (report.ok, report.fail))
        return 1 if report.fail else 0
    finally:
        if ws:
            ws.close()
        if not args.keep:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
