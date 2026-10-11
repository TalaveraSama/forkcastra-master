# -*- coding: utf-8 -*-
"""Servidor HTTP del panel: enrutador, sesiones, estáticos y SSE.

Se usa sólo la biblioteca estándar (http.server + socketserver) para que el
paquete .deb no arrastre dependencias.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, logs, security, util

MAX_BODY = 4 * 1024 * 1024
ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data: https:; "
                                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                                "connect-src 'self'; frame-ancestors 'none'"),
}


class ApiError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


def json_response(payload, status=200, headers=None):
    body = util.dumps(payload).encode("utf-8")
    base = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        base.update(headers)
    return Response(status, body, base)


def text_response(text, status=200, content_type="text/plain; charset=utf-8", headers=None):
    base = {"Content-Type": content_type}
    if headers:
        base.update(headers)
    return Response(status, text.encode("utf-8"), base)


class Response:
    def __init__(self, status=200, body=b"", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.status = status
        self.body = body or b""
        self.headers = dict(headers or {})


class SSE:
    """Respuesta de Server-Sent Events: el handler escribe los trozos."""

    def __init__(self, iterator, headers=None, keep_alive=15):
        self.iterator = iterator
        self.headers = dict(headers or {})
        self.keep_alive = keep_alive


class Request:
    def __init__(self, app, method, path, query_string, headers, body, client, scheme="http"):
        self.app = app
        self.method = method.upper()
        self.raw_path = path
        self.path = urllib.parse.unquote(path)
        self.query_string = query_string or ""
        self.query = urllib.parse.parse_qs(query_string or "", keep_blank_values=True)
        self.headers = headers
        self.body = body or b""
        self.client = client
        self.scheme = scheme
        self.session = None
        self.user = None
        self.params = {}
        self._json = None

    # -- accesos -----------------------------------------------------------

    @property
    def host(self):
        return self.headers.get("host", "")

    @property
    def ip(self):
        if util.to_bool(self.app.cfg.get("security", {}).get("trust_proxy")):
            forwarded = self.headers.get("x-forwarded-for", "")
            if forwarded:
                return util.sanitize_text(forwarded.split(",")[0], 64)
            real = self.headers.get("x-real-ip", "")
            if real:
                return util.sanitize_text(real, 64)
        return util.sanitize_text(self.client, 64)

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    def get(self, name, default=None):
        values = self.query.get(name)
        if not values:
            return default
        return values[0]

    def get_int(self, name, default=0, minimum=None, maximum=None):
        return util.to_int(self.get(name), default, minimum=minimum, maximum=maximum)

    def get_bool(self, name, default=False):
        return util.to_bool(self.get(name), default)

    def cookies(self):
        out = {}
        raw = self.headers.get("cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                key, _, value = part.partition("=")
                out[key.strip()] = value.strip()
        return out

    def json(self):
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self.body.decode("utf-8", "replace"))
                except ValueError as exc:
                    raise ApiError(400, "cuerpo JSON inválido: %s" % exc)
                if not isinstance(self._json, dict):
                    raise ApiError(400, "se esperaba un objeto JSON")
        return self._json

    def form(self):
        parsed = urllib.parse.parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: v[0] if v else "" for k, v in parsed.items()}

    def base_url(self):
        return config.panel_base_url(self.app.cfg, self.host)


class Route:
    def __init__(self, method, pattern, handler, public=False, min_role="viewer", csrf=True):
        self.method = method.upper()
        self.pattern = re.compile("^" + pattern + "$")
        self.handler = handler
        self.public = public
        self.min_role = min_role
        self.csrf = csrf


class Router:
    def __init__(self, app):
        self.app = app
        self.routes = []

    def add(self, method, pattern, handler=None, **opts):
        """Registra una ruta. Se puede usar como decorador:

            @router.add("GET", "/api/health", public=True)
            def health(req): ...
        """
        def decorator(func):
            self.routes.append(Route(method, pattern, func, **opts))
            return func

        if handler is None:
            return decorator
        return decorator(handler)

    def find(self, method, path):
        fallback = None
        for route in self.routes:
            match = route.pattern.match(path)
            if not match:
                continue
            if route.method == method or (method == "HEAD" and route.method == "GET"):
                return route, match.groupdict()
            fallback = fallback or (route, match.groupdict())
        return (None, None) if fallback is None else (None, fallback[1])

    def dispatch(self, req):
        app = self.app
        if not req.path.startswith("/api"):
            return app.static.serve(req)

        route, params = self.find(req.method, req.path)
        if route is None:
            if params is not None:
                raise ApiError(405, "método no permitido para %s" % req.path)
            raise ApiError(404, "ruta no encontrada: %s" % req.path)

        req.params = params or {}

        # red permitida
        networks = app.cfg.get("security", {}).get("allowed_networks") or []
        if networks and not _ip_allowed(req.ip, networks):
            raise ApiError(403, "acceso no permitido desde %s" % req.ip)

        if not route.public:
            session = app.current_session(req)
            if not session:
                raise ApiError(401, "sesión no válida o caducada")
            req.session = session
            user = app.users.find(user_id=session.get("user_id"))
            if not user or not user.get("enabled", True):
                app.sessions.destroy(req.cookies().get(security.SESSION_COOKIE))
                raise ApiError(401, "usuario no disponible")
            req.user = user

            level = ROLE_LEVEL.get(user.get("role"), 0)
            if level < ROLE_LEVEL.get(route.min_role, 1):
                raise ApiError(403, "no tienes permisos para esta operación")

            if route.csrf and req.method in ("POST", "PUT", "PATCH", "DELETE"):
                token = req.header("x-csrf-token")
                if not token or token != session.get("csrf"):
                    raise ApiError(403, "token CSRF inválido (recarga la página)")

        result = route.handler(req, **(params or {}))
        if result is None:
            return json_response({"ok": True})
        if isinstance(result, (Response, SSE)):
            return result
        if isinstance(result, dict):
            return json_response(result)
        raise ApiError(500, "respuesta no soportada del manejador")


def ip_allowed(ip, networks):
    """¿La IP está dentro de alguna de las redes indicadas?"""
    import ipaddress
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in networks or []:
        try:
            if address in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


# compatibilidad con el nombre anterior
_ip_allowed = ip_allowed


class StaticFiles:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.mimetypes = mimetypes.MimeTypes()
        self.mimetypes.add_type("application/javascript", ".js")
        self.mimetypes.add_type("text/css", ".css")
        self.mimetypes.add_type("image/svg+xml", ".svg")
        self.mimetypes.add_type("application/manifest+json", ".webmanifest")

    def _resolve(self, path):
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        candidate = os.path.abspath(os.path.join(self.root, rel))
        if not (candidate == self.root or candidate.startswith(self.root + os.sep)):
            return None
        return candidate

    def serve(self, req):
        path = req.path
        if path.endswith("/"):
            path += "index.html"
        target = self._resolve(path)

        if target is None or not os.path.isfile(target):
            # SPA: cualquier ruta sin extensión devuelve index.html
            if "." not in os.path.basename(path):
                target = self._resolve("index.html")
            if target is None or not os.path.isfile(target):
                return text_response("404 no encontrado", 404)

        try:
            stat = os.stat(target)
        except OSError:
            return text_response("404 no encontrado", 404)

        etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)
        if req.header("if-none-match") == etag:
            return Response(304, b"", {"ETag": etag})

        try:
            with open(target, "rb") as handle:
                body = handle.read()
        except OSError:
            return text_response("500 no se pudo leer el recurso", 500)

        content_type = self.mimetypes.guess_type(target)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "image/svg+xml"):
            content_type += "; charset=utf-8"
        headers = {
            "Content-Type": content_type,
            "ETag": etag,
            "Last-Modified": _http_date(stat.st_mtime),
        }
        name = os.path.basename(target)
        if name == "index.html":
            headers["Cache-Control"] = "no-store"
        elif content_type.startswith("application/javascript") or name.endswith((".js", ".css", ".html")):
            # El código del panel cambia con cada actualización: se revalida siempre con el
            # ETag (sigue siendo barato: 304 si no cambió) para que al recargar se vean las
            # novedades sin tener que forzar la caché del navegador.
            headers["Cache-Control"] = "no-cache"
        else:
            headers["Cache-Control"] = "public, max-age=300"

        return Response(200, body, headers)


def _http_date(timestamp):
    import email.utils
    return email.utils.formatdate(timestamp, usegmt=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "astra-webui"
    sys_version = ""
    disable_nagle_algorithm = True

    # -- verbos ------------------------------------------------------------

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self._respond(Response(204, b"", {"Allow": "GET, POST, PUT, PATCH, DELETE, OPTIONS"}))

    # -- implementación ----------------------------------------------------

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):  # noqa: A003
        logs.get().debug("%s - %s", self.address_string(), fmt % args)

    def _read_body(self):
        length = util.to_int(self.headers.get("Content-Length"), 0, minimum=0)
        if length <= 0:
            return b""
        if length > MAX_BODY:
            raise ApiError(413, "petición demasiado grande")
        return self.rfile.read(length)

    def _handle(self, method):
        try:
            body = self._read_body()
        except ApiError as exc:
            self._respond(json_response({"ok": False, "error": exc.message}, exc.status))
            return
        except OSError:
            self.close_connection = True
            return

        parsed = urllib.parse.urlsplit(self.path)
        headers = {k.lower(): v for k, v in self.headers.items()}
        client = self.client_address[0] if self.client_address else ""
        scheme = "https" if headers.get("x-forwarded-proto") == "https" else "http"
        req = Request(self.app, method, parsed.path, parsed.query, headers, body, client, scheme)

        try:
            result = self.app.router.dispatch(req)
        except ApiError as exc:
            payload = {"ok": False, "error": exc.message}
            payload.update(exc.extra or {})
            result = json_response(payload, exc.status)
        except Exception:  # noqa: BLE001
            logs.get().error("error no controlado en %s %s\n%s", method, self.path, traceback.format_exc())
            result = json_response({"ok": False, "error": "error interno del servidor"}, 500)

        self._respond(result, head_only=(method == "HEAD"))

    def _respond(self, result, head_only=False):
        if isinstance(result, SSE):
            self._respond_sse(result)
            return

        body = result.body if not head_only else b""
        try:
            self.send_response(result.status)
            headers = dict(SECURITY_HEADERS)
            headers.update(result.headers)
            headers["Content-Length"] = str(len(result.body))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _respond_sse(self, result):
        try:
            self.send_response(result.status if hasattr(result, "status") else 200)
            headers = dict(SECURITY_HEADERS)
            headers.update(result.headers)
            headers["Content-Type"] = "text/event-stream; charset=utf-8"
            headers["Cache-Control"] = "no-cache, no-transform"
            headers["X-Accel-Buffering"] = "no"
            headers["Connection"] = "close"
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            return

        self.close_connection = True
        try:
            for chunk in result.iterator:
                if chunk is None:
                    continue
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        logs.get().debug("error atendiendo a %s: %s", client_address, traceback.format_exc())


def serve(app):
    """Arranca el servidor (bloqueante)."""
    cfg = app.cfg
    listen = cfg.get("listen") or "0.0.0.0"
    port = util.to_int(cfg.get("port"), 8000, minimum=1, maximum=65535)
    httpd = Server((listen, port), app)
    logs.get().info("panel escuchando en http://%s:%d", listen, port)
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        logs.get().info("interrupción recibida, cerrando")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0
