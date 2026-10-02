#!/usr/bin/python3
"""Small, dependency-free administration UI for Forkcastra."""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB = Path(os.environ.get("FORKCASTRA_PANEL_DB", "/var/lib/forkcastra/panel.db"))
STATIC = Path(os.environ.get("FORKCASTRA_PANEL_STATIC", "/usr/share/forkcastra/panel"))
SESSION_TTL = 8 * 60 * 60


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt BLOB NOT NULL, digest BLOB NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash BLOB PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL, expires INTEGER NOT NULL)")
    db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
    db.commit()
    return db


def password_digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def create_admin(username):
    import getpass
    if not username or len(username) > 64:
        raise SystemExit("Invalid username")
    first = getpass.getpass("New panel password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second or len(first) < 12:
        raise SystemExit("Passwords differ or contain fewer than 12 characters")
    salt = secrets.token_bytes(16)
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?)", (username, salt, password_digest(first, salt)))
    print("Administrator created. Start forkcastra-panel.service")


def service_state():
    try:
        result = subprocess.run(["systemctl", "is-active", "forkcastra.service"], text=True, capture_output=True, timeout=3)
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


class Handler(BaseHTTPRequestHandler):
    server_version = "ForkcastraPanel/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def send_headers(self, status=200, content_type="application/json; charset=utf-8", cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16384:
            raise ValueError("request too large")
        return self.rfile.read(length)

    def session(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = jar.get("forkcastra_session")
        if not morsel:
            return None
        token_hash = hashlib.sha256(morsel.value.encode()).digest()
        with connect() as db:
            return db.execute("SELECT username, csrf FROM sessions WHERE token_hash=? AND expires>=?", (token_hash, int(time.time()))).fetchone()

    def json(self, data, status=200, cookie=None):
        self.send_headers(status, cookie=cookie)
        self.wfile.write(json.dumps(data).encode())

    def file(self, name, content_type):
        path = STATIC / name
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_headers(200, content_type)
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.file("index.html", "text/html; charset=utf-8")
        if self.path == "/app.css":
            return self.file("app.css", "text/css; charset=utf-8")
        if self.path == "/app.js":
            return self.file("app.js", "application/javascript; charset=utf-8")
        if self.path == "/api/session":
            session = self.session()
            return self.json({"authenticated": bool(session), "username": session[0] if session else None, "csrf": session[1] if session else None})
        if self.path == "/api/status":
            session = self.session()
            if not session:
                return self.json({"error": "unauthorized"}, 401)
            config = Path("/etc/forkcastra/forkcastra.lua")
            return self.json({"service": service_state(), "config": str(config), "configured": config.exists(), "version": "4.0.282-3"})
        self.send_error(404)

    def do_POST(self):
        try:
            data = json.loads(self.body() or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.json({"error": "invalid request"}, 400)
        if self.path == "/api/login":
            username = str(data.get("username", ""))
            password = str(data.get("password", ""))
            with connect() as db:
                user = db.execute("SELECT salt, digest FROM users WHERE username=?", (username,)).fetchone()
                valid = user and hmac.compare_digest(password_digest(password, user[0]), user[1])
                if not valid:
                    time.sleep(0.4)
                    return self.json({"error": "invalid credentials"}, 401)
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
                db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (hashlib.sha256(token.encode()).digest(), username, csrf, int(time.time()) + SESSION_TTL))
            cookie = "forkcastra_session=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (token, SESSION_TTL)
            return self.json({"ok": True, "csrf": csrf}, cookie=cookie)
        if self.path == "/api/logout":
            session = self.session()
            if session and hmac.compare_digest(str(data.get("csrf", "")), session[1]):
                jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
                token = jar["forkcastra_session"].value
                with connect() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).digest(),))
            return self.json({"ok": True}, cookie="forkcastra_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
        self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description="Forkcastra administration panel")
    parser.add_argument("--create-admin", metavar="USERNAME")
    parser.add_argument("--bind", default=os.environ.get("FORKCASTRA_PANEL_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FORKCASTRA_PANEL_PORT", "8088")))
    args = parser.parse_args()
    if args.create_admin:
        create_admin(args.create_admin)
        return
    connect().close()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print("Forkcastra panel listening on %s:%d" % (args.bind, args.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
