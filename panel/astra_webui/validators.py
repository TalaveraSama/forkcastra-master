# -*- coding: utf-8 -*-
"""Validación y normalización del modelo de datos.

Las reglas replican las que aplica el agente Lua (agent/aw_channels.lua) para
que un canal guardado desde el panel nunca provoque un ``astra_abort()`` en el
core (que terminaría el proceso de streaming).
"""

from __future__ import annotations

import ipaddress
import os
import re

from . import config, util

INPUT_TYPES = ("http", "udp", "file", "dvb")
OUTPUT_TYPES = ("http", "udp", "file")
DELIVERY_SYSTEMS = ("S", "S2", "T", "T2", "C")
POLARIZATIONS = ("H", "V", "R", "L")
FEC_VALUES = ("AUTO", "NONE", "1/2", "2/3", "3/4", "4/5", "5/6", "6/7", "7/8", "8/9", "3/5", "9/10")
MODULATIONS = ("NONE", "AUTO", "QPSK", "QAM16", "QAM32", "QAM64", "QAM128", "QAM256",
               "VSB8", "VSB16", "PSK8", "APSK16", "APSK32", "DQPSK")
BANDWIDTHS = ("AUTO", "8MHZ", "7MHZ", "6MHZ")
GUARD_INTERVALS = ("AUTO", "1/32", "1/16", "1/8", "1/4")
TRANSMIT_MODES = ("AUTO", "2K", "8K", "4K")
HIERARCHIES = ("AUTO", "NONE", "1", "2", "4")
ROLLOFFS = ("AUTO", "35", "20", "25")
ROLES = ("admin", "operator", "viewer")

_URI_RE = re.compile(r"^/[A-Za-z0-9._~\-/]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _err(errors, message):
    errors.append(message)


def _clean_input_common(raw, errors, prefix):
    options = raw.get("options")
    if not isinstance(options, dict):
        options = {}
    clean_options = {}
    pnr = util.to_int(options.get("pnr"), 0, minimum=0, maximum=65535)
    if pnr:
        clean_options["pnr"] = pnr
    set_pnr = util.to_int(options.get("set_pnr"), 0, minimum=0, maximum=65535)
    if set_pnr:
        clean_options["set_pnr"] = set_pnr
    if util.to_bool(options.get("no_analyze")):
        clean_options["no_analyze"] = True
    biss = util.sanitize_text(options.get("biss"), 40)
    if biss:
        if not re.match(r"^[0-9A-Fa-f]{16}$", biss):
            _err(errors, "%s: la clave BISS debe tener 16 dígitos hexadecimales" % prefix)
        else:
            clean_options["biss"] = biss.upper()
    filt = util.sanitize_text(options.get("filter"), 128)
    if filt:
        clean_options["filter"] = filt
    if util.to_bool(options.get("filter_reverse")):
        clean_options["filter_reverse"] = True
    ecm_pid = util.sanitize_text(options.get("ecm_pid"), 32)
    if ecm_pid:
        if not re.match(r"^[0-9]{1,5}$", ecm_pid):
            _err(errors, "%s: 'ecm_pid' debe ser un PID numérico (32-8190)" % prefix)
        else:
            clean_options["ecm_pid"] = ecm_pid
    ecm_swap = util.to_int(options.get("ecm_swap_time"), 0, minimum=0, maximum=60000)
    if ecm_swap:
        clean_options["ecm_swap_time"] = ecm_swap
    # Servidor de tarjeta (CAM) por newcamd: OSCam con un lector PC/SC, por ejemplo.
    cam = options.get("cam")
    if isinstance(cam, dict) and cam:
        clean_cam = {}
        host = util.sanitize_text(cam.get("host"), 64)
        port = util.to_int(cam.get("port"), 0, minimum=1, maximum=65535)
        cam_user = util.sanitize_text(cam.get("user"), 64)
        if not host or not port or not cam_user:
            _err(errors, "%s: el CAM necesita 'host', 'port' y 'user'" % prefix)
        else:
            clean_cam["host"] = host
            clean_cam["port"] = port
            clean_cam["user"] = cam_user
            cam_pass = util.sanitize_text(cam.get("pass"), 64)
            if cam_pass:
                clean_cam["pass"] = cam_pass
            cam_key = util.sanitize_text(cam.get("key"), 64)
            if cam_key:
                if not re.match(r"^[0-9A-Fa-f]{28,32}$", cam_key):
                    _err(errors, "%s: la clave DES del CAM debe tener 14 o 16 bytes en "
                                 "hexadecimal (28 o 32 caracteres)" % prefix)
                else:
                    clean_cam["key"] = cam_key.upper()
            if util.to_bool(cam.get("disable_emm")):
                clean_cam["disable_emm"] = 1
            # "Make new connection for each input": cada entrada abre su propia conexión
            # con el servidor CAM en vez de compartir una instancia.
            if util.to_bool(cam.get("each_input")):
                clean_cam["each_input"] = 1
            clean_options["cam"] = clean_cam
    cas_data = util.sanitize_text(options.get("cas_data"), 64)
    if cas_data:
        if not re.match(r"^[0-9A-Fa-f]+$", cas_data):
            _err(errors, "%s: 'cas_data' debe ser hexadecimal" % prefix)
        else:
            clean_options["cas_data"] = cas_data.upper()
    return clean_options


def validate_input(raw, index, errors):
    prefix = "entrada %d" % (index + 1)
    if not isinstance(raw, dict):
        _err(errors, "%s: formato inválido" % prefix)
        return None

    itype = util.sanitize_text(raw.get("type"), 16).lower()
    if itype not in INPUT_TYPES:
        _err(errors, "%s: tipo '%s' no soportado (usa %s)" % (prefix, itype or "?", ", ".join(INPUT_TYPES)))
        return None

    item = {"type": itype}

    if itype == "http":
        url = util.sanitize_text(raw.get("url"), 1024)
        if not url:
            _err(errors, "%s: falta la url" % prefix)
            return None
        match = re.match(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://(?P<rest>.+)$", url)
        if not match:
            _err(errors, "%s: la url debe empezar por http://" % prefix)
            return None
        scheme = match.group("scheme").lower()
        if scheme != "http":
            _err(errors, "%s: el core sólo soporta http sin TLS (recibido %s://)" % (prefix, scheme))
            return None
        rest = match.group("rest")
        userinfo = None
        at = rest.find("@")
        slash = rest.find("/")
        if at != -1 and (slash == -1 or at < slash):
            userinfo = rest[:at]
            rest = rest[at + 1:]
        hostport, _, path = rest.partition("/")
        host, _, port_s = hostport.partition(":")
        if not _HOST_RE.match(host or ""):
            _err(errors, "%s: host inválido" % prefix)
            return None
        port = util.to_int(port_s, 80, minimum=1, maximum=65535)
        item["url"] = "http://" + (("%s@" % userinfo) if userinfo else "") + host + ":" + str(port) + "/" + path
        item["options"] = _clean_input_common(raw, errors, prefix)

    elif itype == "udp":
        addr = util.sanitize_text(raw.get("addr"), 64)
        if not _valid_ipv4(addr):
            _err(errors, "%s: 'addr' debe ser una IPv4 (p. ej. 239.0.0.1)" % prefix)
        port = util.to_int(raw.get("port"), 1234, minimum=1, maximum=65535)
        localaddr = util.sanitize_text(raw.get("localaddr"), 64)
        if localaddr and not _valid_ipv4(localaddr):
            _err(errors, "%s: 'localaddr' debe ser una IPv4" % prefix)
        item.update({
            "addr": addr,
            "port": port,
            "localaddr": localaddr,
            "rtp": util.to_bool(raw.get("rtp")),
            "socket_size": util.to_int(raw.get("socket_size"), 0, minimum=0, maximum=262144),
            "renew": util.to_int(raw.get("renew"), 0, minimum=0, maximum=86400),
            "options": _clean_input_common(raw, errors, prefix),
        })

    elif itype == "file":
        filename = util.sanitize_text(raw.get("filename"), 512)
        if not os.path.isabs(filename):
            _err(errors, "%s: 'filename' debe ser una ruta absoluta" % prefix)
        elif ".." in filename:
            _err(errors, "%s: la ruta no puede contener '..'" % prefix)
        else:
            item["filename"] = filename
            if not os.path.isfile(filename):
                _err(errors, "%s: el fichero no existe (%s)" % (prefix, filename))
        item["loop"] = util.to_bool(raw.get("loop"))
        item["options"] = _clean_input_common(raw, errors, prefix)

    elif itype == "dvb":
        delivery = util.sanitize_text(raw.get("delivery") or raw.get("fe_type") or "", 8).upper()
        delivery = delivery.replace("DVB-", "")
        if delivery not in DELIVERY_SYSTEMS:
            _err(errors, "%s: 'delivery' debe ser uno de %s" % (prefix, ", ".join(DELIVERY_SYSTEMS)))
            delivery = "S2"
        adapter = util.to_int(raw.get("adapter"), 0, minimum=0, maximum=63)
        device = util.to_int(raw.get("device"), 0, minimum=0, maximum=63)
        frequency = util.to_int(raw.get("frequency"), 0, minimum=0, maximum=100000000)
        if frequency <= 0:
            _err(errors, "%s: 'frequency' debe ser mayor que 0 (MHz para S/T, kHz para C)" % prefix)
        item.update({
            "delivery": delivery,
            "adapter": adapter,
            "device": device,
            "frequency": frequency,
            "polarization": util.sanitize_text(raw.get("polarization"), 2).upper()[:1] or "H",
            "symbolrate": util.to_int(raw.get("symbolrate"), 0, minimum=0, maximum=100000),
            "lof1": util.to_int(raw.get("lof1"), 9750, minimum=0, maximum=100000),
            "lof2": util.to_int(raw.get("lof2"), 10600, minimum=0, maximum=100000),
            "slof": util.to_int(raw.get("slof"), 11700, minimum=0, maximum=100000),
            "diseqc": util.to_int(raw.get("diseqc"), 0, minimum=0, maximum=4),
            "tone": util.to_int(raw.get("tone"), 0, minimum=0, maximum=2),
            "lnb_sharing": util.to_int(raw.get("lnb_sharing"), 0, minimum=0, maximum=1),
            "budget": util.to_int(raw.get("budget"), 0, minimum=0, maximum=1),
            "buffer_size": util.to_int(raw.get("buffer_size"), 0, minimum=0, maximum=262144),
            "stream_id": util.to_int(raw.get("stream_id"), 0, minimum=0, maximum=65535),
            "fec": util.sanitize_text(raw.get("fec") or "AUTO", 8).upper(),
            "modulation": util.sanitize_text(raw.get("modulation") or "", 16).upper(),
            "bandwidth": util.sanitize_text(raw.get("bandwidth") or "AUTO", 8).upper(),
            "guardinterval": util.sanitize_text(raw.get("guardinterval") or "AUTO", 8).upper(),
            "transmitmode": util.sanitize_text(raw.get("transmitmode") or "AUTO", 8).upper(),
            "hierarchy": util.sanitize_text(raw.get("hierarchy") or "AUTO", 8).upper(),
            "rolloff": util.sanitize_text(raw.get("rolloff") or "AUTO", 8).upper(),
            "cam": util.to_bool(raw.get("cam")),
            "options": _clean_input_common(raw, errors, prefix),
        })
        if item["polarization"] not in POLARIZATIONS:
            _err(errors, "%s: 'polarization' debe ser H, V, R o L" % prefix)
        if delivery in ("S", "S2"):
            if item["symbolrate"] <= 0:
                _err(errors, "%s: DVB-S/S2 necesita 'symbolrate' (kS/s)" % prefix)
            if item["lof1"] <= 0:
                _err(errors, "%s: DVB-S/S2 necesita 'lof1' (LNB)" % prefix)
        if delivery == "C" and item["symbolrate"] <= 0:
            _err(errors, "%s: DVB-C necesita 'symbolrate'" % prefix)
        if item["fec"] not in FEC_VALUES:
            _err(errors, "%s: 'fec' inválido (%s)" % (prefix, item["fec"]))
        if item["modulation"] and item["modulation"] not in MODULATIONS:
            _err(errors, "%s: 'modulation' inválida (%s)" % (prefix, item["modulation"]))
        if item["bandwidth"] not in BANDWIDTHS:
            _err(errors, "%s: 'bandwidth' inválido (%s)" % (prefix, item["bandwidth"]))
        if item["guardinterval"] not in GUARD_INTERVALS:
            _err(errors, "%s: 'guardinterval' inválido (%s)" % (prefix, item["guardinterval"]))
        if item["transmitmode"] not in TRANSMIT_MODES:
            _err(errors, "%s: 'transmitmode' inválido (%s)" % (prefix, item["transmitmode"]))
        if item["hierarchy"] not in HIERARCHIES:
            _err(errors, "%s: 'hierarchy' inválido (%s)" % (prefix, item["hierarchy"]))
        if item["rolloff"] not in ROLLOFFS:
            _err(errors, "%s: 'rolloff' inválido (%s)" % (prefix, item["rolloff"]))

    return item


def validate_output(raw, index, errors, warnings, allowed_roots=None):
    prefix = "salida %d" % (index + 1)
    if not isinstance(raw, dict):
        _err(errors, "%s: formato inválido" % prefix)
        return None

    otype = util.sanitize_text(raw.get("type"), 16).lower()
    if otype not in OUTPUT_TYPES:
        _err(errors, "%s: tipo '%s' no soportado (usa %s)" % (prefix, otype or "?", ", ".join(OUTPUT_TYPES)))
        return None

    item = {"type": otype}

    if otype == "http":
        uri = util.sanitize_text(raw.get("uri"), 128)
        if not uri:
            uri = "/canal"
        if not uri.startswith("/"):
            uri = "/" + uri
        if not _URI_RE.match(uri):
            _err(errors, "%s: la uri sólo admite letras, números, '-', '_', '.' y '/'" % prefix)
        item.update({
            "uri": uri,
            "public": util.to_bool(raw.get("public")),
            "cors": util.to_bool(raw.get("cors"), True),
        })

    elif otype == "udp":
        addr = util.sanitize_text(raw.get("addr"), 64)
        if not _valid_ipv4(addr):
            _err(errors, "%s: 'addr' debe ser una IPv4" % prefix)
        item.update({
            "addr": addr,
            "port": util.to_int(raw.get("port"), 1234, minimum=1, maximum=65535),
            "ttl": util.to_int(raw.get("ttl"), 32, minimum=1, maximum=255),
            "localaddr": util.sanitize_text(raw.get("localaddr"), 64),
            "rtp": util.to_bool(raw.get("rtp")),
            "sync": util.to_int(raw.get("sync"), 0, minimum=0, maximum=1000),
            "socket_size": util.to_int(raw.get("socket_size"), 0, minimum=0, maximum=262144),
        })

    elif otype == "file":
        filename = util.sanitize_text(raw.get("filename"), 512)
        roots = allowed_roots or config.ALLOWED_OUTPUT_ROOTS
        if not os.path.isabs(filename):
            _err(errors, "%s: 'filename' debe ser una ruta absoluta" % prefix)
        elif ".." in filename:
            _err(errors, "%s: la ruta no puede contener '..'" % prefix)
        elif not util.is_within(filename, roots):
            _err(errors, "%s: la grabación debe estar dentro de %s" % (prefix, ", ".join(roots)))
        else:
            item["filename"] = filename
            parent = os.path.dirname(filename)
            if not os.path.isdir(parent):
                try:
                    os.makedirs(parent, mode=0o750, exist_ok=True)
                    warnings.append("%s: se creó el directorio %s" % (prefix, parent))
                except OSError as exc:
                    _err(errors, "%s: no se pudo crear el directorio %s (%s)" % (prefix, parent, exc))
        item.update({
            "m2ts": util.to_bool(raw.get("m2ts")),
            "buffer_size": util.to_int(raw.get("buffer_size"), 0, minimum=0, maximum=262144),
        })

    return item


def validate_channel(raw, existing=None, all_channels=(), allowed_roots=None):
    """Normaliza un canal. Devuelve (canal, errores, avisos)."""
    errors = []
    warnings = []
    existing = existing or {}
    if not isinstance(raw, dict):
        return None, ["formato de canal inválido"], warnings

    name = util.sanitize_text(raw.get("name"), 96)
    if not name:
        _err(errors, "el nombre del canal es obligatorio")

    channel_id = util.sanitize_text(raw.get("id"), 32) or existing.get("id") or util.new_id("ch_")
    if not re.match(r"^[A-Za-z0-9_\-]{3,32}$", channel_id):
        _err(errors, "el identificador sólo admite letras, números, '-' y '_'")
        channel_id = existing.get("id") or util.new_id("ch_")

    for other in all_channels:
        if existing and other.get("id") == existing.get("id"):
            continue
        if other.get("id") == channel_id:
            _err(errors, "ya existe un canal con el identificador '%s'" % channel_id)
        if other.get("name") == name:
            warnings.append("ya existe otro canal llamado '%s'" % name)

    inputs_raw = raw.get("inputs")
    if not isinstance(inputs_raw, list) or not inputs_raw:
        _err(errors, "el canal necesita al menos una entrada")
        inputs_raw = []

    inputs = []
    for index, item in enumerate(inputs_raw):
        clean = validate_input(item, index, errors)
        if clean:
            inputs.append(clean)

    outputs_raw = raw.get("outputs")
    if outputs_raw is None:
        outputs_raw = []
    if not isinstance(outputs_raw, list):
        _err(errors, "las salidas deben ser una lista")
        outputs_raw = []

    outputs = []
    seen_uris = set()
    for index, item in enumerate(outputs_raw):
        clean = validate_output(item, index, errors, warnings, allowed_roots)
        if clean:
            if clean["type"] == "http":
                if clean["uri"] in seen_uris:
                    _err(errors, "salida %d: la uri '%s' está repetida" % (index + 1, clean["uri"]))
                seen_uris.add(clean["uri"])
            outputs.append(clean)

    if not outputs:
        warnings.append("el canal no tiene salidas: no se podrá ver desde el panel ni por UDP/fichero")

    tags = []
    raw_tags = raw.get("tags")
    if isinstance(raw_tags, list):
        for tag in raw_tags[:12]:
            tag = util.sanitize_text(tag, 24)
            if tag and tag not in tags:
                tags.append(tag)
    elif isinstance(raw_tags, str):
        for tag in raw_tags.split(",")[:12]:
            tag = util.sanitize_text(tag, 24)
            if tag and tag not in tags:
                tags.append(tag)

    channel = {
        "id": channel_id,
        "name": name,
        "group": util.sanitize_text(raw.get("group"), 48) or "General",
        "logo": util.sanitize_text(raw.get("logo"), 512),
        "epg_id": util.sanitize_text(raw.get("epg_id"), 96),
        "enabled": util.to_bool(raw.get("enabled"), True),
        "tags": tags,
        "notes": util.sanitize_text(raw.get("notes"), 512, keep_newlines=True),
        "inputs": inputs,
        "outputs": outputs,
        "created_at": util.to_int(existing.get("created_at") or raw.get("created_at"), util.now()),
        "updated_at": util.now(),
    }

    analyze = raw.get("analyze")
    if isinstance(analyze, dict):
        channel["analyze"] = {"enabled": util.to_bool(analyze.get("enabled"), True)}

    return channel, errors, warnings


def _valid_ipv4(addr):
    if not isinstance(addr, str) or not addr:
        return False
    try:
        ipaddress.IPv4Address(addr)
        return True
    except ipaddress.AddressValueError:
        return False


def validate_transponder(raw, index=0):
    errors = []
    warnings = []
    channel, errs, warns = validate_channel({
        "name": "tp%d" % (index + 1),
        "inputs": [dict(raw or {}, type="dvb")],
        "outputs": [],
        "enabled": True,
    }, allowed_roots=[])
    errors.extend(errs)
    warnings.extend(warns)
    if not channel:
        return None, errors
    return channel["inputs"][0], errors


def validate_user(raw, existing=None, all_users=()):
    errors = []
    existing = existing or {}
    if not isinstance(raw, dict):
        return None, ["formato de usuario inválido"]

    username = util.sanitize_text(raw.get("username"), 32).lower()
    if not re.match(r"^[a-z0-9._\-]{3,32}$", username):
        errors.append("el usuario sólo admite letras minúsculas, números, '.', '-' y '_' (3-32 caracteres)")

    for other in all_users:
        if other.get("username") == username and other.get("id") != existing.get("id"):
            errors.append("ya existe un usuario con ese nombre")

    role = util.sanitize_text(raw.get("role"), 16).lower()
    if role not in ROLES:
        errors.append("rol inválido: usa %s" % ", ".join(ROLES))
        role = "viewer"

    expires_at = util.to_int(raw.get("expires_at"), 0, minimum=0)
    if util.to_bool(raw.get("expires_enabled")):
        days = util.to_int(raw.get("expires_days"), 0, minimum=0, maximum=3650)
        expires_at = util.now() + days * 86400

    user = {
        "id": existing.get("id") or util.new_id("u_"),
        "username": username,
        "display_name": util.sanitize_text(raw.get("display_name"), 64) or username,
        "role": role,
        "enabled": util.to_bool(raw.get("enabled"), True),
        "all_channels": util.to_bool(raw.get("all_channels"), True),
        "allowed_channels": [util.sanitize_text(c, 32) for c in (raw.get("allowed_channels") or [])
                             if util.sanitize_text(c, 32)][:200],
        "max_connections": util.to_int(raw.get("max_connections"), 1, minimum=0, maximum=100),
        "expires_at": expires_at,
        "created_at": util.to_int(existing.get("created_at"), util.now()),
        "last_login": util.to_int(existing.get("last_login"), 0),
        "stream_token": existing.get("stream_token") or util.random_token(32),
        "password": existing.get("password"),
        "failed_logins": util.to_int(existing.get("failed_logins"), 0),
    }

    if user["role"] == "admin" and not user["enabled"]:
        if _count_enabled_admins(all_users, existing) == 0:
            errors.append("no puedes deshabilitar el único administrador")

    return user, errors


def _count_enabled_admins(all_users, existing):
    count = 0
    for user in all_users:
        if user.get("id") == existing.get("id"):
            continue
        if user.get("role") == "admin" and user.get("enabled"):
            count += 1
    return count


def transponder_key(tp):
    return "|".join(str(tp.get(k, "")) for k in (
        "adapter", "device", "delivery", "frequency", "polarization", "symbolrate", "diseqc", "lof1"))


def sanitize_settings(payload, current):
    """Normaliza los ajustes editables desde la interfaz.

    Importante: se trabaja sobre una COPIA PROFUNDA de `current` porque los
    diccionarios anidados son compartidos; si se mutaran directamente, un error
    de validación dejaría la configuración en memoria a medio aplicar.
    """
    try:
        settings = util.loads(util.dumps(current), {})
    except (ValueError, TypeError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    if not isinstance(payload, dict):
        return settings, ["formato de ajustes inválido"]

    errors = []
    if "listen" in payload:
        listen = util.sanitize_text(payload.get("listen"), 64)
        if listen and not _valid_ipv4(listen):
            errors.append("'listen' debe ser una IPv4 (usa 0.0.0.0 para todas)")
        else:
            settings["listen"] = listen or "0.0.0.0"
    for key, minimum, maximum in (("port", 1, 65535), ("session_hours", 1, 720)):
        if key in payload:
            settings[key] = util.to_int(payload.get(key), settings.get(key), minimum=minimum, maximum=maximum)
    if "base_url" in payload:
        settings["base_url"] = util.sanitize_text(payload.get("base_url"), 256)

    stream = settings.setdefault("stream", {})
    if isinstance(payload.get("stream"), dict):
        src = payload["stream"]
        if "host" in src:
            stream["host"] = util.sanitize_text(src.get("host"), 128)
        if "port" in src:
            stream["port"] = util.to_int(src.get("port"), stream.get("port", 8001), minimum=1, maximum=65535)
        if "public_base" in src:
            stream["public_base"] = util.sanitize_text(src.get("public_base"), 256)

    ui = settings.setdefault("ui", {})
    if isinstance(payload.get("ui"), dict):
        src = payload["ui"]
        if "title" in src:
            ui["title"] = util.sanitize_text(src.get("title"), 64) or "Astra WebUI"
        if "language" in src:
            lang = util.sanitize_text(src.get("language"), 5).lower()
            ui["language"] = lang if lang in ("es", "en") else "es"
        if "refresh_ms" in src:
            ui["refresh_ms"] = util.to_int(src.get("refresh_ms"), ui.get("refresh_ms", 2000),
                                           minimum=500, maximum=60000)
        if "theme" in src:
            theme = util.sanitize_text(src.get("theme"), 16).lower()
            ui["theme"] = theme if theme in ("dark", "midnight", "light") else "dark"

    security = settings.setdefault("security", {})
    if isinstance(payload.get("security"), dict):
        src = payload["security"]
        if "max_login_attempts" in src:
            security["max_login_attempts"] = util.to_int(src.get("max_login_attempts"),
                                                         security.get("max_login_attempts", 5),
                                                         minimum=1, maximum=100)
        if "trust_proxy" in src:
            security["trust_proxy"] = util.to_bool(src.get("trust_proxy"))
        if "allowed_networks" in src:
            networks = src.get("allowed_networks")
            if isinstance(networks, str):
                networks = [n.strip() for n in networks.replace(";", ",").split(",") if n.strip()]
            clean = []
            for net in networks or []:
                net = util.sanitize_text(net, 64)
                try:
                    ipaddress.ip_network(net, strict=False)
                    clean.append(net)
                except ValueError:
                    errors.append("red no válida: %s (usa 10.0.0.0/8 o 192.168.1.5)" % net)
            security["allowed_networks"] = clean

    if isinstance(payload.get("service_control"), dict):
        src = payload["service_control"]
        service = settings.setdefault("service_control", {})
        if "enabled" in src:
            service["enabled"] = util.to_bool(src.get("enabled"))
        if "unit" in src:
            unit = util.sanitize_text(src.get("unit"), 64)
            if not re.match(r"^[A-Za-z0-9@._\-]+\.service$", unit):
                errors.append("el nombre de la unidad systemd no es válido")
            else:
                service["unit"] = unit

    if isinstance(payload.get("history"), dict):
        hist = settings.setdefault("history", {})
        src = payload["history"]
        if "points" in src:
            hist["points"] = util.to_int(src.get("points"), hist.get("points", 720), minimum=60, maximum=20000)
        if "interval" in src:
            hist["interval"] = util.to_int(src.get("interval"), hist.get("interval", 5), minimum=1, maximum=300)

    return settings, errors
