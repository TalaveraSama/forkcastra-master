#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monta el paquete de instalación SIN INTERNET (offline).

Genera `release/astra-webui-offline_<versión>_<arch>.tar.gz` con todo lo necesario
para instalar en un Ubuntu/Debian sin red:

    astra-webui-offline/
      LEEME.txt                     instrucciones de 1 minuto
      instalar-offline.sh           instalador (no usa la red)
      VERSION                       versiones del núcleo y del panel
      SHA256SUMS                    sumas de todo el paquete
      debs/astra-webui_*_amd64.deb      paquete completo (si existe)
      debs/astra-webui-panel_*_all.deb  panel + agente
      deps/*.deb                    dependencias opcionales (--deps DIR)
      src/astra-webui-*-src.tar.gz      código del panel
      src/forkcastra-master.tar.gz      código del fork (para compilar el núcleo)
      docs/*.md                     INSTALL, OFFLINE, TROUBLESHOOTING…
      plantillas/nginx-astra-webui.conf ejemplo de proxy con TLS

    python3 tools/make_offline_bundle.py
    python3 tools/make_offline_bundle.py --deps C:\\debs --no-fork
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    import deb_stage
except ImportError:  # pragma: no cover
    deb_stage = None

DOCS = ("INSTALL.md", "OFFLINE.md", "TROUBLESHOOTING.md", "INSTALL-WSL.md", "RELEASE.md")


def log(message):
    print("\033[1m[offline]\033[0m %s" % message)


def info(message):
    print("  · %s" % message)


def warn(message):
    print("\033[33m[offline] AVISO:\033[0m %s" % message)


def fork_source():
    for candidate in (os.path.join(ROOT, "repo", "tree"), ROOT, os.path.dirname(ROOT)):
        if os.path.isfile(os.path.join(candidate, "version.h")):
            return candidate
    return None


def core_version(source):
    if not source:
        return "4.0.282"
    import re
    text = open(os.path.join(source, "version.h"), encoding="utf-8", errors="replace").read()
    major = re.search(r"ASTRA_VERSION_MAJOR\s+(\d+)", text)
    minor = re.search(r"ASTRA_VERSION_MINOR\s+(\d+)", text)
    dev = re.search(r"ASTRA_VERSION_DEV\s+(\d+)", text)
    if major and minor:
        version = "%s.%s" % (major.group(1), minor.group(1))
        if dev:
            version += ".%s" % dev.group(1)
        return version
    return "4.0.282"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_if_exists(src, dst):
    if src and os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def tar_fork(source, dest):
    """Empaqueta el código del fork (sin .git ni artefactos)."""
    with tarfile.open(dest, "w:gz") as tar:
        for base, dirs, names in os.walk(source):
            dirs[:] = [d for d in dirs if d not in (".git", "build", "dist", "__pycache__")]
            for name in sorted(names):
                full = os.path.join(base, name)
                if name.endswith((".deb", ".tar.gz", ".o", ".pyc")):
                    continue
                relative = os.path.relpath(full, source)
                tar.add(full, arcname=os.path.join("forkcastra-master", relative))
    return dest


def write_leeme(bundle, version, core, arch, has_full, has_deps):
    full_note = ("Incluye el paquete completo (núcleo + agente + panel + WebUI)."
                 if has_full else
                 "OJO: este paquete NO incluye el binario del núcleo; el instalador lo\n"
                 "compilará con el código de src/ (necesita gcc y make ya instalados) o\n"
                 "instalará sólo el panel si ya tienes astra 4.0 en el sistema.")
    deps_note = ("Incluye deps/ con dependencias de sistema."
                 if has_deps else
                 "No incluye deps/: el instalador comprobará las dependencias y te dirá\n"
                 "cuáles faltan (python3 y adduser son los habituales).")
    text = f"""Astra WebUI {version} — instalación SIN INTERNET
=====================================================

Contenido: agente Lua para astra, panel web (Python 3, sólo biblioteca estándar),
WebUI estática, el paquete .deb y el código fuente por si hay que compilar.

Núcleo: {core}   Arquitectura: {arch}

{full_note}
{deps_note}

Instalar (en el servidor, sin red)
----------------------------------
    tar -xzf astra-webui-offline_{version}_{arch}.tar.gz
    cd astra-webui-offline_{version}_{arch}
    sudo ./instalar-offline.sh

El instalador:
  1. comprueba las sumas SHA-256 de todo el paquete,
  2. busca el paquete completo en debs/ (y si no, compila el núcleo desde src/),
  3. revisa las dependencias y las instala desde deps/ si están ahí,
  4. instala con dpkg (sin descargar nada),
  5. arranca astra y el panel y muestra la contraseña inicial de admin.

Opciones útiles
---------------
    sudo ./instalar-offline.sh --check        comprobar sin instalar
    sudo ./instalar-offline.sh --no-start     instalar sin arrancar
    sudo ./instalar-offline.sh --build        compilar el núcleo aunque haya .deb
    sudo ./instalar-offline.sh --deps-dir /ruta/con/debs
    sudo ./instalar-offline.sh --port 8080

Después de instalar
-------------------
  * Panel:    http://IP-DEL-SERVIDOR:8000   (usuario admin)
  * Contraseña inicial: /etc/astra-webui/initial-admin-password
  * Logs:     /var/log/astra/astra.log y /var/log/astra-webui/panel.log
  * Servicios: systemctl status astra astra-webui

Documentación
-------------
  docs/INSTALL.md          instalación completa, DVB, TLS, desinstalación
  docs/OFFLINE.md          detalles de este paquete sin Internet
  docs/TROUBLESHOOTING.md  problemas habituales y diagnóstico

Licencia GPLv3 (igual que el núcleo Astra del que deriva).
"""
    path = os.path.join(bundle, "LEEME.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def main():
    parser = argparse.ArgumentParser(description="Monta el paquete de instalación sin Internet")
    parser.add_argument("--out", default=os.path.join(ROOT, "release"),
                        help="carpeta de salida (por defecto release/)")
    parser.add_argument("--release-dir", default="",
                        help="carpeta con los .deb ya construidos (por defecto, --out y dist/)")
    parser.add_argument("--deps", default="", help="carpeta con .deb de dependencias a incluir")
    parser.add_argument("--arch", default="amd64", help="arquitectura (por defecto amd64)")
    parser.add_argument("--no-fork", action="store_true", help="no incluir el código del fork")
    parser.add_argument("--keep", action="store_true", help="no borrar la carpeta descomprimida")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    search_dirs = [os.path.abspath(args.release_dir) if args.release_dir else out_dir,
                   os.path.join(ROOT, "dist")]

    source = fork_source()
    core = core_version(source)
    version = deb_stage.PANEL_VERSION if deb_stage else "1.0.0"
    name = "astra-webui-offline_%s_%s" % (version, args.arch)
    bundle = os.path.join(out_dir, name)

    log("montando %s" % name)
    if os.path.isdir(bundle):
        shutil.rmtree(bundle)
    os.makedirs(os.path.join(bundle, "debs"), exist_ok=True)
    os.makedirs(os.path.join(bundle, "src"), exist_ok=True)
    os.makedirs(os.path.join(bundle, "docs"), exist_ok=True)

    # -- paquetes .deb ------------------------------------------------------
    full_deb = None
    panel_deb = None
    sdist = None
    for directory in search_dirs:
        if not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            path = os.path.join(directory, entry)
            if entry.startswith("astra-webui_") and entry.endswith(".deb") and not full_deb:
                full_deb = path
            elif entry.startswith("astra-webui-panel_") and entry.endswith(".deb") and not panel_deb:
                panel_deb = path
            elif entry.startswith("astra-webui-") and entry.endswith("-src.tar.gz") and not sdist:
                sdist = path
    if full_deb and copy_if_exists(full_deb, os.path.join(bundle, "debs", os.path.basename(full_deb))):
        info("paquete completo: %s" % os.path.basename(full_deb))
    else:
        warn("no hay paquete completo (astra-webui_*_%s.deb); se incluirá el código para compilarlo"
             % args.arch)
    if panel_deb and copy_if_exists(panel_deb, os.path.join(bundle, "debs", os.path.basename(panel_deb))):
        info("paquete del panel: %s" % os.path.basename(panel_deb))

    # -- código fuente ------------------------------------------------------
    if sdist and copy_if_exists(sdist, os.path.join(bundle, "src", os.path.basename(sdist))):
        info("código del panel: %s" % os.path.basename(sdist))
    else:
        try:
            import make_release  # noqa: PLC0415
            entrega, _stats = make_release.build_entrega(bundle)
            generated = make_release.make_tarball(bundle, entrega, version)
            target = os.path.join(bundle, "src", os.path.basename(generated))
            shutil.move(generated, target)
            shutil.rmtree(entrega, ignore_errors=True)
            sdist = target
            info("código del panel generado: %s" % os.path.basename(target))
        except Exception as exc:  # noqa: BLE001
            warn("no se pudo incluir el código del panel: %s" % exc)

    if not args.no_fork and source:
        dest = os.path.join(bundle, "src", "forkcastra-master.tar.gz")
        tar_fork(source, dest)
        info("código del fork: %s (%.1f MiB)" % (os.path.basename(dest),
                                                 os.path.getsize(dest) / 1048576))
    elif not args.no_fork:
        warn("no se encontró el código del fork: el instalador no podrá compilar el núcleo")

    # -- dependencias opcionales -------------------------------------------
    has_deps = False
    if args.deps and os.path.isdir(args.deps):
        target = os.path.join(bundle, "deps")
        os.makedirs(target, exist_ok=True)
        for entry in sorted(os.listdir(args.deps)):
            if entry.endswith(".deb"):
                shutil.copy2(os.path.join(args.deps, entry), os.path.join(target, entry))
                has_deps = True
        info("dependencias incluidas: %d" % len(os.listdir(target)))
    elif args.deps:
        warn("la carpeta --deps %s no existe" % args.deps)

    # -- instalador, documentación y plantillas ----------------------------
    installer = os.path.join(ROOT, "packaging", "offline", "instalar-offline.sh")
    copy_if_exists(installer, os.path.join(bundle, "instalar-offline.sh"))
    os.chmod(os.path.join(bundle, "instalar-offline.sh"), 0o755)

    for doc in DOCS:
        copy_if_exists(os.path.join(ROOT, "docs", doc), os.path.join(bundle, "docs", doc))
    for template in ("nginx-astra-webui.conf", "astra-webui.service.example"):
        copy_if_exists(os.path.join(ROOT, "packaging", "templates", template),
                       os.path.join(bundle, "plantillas", template))

    with open(os.path.join(bundle, "VERSION"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("panel=%s\ncore=%s\narch=%s\n" % (version, core, args.arch))

    write_leeme(bundle, version, core, args.arch, bool(full_deb), has_deps)

    # -- sumas de todo el paquete ------------------------------------------
    sums = []
    for base, dirs, names in os.walk(bundle):
        dirs[:] = [d for d in dirs if d != "_entrega"]
        for entry in sorted(names):
            if entry == "SHA256SUMS":
                continue
            path = os.path.join(base, entry)
            sums.append("%s  %s" % (sha256(path), os.path.relpath(path, bundle)))
    with open(os.path.join(bundle, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(sorted(sums, key=lambda line: line.split("  ", 1)[1])) + "\n")
    info("sumas calculadas: %d ficheros" % len(sums))

    tarball = os.path.join(out_dir, name + ".tar.gz")
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(bundle, arcname=name)
    info("paquete: %s (%.2f MiB)" % (os.path.basename(tarball),
                                     os.path.getsize(tarball) / 1048576))

    # -- comprobación del paquete ------------------------------------------
    problems = []
    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    for required in ("instalar-offline.sh", "LEEME.txt", "SHA256SUMS", "VERSION"):
        if not any(n.endswith("/" + required) for n in names):
            problems.append("falta %s" % required)
    if not any("/debs/" in n and n.endswith(".deb") for n in names):
        problems.append("no hay ningún .deb dentro")
    if not any("/src/" in n and n.endswith(".tar.gz") for n in names):
        problems.append("no hay código fuente dentro")
    for problem in problems:
        warn(problem)

    if not args.keep:
        shutil.rmtree(bundle, ignore_errors=True)

    log("listo: %s" % tarball)
    if problems:
        warn("revisa los avisos: el paquete puede no ser autosuficiente")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
