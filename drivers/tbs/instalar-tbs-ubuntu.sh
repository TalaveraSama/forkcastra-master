#!/bin/bash
# Instalador del driver TBS para Ubuntu 20.04 / 22.04 / 24.04
#
# Basado en irtec/Driver_DVB_Card y en la guía oficial de TBS (tbsdtv/media_build).
# Compila el árbol de media con soporte TBS, sube el límite de adaptadores DVB e
# instala los firmwares. Probado con TBS 6904se (4 sintonizadores DVB-S/S2/S2x).
#
# Uso:
#   sudo bash instalar-tbs-ubuntu.sh [opciones]
#
# Opciones:
#   --j N                 trabajos en paralelo al compilar (por defecto: núcleos)
#   --source tbsdtv|ljalves   de dónde clonar el árbol (por defecto tbsdtv)
#   --no-firmware         no descargar/instalar firmwares
#   --limpiar-kernel      borrar los módulos de media del kernel (lo que hace la guía
#                         original de TBS; NO suele ser necesario porque los de
#                         updates/ tienen prioridad)
#   --max-adapters N      límite de adaptadores DVB (por defecto 48)
#   --sin-reiniciar       no preguntar por el reinicio al terminar
#   -h|--help             esta ayuda

set -euo pipefail

LOG=/var/log/instalar-tbs.log
SRC=/usr/src
JOBS=$(nproc 2>/dev/null || echo 4)
REPO=tbsdtv
FIRMWARE=1
LIMPIAR=0
MAX_ADAPTERS=48
REINICIAR=1

while [ $# -gt 0 ]; do
    case "$1" in
        --j) JOBS="$2"; shift 2 ;;
        --source) REPO="$2"; shift 2 ;;
        --no-firmware) FIRMWARE=0; shift ;;
        --limpiar-kernel) LIMPIAR=1; shift ;;
        --max-adapters) MAX_ADAPTERS="$2"; shift 2 ;;
        --sin-reiniciar) REINICIAR=0; shift ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "opción desconocida: $1 (usa --help)"; exit 2 ;;
    esac
done

log() { echo -e "$*" | tee -a "$LOG"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: hay que ejecutarlo como root (sudo bash $0)" >&2
    exit 1
fi

: > "$LOG"
KERNEL=$(uname -r)
log "== Instalador del driver TBS =="
log "  kernel: $KERNEL"
log "  log:    $LOG"

# ---------------------------------------------------------------------------
# 1. comprobar el sistema
# ---------------------------------------------------------------------------
log "\n== 1. comprobando el sistema =="
if ! command -v apt-get >/dev/null 2>&1; then
    log "ERROR: este script es para Ubuntu/Debian (no hay apt-get)"; exit 1
fi

if [ ! -d "/lib/modules/$KERNEL/build" ]; then
    log "  faltan las cabeceras del kernel: se instalan"
    apt-get update >>"$LOG" 2>&1 || true
    apt-get install -y "linux-headers-$KERNEL" >>"$LOG" 2>&1 || {
        log "ERROR: no se pudieron instalar linux-headers-$KERNEL"; exit 1; }
fi
log "  cabeceras del kernel: OK"

log "  instalando utilidades de compilación"
apt-get update >>"$LOG" 2>&1 || true
apt-get install -y build-essential patchutils libproc-processtable-perl git wget \
                   "linux-headers-$KERNEL" >>"$LOG" 2>&1
log "  utilidades: OK"

log "  tarjetas DVB detectadas en el bus:"
lspci -nn 2>/dev/null | grep -iE 'tbs|multimedia' | sed 's/^/    /' || \
    log "    (aviso: no se ve ninguna tarjeta multimedia en lspci)"

# ---------------------------------------------------------------------------
# 2. límite de adaptadores y limpieza opcional
# ---------------------------------------------------------------------------
log "\n== 2. preparando el árbol del kernel =="
CONFIG="/lib/modules/$KERNEL/build/.config"
if [ -f "$CONFIG" ]; then
    cp -n "$CONFIG" "$CONFIG.bak-$(date +%s)" 2>/dev/null || true
    if grep -q '^CONFIG_DVB_MAX_ADAPTERS=' "$CONFIG"; then
        sed -i -e "s/^\(CONFIG_DVB_MAX_ADAPTERS\)=.*/\1=$MAX_ADAPTERS/" "$CONFIG"
    else
        echo "CONFIG_DVB_MAX_ADAPTERS=$MAX_ADAPTERS" >> "$CONFIG"
    fi
    log "  CONFIG_DVB_MAX_ADAPTERS=$MAX_ADAPTERS"
else
    log "  aviso: no existe $CONFIG (se continúa)"
fi

if [ "$LIMPIAR" = "1" ]; then
    log "  borrando los módulos de media del kernel (--limpiar-kernel)"
    rm -rf "/lib/modules/$KERNEL/extra"
    rm -rf "/lib/modules/$KERNEL/kernel/drivers/media"
    rm -rf "/lib/modules/$KERNEL/kernel/drivers/staging/media"
fi

# ---------------------------------------------------------------------------
# 3. clonar y compilar media_build
# ---------------------------------------------------------------------------
log "\n== 3. compilando el driver (esto tarda bastante) =="
cd "$SRC"
rm -rf "$SRC/media_build" "$SRC/media"

if [ "$REPO" = "ljalves" ]; then
    MEDIA_BUILD="https://github.com/ljalves/media_build.git"
    LINUX_MEDIA="https://github.com/ljalves/linux_media.git"
else
    MEDIA_BUILD="https://github.com/tbsdtv/media_build.git"
    LINUX_MEDIA="https://github.com/tbsdtv/linux_media.git"
fi

log "  clonando $MEDIA_BUILD"
git clone --depth 1 "$MEDIA_BUILD" "$SRC/media_build" >>"$LOG" 2>&1
log "  clonando $LINUX_MEDIA"
git clone --depth 1 -b latest "$LINUX_MEDIA" "$SRC/media" >>"$LOG" 2>&1

cd "$SRC/media_build"

if [ "$REPO" = "ljalves" ]; then
    make dir DIR=../media >>"$LOG" 2>&1
    make distclean >>"$LOG" 2>&1 || true
    log "  compilando (make -j$JOBS)"
    make -j"$JOBS" >>"$LOG" 2>&1
else
    make dir DIR=../media >>"$LOG" 2>&1
    make allyesconfig >>"$LOG" 2>&1
    log "  compilando (make -j$JOBS)"
    make -j"$JOBS" >>"$LOG" 2>&1
fi

log "  instalando módulos (make install)"
make install >>"$LOG" 2>&1
depmod -a

# ---------------------------------------------------------------------------
# 4. firmware
# ---------------------------------------------------------------------------
if [ "$FIRMWARE" = "1" ]; then
    log "\n== 4. instalando firmwares =="
    cd "$SRC"
    FW_URL="http://www.tbsdtv.com/download/document/linux/tbs-tuner-firmwares_v1.0.tar.bz2"
    if wget -q -O "$SRC/tbs-tuner-firmwares_v1.0.tar.bz2" "$FW_URL" 2>>"$LOG"; then
        tar jxf "$SRC/tbs-tuner-firmwares_v1.0.tar.bz2" -C /lib/firmware/ 2>>"$LOG" || true
        log "  firmwares instalados en /lib/firmware"
    else
        log "  aviso: no se pudo descargar el firmware (¿sin internet?); se continúa"
    fi
fi

# ---------------------------------------------------------------------------
# 5. resumen
# ---------------------------------------------------------------------------
log "\n== 5. resumen =="
log "  módulos instalados:"
find "/lib/modules/$KERNEL/updates" -name 'tbsecp3.ko*' -o -name 'gx1133.ko*' -o \
     -name 'tas2101.ko*' 2>/dev/null | sed 's/^/    /' || true
log "  firmwares:"
ls /lib/firmware 2>/dev/null | grep -iE 'm88|dvb-demod|gx|tas' | head -5 | sed 's/^/    /' || true
log "\n  Listo. Hace falta REINICIAR para cargar los módulos nuevos."
log "  Si la tarjeta no aparece tras el reinicio, apaga el equipo por completo"
log "  (no reinicies): hay tarjetas TBS que solo vuelven con un apagado real."
log "\n  Después del reinicio comprueba:"
log "    ls /dev/dvb        # adaptadores (la 6904se da 4)"
log "    sudo femon -a 0    # signal/snr"

if [ "$REINICIAR" = "1" ]; then
    read -r -p "¿Reiniciar ahora? [s/N] " resp
    case "$resp" in
        s|S|y|Y) shutdown -r now ;;
        *) log "  reinicia tú cuando quieras: sudo reboot" ;;
    esac
fi
