#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprueba que un .deb de Astra WebUI tiene todo lo que debe tener.

Sirve para dos cosas:

  * en la construcción (packaging/build-deb.sh y CI) para no publicar un paquete
    incompleto: detectó, por ejemplo, que dpkg-deb no aplicaba el registro de
    permisos y el paquete salía sin /usr/bin/astra y con el lanzador en 644;
  * después, para verificar un .deb descargado de una release:
        python3 tools/check_package.py astra-webui_4.0.282-1_amd64.deb

    python3 tools/check_package.py --panel dist/astra-webui-panel_1.0.0-1_all.deb
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_deb  # noqa: E402

FAILURES = []
CHECKS = [0]


def check(condition, label, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  \033[32mOK\033[0m   %s" % label)
    else:
        FAILURES.append(label)
        print("  \033[31mFALLO\033[0m %s %s" % (label, detail))
    return bool(condition)


def read(deb):
    members = dict(build_deb.ar_read(deb))
    control_name = build_deb.control_member(members)
    fields, conffiles = {}, []
    if control_name:
        with tarfile.open(fileobj=io.BytesIO(members[control_name]), mode="r:*") as tar:
            handle = tar.extractfile("./control")
            if handle:
                fields = build_deb.control_fields(handle.read().decode("utf-8"))
            if "./conffiles" in tar.getnames():
                conffiles = [line.strip() for line in
                             tar.extractfile("./conffiles").read().decode().splitlines() if line.strip()]
    entries = {}
    data_name = next((n for n in members if n.startswith("data.tar")), None)
    if data_name:
        with tarfile.open(fileobj=io.BytesIO(members[data_name]), mode="r:*") as tar:
            for entry in tar.getmembers():
                entries[build_deb._norm_path(entry.name)] = entry
    return members, fields, conffiles, entries, data_name


def binary_bytes(deb, name):
    members = dict(build_deb.ar_read(deb))
    data_name = next((n for n in members if n.startswith("data.tar")), None)
    with tarfile.open(fileobj=io.BytesIO(members[data_name]), mode="r:*") as tar:
        handle = tar.extractfile("./" + name)
        return handle.read() if handle else b""


def ar_structure(deb):
    """Recorre los miembros del archivo ar y devuelve (miembros, bytes_sobrantes).

    Es la comprobación que detecta paquetes que `dpkg-deb` lee pero `apt` rechaza:
    dpkg 1.19 con --uniform-compression dejaba bytes de más al final del archivo y
    apt respondía «Cabecera de miembro del archivo inválida».
    """
    raw = open(deb, "rb").read()
    if not raw.startswith(b"!<arch>\n"):
        return [], len(raw)
    offset = 8
    members = []
    while offset + 60 <= len(raw):
        header = raw[offset:offset + 60]
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            break
        name = header[0:16].decode("ascii", "replace").strip().rstrip("/")
        if not name:
            break
        members.append((name, size))
        offset += 60 + size + (size % 2)
    return members, max(0, len(raw) - offset)


def main():
    parser = argparse.ArgumentParser(description="Comprueba el contenido de un .deb de Astra WebUI")
    parser.add_argument("deb", help="fichero .deb a comprobar")
    parser.add_argument("--panel", action="store_true", help="variante sin núcleo (sólo panel)")
    args = parser.parse_args()

    deb = args.deb
    if not os.path.isfile(deb):
        print("ERROR: no existe %s" % deb, file=sys.stderr)
        return 2

    print("Paquete: %s (%.1f KiB)" % (deb, os.path.getsize(deb) / 1024.0))
    members, fields, conffiles, entries, data_name = read(deb)

    print("\n== estructura y control")
    miembros, sobrantes = ar_structure(deb)
    check(bool(miembros), "el archivo ar se puede recorrer", "%d miembros" % len(miembros))
    check(sobrantes == 0, "la estructura ar termina en el último miembro",
          "%d bytes sobrantes (apt rechazaría el paquete)" % sobrantes)
    check("debian-binary" in members, "tiene debian-binary")
    check(bool(data_name), "tiene data.tar.*", data_name or "")
    check(bool(build_deb.control_member(members)), "tiene control.tar.*")
    esperado = "astra-webui-panel" if args.panel else "astra-webui"
    check(fields.get("Package") == esperado, "nombre del paquete: %s" % esperado,
          fields.get("Package", ""))
    check(bool(fields.get("Version")), "versión presente", fields.get("Version", ""))
    check(fields.get("Architecture") == ("all" if args.panel else "amd64"),
          "arquitectura %s" % ("all" if args.panel else "amd64"), fields.get("Architecture", ""))
    check("python3" in (fields.get("Depends") or ""), "depende de python3")
    if not args.panel:
        check("libc6" in (fields.get("Depends") or ""), "depende de libc6")
    check(int(fields.get("Installed-Size") or 0) > 0, "tamaño instalado calculado")

    print("\n== ejecutables")
    launcher = entries.get("usr/bin/astra-webui")
    check(launcher is not None, "incluye /usr/bin/astra-webui")
    if launcher is not None:
        check(launcher.isfile(), "/usr/bin/astra-webui es un fichero")
        check(bool(launcher.mode & 0o111), "/usr/bin/astra-webui es ejecutable",
              "modo %o" % launcher.mode)
    if args.panel:
        check("usr/bin/astra-4.0" not in entries, "la variante sin núcleo no lleva el binario")
        check("usr/bin/astra" not in entries, "la variante sin núcleo no lleva el enlace")
    else:
        core = entries.get("usr/bin/astra-4.0")
        check(core is not None, "incluye /usr/bin/astra-4.0")
        if core is not None:
            check(core.isfile(), "/usr/bin/astra-4.0 es un fichero")
            check(bool(core.mode & 0o111), "/usr/bin/astra-4.0 es ejecutable", "modo %o" % core.mode)
            raw = binary_bytes(deb, "usr/bin/astra-4.0")
            check(raw[:4] == b"\x7fELF", "el núcleo es un binario ELF")
            check(len(raw) > 100000, "el binario tiene tamaño razonable", "%d bytes" % len(raw))
        link = entries.get("usr/bin/astra")
        check(link is not None and link.issym(), "/usr/bin/astra es un enlace simbólico")
        if link is not None and link.issym():
            check(link.linkname == "astra-4.0", "el enlace apunta a astra-4.0", link.linkname)

    print("\n== agente, panel y WebUI")
    for name in ("agent.lua", "aw_channels.lua", "aw_stream.lua", "aw_control.lua",
                 "aw_dvb.lua", "aw_http.lua", "aw_auth.lua", "aw_json.lua", "aw_log.lua",
                 "aw_util.lua"):
        check("usr/share/astra-webui/agent/%s" % name in entries, "agente: %s" % name)
    for name in ("app.py", "httpd.py", "store.py", "validators.py", "api_auth.py",
                 "api_channels.py", "api_core.py", "api_dvb.py", "metrics.py", "m3u.py"):
        check("usr/share/astra-webui/panel/astra_webui/%s" % name in entries, "panel: %s" % name)
    for name in ("index.html", "css/app.css", "js/app.js", "js/api.js", "js/i18n.js",
                 "js/ui.js", "js/views/dashboard.js", "js/views/channels.js", "js/views/dvb.js"):
        check("usr/share/astra-webui/web/%s" % name in entries, "web: %s" % name)

    print("\n== servicios y configuración")
    for unit in ("astra.service", "astra-webui.service"):
        path = "lib/systemd/system/%s" % unit
        check(path in entries, "unidad systemd: %s" % unit)
    for path in ("usr/share/polkit-1/rules.d/50-astra-webui.rules", "etc/logrotate.d/astra-webui",
                 "etc/sudoers.d/astra-webui", "etc/default/astra-webui"):
        check(path in entries, "configuración: %s" % path)
    for path in ("etc/astra-webui/panel.json", "etc/astra-webui/agent.json"):
        entry = entries.get(path)
        check(entry is not None, "configuración: %s" % path)
        if entry is not None:
            # 0640 está bien (lectura para el grupo astra); lo que no vale es que
            # cualquier usuario del sistema pueda leerlo
            check(not entry.mode & 0o007, "permisos restrictivos en %s" % path, "modo %o" % entry.mode)
    for conf in ("/etc/astra-webui/panel.json", "/etc/astra-webui/agent.json",
                 "/etc/default/astra-webui"):
        check(conf in conffiles, "declarado conffile: %s" % conf)

    print("\n== limpieza")
    check(not any("__pycache__" in name for name in entries), "sin __pycache__")
    check(not any(name.endswith(".pyc") for name in entries), "sin ficheros .pyc")
    check(not any(name.upper().startswith("DEBIAN/") for name in entries), "DEBIAN no está en data.tar")
    check(not any("register.json" in name for name in entries),
          "el registro interno no viaja en el paquete")
    check(not any(entry.mode & 0o002 for entry in entries.values() if entry.isfile()),
          "ningún fichero es escribible por todos")
    for name in ("postinst", "prerm", "postrm"):
        members_all = dict(build_deb.ar_read(deb))
        with tarfile.open(fileobj=io.BytesIO(members_all[build_deb.control_member(members_all)]),
                          mode="r:*") as tar:
            check("./%s" % name in tar.getnames(), "script de mantenimiento: %s" % name)

    print("\n  comprobaciones: %d, fallos: %d" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        print("\033[31mPaquete INCOMPLETO\033[0m: %s" % ", ".join(FAILURES[:6]))
        return 1
    print("\033[32mEl paquete está completo\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
