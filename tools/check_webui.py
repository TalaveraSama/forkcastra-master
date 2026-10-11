#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprobaciones estáticas de la WebUI (sin navegador).

    python3 tools/check_webui.py

Verifica:
  1. que `index.html` no lleva scripts en línea ni recursos externos (la CSP del
     panel es `script-src 'self'`) y que todos sus `src`/`href` existen;
  2. que todas las claves `t('…')`/`data-i18n` usadas están definidas en `i18n.js`;
  3. que las vistas declaradas en `app.js` existen;
  4. que todas las rutas `/api/...` que llama el JavaScript existen en el backend
     (`@router.add` de panel/astra_webui/api_*.py);
  5. la sintaxis de los módulos ES (si está instalado `esprima`: `pip install esprima`;
     los operadores `?.` y `??` de ES2020 se aceptan como válidos).

Complementa a `tools/webui_browser_check.py`, que sí abre Chrome/Edge headless.
"""

from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "panel", "web")
JS = os.path.join(WEB, "js")
BACKEND = os.path.join(ROOT, "panel", "astra_webui")

problems = []


def fail(message):
    problems.append(message)
    print("  \033[31mFALLO\033[0m %s" % message)


def ok(message):
    print("  \033[32mOK\033[0m   %s" % message)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# 1. index.html
# ---------------------------------------------------------------------------

def check_index():
    print("\n\033[1m1. index.html\033[0m")
    path = os.path.join(WEB, "index.html")
    if not os.path.isfile(path):
        fail("no existe panel/web/index.html")
        return
    html = read(path)
    ok("existe index.html (%d líneas)" % (html.count("\n") + 1))

    inline = re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    if inline:
        fail("hay un <script> en línea (la CSP no lo permite)")
    else:
        ok("sin scripts en línea")

    external = re.findall(r"<(?:script|link)[^>]+(?:src|href)=\"((?:https?:)?//[^\"]+)\"", html)
    if external:
        fail("recursos externos (sin CDN): %s" % ", ".join(external))
    else:
        ok("sin recursos externos")

    missing = []
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        if ref.startswith(("http", "//", "data:", "#")):
            continue
        if not os.path.isfile(os.path.join(WEB, ref)):
            missing.append(ref)
    if missing:
        fail("referencias rotas en index.html: %s" % ", ".join(missing))
    else:
        ok("todos los recursos referenciados existen")


# ---------------------------------------------------------------------------
# 2. i18n
# ---------------------------------------------------------------------------

def check_i18n():
    print("\n\033[1m2. Traducciones (es/en)\033[0m")
    i18n_path = os.path.join(JS, "i18n.js")
    if not os.path.isfile(i18n_path):
        fail("no existe js/i18n.js")
        return
    i18n = read(i18n_path)
    defined = set(re.findall(r"['\"]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)['\"]\s*:", i18n))

    used = set()
    sources = [os.path.join(JS, "app.js"), os.path.join(JS, "ui.js"), os.path.join(WEB, "index.html")]
    sources += sorted(glob.glob(os.path.join(JS, "views", "*.js")))
    for src in sources:
        text = read(src)
        for match in re.finditer(r"\bt\(\s*['\"]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)['\"]", text):
            used.add(match.group(1))
        for match in re.finditer(r"data-i18n(?:-attr)?=\"([^\"]+)\"", text):
            for key in re.split(r"[,:;]", match.group(1)):
                key = key.strip()
                if key and "." in key:
                    used.add(key)

    missing = sorted(key for key in used if key not in defined)
    if missing:
        fail("claves sin traducir: %s" % ", ".join(missing))
    else:
        ok("%d claves usadas, todas definidas (de %d)" % (len(used), len(defined)))

    for lang in ("es", "en"):
        if "  %s:" % lang not in i18n and "%s:" % lang not in i18n:
            fail("no encuentro el idioma '%s' en i18n.js" % lang)
    ok("idiomas es/en presentes")


# ---------------------------------------------------------------------------
# 3. vistas
# ---------------------------------------------------------------------------

def check_views():
    print("\n\033[1m3. Vistas declaradas\033[0m")
    app_js = read(os.path.join(JS, "app.js"))
    modules = re.findall(r"import\('(\./(?:views|components)/[^']+)'\)", app_js)
    if not modules:
        fail("app.js no declara vistas con import() dinámico")
        return
    missing = [m for m in modules if not os.path.isfile(os.path.join(JS, m[2:]))]
    if missing:
        fail("vistas que no existen: %s" % ", ".join(missing))
    else:
        ok("%d vistas declaradas y presentes" % len(modules))

    for required in ("dashboard", "channels", "users", "dvb", "logs", "audit", "settings"):
        if not any(required in module for module in modules):
            fail("falta la vista '%s' en el enrutador" % required)
    ok("las 7 vistas esperadas están enrutadas")


# ---------------------------------------------------------------------------
# 4. rutas de la API
# ---------------------------------------------------------------------------

def _backend_routes():
    """(método, [segmentos]) de cada @router.add del backend.

    Los patrones son literales, constantes (CHANNEL_ID, …) o expresiones regulares
    con nombre; todo lo dinámico se representa como el comodín `{W}` para poder
    comparar segmento a segmento sin confundirse con las barras de `[^/]+`.
    """
    routes = []
    for name in sorted(glob.glob(os.path.join(BACKEND, "api_*.py"))):
        for line in read(name).splitlines():
            match = re.search(r'@router\.add\(\s*"(\w+)"\s*,\s*(.+?)\)\s*$', line)
            if not match:
                continue
            method, expr = match.groups()
            # quita los kwargs (min_role=, public=, csrf=)
            expr = re.split(r",\s*(?:public|min_role|csrf)\s*=", expr)[0]
            # literales r"..." / "..." → su contenido (así no se toca la 'r' de /reorder)
            pattern = re.sub(r'r?"([^"]*)"', lambda m: m.group(1), expr)
            pattern = re.sub(r"r?'([^']*)'", lambda m: m.group(1), pattern)
            pattern = pattern.replace("+", " ").strip()
            pattern = re.sub(r"\(\?P<\w+>[^)]*\)", "{W}", pattern)
            for key in ("CHANNEL_ID", "USER_ID", "TOKEN", "SAT_ID", "TP_ID", "SCAN_ID"):
                pattern = pattern.replace(key, "{W}")
            pattern = pattern.replace("\\.", ".").replace("?", "")
            segments = [seg.strip() for seg in pattern.split("/") if seg.strip()]
            routes.append((method.upper(), segments))
    return routes


def _literal_candidates(segment):
    """Literales posibles de un segmento del backend ('{W}' → None = cualquiera)."""
    if "{W}" in segment or ".*" in segment:
        return None
    match = re.fullmatch(r"\(([^()]+)\)", segment)
    if match:
        return [part for part in match.group(1).split("|") if part]
    return [segment]


def _front_fragments(segment):
    """Trozos literales de un segmento de la URL que llama la WebUI."""
    return [frag for frag in re.split(r"\$\{[^}]*\}", segment) if frag]


def _strip_query(path):
    """Corta la query string respetando las interpolaciones `${…}` (pueden llevar '?')."""
    depth = 0
    for index, char in enumerate(path):
        if path.startswith("${", index):
            depth = 1
            continue
        if depth and char == "}":
            depth = 0
            continue
        if not depth and char == "?":
            return path[:index]
    return path


def _segments_compatible(front_segment, back_segment):
    candidates = _literal_candidates(back_segment)
    if candidates is None:
        return True  # el backend acepta cualquier valor
    fragments = _front_fragments(front_segment)
    if not fragments:
        return True  # la WebUI interpola el valor completo
    for fragment in fragments:
        if not any(fragment in candidate for candidate in candidates):
            return False
    return True


def check_routes():
    print("\n\033[1m4. Rutas /api usadas por la WebUI\033[0m")
    routes = _backend_routes()

    called = set()
    for src in [os.path.join(JS, "api.js"), os.path.join(JS, "app.js"), os.path.join(JS, "ui.js")] \
            + sorted(glob.glob(os.path.join(JS, "views", "*.js"))):
        text = read(src)
        for match in re.finditer(r"\.(get|post|put|delete)\(\s*[`'\"]([^`'\"]+)", text):
            called.add((match.group(1).upper(), match.group(2)))
        for match in re.finditer(r"\bsse\(\s*[`'\"]([^`'\"]+)", text):
            called.add(("GET", match.group(1)))
        for match in re.finditer(r"fetch\(\s*[`'\"]([^`'\"]+)", text):
            called.add(("GET", match.group(1)))

    unknown = []
    checked = 0
    for method, path in sorted(called):
        if not path.startswith("/api"):
            continue
        checked += 1
        segments = [seg for seg in _strip_query(path).split("/") if seg != ""]
        found = False
        for route_method, route_segments in routes:
            if route_method != method or len(route_segments) != len(segments):
                continue
            if all(_segments_compatible(front, back) for front, back in zip(segments, route_segments)):
                found = True
                break
        if not found:
            unknown.append("%s %s" % (method, path))

    if unknown:
        fail("rutas llamadas y no definidas en el backend: %s" % "; ".join(unknown))
    else:
        ok("%d llamadas a la API, todas con ruta equivalente en el backend (%d rutas)"
           % (checked, len(routes)))


# ---------------------------------------------------------------------------
# 5. sintaxis
# ---------------------------------------------------------------------------

def check_syntax():
    print("\n\033[1m5. Sintaxis de los módulos JS\033[0m")
    files = [os.path.join(JS, "app.js"), os.path.join(JS, "api.js"), os.path.join(JS, "ui.js"),
             os.path.join(JS, "i18n.js"), os.path.join(JS, "format.js")]
    files += sorted(glob.glob(os.path.join(JS, "views", "*.js")))
    try:
        import esprima  # noqa: PLC0415
    except ImportError:
        print("  \033[33mOMIT\033[0m esprima no está instalado (pip install esprima)")
        return
    bad = 0
    for path in files:
        source = read(path)
        try:
            esprima.parseModule(source)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "Unexpected token ?" in message or "Unexpected token ." in message:
                continue  # ?. y ?? son ES2020
            bad += 1
            fail("%s: %s" % (os.path.relpath(path, ROOT), message.splitlines()[0]))
    if not bad:
        ok("%d módulos ES correctos" % len(files))


def main():
    print("\033[1mComprobaciones estáticas de la WebUI\033[0m (%s)" % WEB)
    if not os.path.isdir(JS):
        print("ERROR: no existe %s" % JS)
        return 2
    check_index()
    check_i18n()
    check_views()
    check_routes()
    check_syntax()
    print("\n%s" % ("-" * 62))
    if problems:
        print("\033[31m%d problema(s)\033[0m" % len(problems))
        return 1
    print("\033[32mSin problemas\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
