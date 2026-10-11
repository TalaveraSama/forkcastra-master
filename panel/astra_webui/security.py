# -*- coding: utf-8 -*-
"""Seguridad del panel: contraseñas, sesiones, CSRF y límite de intentos."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading

from . import util

PBKDF2_ALGO = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 260000
SESSION_COOKIE = "astra_session"


# ---------------------------------------------------------------------------
# contraseñas
# ---------------------------------------------------------------------------

def hash_password(password, iterations=PBKDF2_ITERATIONS):
    if not isinstance(password, str) or len(password) < 4:
        raise ValueError("la contraseña es demasiado corta")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "algo": PBKDF2_ALGO,
        "iterations": int(iterations),
        "salt": salt.hex(),
        "hash": digest.hex(),
    }


def verify_password(stored, password):
    if not isinstance(stored, dict) or not isinstance(password, str):
        return False
    try:
        salt = bytes.fromhex(stored.get("salt", ""))
        expected = bytes.fromhex(stored.get("hash", ""))
        iterations = util.to_int(stored.get("iterations"), PBKDF2_ITERATIONS)
    except (ValueError, TypeError):
        return False
    if not salt or not expected:
        return False
    digest = util.hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def password_policy_problems(password, username=""):
    problems = []
    if not isinstance(password, str) or len(password) < 8:
        problems.append("la contraseña debe tener al menos 8 caracteres")
    if isinstance(password, str) and password.lower() in ("12345678", "password", "astra123", "admin123"):
        problems.append("esa contraseña es demasiado común")
    if username and isinstance(password, str) and username.lower() in password.lower():
        problems.append("la contraseña no debe contener el nombre de usuario")
    return problems


# ---------------------------------------------------------------------------
# sesiones
# ---------------------------------------------------------------------------

class SessionManager:
    """Sesiones persistidas en sessions.json (sobreviven al reinicio).

    Sólo se guarda el SHA-256 del identificador: si alguien lee el fichero no
    puede suplantar sesiones.
    """

    def __init__(self, store, hours=12):
        self.store = store
        self.hours = util.to_int(hours, 12, minimum=1, maximum=720)
        self._lock = threading.RLock()

    def _load(self):
        data = self.store.load("sessions")
        if not isinstance(data, dict):
            data = {}
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        return {"version": 1, "sessions": sessions}

    def _save(self, data):
        self.store.save("sessions", data)

    def create(self, user, ip="", user_agent=""):
        with self._lock:
            data = self._load()
            sid = util.random_secret(48)
            csrf = util.random_secret(32)
            entry = {
                "user_id": user.get("id"),
                "username": user.get("username"),
                "created": util.now(),
                "expires": util.now() + self.hours * 3600,
                "csrf": csrf,
                "ip": util.sanitize_text(ip, 64),
                "ua": util.sanitize_text(user_agent, 160),
                "last_seen": util.now(),
            }
            data["sessions"][util.sha256_hex(sid)] = entry
            self.purge(data)
            self._save(data)
            return sid, entry

    def get(self, sid, touch=True):
        if not sid or not isinstance(sid, str):
            return None
        key = util.sha256_hex(sid)
        with self._lock:
            data = self._load()
            entry = data["sessions"].get(key)
            if not entry:
                return None
            if util.to_int(entry.get("expires"), 0) < util.now():
                data["sessions"].pop(key, None)
                self._save(data)
                return None
            if touch:
                entry["last_seen"] = util.now()
                self._save(data)
            return entry

    def destroy(self, sid):
        if not sid:
            return
        key = util.sha256_hex(sid)
        with self._lock:
            data = self._load()
            if data["sessions"].pop(key, None) is not None:
                self._save(data)

    def destroy_user(self, user_id):
        with self._lock:
            data = self._load()
            removed = [k for k, v in data["sessions"].items() if v.get("user_id") == user_id]
            for key in removed:
                data["sessions"].pop(key, None)
            if removed:
                self._save(data)
            return len(removed)

    def purge(self, data=None):
        own = data is None
        if own:
            data = self._load()
        current = util.now()
        expired = [k for k, v in data["sessions"].items() if util.to_int(v.get("expires"), 0) < current]
        for key in expired:
            data["sessions"].pop(key, None)
        if own and expired:
            self._save(data)
        return len(expired)

    def count(self):
        with self._lock:
            data = self._load()
            self.purge(data)
            return len(data["sessions"])

    def list_active(self):
        with self._lock:
            data = self._load()
            self.purge(data)
            out = []
            for entry in data["sessions"].values():
                out.append({
                    "username": entry.get("username"),
                    "ip": entry.get("ip"),
                    "created": util.to_int(entry.get("created"), 0),
                    "last_seen": util.to_int(entry.get("last_seen"), 0),
                    "expires": util.to_int(entry.get("expires"), 0),
                })
            out.sort(key=lambda item: item["last_seen"], reverse=True)
            return out


# ---------------------------------------------------------------------------
# límite de intentos de acceso
# ---------------------------------------------------------------------------

class RateLimiter:
    """Ventana deslizante en memoria por clave (usuario+IP)."""

    def __init__(self, max_attempts=5, window=300):
        self.max_attempts = util.to_int(max_attempts, 5, minimum=1, maximum=100)
        self.window = util.to_int(window, 300, minimum=30, maximum=86400)
        self._hits = {}
        self._lock = threading.Lock()

    def _prune(self, key, current):
        hits = [t for t in self._hits.get(key, []) if current - t < self.window]
        self._hits[key] = hits
        return hits

    def check(self, key):
        with self._lock:
            current = util.now()
            hits = self._prune(key, current)
            if len(hits) >= self.max_attempts:
                retry = self.window - (current - hits[0])
                return False, max(1, int(retry))
            return True, 0

    def fail(self, key):
        with self._lock:
            current = util.now()
            hits = self._prune(key, current)
            hits.append(current)
            self._hits[key] = hits
            return len(hits)

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def attempts(self, key):
        with self._lock:
            return len(self._prune(key, util.now()))
