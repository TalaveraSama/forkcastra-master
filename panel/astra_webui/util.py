# -*- coding: utf-8 -*-
"""Utilidades comunes del panel Astra WebUI.

Incluye el escritor de JSON compatible con el parser del core de Astra 4.0,
que es bastante limitado:

* no entiende escapes ``\\uXXXX`` → hay que emitir UTF-8 literal y sin
  caracteres de control;
* trunca los decimales → sólo se usan enteros en los ficheros que lee el agente;
* convierte una tabla vacía en ``[]`` → no se pueden representar objetos vacíos
  en las respuestas del agente (el panel normaliza ``[]`` → ``{}`` al leer).
"""

from __future__ import annotations

import datetime
import errno
import hashlib
import json
import os
import random
import re
import secrets
import string
import tempfile
import time

# ---------------------------------------------------------------------------
# texto
# ---------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")


def sanitize_text(value, maxlen=512, keep_newlines=False):
    """Limpia una cadena para que el core pueda parsearla sin problemas."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if keep_newlines:
        value = _CTRL_RE.sub("", value)
    else:
        value = _WS_RE.sub(" ", value)
        value = _CTRL_RE.sub("", value)
    value = value.strip()
    if maxlen and len(value) > maxlen:
        value = value[:maxlen]
    return value


def to_int(value, default=0, minimum=None, maximum=None):
    """Convierte a entero (el core trunca los decimales: nunca se usan floats)."""
    if value is None or value == "":
        result = default
    elif isinstance(value, bool):
        result = 1 if value else 0
    elif isinstance(value, (int, float)):
        result = int(value)
    else:
        try:
            result = int(float(str(value).strip().replace(",", ".")))
        except (TypeError, ValueError):
            result = default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def slugify(value, maxlen=48):
    value = sanitize_text(value, maxlen=256).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:maxlen] or "canal"


def truncate(value, maxlen):
    value = value or ""
    return value[:maxlen]


# ---------------------------------------------------------------------------
# identificadores y tokens
# ---------------------------------------------------------------------------

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix="ch_"):
    return prefix + "".join(random.choice(_ALPHABET) for _ in range(6))


def random_token(length=32):
    """Token de streaming: sólo [0-9a-f] para que la URL sea simple."""
    return secrets.token_hex(length // 2)


def random_secret(length=48):
    return secrets.token_urlsafe(length)[:length]


def sha256_hex(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


# ---------------------------------------------------------------------------
# fechas
# ---------------------------------------------------------------------------

def now():
    return int(time.time())


def iso(ts=None):
    ts = now() if ts is None else int(ts)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def human_duration(seconds):
    seconds = to_int(seconds, 0, minimum=0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return "%dd %02dh %02dm" % (days, hours, minutes)
    if hours:
        return "%dh %02dm %02ds" % (hours, minutes, secs)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

class JsonError(ValueError):
    pass


def dumps(obj, indent=None):
    """JSON para consumo propio del panel (admite floats y \\uXXXX)."""
    return json.dumps(obj, ensure_ascii=False, indent=indent, separators=(",", ":") if not indent else None)


def dumps_core(obj):
    """JSON que el parser del core de Astra es capaz de leer.

    Lanza JsonError si el resultado contiene escapes \\uXXXX (el core no los
    soporta) o números en coma flotante (el core los trunca).
    """
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                      default=_json_default)
    if "\\u" in text:
        raise JsonError("el JSON contiene escapes \\uXXXX que el core de Astra no entiende; "
                        "revisa los caracteres de control de las cadenas")
    _check_no_floats(obj)
    return text


def _json_default(value):
    raise JsonError("tipo no serializable: %r" % (type(value),))


def _check_no_floats(obj, path="root"):
    if isinstance(obj, float):
        raise JsonError("número decimal en %s: el core de Astra trunca los decimales" % path)
    if isinstance(obj, dict):
        for key, value in obj.items():
            _check_no_floats(value, "%s.%s" % (path, key))
    elif isinstance(obj, (list, tuple)):
        for idx, value in enumerate(obj):
            _check_no_floats(value, "%s[%d]" % (path, idx))


def loads(text, default=None):
    if text is None:
        return default
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.strip()
    if not text:
        return default
    return json.loads(text)


def normalize_empty_objects(obj):
    """El agente devuelve [] donde quería un objeto vacío: se traduce a {}."""
    if isinstance(obj, list):
        return [normalize_empty_objects(v) for v in obj]
    if isinstance(obj, dict):
        return {k: normalize_empty_objects(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# ficheros
# ---------------------------------------------------------------------------

def ensure_dir(path, mode=0o750):
    if not path:
        return
    try:
        os.makedirs(path, mode=mode, exist_ok=True)
    except OSError as exc:  # pragma: no cover
        if exc.errno != errno.EEXIST:
            raise


def read_text(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return default


def write_atomic(path, data, mode=0o640):
    """Escritura atómica (tmp + fsync + rename) para que el agente nunca lea un
    fichero a medias."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    directory = os.path.dirname(os.path.abspath(path)) or "."
    ensure_dir(directory, mode=0o750)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(directory)


def _fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def append_line(path, line, mode=0o640):
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".", mode=0o750)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def tail_lines(path, max_lines=200, max_bytes=262144):
    """Devuelve las últimas líneas de un fichero sin cargarlo entero."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size <= 0:
        return []
    start = max(0, size - max_bytes)
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    lines = text.split("\n")
    if start > 0 and lines:
        lines = lines[1:]
    lines = [l.rstrip("\r") for l in lines if l.strip()]
    return lines[-max_lines:]


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def is_within(path, roots):
    """Comprueba que `path` está dentro de alguno de los directorios permitidos."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    for root in roots:
        root_real = os.path.realpath(root)
        if real == root_real or real.startswith(root_real.rstrip("/") + os.sep):
            return True
    return False


def parse_iso_or_int(value, default=0):
    value = to_int(value, None)
    if value is not None:
        return value
    return default
