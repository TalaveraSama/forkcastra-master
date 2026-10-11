#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepara la entrega y los ficheros de una release de Astra WebUI.

Genera en la carpeta indicada (por defecto `release/`):

  entrega/                                   árbol listo para copiar en el repositorio
  astra-webui-<versión>-src.tar.gz           el mismo árbol empaquetado
  astra-webui-panel_<versión>-1_all.deb      paquete sin núcleo (arch all), si se puede
  astra-webui_<core>-1_amd64.deb             paquete completo (sólo si hay gcc en Linux)
  SHA256SUMS                                 sumas de todo lo anterior
  RELEASE-NOTES.md                           texto de la release
  COMO-SUBIRLO.md                            pasos con GitHub Desktop y con Actions

    python3 tools/make_release.py --tag v4.0.282-10
    python3 tools/make_release.py --out C:\\entrega --no-deb
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    import deb_stage
    import build_deb
except ImportError:  # pragma: no cover
    deb_stage = None
    build_deb = None

# Qué se copia a la entrega (lo que se sube al repositorio)
INCLUDE_DIRS = ("agent", "panel", "packaging", "tools", "docs", ".github")
INCLUDE_FILES = ("README.md", ".gitignore", "gitattributes-astra-webui.txt")
EXCLUDE_DIRS = {"__pycache__", ".git", ".github/workflows/.cache", "repo", "build", "dist",
                "release", "var", "node_modules", ".vscode", ".idea"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".deb", ".swp", "~")
EXCLUDE_FILES = {"gitignore-astra-webui.txt", "COMO-SUBIRLO.md", "RELEASE-NOTES.md",
                 "README-astra-webui.md"}


def log(message):
    print("\033[1m[entrega]\033[0m %s" % message)


def info(message):
    print("  · %s" % message)


def warn(message):
    print("\033[33m[entrega] AVISO:\033[0m %s" % message)


def fork_source():
    """Código del fork: repo/tree (desarrollo) o la raíz del proyecto (entrega)."""
    for candidate in (os.path.join(ROOT, "repo", "tree"), ROOT, os.path.dirname(ROOT)):
        if os.path.isfile(os.path.join(candidate, "version.h")):
            return candidate
    return None


def core_version(source):
    if not source:
        return "4.0.282"
    try:
        text = open(os.path.join(source, "version.h"), encoding="utf-8", errors="replace").read()
    except OSError:
        return "4.0.282"
    import re
    values = {}
    for key in ("MAJOR", "MINOR", "DEV"):
        match = re.search(r"ASTRA_VERSION_%s\s+(\d+)" % key, text)
        values[key] = match.group(1) if match else ""
    if values["MAJOR"] and values["MINOR"]:
        version = "%s.%s" % (values["MAJOR"], values["MINOR"])
        if values["DEV"]:
            version += ".%s" % values["DEV"]
        return version
    return "4.0.282"


def copy_tree(src, dst, stats, rel=""):
    """Copia recursiva con filtros; devuelve el número de ficheros."""
    for name in sorted(os.listdir(src)):
        full = os.path.join(src, name)
        relative = os.path.join(rel, name) if rel else name
        if os.path.isdir(full):
            if name in EXCLUDE_DIRS:
                continue
            copy_tree(full, os.path.join(dst, name), stats, relative)
        else:
            # los nombres de EXCLUDE_FILES sólo se descartan en la raíz: dentro de
            # docs/ sí hacen falta (docs/RELEASE-NOTES.md lo usa el workflow)
            if (not rel and name in EXCLUDE_FILES) or name.endswith(EXCLUDE_SUFFIXES):
                continue
            if os.sep + "tools" + os.sep in os.sep + relative and name.startswith("_"):
                continue
            target = os.path.join(dst, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)
            stats["files"] += 1
            stats["bytes"] += os.path.getsize(full)
    return stats


def build_entrega(out_dir):
    entrega = os.path.join(out_dir, "entrega")
    if os.path.isdir(entrega):
        shutil.rmtree(entrega)
    os.makedirs(entrega, exist_ok=True)
    stats = {"files": 0, "bytes": 0}
    for name in INCLUDE_DIRS:
        src = os.path.join(ROOT, name)
        if os.path.isdir(src):
            copy_tree(src, os.path.join(entrega, name), stats, name)
    for name in INCLUDE_FILES:
        src = os.path.join(ROOT, name)
        if not os.path.isfile(src):
            continue
        target_name = name
        if name == "README.md":
            # el fork ya tiene su propio README en la raíz: el del panel va aparte
            target_name = "README-astra-webui.md"
        elif name == ".gitignore":
            # no se pisa el .gitignore del fork: se entrega para añadir sus líneas
            target_name = "gitignore-astra-webui.txt"
        shutil.copy2(src, os.path.join(entrega, target_name))
        stats["files"] += 1
        stats["bytes"] += os.path.getsize(src)
    return entrega, stats


def make_tarball(out_dir, entrega, version):
    path = os.path.join(out_dir, "astra-webui-%s-src.tar.gz" % version)
    with tarfile.open(path, "w:gz") as tar:
        for base, _dirs, names in os.walk(entrega):
            for name in sorted(names):
                full = os.path.join(base, name)
                tar.add(full, arcname=os.path.join("astra-webui",
                                                   os.path.relpath(full, entrega)))
    return path


def build_panel_deb(out_dir, entrega):
    """Construye el .deb sin núcleo (arch all) con las herramientas del proyecto."""
    if deb_stage is None or build_deb is None:
        return None, "no se pudieron importar las herramientas de empaquetado"
    root = os.path.join(out_dir, "_pkgroot")
    if os.path.isdir(root):
        # en Windows los ficheros con permisos restrictivos (sudoers 0440) no se
        # pueden borrar sin quitarlo antes: se ignora el error y se limpia aparte
        shutil.rmtree(root, ignore_errors=True)
        if os.path.isdir(root):
            _chmod_tree(root)
            shutil.rmtree(root, ignore_errors=True)
    argv = sys.argv
    try:
        sys.argv = ["deb_stage.py", "--root", root, "--no-core", "--json"]
        code = deb_stage.main()
        if code:
            return None, "deb_stage.py devolvió %s" % code
    except SystemExit as exc:  # deb_stage usa SystemExit
        if exc.code:
            return None, "deb_stage.py terminó con %s" % exc.code
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    finally:
        sys.argv = argv

    deb = os.path.join(out_dir, "astra-webui-panel_%s-1_all.deb" % deb_stage.PANEL_VERSION)
    build_deb.build_deb(root, deb)
    problems = build_deb.verify(deb)
    shutil.rmtree(root, ignore_errors=True)
    if problems:
        return None, "la verificación falló"
    return deb, ""


def build_full_deb(out_dir, source):
    """Intenta el paquete completo (necesita Linux con gcc y el código del fork)."""
    if not sys.platform.startswith("linux"):
        return None, "sólo se puede compilar el núcleo en Linux"
    if not shutil.which("gcc") or not shutil.which("make"):
        return None, "no hay gcc/make en este sistema"
    if not source:
        return None, "no se encontró el código del fork"
    script = os.path.join(ROOT, "packaging", "build-deb.sh")
    result = subprocess.run(["/bin/sh", script, "--source", source, "--out", out_dir],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "").strip().splitlines()[-1:]
    debs = [os.path.join(out_dir, name) for name in os.listdir(out_dir)
            if name.startswith("astra-webui_") and name.endswith(".deb")]
    return (debs[0] if debs else None), ("" if debs else "no se generó ningún .deb")


def _chmod_tree(root):
    """Quita el modo de sólo lectura de un árbol (necesario en Windows)."""
    for base, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sums(out_dir, files):
    """Sumas SHA-256 de todos los ficheros publicables de la carpeta.

    Se incluyen también los .deb y .tar.gz que haya allí aunque no los haya generado
    este script (por ejemplo el paquete de instalación sin Internet).
    """
    import glob
    candidates = list(files)
    for pattern in ("*.deb", "*.tar.gz", "*.zip"):
        candidates += glob.glob(os.path.join(out_dir, pattern))
    lines = []
    seen = set()
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        if name in seen or name == "SHA256SUMS":
            continue
        seen.add(name)
        lines.append("%s  %s" % (sha256(path), name))
    path = os.path.join(out_dir, "SHA256SUMS")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(sorted(lines, key=lambda line: line.split("  ", 1)[1])) + "\n")
    return path


def main():
    parser = argparse.ArgumentParser(description="Prepara la entrega y los ficheros de la release")
    parser.add_argument("--out", default=os.path.join(ROOT, "release"),
                        help="carpeta de salida (por defecto release/)")
    parser.add_argument("--tag", default="", help="etiqueta de la release, p. ej. v4.0.282-10")
    parser.add_argument("--version", default="", help="versión del panel (por defecto la del paquete)")
    parser.add_argument("--no-deb", action="store_true", help="no construir ningún .deb")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    source = fork_source()
    core = core_version(source)
    version = args.version or (deb_stage.PANEL_VERSION if deb_stage else "1.0.0")
    tag = args.tag or "v%s-%s" % (core, time.strftime("%Y%m%d"))

    log("generando la entrega en %s" % out_dir)
    info("versión del panel: %s" % version)
    info("versión del núcleo: %s" % core)
    info("código del fork: %s" % (source or "no encontrado"))

    entrega, stats = build_entrega(out_dir)
    info("entrega: %d ficheros, %.1f MiB" % (stats["files"], stats["bytes"] / 1048576))

    artifacts = []
    tarball = make_tarball(out_dir, entrega, version)
    artifacts.append(tarball)
    info("tarball: %s" % os.path.basename(tarball))

    if not args.no_deb:
        deb, error = build_full_deb(out_dir, source)
        if deb:
            artifacts.append(deb)
            info("paquete completo: %s" % os.path.basename(deb))
        else:
            warn("paquete completo no construido aquí (%s); lo genera GitHub Actions" % error)
        deb, error = build_panel_deb(out_dir, entrega)
        if deb:
            artifacts.append(deb)
            info("paquete sólo panel: %s" % os.path.basename(deb))
        else:
            warn("paquete sólo panel no construido (%s)" % error)

    # notas y instrucciones dentro de la carpeta de salida
    notes_src = os.path.join(ROOT, "docs", "RELEASE-NOTES.md")
    notes = os.path.join(out_dir, "RELEASE-NOTES.md")
    if os.path.isfile(notes_src):
        text = open(notes_src, encoding="utf-8").read()
        text = text.replace("@@VERSION@@", version).replace("@@CORE@@", core).replace("@@TAG@@", tag)
        with open(notes, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        info("notas: RELEASE-NOTES.md")

    guide_src = os.path.join(ROOT, "docs", "COMO-SUBIRLO.md")
    if os.path.isfile(guide_src):
        text = open(guide_src, encoding="utf-8").read()
        text = text.replace("@@VERSION@@", version).replace("@@CORE@@", core).replace("@@TAG@@", tag)
        with open(os.path.join(out_dir, "COMO-SUBIRLO.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        info("guía: COMO-SUBIRLO.md")

    sums = write_sums(out_dir, artifacts)
    info("sumas: SHA256SUMS (%d ficheros)" % len(artifacts))
    log("contenido de %s" % out_dir)
    for name in sorted(os.listdir(out_dir)):
        full = os.path.join(out_dir, name)
        if os.path.isdir(full):
            count = sum(len(files) for _r, _d, files in os.walk(full))
            print("    %-46s <dir> (%d ficheros)" % (name + "/", count))
        else:
            print("    %-46s %8.1f KiB" % (name, os.path.getsize(full) / 1024.0))

    print("\n  Siguiente paso: abre la carpeta 'entrega' en GitHub Desktop")
    print("  (lee COMO-SUBIRLO.md, está todo explicado paso a paso)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
