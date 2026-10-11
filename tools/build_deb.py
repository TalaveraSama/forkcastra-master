#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constructor de paquetes Debian (.deb) en Python puro.

Se usa como respaldo de `dpkg-deb` (que no está en todas partes) y permite
construir/inspeccionar el paquete desde cualquier sistema, incluido Windows.
El formato es simple:

    !<arch>
    debian-binary     -> "2.0\n"
    control.tar.gz    -> ./control, ./conffiles, ./postinst, ...
    data.tar.xz       -> árbol de ficheros instalado

Uso:
    python3 tools/build_deb.py --root pkgroot --out dist/panel.deb
    python3 tools/build_deb.py --inspect dist/panel.deb
    python3 tools/build_deb.py --verify dist/panel.deb
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import lzma
import os
import stat
import sys
import tarfile
import time

AR_MAGIC = b"!<arch>\n"
DEBIAN_BINARY = b"2.0\n"
FIXED_MTIME = int(os.environ.get("SOURCE_DATE_EPOCH", "1700000000"))

# Ficheros de DEBIAN/ que van en control.tar y no en data.tar
CONTROL_MEMBERS = (
    "control", "conffiles", "preinst", "postinst", "prerm", "postrm",
    "triggers", "shlibs", "symbols", "templates", "config", "md5sums",
)


# ---------------------------------------------------------------------------
# ar
# ---------------------------------------------------------------------------

def ar_write(path, members):
    """Escribe un archivo `ar` con los miembros indicados [(nombre, bytes)]."""
    with open(path, "wb") as handle:
        handle.write(AR_MAGIC)
        for name, data in members:
            encoded = name.encode("ascii")
            if len(encoded) > 16:
                raise ValueError("nombre de miembro ar demasiado largo: %s" % name)
            header = (
                encoded.ljust(16)
                + str(FIXED_MTIME).encode().ljust(12)
                + b"0".ljust(6)          # uid
                + b"0".ljust(6)          # gid
                + b"100644".ljust(8)     # modo
                + str(len(data)).encode().ljust(10)
                + b"\x60\x0a"
            )
            handle.write(header)
            handle.write(data)
            if len(data) % 2:
                handle.write(b"\n")


def ar_read(path):
    """Lee un archivo `ar` y devuelve [(nombre, bytes)]."""
    members = []
    with open(path, "rb") as handle:
        if handle.read(len(AR_MAGIC)) != AR_MAGIC:
            raise ValueError("no es un archivo ar/deb válido")
        while True:
            header = handle.read(60)
            if len(header) < 60:
                break
            name = header[0:16].decode("ascii", "replace").strip()
            size = int(header[48:58].decode("ascii", "replace").strip())
            data = handle.read(size)
            if size % 2:
                handle.read(1)
            members.append((name, data))
    return members


# ---------------------------------------------------------------------------
# tar
# ---------------------------------------------------------------------------

def _tarinfo(name, mode, size=0, mtime=FIXED_MTIME, uname="root", gname="root"):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.size = size
    info.mtime = mtime
    info.uid = 0
    info.gid = 0
    info.uname = uname
    info.gname = gname
    return info


def load_register(root):
    """Lee el registro de permisos creado por tools/deb_stage.py (si existe).

    Permite construir el .deb desde Windows (donde no hay permisos POSIX) con
    los modos correctos y con enlaces simbólicos que no existen en disco.
    """
    path = os.path.join(root, ".register.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    out = {}
    for entry in data.get("entries", []):
        rel = str(entry.get("path", "")).strip("/")
        if not rel or rel.split("/")[0] == "DEBIAN":
            continue
        out[rel] = entry
    return out


def collect_entries(root, register):
    """Devuelve {ruta_relativa: (tipo, modo, destino)} a partir del disco y del registro."""
    entries = {}

    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel == ".":
            rel = ""
        if rel.split("/")[0] == "DEBIAN":
            dirnames[:] = []
            continue
        for name in dirnames:
            path = (rel + "/" + name).lstrip("/")
            if path.split("/")[0] == "DEBIAN":
                continue
            entries[path] = ("dir", 0o755, None)
        for name in filenames:
            path = (rel + "/" + name).lstrip("/")
            if "/" not in path and path.startswith("."):
                continue  # .register.json
            if path.split("/")[0] == "DEBIAN":
                continue
            entries[path] = ("file", 0o644, None)

    for path, entry in register.items():
        kind = entry.get("type") or "file"
        mode = entry.get("mode", 0o644)
        try:
            mode = int(str(mode), 8)
        except (TypeError, ValueError):
            mode = 0o644
        entries[path] = (kind, mode, entry.get("target"))

    return entries


def build_data_tar(root, compression="xz", register=None):
    """Empaqueta todo el árbol `root` (excepto DEBIAN/) como data.tar.*"""
    register = register if register is not None else load_register(root)
    entries = collect_entries(root, register)

    buffer = io.BytesIO()
    mode = "w:" + compression if compression != "none" else "w"
    with tarfile.open(fileobj=buffer, mode=mode, format=tarfile.GNU_FORMAT) as tar:
        ordered = sorted(entries.items(), key=lambda item: (item[1][0] != "dir", item[0]))
        for path, (kind, file_mode, target) in ordered:
            full = os.path.join(root, path.replace("/", os.sep))
            arcname = "./" + path

            if kind == "dir":
                info = _tarinfo(arcname.rstrip("/") + "/", file_mode or 0o755)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
                continue

            if os.path.islink(full):
                info = _tarinfo(arcname, 0o777)
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(full)
                tar.addfile(info)
                continue

            if kind == "symlink":
                info = _tarinfo(arcname, 0o777)
                info.type = tarfile.SYMTYPE
                info.linkname = target or os.path.basename(path)
                tar.addfile(info)
                continue

            if not os.path.isfile(full):
                continue
            size = os.path.getsize(full)
            info = _tarinfo(arcname, file_mode or 0o644, size=size)
            with open(full, "rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


def build_control_tar(control_dir):
    """Empaqueta DEBIAN/ como control.tar.gz con los scripts en modo 0755."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        directory = _tarinfo("./", 0o755)
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name in sorted(os.listdir(control_dir)):
            full = os.path.join(control_dir, name)
            if not os.path.isfile(full):
                continue
            script = name in ("preinst", "postinst", "prerm", "postrm", "config")
            mode = 0o755 if script else 0o644
            data = open(full, "rb").read()
            info = _tarinfo("./" + name, mode, size=len(data))
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def build_control_tar_from_files(files):
    """Igual que build_control_tar pero a partir de un diccionario {nombre: bytes}."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        directory = _tarinfo("./", 0o755)
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name in sorted(files):
            data = files[name]
            if isinstance(data, str):
                data = data.encode("utf-8")
            script = name in ("preinst", "postinst", "prerm", "postrm", "config")
            info = _tarinfo("./" + name, 0o755 if script else 0o644, size=len(data))
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# construcción
# ---------------------------------------------------------------------------

def _norm_path(name):
    """Normaliza el nombre de una entrada de tar: './etc/x' y '/etc/x' → 'etc/x'."""
    return name.lstrip("./").rstrip("/")


def control_fields(text):
    """Parsea un fichero control (campo: valor, con continuaciones)."""
    fields = {}
    key = None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def build_deb(root, out_path, compression=None, verbose=False):
    control_dir = os.path.join(root, "DEBIAN")
    if not os.path.isdir(control_dir):
        raise SystemExit("ERROR: no existe %s" % control_dir)
    control_path = os.path.join(control_dir, "control")
    if not os.path.isfile(control_path):
        raise SystemExit("ERROR: falta %s" % control_path)

    if compression is None:
        compression = "xz" if _has_xz() else "gz"

    if verbose:
        print("  empaquetando data.tar.%s" % compression)
    data_tar = build_data_tar(root, compression, load_register(root))
    if verbose:
        print("  empaquetando control.tar.gz")
    control_tar = build_control_tar(control_dir)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    ar_write(out_path, [
        ("debian-binary", DEBIAN_BINARY),
        ("control.tar.gz", control_tar),
        ("data.tar." + compression, data_tar),
    ])
    return out_path


def _has_xz():
    try:
        import lzma  # noqa: F401
        return True
    except ImportError:  # pragma: no cover
        return False


def control_member(members):
    """Devuelve el nombre del miembro de control.

    Los .deb construidos aquí usan control.tar.gz, pero dpkg-deb (1.19+) genera
    control.tar.xz por defecto: hay que aceptar cualquier compresión.
    """
    for name in sorted(members):
        if name.startswith("control.tar"):
            return name
    return None


def open_member(members, name):
    """Abre un miembro tar del .deb; devuelve None si la compresión no se soporta.

    dpkg 1.21+ puede usar zstd en data.tar, que el módulo tarfile de Python no
    entiende antes de la 3.14: en ese caso no se debe reventar con un Traceback.
    """
    try:
        return tarfile.open(fileobj=io.BytesIO(members[name]), mode="r:*")
    except (tarfile.ReadError, tarfile.CompressionError, OSError) as exc:
        return exc


def inspect(deb_path):
    """Muestra el contenido de un .deb (campos de control y tamaño de data)."""
    members = dict(ar_read(deb_path))
    control_name = control_member(members)
    print("Paquete: %s" % deb_path)
    print("Tamaño:  %d bytes" % os.path.getsize(deb_path))
    print("Miembros: %s" % ", ".join(members))
    if "debian-binary" in members:
        print("Formato: %s" % members["debian-binary"].decode().strip())
    if control_name:
        tar = open_member(members, control_name)
        if isinstance(tar, Exception):
            print("\n%s: no se pudo leer (%s)" % (control_name, tar))
        else:
            with tar:
                names = tar.getnames()
                print("\n%s: %s" % (control_name, ", ".join(names)))
                control = tar.extractfile("./control")
                if control:
                    fields = control_fields(control.read().decode("utf-8"))
                    print("\nCampos de control:")
                    for key in ("Package", "Version", "Architecture", "Depends", "Recommends",
                                "Section", "Priority", "Maintainer", "Installed-Size", "Description"):
                        if key in fields:
                            print("  %-16s %s" % (key + ":", fields[key]))
    for name, data in members.items():
        if name.startswith("data.tar"):
            tar = open_member(members, name)
            if isinstance(tar, Exception):
                print("\n%s: %d bytes (no se pudo leer: %s)" % (name, len(data), tar))
                continue
            with tar:
                entries = tar.getmembers()
                print("\n%s: %d entradas, %d bytes" % (name, len(entries), len(data)))
                kinds = {}
                for entry in entries:
                    kinds[entry.type] = kinds.get(entry.type, 0) + 1
                print("  tipos: %s" % kinds)
    return 0


def verify(deb_path):
    """Comprobaciones básicas: estructura, control obligatorio, conffiles y modos."""
    problems = []
    members = dict(ar_read(deb_path))
    if "debian-binary" not in members:
        problems.append("falta el miembro debian-binary")
    control_name = control_member(members)
    if not control_name:
        problems.append("falta el miembro control.tar.*")
    data_names = [n for n in members if n.startswith("data.tar")]
    if not data_names:
        problems.append("falta el miembro data.tar.*")
    if members.get("debian-binary", b"").strip() != b"2.0":
        problems.append("debian-binary no es 2.0")

    fields = {}
    conffiles = []
    if control_name:
        tar = open_member(members, control_name)
        if isinstance(tar, Exception):
            problems.append("%s no se pudo leer (%s)" % (control_name, tar))
        else:
            with tar:
                names = set(tar.getnames())
                for required in ("./control",):
                    if required not in names:
                        problems.append("%s no contiene %s" % (control_name, required))
                handle = tar.extractfile("./control")
                if handle:
                    fields = control_fields(handle.read().decode("utf-8"))
                handle = tar.extractfile("./conffiles") if "./conffiles" in names else None
                if handle:
                    conffiles = [line.strip() for line in handle.read().decode().splitlines()
                                 if line.strip()]

    for key in ("Package", "Version", "Architecture", "Maintainer", "Description"):
        if not fields.get(key):
            problems.append("campo de control obligatorio vacío: %s" % key)

    data_entries = {}
    if data_names:
        tar = open_member(members, data_names[0])
        if isinstance(tar, Exception):
            print("\033[33mAVISO:\033[0m %s usa una compresión que este Python no entiende (%s); "
                  "se omite la comprobación del contenido" % (data_names[0], tar))
        else:
            with tar:
                for entry in tar.getmembers():
                    data_entries[_norm_path(entry.name)] = entry

    for conffile in conffiles:
        if _norm_path(conffile) not in data_entries:
            problems.append("conffile declarado y no incluido: %s" % conffile)

    for name, entry in data_entries.items():
        if entry.isdir():
            continue
        if entry.isfile() and entry.mode & 0o002:
            problems.append("fichero con permiso de escritura para todos: %s" % name)

    if problems:
        print("\033[31m%d problemas en el paquete:\033[0m" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        return 1

    print("\033[32mPaquete correcto\033[0m: %s %s (%s), %d ficheros, %d conffiles"
          % (fields.get("Package"), fields.get("Version"), fields.get("Architecture"),
             len(data_entries), len(conffiles)))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Constructor/validador de .deb en Python")
    parser.add_argument("--root", help="árbol del paquete (con DEBIAN/)")
    parser.add_argument("--out", help="fichero .deb de salida")
    parser.add_argument("--compression", choices=("xz", "gz", "bz2", "none"), default=None,
                        help="compresión de data.tar (por defecto xz si está disponible)")
    parser.add_argument("--inspect", help="muestra el contenido de un .deb existente")
    parser.add_argument("--verify", help="valida un .deb existente")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.inspect:
        return inspect(args.inspect)
    if args.verify:
        return verify(args.verify)
    if not args.root or not args.out:
        parser.error("se requieren --root y --out (o --inspect/--verify)")
    build_deb(args.root, args.out, args.compression, args.verbose)
    if args.verbose:
        print("  creado %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
