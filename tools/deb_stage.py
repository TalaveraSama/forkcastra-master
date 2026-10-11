#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepara el árbol de un paquete .deb de Astra WebUI.

Toda la lógica de empaquetado está en Python (en vez de en el shell) para que
sea reproducible y comprobable en cualquier sistema. `packaging/build-deb.sh`
compila el núcleo y luego llama aquí; `tools/test_packaging.py` lo usa para
verificar la estructura del paquete sin necesidad de Linux ni de gcc.

El árbol generado lleva un fichero `.register.json` con el tipo y los permisos
exactos de cada entrada: en sistemas sin permisos POSIX (Windows) los ficheros
se crean con 0666 y el registro es lo que garantiza los modos correctos dentro
del .deb. Ese fichero nunca se incluye en el paquete.

Uso:
    python3 tools/deb_stage.py --root build/pkgroot --source repo/tree \
        --core-bin repo/tree/astra --core-version 4.0.282 --core-short 4.0 \
        --arch amd64
    python3 tools/deb_stage.py --root build/pkgroot --no-core
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PANEL_VERSION = "1.0.0"
PACKAGE_CORE = "astra-webui"
PACKAGE_PANEL_ONLY = "astra-webui-panel"

LAUNCHER = """#!/bin/sh
# Lanzador del panel Astra WebUI
PYTHONPATH="/usr/share/astra-webui/panel${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
exec /usr/bin/python3 -m astra_webui "$@"
"""

COPYRIGHT = """Formato: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/

Files: *
Copyright: 2012-2013 Andrey Dyldin <and@cesbo.com>
           2024-2026 TalaveraSama (fork forkcastra-master) y colaboradores
License: GPL-3+
 Este paquete incluye software derivado de Astra (http://cesbo.com/astra),
 publicado bajo la GNU General Public License versión 3 o posterior.
 Puedes redistribuirlo y/o modificarlo bajo los términos de la GPL tal y como
 publica la Free Software Foundation; versión 3 o (a tu elección) posterior.
 .
 El texto completo de la licencia está en /usr/share/common-licenses/GPL-3
 y en /usr/share/doc/astra-webui/COPYING.
"""

DOC_FILES = ("INSTALL.md", "INSTALL-WSL.md", "ARCHITECTURE.md", "API.md", "TROUBLESHOOTING.md")

# Directorios que se crean vacíos dentro del paquete
BASE_DIRS = {
    "usr/bin": 0o755,
    "usr/share/astra-webui/agent": 0o755,
    "usr/share/astra-webui/panel": 0o755,
    "usr/share/astra-webui/web": 0o755,
    "usr/share/astra-webui/panel/astra_webui": 0o755,
    "usr/share/doc/astra-webui": 0o755,
    "usr/share/polkit-1/rules.d": 0o755,
    "lib/systemd/system": 0o755,
    "etc/astra-webui": 0o750,
    "etc/default": 0o755,
    "etc/logrotate.d": 0o755,
    "etc/sudoers.d": 0o755,
    "var/lib/astra-webui": 0o750,
    "var/lib/astra-webui/recordings": 0o750,
    "var/log/astra": 0o750,
    "var/log/astra-webui": 0o750,
}

# Ficheros con permisos especiales (el resto: 0644)
SPECIAL_MODES = {
    "usr/bin/astra-webui": 0o755,
    "etc/astra-webui/panel.json": 0o640,
    "etc/astra-webui/agent.json": 0o640,
    "etc/sudoers.d/astra-webui": 0o440,
}


class Register:
    def __init__(self, root):
        self.root = root
        self.entries = []

    def add_dir(self, rel, mode):
        self.entries.append({"path": rel.replace(os.sep, "/"), "type": "dir", "mode": "%04o" % mode})

    def add_file(self, rel, mode=None):
        rel = rel.replace(os.sep, "/")
        if mode is None:
            mode = SPECIAL_MODES.get(rel, 0o644)
        self.entries.append({"path": rel, "type": "file", "mode": "%04o" % mode})

    def add_symlink(self, rel, target):
        self.entries.append({"path": rel.replace(os.sep, "/"), "type": "symlink", "target": target})

    def write(self):
        payload = {"version": 1, "entries": self.entries}
        with open(os.path.join(self.root, ".register.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# copia de ficheros
# ---------------------------------------------------------------------------

def copy_tree(src, dst, register, rel_base, skip_dirs=("__pycache__",), extensions=None):
    """Copia recursivamente y registra cada fichero/directorio."""
    if not os.path.isdir(src):
        raise SystemExit("ERROR: no existe el directorio %s" % src)
    os.makedirs(dst, exist_ok=True)
    count = 0
    for entry in sorted(os.listdir(src)):
        if entry in skip_dirs:
            continue
        source = os.path.join(src, entry)
        target = os.path.join(dst, entry)
        rel = os.path.join(rel_base, entry)
        if os.path.isdir(source):
            copy_tree(source, target, register, rel, skip_dirs, extensions)
        else:
            if extensions and not entry.endswith(extensions):
                continue
            if entry.endswith(".pyc"):
                continue
            shutil.copy2(source, target)
            register.add_file(rel)
            count += 1
    return count


def write_file(path, content, register, rel, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    register.add_file(rel, mode)


# ---------------------------------------------------------------------------
# control
# ---------------------------------------------------------------------------

def build_control(package, version, arch, installed_kb, with_core, core_version):
    description = "Panel administrativo web para Astra 4 (fork forkcastra-master)"
    if with_core:
        description += ", con el núcleo de streaming incluido"

    if with_core:
        depends = "libc6, python3 (>= 3.8), adduser"
        extra = (" Este paquete incluye el binario del núcleo astra (%s) compilado a partir del\n"
                 " fork, los scripts stream.lua/analyze.lua/dvbls.lua, el agente Lua que expone la\n"
                 " API de control local y el panel web en Python 3 (sólo biblioteca estándar).\n"
                 " Incluye dos servicios systemd: astra.service y astra-webui.service." % core_version)
    else:
        depends = "python3 (>= 3.8), adduser"
        extra = (" Este paquete sólo instala el agente Lua y el panel web: requiere que el binario\n"
                 " astra ya esté instalado (paquete astra-webui completo).")

    return """Package: %s
Version: %s
Architecture: %s
Maintainer: TalaveraSama <noreply@github.com>
Installed-Size: %d
Depends: %s
Recommends: sudo, logrotate
Section: video
Priority: optional
Homepage: https://github.com/TalaveraSama/forkcastra-master
Description: %s
 Astra es un servidor de streaming IPTV (MPEG-TS sobre HTTP/UDP/DVB) escrito en C
 con lógica de negocio en Lua. Este paquete añade:
  * un agente Lua embebido que gestiona los canales en caliente (make_channel /
    kill_channel) y expone una API de control en 127.0.0.1:9977;
  * un servidor de streaming con tokens en el puerto 8001 (/live/<token>/<canal>.ts);
  * un panel web autenticado con dashboard, canales, usuarios y listas M3U, visor
    de logs con analizador PSI, escaneo DVB y auditoría de acciones.
%s
""" % (package, version, arch, installed_kb, depends, description, extra)


CONFFILES = "/etc/astra-webui/panel.json\n/etc/astra-webui/agent.json\n/etc/default/astra-webui\n"


# ---------------------------------------------------------------------------
# preparación
# ---------------------------------------------------------------------------

def stage(args):
    root = os.path.abspath(args.root)
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)

    register = Register(root)
    with_core = not args.no_core
    package = PACKAGE_CORE if with_core else PACKAGE_PANEL_ONLY
    version = ("%s-%s" % (args.core_version, args.revision)) if with_core \
        else ("%s-%s" % (PANEL_VERSION, args.revision))
    core_short = args.core_short or ".".join(args.core_version.split(".")[:2])

    # -- directorios base
    for rel, mode in BASE_DIRS.items():
        os.makedirs(os.path.join(root, rel), exist_ok=True)
        for parent in _parents(rel):
            register.add_dir(parent, BASE_DIRS.get(parent, 0o755))
        register.add_dir(rel, mode)
    _dedupe(register)

    # -- agente Lua
    agent_dir = os.path.join(ROOT, "agent")
    count = copy_tree(agent_dir, os.path.join(root, "usr/share/astra-webui/agent"),
                      register, "usr/share/astra-webui/agent", extensions=(".lua",))
    if count == 0:
        raise SystemExit("ERROR: no hay ficheros .lua en %s" % agent_dir)

    # -- panel Python
    panel_dir = os.path.join(ROOT, "panel", "astra_webui")
    count = copy_tree(panel_dir, os.path.join(root, "usr/share/astra-webui/panel/astra_webui"),
                      register, "usr/share/astra-webui/panel/astra_webui", extensions=(".py",))
    if count == 0:
        raise SystemExit("ERROR: no hay módulos Python en %s" % panel_dir)

    # -- interfaz web
    web_dir = os.path.join(ROOT, "panel", "web")
    if not os.path.isfile(os.path.join(web_dir, "index.html")):
        raise SystemExit("ERROR: falta panel/web/index.html (interfaz web)")
    copy_tree(web_dir, os.path.join(root, "usr/share/astra-webui/web"),
              register, "usr/share/astra-webui/web")

    # -- núcleo y scripts del core
    if with_core:
        core_bin = os.path.abspath(args.core_bin or os.path.join(args.source or "", "astra"))
        if not os.path.isfile(core_bin):
            raise SystemExit("ERROR: no encuentro el binario del núcleo: %s" % core_bin)
        target = os.path.join(root, "usr/bin/astra-%s" % core_short)
        shutil.copy2(core_bin, target)
        register.add_file("usr/bin/astra-%s" % core_short, 0o755)
        register.add_symlink("usr/bin/astra", "astra-%s" % core_short)

        if not args.source:
            raise SystemExit("ERROR: --source es obligatorio al incluir el núcleo")
        scripts_dir = os.path.join(args.source, "scripts")
        lua_dst = os.path.join(root, "etc/astra/scripts-%s" % core_short)
        os.makedirs(lua_dst, exist_ok=True)
        register.add_dir("etc/astra", 0o755)
        register.add_dir("etc/astra/scripts-%s" % core_short, 0o755)
        for name in ("stream.lua", "analyze.lua", "dvbls.lua", "xproxy.lua"):
            source = os.path.join(scripts_dir, name)
            if not os.path.isfile(source):
                continue
            with open(source, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines(True)
            if lines:
                lines[0] = lines[0].replace("$$", "-%s" % core_short)
            write_file(os.path.join(lua_dst, name), "".join(lines), register,
                       "etc/astra/scripts-%s/%s" % (core_short, name), 0o755)
        if not os.path.isfile(os.path.join(lua_dst, "stream.lua")):
            raise SystemExit("ERROR: no encuentro scripts/stream.lua en %s" % scripts_dir)

    # -- lanzador del panel
    write_file(os.path.join(root, "usr/bin/astra-webui"), LAUNCHER, register, "usr/bin/astra-webui", 0o755)

    # -- configuración (conffiles)
    for name in ("panel.json", "agent.json"):
        source = os.path.join(ROOT, "packaging", "config", name)
        if not os.path.isfile(source):
            raise SystemExit("ERROR: falta packaging/config/%s" % name)
        shutil.copy2(source, os.path.join(root, "etc/astra-webui", name))
        register.add_file("etc/astra-webui/%s" % name, 0o640)

    shutil.copy2(os.path.join(ROOT, "packaging", "common", "astra-webui.default"),
                 os.path.join(root, "etc/default/astra-webui"))
    register.add_file("etc/default/astra-webui", 0o644)

    # -- systemd, logrotate, sudoers, polkit
    for name in ("astra.service", "astra-webui.service"):
        shutil.copy2(os.path.join(ROOT, "packaging", "common", name),
                     os.path.join(root, "lib/systemd/system", name))
        register.add_file("lib/systemd/system/%s" % name, 0o644)

    shutil.copy2(os.path.join(ROOT, "packaging", "common", "astra-webui.logrotate"),
                 os.path.join(root, "etc/logrotate.d/astra-webui"))
    register.add_file("etc/logrotate.d/astra-webui", 0o644)

    shutil.copy2(os.path.join(ROOT, "packaging", "common", "astra-webui.sudoers"),
                 os.path.join(root, "etc/sudoers.d/astra-webui"))
    register.add_file("etc/sudoers.d/astra-webui", 0o440)

    shutil.copy2(os.path.join(ROOT, "packaging", "common", "50-astra-webui.rules"),
                 os.path.join(root, "usr/share/polkit-1/rules.d/50-astra-webui.rules"))
    register.add_file("usr/share/polkit-1/rules.d/50-astra-webui.rules", 0o644)

    # -- documentación
    doc_dir = os.path.join(root, "usr/share/doc/astra-webui")
    for name in DOC_FILES:
        source = os.path.join(ROOT, "docs", name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(doc_dir, name))
            register.add_file("usr/share/doc/astra-webui/%s" % name, 0o644)
    if os.path.isfile(os.path.join(ROOT, "README.md")):
        shutil.copy2(os.path.join(ROOT, "README.md"), os.path.join(doc_dir, "README.md"))
        register.add_file("usr/share/doc/astra-webui/README.md", 0o644)
    if with_core and args.source and os.path.isfile(os.path.join(args.source, "COPYING")):
        shutil.copy2(os.path.join(args.source, "COPYING"), os.path.join(doc_dir, "COPYING"))
        register.add_file("usr/share/doc/astra-webui/COPYING", 0o644)
    write_file(os.path.join(doc_dir, "copyright"), COPYRIGHT, register,
               "usr/share/doc/astra-webui/copyright", 0o644)

    # -- scripts de mantenimiento
    debian = os.path.join(root, "DEBIAN")
    os.makedirs(debian, exist_ok=True)
    register.add_dir("DEBIAN", 0o755)
    scripts = {}
    for name in ("postinst", "prerm", "postrm"):
        source = os.path.join(ROOT, "packaging", "scripts", name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(debian, name))
            scripts[name] = True

    installed_kb = _installed_size(root)
    control = build_control(package, version, args.arch, installed_kb, with_core, args.core_version)
    write_file(os.path.join(debian, "control"), control, register, "DEBIAN/control", 0o644)
    write_file(os.path.join(debian, "conffiles"), CONFFILES, register, "DEBIAN/conffiles", 0o644)

    # -- registro de permisos (no se incluye en el paquete)
    register.write()
    materialize(register)

    summary = {
        "package": package,
        "version": version,
        "arch": args.arch,
        "installed_size_kb": installed_kb,
        "entries": len(register.entries),
        "with_core": with_core,
        "core_version": args.core_version if with_core else "",
        "root": root,
    }
    return summary


def materialize(register):
    """Aplica en el árbol los permisos y enlaces del registro.

    Es imprescindible cuando el paquete se arma con dpkg-deb: dpkg-deb NO lee
    `.register.json`, usa los ficheros tal y como están en disco. Sin esto, el
    paquete salía sin el enlace /usr/bin/astra y con el lanzador en 644.
    (tools/build_deb.py aplica el mismo registro al empaquetar, así que es
    idempotente.)
    """
    problems = []
    for entry in register.entries:
        path = os.path.join(register.root, entry["path"].replace("/", os.sep))
        kind = entry.get("type")
        try:
            if kind == "dir":
                os.makedirs(path, exist_ok=True)
                os.chmod(path, int(entry.get("mode", "0755"), 8))
            elif kind == "symlink":
                target = entry.get("target", "")
                if not target:
                    continue
                if os.path.islink(path):
                    if os.readlink(path) == target:
                        continue
                    os.unlink(path)
                elif os.path.exists(path):
                    continue
                os.symlink(target, path)
            elif kind == "file":
                if not os.path.exists(path):
                    continue
                os.chmod(path, int(entry.get("mode", "0644"), 8))
        except OSError as exc:
            problems.append("%s (%s)" % (entry["path"], exc))
    if problems:
        # en Windows, os.symlink puede no estar permitido: el registro sigue siendo
        # la referencia para tools/build_deb.py, así que sólo se avisa
        print("AVISO: no se pudieron aplicar %d permisos/enlaces: %s"
              % (len(problems), "; ".join(problems[:4])))
    return problems


def _parents(rel):
    parts = rel.split("/")
    out = []
    for index in range(1, len(parts)):
        out.append("/".join(parts[:index]))
    return out


def _dedupe(register):
    seen = {}
    for entry in register.entries:
        seen[(entry["path"], entry["type"])] = entry
    register.entries = list(seen.values())
    register.entries.sort(key=lambda item: (item["path"], item["type"]))


def _installed_size(root):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "DEBIAN":
            continue
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return max(1, (total + 1023) // 1024)


def main():
    parser = argparse.ArgumentParser(description="Prepara el árbol de un .deb de Astra WebUI")
    parser.add_argument("--root", required=True, help="directorio de trabajo del paquete")
    parser.add_argument("--source", default=None, help="código del fork (para el núcleo y COPYING)")
    parser.add_argument("--core-bin", default=None, help="binario astra ya compilado")
    parser.add_argument("--core-version", default="0.0.0", help="versión del núcleo (4.0.282)")
    parser.add_argument("--core-short", default=None, help="versión corta (4.0)")
    parser.add_argument("--arch", default="amd64", help="arquitectura Debian")
    parser.add_argument("--revision", default="1", help="revisión Debian (por defecto 1)")
    parser.add_argument("--no-core", action="store_true", help="no incluir el núcleo (arch: all)")
    parser.add_argument("--json", action="store_true", help="imprime el resumen en JSON")
    args = parser.parse_args()

    if args.no_core:
        args.arch = "all"
    summary = stage(args)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("[stage] %(package)s %(version)s [%(arch)s] → %(root)s "
              "(%(entries)d entradas, %(installed_size_kb)d KiB)" % summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
