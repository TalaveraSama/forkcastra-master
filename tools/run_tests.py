#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ejecuta todas las pruebas del proyecto y resume el resultado.

    python3 tools/run_tests.py            # todas
    python3 tools/run_tests.py --quick    # sin las pruebas del navegador

Las suites que necesitan dependencias opcionales se omiten con un aviso:

    lua_selftest            -> requiere `pip install lupa`
    _webui_browser_check    -> requiere Chrome o Edge instalado
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SUITES = [
    ("check_webui.py", "WebUI: comprobaciones estáticas (rutas, i18n, CSP, sintaxis)", ()),
    ("lua_selftest.py", "Agente Lua (stream.lua real + stubs del core)", ()),
    ("test_panel.py", "Panel: API REST end-to-end contra el agente simulado", ()),
    ("test_packaging.py", "Empaquetado .deb (estructura, permisos, conffiles)", ()),
]

OPTIONAL = [
    # Necesita el panel y el agente simulado en marcha y la contraseña de admin:
    #   ASTRA_WEBUI_TEST_PASSWORD=... python3 tools/run_tests.py
    ("webui_browser_check.py", "WebUI en Chrome/Edge headless (CDP)",
     ("ASTRA_WEBUI_TEST_PASSWORD",)),
]


def has_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def run(script, label, extra_args=()):
    path = os.path.join(HERE, script)
    if not os.path.isfile(path):
        return "omitida", "no existe %s" % script
    started = time.time()
    print("\n\033[1m▶ %s\033[0m (%s)" % (label, script))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    result = subprocess.run([sys.executable, path] + list(extra_args), env=env, check=False)
    elapsed = time.time() - started
    if result.returncode == 0:
        return "ok", "%.1fs" % elapsed
    return "fallo", "código %d en %.1fs" % (result.returncode, elapsed)


def main():
    parser = argparse.ArgumentParser(description="Pruebas de Astra WebUI")
    parser.add_argument("--quick", action="store_true", help="sin las pruebas del navegador")
    args = parser.parse_args()

    results = []
    for script, label, _deps in SUITES:
        results.append((label, script) + run(script, label))

    if not args.quick:
        chrome = any(os.path.exists(p) for p in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ))
        for script, label, env_vars in OPTIONAL:
            if not chrome:
                results.append((label, script, "omitida", "no hay Chrome/Edge instalado"))
                continue
            pending = [name for name in env_vars if not os.environ.get(name)]
            if pending:
                results.append((label, script, "omitida",
                                "define %s y arranca el panel + agente simulado" % ", ".join(pending)))
                continue
            extra = ["--password", os.environ["ASTRA_WEBUI_TEST_PASSWORD"]]
            results.append((label, script) + run(script, label, extra))

    print("\n%s" % ("=" * 70))
    failures = 0
    for label, script, status, detail in results:
        marker = {"ok": "\033[32mOK   \033[0m", "fallo": "\033[31mFALLO\033[0m",
                  "omitida": "\033[33mOMIT \033[0m"}[status]
        if status == "fallo":
            failures += 1
        print("%s %-52s %s" % (marker, label, detail))
    print("=" * 70)
    if failures:
        print("\033[31m%d suite(s) con fallos\033[0m" % failures)
        return 1
    print("\033[32mTodo correcto\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
