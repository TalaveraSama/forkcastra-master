#!/bin/bash
# Construye el paquete .deb de Astra WebUI (núcleo del fork + agente Lua + panel web).
#
#   sudo apt install -y build-essential python3
#   ./packaging/build-deb.sh                        # detecta el código del fork
#   ./packaging/build-deb.sh --source /srv/forkcastra-master
#   ./packaging/build-deb.sh --core-bin ./astra     # reutiliza un binario ya compilado
#   ./packaging/build-deb.sh --no-core              # sólo panel + agente (arch: all)
#
# Resultado: dist/astra-webui_<versión>_<arquitectura>.deb
#
# No usa debhelper ni dpkg-buildpackage: el paquete incluye un binario compilado
# del fork y se arma con dpkg-deb (o con tools/build_deb.py, Python puro).
# Toda la lógica de preparación del árbol está en tools/deb_stage.py.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
PYTHON="${PYTHON:-python3}"

SOURCE_DIR=""
CORE_BIN=""
OUT_DIR="${ROOT}/dist"
JOBS="$( (nproc 2>/dev/null || echo 2) )"
ARCH=""
WITH_CORE=1
VERBOSE=0
KEEP=0

log()  { printf '\033[1m[build]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[build] AVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[build] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    printf '%s\n' \
        "Uso: packaging/build-deb.sh [opciones]" \
        "" \
        "  --source DIR    código del fork (por defecto: repo/tree o el directorio padre)" \
        "  --core-bin FILE binario astra ya compilado (omite la compilación)" \
        "  --out DIR       directorio de salida (por defecto: dist/)" \
        "  --jobs N        trabajos de make (por defecto: núcleos de CPU)" \
        "  --arch ARCH     arquitectura Debian (por defecto: dpkg --print-architecture)" \
        "  --no-core       no incluir el núcleo astra (paquete arch: all)" \
        "  --keep          conservar build/pkgroot" \
        "  -v, --verbose   salida detallada" \
        "  -h, --help      esta ayuda"
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source)     SOURCE_DIR="$2"; shift 2 ;;
        --source=*)   SOURCE_DIR="${1#*=}"; shift ;;
        --core-bin)   CORE_BIN="$2"; shift 2 ;;
        --core-bin=*) CORE_BIN="${1#*=}"; shift ;;
        --out)        OUT_DIR="$2"; shift 2 ;;
        --out=*)      OUT_DIR="${1#*=}"; shift ;;
        --jobs)       JOBS="$2"; shift 2 ;;
        --jobs=*)     JOBS="${1#*=}"; shift ;;
        --arch)       ARCH="$2"; shift 2 ;;
        --arch=*)     ARCH="${1#*=}"; shift ;;
        --no-core)    WITH_CORE=0; shift ;;
        --keep)       KEEP=1; shift ;;
        -v|--verbose) VERBOSE=1; shift ;;
        -h|--help)    usage ;;
        *) die "opción desconocida: $1 (usa --help)" ;;
    esac
done

command -v "$PYTHON" >/dev/null 2>&1 || die "falta $PYTHON (apt install python3)"
[ -f "${ROOT}/tools/deb_stage.py" ] || die "falta tools/deb_stage.py"
[ -f "${ROOT}/tools/build_deb.py" ] || die "falta tools/build_deb.py"
[ -f "${ROOT}/panel/web/index.html" ] || die "falta la interfaz web (panel/web/index.html)"
[ -f "${ROOT}/agent/agent.lua" ] || die "falta agent/agent.lua"

# ---------------------------------------------------------------------------
# 1. localizar el código del fork y su versión
# ---------------------------------------------------------------------------

if [ -z "$SOURCE_DIR" ]; then
    for candidate in "${ROOT}/repo/tree" "${ROOT}/.." "${ROOT}"; do
        if [ -f "${candidate}/version.h" ] && [ -f "${candidate}/configure.sh" ]; then
            SOURCE_DIR="$(cd "${candidate}" && pwd)"
            break
        fi
    done
fi

CORE_VERSION="0.0.0"
CORE_SHORT="4.0"
if [ "$WITH_CORE" = "1" ]; then
    [ -n "$SOURCE_DIR" ] || die "no encuentro el código del fork; usa --source /ruta/al/fork"
    [ -f "${SOURCE_DIR}/version.h" ] || die "${SOURCE_DIR} no parece el código de Astra (falta version.h)"
    MAJOR="$(sed -n 's/.*ASTRA_VERSION_MAJOR \([0-9]*\).*/\1/p' "${SOURCE_DIR}/version.h")"
    MINOR="$(sed -n 's/.*ASTRA_VERSION_MINOR \([0-9]*\).*/\1/p' "${SOURCE_DIR}/version.h")"
    DEV="$(sed -n 's/.*ASTRA_VERSION_DEV \([0-9]*\).*/\1/p' "${SOURCE_DIR}/version.h")"
    [ -n "$MAJOR" ] && [ -n "$MINOR" ] || die "no puedo leer la versión de ${SOURCE_DIR}/version.h"
    CORE_VERSION="${MAJOR}.${MINOR}.${DEV:-0}"
    CORE_SHORT="${MAJOR}.${MINOR}"
    log "fork: ${SOURCE_DIR} (Astra ${CORE_VERSION})"
else
    log "construyendo SÓLO panel + agente (sin núcleo)"
fi

# ---------------------------------------------------------------------------
# 2. compilar el núcleo
# ---------------------------------------------------------------------------

if [ "$WITH_CORE" = "1" ] && [ -z "$CORE_BIN" ]; then
    command -v gcc  >/dev/null 2>&1 || die "falta gcc (apt install build-essential)"
    command -v make >/dev/null 2>&1 || die "falta make (apt install build-essential)"
    log "compilando el núcleo (make -j${JOBS})…"
    CONFIGURE_LOG="$(mktemp)"
    MAKE_LOG="$(mktemp)"
    # configure.sh se ejecuta con sh a propósito: hay copias del fork en las que el
    # fichero no tiene permiso de ejecución (y `./configure.sh` daría "Permission denied")
    if [ -x "${SOURCE_DIR}/configure.sh" ]; then
        CONFIGURE_CMD="./configure.sh"
    else
        CONFIGURE_CMD="sh ./configure.sh"
    fi
    if ! ( cd "$SOURCE_DIR" && $CONFIGURE_CMD ) >"$CONFIGURE_LOG" 2>&1; then
        tail -30 "$CONFIGURE_LOG" >&2
        die "configure.sh falló (log: $CONFIGURE_LOG)"
    fi
    if ! ( cd "$SOURCE_DIR" && make -j"$JOBS" ) >"$MAKE_LOG" 2>&1; then
        tail -40 "$MAKE_LOG" >&2
        die "la compilación falló (log: $MAKE_LOG)"
    fi
    CORE_BIN="${SOURCE_DIR}/astra"
    [ -x "$CORE_BIN" ] || die "la compilación no produjo ${CORE_BIN}"
    log "núcleo compilado: $(du -h "$CORE_BIN" | cut -f1)"
fi

# ---------------------------------------------------------------------------
# 3. preparar el árbol del paquete
# ---------------------------------------------------------------------------

PKGROOT="${ROOT}/build/pkgroot"
STAGE_ARGS=( --root "$PKGROOT" --arch "${ARCH:-$( [ "$WITH_CORE" = "1" ] && dpkg --print-architecture 2>/dev/null || echo all )}" )
if [ "$WITH_CORE" = "1" ]; then
    STAGE_ARGS+=( --source "$SOURCE_DIR" --core-bin "$CORE_BIN" --core-version "$CORE_VERSION" --core-short "$CORE_SHORT" )
else
    STAGE_ARGS+=( --no-core )
fi

log "preparando el árbol del paquete"
"$PYTHON" "${ROOT}/tools/deb_stage.py" "${STAGE_ARGS[@]}"

# ---------------------------------------------------------------------------
# 4. empaquetar y verificar
# ---------------------------------------------------------------------------

PACKAGE="$(sed -n 's/^Package: //p' "${PKGROOT}/DEBIAN/control")"
VERSION="$(sed -n 's/^Version: //p' "${PKGROOT}/DEBIAN/control")"
PKG_ARCH="$(sed -n 's/^Architecture: //p' "${PKGROOT}/DEBIAN/control")"
DEB_PATH="${OUT_DIR}/${PACKAGE}_${VERSION}_${PKG_ARCH}.deb"
mkdir -p "$OUT_DIR"
rm -f "$DEB_PATH"

log "generando $(basename "$DEB_PATH")"
if command -v dpkg-deb >/dev/null 2>&1; then
    # -Zxz + --uniform-compression: obliga a xz en los dos miembros (control y data)
    # para que el paquete se pueda instalar también en Ubuntu 20.04 (dpkg 1.19 no
    # entiende zstd, que es lo que usa dpkg 1.21+ por defecto) y para que el
    # verificador en Python pueda leerlos sin depender de zstd.
    if ! dpkg-deb --build --root-owner-group -Zxz --uniform-compression "$PKGROOT" "$DEB_PATH" >/dev/null 2>&1; then
        warn "dpkg-deb con xz falló: se reintenta con la compresión por defecto"
        dpkg-deb --build --root-owner-group "$PKGROOT" "$DEB_PATH" >/dev/null
    fi
else
    warn "dpkg-deb no disponible: se usa tools/build_deb.py"
    "$PYTHON" "${ROOT}/tools/build_deb.py" --root "$PKGROOT" --out "$DEB_PATH" $( [ "$VERBOSE" = "1" ] && printf '%s' '-v' )
fi

"$PYTHON" "${ROOT}/tools/build_deb.py" --verify "$DEB_PATH" || die "el paquete no pasa la verificación"
[ "$VERBOSE" = "1" ] && "$PYTHON" "${ROOT}/tools/build_deb.py" --inspect "$DEB_PATH"

if [ "$KEEP" != "1" ]; then
    rm -rf "${ROOT}/build"
else
    log "árbol conservado en ${PKGROOT}"
fi

SIZE="$(du -h "$DEB_PATH" | cut -f1)"
cat <<EOF

$(printf '\033[32m[build] Paquete creado\033[0m')

  Fichero:  $DEB_PATH  ($SIZE)
  Paquete:  $PACKAGE $VERSION  [$PKG_ARCH]

Instalación en el servidor (Ubuntu 20.04+):

  sudo apt install -y "$DEB_PATH"
  sudo systemctl status astra astra-webui
  # la contraseña inicial de 'admin' se muestra al instalar y queda en
  # /etc/astra-webui/initial-admin-password

EOF
