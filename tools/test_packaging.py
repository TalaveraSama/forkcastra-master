#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprueba el empaquetado .deb sin necesidad de Linux, gcc ni dpkg.

Prepara el árbol con tools/deb_stage.py (usando un binario de mentira para el
núcleo), construye el .deb con tools/build_deb.py y valida su contenido:

    python3 tools/test_packaging.py

Verifica estructura ar/tar, campos de control, conffiles, permisos, enlaces
simbólicos, unidades systemd, ausencia de __pycache__ y las dos variantes
(con núcleo y sólo panel).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_deb  # noqa: E402
import deb_stage  # noqa: E402

FAILURES = []
CHECKS = [0]


def fork_source():
    """Código del fork: `repo/tree` (desarrollo) o la raíz del proyecto (entrega)."""
    for candidate in (os.path.join(ROOT, "repo", "tree"), ROOT, os.path.dirname(ROOT)):
        if os.path.isfile(os.path.join(candidate, "version.h")):
            return candidate
    return os.path.join(ROOT, "repo", "tree")


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


def run_stage(root, extra):
    cmd = [sys.executable, os.path.join(HERE, "deb_stage.py"), "--root", root] + extra
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit("deb_stage.py falló:\n%s\n%s" % (result.stdout, result.stderr))
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}


def read_deb(path):
    members = dict(build_deb.ar_read(path))
    control_files = {}
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as tar:
        for entry in tar.getmembers():
            if entry.isfile():
                handle = tar.extractfile(entry)
                control_files[entry.name.lstrip("./")] = handle.read()
    data_name = [n for n in members if n.startswith("data.tar")][0]
    data = {}
    with tarfile.open(fileobj=io.BytesIO(members[data_name]), mode="r:*") as tar:
        for entry in tar.getmembers():
            data[entry.name.lstrip("./")] = entry
    return members, control_files, data, data_name


def test_variant(tmp, with_core):
    label = "con núcleo" if with_core else "sólo panel"
    section("Variante %s" % label)

    root = os.path.join(tmp, "pkgroot-%s" % ("core" if with_core else "panel"))
    extra = ["--json"]
    if with_core:
        fake_bin = os.path.join(tmp, "astra-falso")
        with open(fake_bin, "wb") as handle:
            handle.write(b"#!/bin/sh\necho 'Astra 4.0 dev:282'\n")
        extra += ["--source", fork_source(), "--core-bin", fake_bin,
                  "--core-version", "4.0.282", "--core-short", "4.0", "--arch", "amd64"]
    else:
        extra += ["--no-core"]

    summary = run_stage(root, extra)
    check(bool(summary), "deb_stage.py devuelve el resumen (%s)" % label)

    deb_path = os.path.join(tmp, "paquete-%s.deb" % ("core" if with_core else "panel"))
    build_deb.build_deb(root, deb_path)
    check(os.path.isfile(deb_path), "el .deb se genera")
    check(build_deb.verify(deb_path) == 0, "la verificación del .deb pasa")

    members, control_files, data, data_name = read_deb(deb_path)
    fields = build_deb.control_fields(control_files["control"].decode("utf-8"))

    eq(fields.get("Package"), "astra-webui" if with_core else "astra-webui-panel", "nombre del paquete")
    eq(fields.get("Architecture"), "amd64" if with_core else "all", "arquitectura")
    if with_core:
        eq(fields.get("Version"), "4.0.282-1", "versión derivada del fork")
        check("libc6" in fields.get("Depends", ""), "depende de libc6")
    else:
        eq(fields.get("Version"), "%s-1" % deb_stage.PANEL_VERSION, "versión del panel")
    check("python3" in fields.get("Depends", ""), "depende de python3")
    check("Panel administrativo web" in fields.get("Description", ""), "descripción del paquete")
    check(int(fields.get("Installed-Size", "0")) > 0, "tamaño instalado calculado")

    # -- scripts de mantenimiento
    for name in ("postinst", "prerm", "postrm"):
        check(name in control_files, "control.tar.gz incluye %s" % name)
    for name in ("preinst",):
        check(name not in control_files, "no se incluye %s innecesario" % name)
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as tar:
        modes = {e.name.lstrip("./"): e.mode for e in tar.getmembers()}
    eq(modes.get("postinst", 0) & 0o777, 0o755, "postinst es ejecutable")
    eq(modes.get("control", 0) & 0o777, 0o644, "control no es ejecutable")

    # -- conffiles declarados y presentes
    conffiles = [line.strip() for line in control_files["conffiles"].decode().splitlines() if line.strip()]
    check(len(conffiles) >= 3, "se declaran los conffiles")
    for conffile in conffiles:
        check(conffile.lstrip("/") in data, "conffile incluido: %s" % conffile)

    # -- contenido obligatorio
    required = {
        "usr/bin/astra-webui": 0o755,
        "etc/astra-webui/panel.json": 0o640,
        "etc/astra-webui/agent.json": 0o640,
        "etc/default/astra-webui": 0o644,
        "lib/systemd/system/astra.service": 0o644,
        "lib/systemd/system/astra-webui.service": 0o644,
        "etc/logrotate.d/astra-webui": 0o644,
        "etc/sudoers.d/astra-webui": 0o440,
        "usr/share/polkit-1/rules.d/50-astra-webui.rules": 0o644,
        "usr/share/astra-webui/agent/agent.lua": 0o644,
        "usr/share/astra-webui/panel/astra_webui/__main__.py": 0o644,
        "usr/share/astra-webui/panel/astra_webui/api_channels.py": 0o644,
        "usr/share/astra-webui/web/index.html": 0o644,
        "usr/share/doc/astra-webui/copyright": 0o644,
        "var/lib/astra-webui/recordings": 0o750,
        "var/log/astra": 0o750,
        "var/log/astra-webui": 0o750,
        "etc/astra-webui": 0o750,
    }
    for path, mode in required.items():
        entry = data.get(path)
        check(entry is not None, "incluye %s" % path)
        if entry is not None:
            eq(entry.mode & 0o777, mode, "permisos de %s" % path)
    for path in ("var/lib/astra-webui/recordings", "var/log/astra", "etc/astra-webui"):
        entry = data.get(path)
        check(entry is not None and entry.isdir(), "%s es un directorio" % path)

    # -- agente completo y sin residuos
    agent_files = [p for p in data if p.startswith("usr/share/astra-webui/agent/")]
    check(len(agent_files) >= 8, "se incluyen todos los módulos del agente (%d)" % len(agent_files))
    panel_files = [p for p in data if p.startswith("usr/share/astra-webui/panel/")]
    check(len(panel_files) >= 12, "se incluyen todos los módulos del panel (%d)" % len(panel_files))
    web_files = [p for p in data if p.startswith("usr/share/astra-webui/web/")]
    check(len(web_files) >= 5, "se incluye la interfaz web (%d ficheros)" % len(web_files))
    check(not [p for p in data if "__pycache__" in p or p.endswith(".pyc")],
          "sin __pycache__ ni .pyc")
    check(not [p for p in data if p.split("/")[0] == "DEBIAN"], "DEBIAN no está en data.tar")
    check(".register.json" not in data, "el registro interno no se empaqueta")
    check(not data_name.endswith(".tar"), "data.tar usa compresión (%s)" % data_name)
    for name in ("README.md", "INSTALL.md", "INSTALL-WSL.md", "ARCHITECTURE.md", "API.md",
                 "TROUBLESHOOTING.md"):
        check("usr/share/doc/astra-webui/%s" % name in data, "documentación: %s" % name)
    # -- núcleo y scripts
    if with_core:
        entry = data.get("usr/bin/astra-4.0")
        check(entry is not None, "incluye el binario del núcleo")
        if entry:
            eq(entry.mode & 0o777, 0o755, "el binario es ejecutable")
            check(entry.size > 0, "el binario tiene contenido")
        link = data.get("usr/bin/astra")
        check(link is not None and link.issym(), "/usr/bin/astra es un enlace simbólico")
        if link is not None:
            eq(link.linkname, "astra-4.0", "el enlace apunta al binario versionado")
        scripts = [p for p in data if p.startswith("etc/astra/scripts-4.0/")]
        check(len(scripts) >= 4, "se incluyen los scripts del core (%d)" % len(scripts))
        check("etc/astra/scripts-4.0/stream.lua" in data, "incluye stream.lua")
        check("usr/share/doc/astra-webui/COPYING" in data, "incluye la licencia GPL")
    else:
        check("usr/bin/astra-4.0" not in data, "la variante sin núcleo no lleva el binario")
        check("usr/bin/astra" not in data, "la variante sin núcleo no lleva el enlace")

    return deb_path


def test_deb_reader(tmp):
    section("Lector/verificador de .deb")
    root = os.path.join(tmp, "pkgroot-broken")
    os.makedirs(os.path.join(root, "DEBIAN"), exist_ok=True)
    with open(os.path.join(root, "DEBIAN", "control"), "w", encoding="utf-8") as handle:
        handle.write("Package: roto\nVersion: 1\nArchitecture: all\nMaintainer: x <x@y>\n"
                     "Description: paquete incompleto\n sin conffiles válidos\n")
    with open(os.path.join(root, "DEBIAN", "conffiles"), "w", encoding="utf-8") as handle:
        handle.write("/etc/no-esta-en-el-paquete.conf\n")
        handle.write("/etc/inexistente.conf\n")
    payload = os.path.join(root, "etc")
    os.makedirs(payload, exist_ok=True)
    with open(os.path.join(payload, "inexistente.conf"), "w", encoding="utf-8") as handle:
        handle.write("# existe\n")
    broken = os.path.join(tmp, "roto.deb")
    build_deb.build_deb(root, broken)
    check(build_deb.verify(broken) == 1, "el verificador detecta un paquete incompleto")

    with open(os.path.join(tmp, "no-es-deb.txt"), "wb") as handle:
        handle.write(b"hola")
    try:
        build_deb.ar_read(os.path.join(tmp, "no-es-deb.txt"))
        check(False, "ar_read rechaza ficheros que no son ar")
    except ValueError:
        check(True, "ar_read rechaza ficheros que no son ar")


def main():
    print("Pruebas del empaquetado .deb")
    tmp = tempfile.mkdtemp(prefix="astra-webui-deb-")
    try:
        if not os.path.isfile(os.path.join(ROOT, "panel", "web", "index.html")):
            print("\033[31mERROR\033[0m: falta panel/web/index.html (la WebUI). "
                  "El empaquetado no se puede probar todavía.")
            return 2
        test_variant(tmp, with_core=True)
        test_variant(tmp, with_core=False)
        test_deb_reader(tmp)
    finally:
        if not os.environ.get("ASTRA_WEBUI_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("datos en %s" % tmp)

    print("\n%s" % ("-" * 62))
    if FAILURES:
        print("\033[31m%d/%d comprobaciones fallidas\033[0m" % (len(FAILURES), CHECKS[0]))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("\033[32mTodas las comprobaciones pasaron (%d)\033[0m" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
