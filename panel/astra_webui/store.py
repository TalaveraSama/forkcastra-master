# -*- coding: utf-8 -*-
"""Persistencia del panel: canales, usuarios, tokens de streaming, DVB, auditoría.

La "fuente de verdad" son ficheros JSON en /var/lib/astra-webui. El agente Lua
lee `channels.json` y `auth.json`; el panel los escribe de forma atómica y con
el formato que el parser JSON del core entiende (util.dumps_core).
"""

from __future__ import annotations

import os
import threading

from . import config, security, util, validators


class Store:
    """Acceso a los ficheros de datos con caché en memoria y bloqueo."""

    KINDS = {
        "channels": ("paths", "channels", True),
        "auth": ("paths", "auth", True),
        "users": ("paths", "users", False),
        "sessions": ("paths", "sessions", False),
        "dvb": ("paths", "dvb", False),
        "history": ("paths", "history", False),
        "audit": ("paths", "audit", False),
        "panel_log": ("paths", "panel_log", False),
    }

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self._cache = {}

    def path(self, kind):
        section, key, _core = self.KINDS[kind]
        return config.get_path(self.cfg, "%s.%s" % (section, key))

    def is_core_file(self, kind):
        return self.KINDS[kind][2]

    def load(self, kind, default=None):
        path = self.path(kind)
        with self.lock:
            if kind in self._cache:
                return self._cache[kind]
            text = util.read_text(path)
            if not text:
                data = default if default is not None else {}
            else:
                try:
                    data = util.loads(text, default if default is not None else {})
                except ValueError:
                    data = default if default is not None else {}
                if not isinstance(data, dict):
                    data = default if default is not None else {}
            self._cache[kind] = data
            return data

    def save(self, kind, data):
        path = self.path(kind)
        if self.is_core_file(kind):
            text = util.dumps_core(data)
        else:
            text = util.dumps(data)
        with self.lock:
            util.write_atomic(path, text, mode=0o640)
            self._cache[kind] = data
        return path

    def invalidate(self, kind=None):
        with self.lock:
            if kind is None:
                self._cache.clear()
            else:
                self._cache.pop(kind, None)

    # -- auditoría ---------------------------------------------------------

    def audit(self, user, ip, action, target="", result="ok", detail=""):
        entry = {
            "t": util.now(),
            "user": util.sanitize_text(user, 32) if isinstance(user, str) else util.sanitize_text(
                (user or {}).get("username"), 32),
            "ip": util.sanitize_text(ip, 64),
            "action": util.sanitize_text(action, 64),
            "target": util.sanitize_text(target, 128),
            "result": util.sanitize_text(result, 32),
            "detail": util.sanitize_text(detail, 512, keep_newlines=False),
        }
        try:
            util.append_line(self.path("audit"), util.dumps(entry))
        except OSError:
            pass
        return entry

    def audit_tail(self, limit=200):
        lines = util.tail_lines(self.path("audit"), max_lines=util.to_int(limit, 200, minimum=1, maximum=5000))
        entries = []
        for line in lines:
            try:
                item = util.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                item["iso"] = util.iso(item.get("t"))
                entries.append(item)
        entries.reverse()
        return entries


# ---------------------------------------------------------------------------
# canales
# ---------------------------------------------------------------------------

class ChannelsDB:
    def __init__(self, store):
        self.store = store
        self.lock = store.lock

    def _doc(self):
        doc = self.store.load("channels")
        if not isinstance(doc, dict):
            doc = {}
        if not isinstance(doc.get("channels"), list):
            doc["channels"] = []
        doc.setdefault("version", 1)
        return doc

    def list(self):
        with self.lock:
            return list(self._doc()["channels"])

    def get(self, channel_id):
        for channel in self.list():
            if channel.get("id") == channel_id:
                return channel
        return None

    def save_all(self, channels, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            doc["channels"] = channels
            doc["updated_at"] = util.now()
            path = self.store.save("channels", doc)
        if user is not None:
            self.store.audit(user, ip, "channels.save", "channels.json", "ok",
                             "%d canales" % len(channels))
        return path

    def create(self, payload, user=None, ip=""):
        with self.lock:
            channels = self.list()
            channel, errors, warnings = validators.validate_channel(payload, None, channels)
            if errors:
                return None, errors, warnings
            channels.append(channel)
            self.save_all(channels, user, ip)
            return channel, [], warnings

    def update(self, channel_id, payload, user=None, ip=""):
        with self.lock:
            channels = self.list()
            existing = None
            for channel in channels:
                if channel.get("id") == channel_id:
                    existing = channel
                    break
            if existing is None:
                return None, ["canal no encontrado"], []
            merged = dict(existing)
            merged.update(payload or {})
            merged["id"] = existing.get("id")
            if "id" in (payload or {}) and payload["id"] != existing.get("id"):
                return None, ["no se puede cambiar el identificador del canal"], []
            channel, errors, warnings = validators.validate_channel(merged, existing, channels)
            if errors:
                return None, errors, warnings
            index = channels.index(existing)
            channels[index] = channel
            self.save_all(channels, user, ip)
            return channel, [], warnings

    def delete(self, channel_id, user=None, ip=""):
        with self.lock:
            channels = self.list()
            remaining = [c for c in channels if c.get("id") != channel_id]
            if len(remaining) == len(channels):
                return False
            self.save_all(remaining, user, ip)
            return True

    def duplicate(self, channel_id, user=None, ip=""):
        with self.lock:
            channels = self.list()
            source = None
            for channel in channels:
                if channel.get("id") == channel_id:
                    source = channel
                    break
            if source is None:
                return None, ["canal no encontrado"], []
            copy_payload = util.loads(util.dumps(source), {})
            copy_payload["id"] = util.new_id("ch_")
            copy_payload["name"] = (source.get("name") or "Canal") + " (copia)"
            channel, errors, warnings = validators.validate_channel(copy_payload, None, channels)
            if errors:
                return None, errors, warnings
            channels.append(channel)
            self.save_all(channels, user, ip)
            return channel, [], warnings

    def reorder(self, ids, user=None, ip=""):
        with self.lock:
            channels = self.list()
            by_id = {c.get("id"): c for c in channels}
            ordered = [by_id[i] for i in ids if i in by_id]
            for channel in channels:
                if channel.get("id") not in ids:
                    ordered.append(channel)
            self.save_all(ordered, user, ip)
            return ordered

    def set_enabled(self, channel_id, enabled, user=None, ip=""):
        return self.update(channel_id, {"enabled": bool(enabled)}, user, ip)

    def import_m3u(self, text, group="Importado", user=None, ip=""):
        """Importa una lista M3U/M3U8 como canales http."""
        channels = self.list()
        imported = []
        errors = []
        pending_name = None
        pending_attrs = {}
        index = 0
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF"):
                index += 1
                body = line.split(",", 1)
                pending_name = util.sanitize_text(body[1] if len(body) > 1 else ("Canal %d" % index), 96)
                attrs = {}
                for key in ("tvg-id", "tvg-logo", "group-title"):
                    marker = '%s="' % key
                    start = line.find(marker)
                    if start != -1:
                        start += len(marker)
                        end = line.find('"', start)
                        if end != -1:
                            attrs[key] = line[start:end]
                pending_attrs = attrs
                continue
            if line.startswith("#"):
                continue

            url = util.sanitize_text(line, 1024)
            if not url.lower().startswith("http"):
                errors.append("url no soportada: %s" % url[:80])
                continue
            payload = {
                "name": pending_name or ("Canal %d" % (index or 1)),
                "group": pending_attrs.get("group-title") or group,
                "logo": pending_attrs.get("tvg-logo", ""),
                "epg_id": pending_attrs.get("tvg-id", ""),
                "enabled": True,
                "inputs": [{"type": "http", "url": url, "options": {}}],
                "outputs": [{"type": "http", "uri": "/" + util.slugify(pending_name or "canal"), "public": False}],
            }
            channel, errs, _warns = validators.validate_channel(payload, None, channels + imported)
            if errs:
                errors.append("%s: %s" % (payload["name"], "; ".join(errs)))
            else:
                imported.append(channel)
            pending_name = None
            pending_attrs = {}

        if imported:
            channels.extend(imported)
            self.save_all(channels, user, ip)
        return imported, errors


# ---------------------------------------------------------------------------
# usuarios + tokens de streaming
# ---------------------------------------------------------------------------

class UsersDB:
    def __init__(self, store):
        self.store = store
        self.lock = store.lock

    def _doc(self):
        doc = self.store.load("users")
        if not isinstance(doc, dict):
            doc = {}
        if not isinstance(doc.get("users"), list):
            doc["users"] = []
        doc.setdefault("version", 1)
        return doc

    def list(self):
        with self.lock:
            return list(self._doc()["users"])

    def find(self, username=None, user_id=None):
        for user in self.list():
            if username is not None and user.get("username") == username:
                return user
            if user_id is not None and user.get("id") == user_id:
                return user
        return None

    def count(self):
        return len(self.list())

    def save_all(self, users, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            doc["users"] = users
            doc["updated_at"] = util.now()
            self.store.save("users", doc)
            self.sync_auth()
        if user is not None:
            self.store.audit(user, ip, "users.save", "users.json", "ok", "%d usuarios" % len(users))

    def create(self, payload, password=None, user=None, ip=""):
        with self.lock:
            users = self.list()
            clean, errors = validators.validate_user(payload, None, users)
            if errors:
                return None, errors
            if not password:
                errors.append("la contraseña es obligatoria")
                return None, errors
            problems = security.password_policy_problems(password, clean.get("username"))
            if problems:
                return None, problems
            clean["password"] = security.hash_password(password)
            users.append(clean)
            self.save_all(users, user, ip)
            return clean, []

    def update(self, user_id, payload, password=None, user=None, ip=""):
        with self.lock:
            users = self.list()
            existing = None
            for item in users:
                if item.get("id") == user_id:
                    existing = item
                    break
            if existing is None:
                return None, ["usuario no encontrado"]
            merged = dict(existing)
            merged.update(payload or {})
            merged["id"] = existing.get("id")
            clean, errors = validators.validate_user(merged, existing, users)
            if errors:
                return None, errors
            if password:
                problems = security.password_policy_problems(password, clean.get("username"))
                if problems:
                    return None, problems
                clean["password"] = security.hash_password(password)
            index = users.index(existing)
            users[index] = clean
            self.save_all(users, user, ip)
            return clean, []

    def delete(self, user_id, user=None, ip=""):
        with self.lock:
            users = self.list()
            target = None
            for item in users:
                if item.get("id") == user_id:
                    target = item
                    break
            if target is None:
                return False, "usuario no encontrado"
            admins = [u for u in users if u.get("role") == "admin" and u.get("enabled") and u.get("id") != user_id]
            if target.get("role") == "admin" and not admins:
                return False, "no puedes borrar el único administrador"
            remaining = [u for u in users if u.get("id") != user_id]
            self.save_all(remaining, user, ip)
            security.SessionManager(self.store).destroy_user(user_id)
            return True, None

    def rotate_token(self, user_id, user=None, ip=""):
        with self.lock:
            users = self.list()
            for item in users:
                if item.get("id") == user_id:
                    item["stream_token"] = util.random_token(32)
                    self.save_all(users, user, ip)
                    return item, None
            return None, "usuario no encontrado"

    def authenticate(self, username, password):
        user = self.find(username=(username or "").strip().lower())
        if not user:
            return None, "usuario o contraseña incorrectos"
        if not security.verify_password(user.get("password"), password):
            return None, "usuario o contraseña incorrectos"
        if not user.get("enabled", True):
            return None, "el usuario está deshabilitado"
        return user, None

    def mark_login(self, user_id, user=None, ip=""):
        with self.lock:
            users = self.list()
            for item in users:
                if item.get("id") == user_id:
                    item["last_login"] = util.now()
                    item["failed_logins"] = 0
                    self.save_all(users, user, ip)
                    return

    def change_password(self, user_id, new_password, user=None, ip=""):
        with self.lock:
            users = self.list()
            for item in users:
                if item.get("id") == user_id:
                    problems = security.password_policy_problems(new_password, item.get("username"))
                    if problems:
                        return None, problems
                    item["password"] = security.hash_password(new_password)
                    self.save_all(users, user, ip)
                    return item, []
            return None, ["usuario no encontrado"]

    # -- sincronización con el agente -------------------------------------

    def sync_auth(self):
        """Genera auth.json (tokens que valida el agente) desde los usuarios."""
        tokens = {}
        for user in self.list():
            if not user.get("enabled", True):
                continue
            token = user.get("stream_token")
            if not token:
                continue
            tokens[token] = {
                "username": user.get("username", ""),
                "display_name": user.get("display_name", ""),
                "enabled": True,
                "channels": list(user.get("allowed_channels") or []),
                "all_channels": bool(user.get("all_channels", True)),
                "expires_at": util.to_int(user.get("expires_at"), 0),
                "max_connections": util.to_int(user.get("max_connections"), 1),
                "allow_public": user.get("role") == "admin",
            }
        doc = {"version": 1, "enabled": True, "tokens": tokens}
        self.store.save("auth", doc)
        return doc

    def token_owner(self, token):
        for user in self.list():
            if user.get("stream_token") == token:
                return user
        return None


# ---------------------------------------------------------------------------
# DVB: satélites, transponders y resultados de escaneo
# ---------------------------------------------------------------------------

class DvbDB:
    def __init__(self, store):
        self.store = store
        self.lock = store.lock

    def _doc(self):
        doc = self.store.load("dvb")
        if not isinstance(doc, dict):
            doc = {}
        if not isinstance(doc.get("satellites"), list):
            doc["satellites"] = []
        if not isinstance(doc.get("scans"), list):
            doc["scans"] = []
        doc.setdefault("version", 1)
        return doc

    def satellites(self):
        with self.lock:
            return list(self._doc()["satellites"])

    def _save(self, doc, user=None, ip="", action="dvb.save"):
        with self.lock:
            self.store.save("dvb", doc)
        if user is not None:
            self.store.audit(user, ip, action, "dvb.json")
        return doc

    def create_satellite(self, payload, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            name = util.sanitize_text((payload or {}).get("name"), 64) or "Satélite"
            satellite = {
                "id": util.new_id("sat_"),
                "name": name,
                "position": util.sanitize_text((payload or {}).get("position"), 16),
                "band": util.sanitize_text((payload or {}).get("band"), 8) or "KU",
                "lnb": {
                    "lof1": util.to_int((payload or {}).get("lof1"), 9750, minimum=0, maximum=100000),
                    "lof2": util.to_int((payload or {}).get("lof2"), 10600, minimum=0, maximum=100000),
                    "slof": util.to_int((payload or {}).get("slof"), 11700, minimum=0, maximum=100000),
                },
                "diseqc": util.to_int((payload or {}).get("diseqc"), 0, minimum=0, maximum=4),
                "transponders": [],
                "created_at": util.now(),
            }
            doc["satellites"].append(satellite)
            self._save(doc, user, ip, "dvb.satellite.create")
            return satellite

    def update_satellite(self, sat_id, payload, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            for satellite in doc["satellites"]:
                if satellite.get("id") == sat_id:
                    for key in ("name", "position", "band"):
                        if key in (payload or {}):
                            satellite[key] = util.sanitize_text(payload.get(key), 64)
                    if isinstance((payload or {}).get("lnb"), dict):
                        for key in ("lof1", "lof2", "slof"):
                            if key in payload["lnb"]:
                                satellite["lnb"][key] = util.to_int(payload["lnb"].get(key),
                                                                   satellite["lnb"].get(key, 0),
                                                                   minimum=0, maximum=100000)
                    if "diseqc" in (payload or {}):
                        satellite["diseqc"] = util.to_int(payload.get("diseqc"), 0, minimum=0, maximum=4)
                    self._save(doc, user, ip, "dvb.satellite.update")
                    return satellite
            return None

    def delete_satellite(self, sat_id, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            before = len(doc["satellites"])
            doc["satellites"] = [s for s in doc["satellites"] if s.get("id") != sat_id]
            if len(doc["satellites"]) == before:
                return False
            self._save(doc, user, ip, "dvb.satellite.delete")
            return True

    def add_transponders(self, sat_id, transponders, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            for satellite in doc["satellites"]:
                if satellite.get("id") != sat_id:
                    continue
                existing = {validators.transponder_key(t) for t in satellite["transponders"]}
                added = []
                errors = []
                for index, raw in enumerate(transponders or []):
                    payload = dict(raw or {})
                    payload.setdefault("delivery", satellite.get("band") and "S2" or "S2")
                    payload.setdefault("adapter", 0)
                    payload.setdefault("device", 0)
                    payload.setdefault("frequency", 0)
                    lnb = satellite.get("lnb", {})
                    payload.setdefault("lof1", lnb.get("lof1", 9750))
                    payload.setdefault("lof2", lnb.get("lof2", 10600))
                    payload.setdefault("slof", lnb.get("slof", 11700))
                    payload.setdefault("diseqc", satellite.get("diseqc", 0))
                    tp, errs = validators.validate_transponder(payload, index)
                    if errs:
                        errors.append("transponder %d: %s" % (index + 1, "; ".join(errs)))
                        continue
                    key = validators.transponder_key(tp)
                    if key in existing:
                        continue
                    existing.add(key)
                    tp["id"] = util.new_id("tp_")
                    satellite["transponders"].append(tp)
                    added.append(tp)
                self._save(doc, user, ip, "dvb.transponder.add")
                return added, errors
            return [], ["satélite no encontrado"]

    def delete_transponder(self, sat_id, tp_id, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            for satellite in doc["satellites"]:
                if satellite.get("id") != sat_id:
                    continue
                before = len(satellite["transponders"])
                satellite["transponders"] = [t for t in satellite["transponders"] if t.get("id") != tp_id]
                if len(satellite["transponders"]) == before:
                    return False
                self._save(doc, user, ip, "dvb.transponder.delete")
                return True
            return False

    def transponders(self, sat_id=None):
        with self.lock:
            out = []
            for satellite in self._doc()["satellites"]:
                if sat_id and satellite.get("id") != sat_id:
                    continue
                for tp in satellite.get("transponders", []):
                    item = dict(tp)
                    item["satellite_id"] = satellite.get("id")
                    item["satellite_name"] = satellite.get("name")
                    out.append(item)
            return out

    def remember_scan(self, scan, user=None, ip=""):
        with self.lock:
            doc = self._doc()
            doc["scans"].insert(0, scan)
            del doc["scans"][20:]
            doc["last_scan"] = scan
            self._save(doc, user, ip, "dvb.scan")

    def last_scan(self):
        with self.lock:
            return self._doc().get("last_scan")

    def scans(self):
        with self.lock:
            return list(self._doc().get("scans") or [])
