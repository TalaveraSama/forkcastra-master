#!/bin/bash
# Astra WebUI — instalador SIN INTERNET.
#
# Instala el panel (y el núcleo, si el paquete lo incluye) en un Ubuntu/Debian que no
# tiene salida a Internet. No ejecuta "apt-get update" ni descarga nada: usa los .deb
# que vienen en este paquete y, si hiciera falta, compila el núcleo con las fuentes
# incluidas (necesita build-essential ya instalado).
#
#   sudo ./instalar-offline.sh                 # instala y arranca
#   sudo ./instalar-offline.sh --no-start      # instala sin arrancar
#   sudo ./instalar-offline.sh --build         # fuerza compilar el núcleo desde las fuentes
#   sudo ./instalar-offline.sh --deps-dir DIR  # carpeta con .deb de dependencias
#   ./instalar-offline.sh --check              # sólo comprueba (no requiere root)

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
DEBS_DIR="$HERE/debs"
DEPS_DIR="$HERE/deps"
SRC_DIR="$HERE/src"
DO_START=1
FORCE_BUILD=0
CHECK_ONLY=0
PANEL_PORT=""

log()  { printf '\033[1m[offline]\033[0m %s\n' "$*"; }
info() { printf '  · %s\n' "$*"; }
warn() { printf '\033[33m[offline] AVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[offline] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-start)  DO_START=0; shift ;;
        --build)     FORCE_BUILD=1; shift ;;
        --check)     CHECK_ONLY=1; shift ;;
        --deps-dir)  DEPS_DIR="$2"; shift 2 ;;
        --deps-dir=*) DEPS_DIR="${1#*=}"; shift ;;
        --port)      PANEL_PORT="$2"; shift 2 ;;
        --port=*)    PANEL_PORT="${1#*=}"; shift ;;
        -h|--help)   usage ;;
        *) die "opción desconocida: $1 (usa --help)" ;;
    esac
done

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if [ "$CHECK_ONLY" = "1" ]; then
        SUDO=""
    elif command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        die "hay que ejecutarlo como root: sudo $0"
    fi
fi

# ---------------------------------------------------------------------------
# 1. entorno
# ---------------------------------------------------------------------------

log "comprobando el sistema"
[ "$(uname -s)" = "Linux" ] || die "este instalador es para Linux (detectado $(uname -s))"

if [ -r /etc/os-release ]; then
    . /etc/os-release
    info "sistema: ${PRETTY_NAME:-$NAME}"
else
    warn "no puedo leer /etc/os-release"
fi
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
info "arquitectura: $ARCH"

if [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" = "systemd" ]; then
    HAS_SYSTEMD=1
else
    HAS_SYSTEMD=0
    info "systemd no está activo: los procesos se arrancarán a mano"
fi

command -v dpkg >/dev/null 2>&1 || die "no existe dpkg (¿seguro que es Ubuntu/Debian?)"

# ---------------------------------------------------------------------------
# 2. integridad
# ---------------------------------------------------------------------------

if [ -f "$HERE/SHA256SUMS" ] && command -v sha256sum >/dev/null 2>&1; then
    log "comprobando las sumas SHA-256"
    if ( cd "$HERE" && sha256sum -c --quiet SHA256SUMS ) 2>/dev/null; then
        info "todos los ficheros coinciden"
    else
        warn "alguna suma no coincide; revisa el paquete antes de instalar"
        ( cd "$HERE" && sha256sum -c SHA256SUMS ) 2>&1 | grep -v ': OK$' | head -10
        [ "$CHECK_ONLY" = "1" ] || die "paquete corrupto (usa --check para verlo sin instalar)"
    fi
else
    warn "no hay SHA256SUMS; no se puede comprobar la integridad"
fi

# ---------------------------------------------------------------------------
# 3. elegir el paquete
# ---------------------------------------------------------------------------

log "buscando el paquete a instalar"
FULL_DEB=""
PANEL_DEB=""
if [ -d "$DEBS_DIR" ]; then
    FULL_DEB="$(ls -1t "$DEBS_DIR"/astra-webui_[0-9]*_*.deb 2>/dev/null | head -1)"
    PANEL_DEB="$(ls -1t "$DEBS_DIR"/astra-webui-panel_*_all.deb 2>/dev/null | head -1)"
fi

BUILD_NEEDED=0
DEB=""
if [ -n "$FULL_DEB" ] && [ "$FORCE_BUILD" = "0" ]; then
    DEB="$FULL_DEB"
    info "paquete completo (con núcleo): $(basename "$DEB")"
elif [ -n "$PANEL_DEB" ] && [ "$FORCE_BUILD" = "0" ]; then
    DEB="$PANEL_DEB"
    warn "sólo hay el paquete del panel: instala el agente y el panel, pero NO el binario astra"
    info "si ya tienes astra 4.0 instalado, este paquete es suficiente"
    BUILD_NEEDED=1
else
    BUILD_NEEDED=1
fi

if [ "$BUILD_NEEDED" = "1" ] && [ "$FORCE_BUILD" = "0" ] && [ -n "$FULL_DEB" ]; then
    DEB="$FULL_DEB"
    BUILD_NEEDED=0
fi

if [ "$BUILD_NEEDED" = "1" ]; then
    log "no hay paquete completo: se intentará compilar el núcleo con las fuentes incluidas"
    MISSING=""
    for tool in gcc make; do
        command -v "$tool" >/dev/null 2>&1 || MISSING="$MISSING $tool"
    done
    if [ -n "$MISSING" ]; then
        warn "faltan herramientas de compilación:$MISSING"
        warn "sin Internet no se pueden instalar; opciones:"
        warn "  * copia en debs/ el paquete astra-webui_*_${ARCH}.deb ya compilado"
        warn "  * o consigue build-essential en un .deb y añádelo a deps/"
        if [ -n "$PANEL_DEB" ]; then
            warn "  * o continúa con el paquete del panel si ya tienes astra instalado"
        else
            die "no hay nada que instalar"
        fi
    else
        info "hay gcc y make: se compilará desde las fuentes"
    fi
fi

if [ "$CHECK_ONLY" = "1" ]; then
    log "modo comprobación: no se instala nada"
    [ -n "$DEB" ] && info "se instalaría: $(basename "$DEB")"
    [ "$BUILD_NEEDED" = "1" ] && info "se compilaría el núcleo desde src/"
    info "puerto del panel: ${PANEL_PORT:-8000 (por defecto)}"
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. compilar el núcleo con las fuentes incluidas (si hace falta)
# ---------------------------------------------------------------------------

if [ "$BUILD_NEEDED" = "1" ]; then
    PANEL_SRC="$(ls -1 "$SRC_DIR"/astra-webui-*-src.tar.gz 2>/dev/null | head -1)"
    FORK_SRC="$(ls -1 "$SRC_DIR"/*forkcastra*.tar.gz "$SRC_DIR"/*fork*.tar.gz 2>/dev/null | head -1)"
    [ -n "$PANEL_SRC" ] || die "no encuentro el código del panel en src/"
    [ -n "$FORK_SRC" ] || die "no encuentro el código del fork en src/ (hace falta para compilar el núcleo)"

    WORK="$(mktemp -d)"
    log "descomprimiendo el código en $WORK"
    tar -xzf "$PANEL_SRC" -C "$WORK" || die "no se pudo descomprimir $PANEL_SRC"
    tar -xzf "$FORK_SRC" -C "$WORK/fork" 2>/dev/null || {
        mkdir -p "$WORK/fork"
        tar -xzf "$FORK_SRC" -C "$WORK/fork" || die "no se pudo descomprimir $FORK_SRC"
    }
    PANEL_ROOT="$(find "$WORK" -maxdepth 2 -name build-deb.sh -path '*packaging*' | head -1)"
    [ -n "$PANEL_ROOT" ] || die "no encuentro packaging/build-deb.sh en el código del panel"
    PANEL_ROOT="$(cd "$(dirname "$PANEL_ROOT")/.." && pwd)"
    FORK_ROOT="$(find "$WORK/fork" -maxdepth 2 -name version.h | head -1)"
    [ -n "$FORK_ROOT" ] || die "no encuentro version.h en el código del fork"
    FORK_ROOT="$(dirname "$FORK_ROOT")"

    log "compilando el núcleo (puede tardar unos minutos)"
    if ! ( cd "$PANEL_ROOT" && ./packaging/build-deb.sh --source "$FORK_ROOT" --out "$PANEL_ROOT/dist" ) \
            > "$WORK/build.log" 2>&1; then
        tail -25 "$WORK/build.log" >&2
        die "la compilación falló (log completo en $WORK/build.log)"
    fi
    DEB="$(ls -1t "$PANEL_ROOT"/dist/astra-webui_[0-9]*_*.deb 2>/dev/null | head -1)"
    [ -n "$DEB" ] || die "la compilación no generó ningún .deb"
    info "paquete construido: $(basename "$DEB")"
fi

# ---------------------------------------------------------------------------
# 5. dependencias del paquete elegido
# ---------------------------------------------------------------------------

check_dep() {
    # $1 = nombre del paquete (sin versión); devuelve 0 si está instalado
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

if [ -n "$DEB" ]; then
    log "comprobando las dependencias de $(basename "$DEB")"
    DEPS_RAW="$(dpkg-deb -f "$DEB" Depends 2>/dev/null || true)"
    MISSING_DEPS=""
    OLDIFS="$IFS"; IFS=','
    for entry in $DEPS_RAW; do
        name="$(printf '%s' "$entry" | sed 's/(.*//; s/|.*//; s/[[:space:]]//g')"
        [ -n "$name" ] || continue
        if ! check_dep "$name"; then
            MISSING_DEPS="$MISSING_DEPS $name"
        fi
    done
    IFS="$OLDIFS"

    if [ -n "$MISSING_DEPS" ]; then
        warn "faltan dependencias:$MISSING_DEPS"
        if [ -d "$DEPS_DIR" ]; then
            for name in $MISSING_DEPS; do
                dep_deb="$(ls -1t "$DEPS_DIR"/${name}_*.deb 2>/dev/null | head -1)"
                if [ -n "$dep_deb" ]; then
                    info "instalando dependencia $(basename "$dep_deb")"
                    $SUDO dpkg -i "$dep_deb" >/dev/null 2>&1 || warn "no se pudo instalar $(basename "$dep_deb")"
                fi
            done
        fi
        STILL=""
        for name in $MISSING_DEPS; do
            check_dep "$name" || STILL="$STILL $name"
        done
        if [ -n "$STILL" ]; then
            warn "siguen faltando:$STILL"
            warn "en un equipo con Internet: apt-get install -y$STILL"
            warn "sin Internet: copia esos .deb en deps/ y vuelve a ejecutar este instalador"
        else
            info "dependencias resueltas con los .deb de deps/"
        fi
    else
        info "todas las dependencias están instaladas"
    fi
fi

# ---------------------------------------------------------------------------
# 6. instalar
# ---------------------------------------------------------------------------

log "instalando $(basename "$DEB")"
if ! $SUDO dpkg -i "$DEB" >/tmp/astra-offline-install.log 2>&1; then
    warn "dpkg -i falló; se intenta resolver con la caché local de apt (sin red)"
    $SUDO apt-get -f install -y --no-download >>/tmp/astra-offline-install.log 2>&1 || true
    if ! $SUDO dpkg -i "$DEB" >>/tmp/astra-offline-install.log 2>&1; then
        tail -25 /tmp/astra-offline-install.log >&2
        die "no se pudo instalar el paquete (log en /tmp/astra-offline-install.log)"
    fi
fi
info "instalado: $(dpkg-query -W -f='${Package} ${Version}' astra-webui 2>/dev/null || dpkg-query -W -f='${Package} ${Version}' astra-webui-panel 2>/dev/null || echo '?')"

if [ -z "$PANEL_PORT" ] && [ -r /etc/astra-webui/panel.json ]; then
    PANEL_PORT="$(python3 -c "
import json
try:
    print(json.load(open('/etc/astra-webui/panel.json')).get('port', 8000))
except Exception:
    print(8000)
" 2>/dev/null || echo 8000)"
fi
PANEL_PORT="${PANEL_PORT:-8000}"

# ---------------------------------------------------------------------------
# 7. arrancar
# ---------------------------------------------------------------------------

if [ "$DO_START" = "1" ]; then
    if [ "$HAS_SYSTEMD" = "1" ]; then
        log "arrancando los servicios"
        $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
        $SUDO systemctl enable --now astra.service astra-webui.service >/dev/null 2>&1 || \
            warn "systemctl falló; revisa: systemctl status astra astra-webui"
    else
        log "arrancando astra y el panel en segundo plano (sin systemd)"
        $SUDO pkill -f 'astra-webui' >/dev/null 2>&1 || true
        $SUDO pkill -f 'agent.lua' >/dev/null 2>&1 || true
        sleep 1
        $SUDO mkdir -p /var/log/astra /var/log/astra-webui
        $SUDO chown astra:astra /var/log/astra /var/log/astra-webui 2>/dev/null || true
        if [ -x /usr/bin/astra ]; then
            $SUDO bash -c 'nohup /usr/bin/astra /usr/share/astra-webui/agent/agent.lua /etc/astra-webui/agent.json >>/var/log/astra/astra.log 2>&1 &'
        else
            warn "/usr/bin/astra no existe: el panel arrancará en modo degradado"
        fi
        $SUDO bash -c 'PYTHONPATH=/usr/share/astra-webui/panel nohup python3 -m astra_webui --config /etc/astra-webui/panel.json >>/var/log/astra-webui/panel.log 2>&1 &'
    fi
fi

# ---------------------------------------------------------------------------
# 8. comprobar y resumir
# ---------------------------------------------------------------------------

HEALTH=""
if [ "$DO_START" = "1" ]; then
    log "comprobando el panel en http://127.0.0.1:$PANEL_PORT"
    for _ in $(seq 1 30); do
        HEALTH="$(curl -fsS "http://127.0.0.1:$PANEL_PORT/api/health" 2>/dev/null || true)"
        [ -n "$HEALTH" ] && break
        sleep 1
    done
    if [ -n "$HEALTH" ]; then
        info "el panel responde: $HEALTH"
    else
        warn "el panel no responde; revisa:"
        warn "  tail -n 40 /var/log/astra-webui/panel.log"
        [ "$HAS_SYSTEMD" = "1" ] && warn "  systemctl status astra astra-webui"
    fi
fi

PASSWORD=""
if [ -r /etc/astra-webui/initial-admin-password ]; then
    PASSWORD="$(cat /etc/astra-webui/initial-admin-password)"
elif [ -n "$SUDO" ]; then
    PASSWORD="$($SUDO cat /etc/astra-webui/initial-admin-password 2>/dev/null || true)"
fi
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<EOF

$(printf '\033[32m[offline] Instalación terminada\033[0m')

  Panel:      http://${IP:-<ip-del-servidor>}:$PANEL_PORT
  Usuario:    admin
  Contraseña: ${PASSWORD:-<mira /etc/astra-webui/initial-admin-password>}
  Streaming:  http://${IP:-<ip-del-servidor>}:8001/live/<token>/<canal>.ts

  Estado:
$([ "$HAS_SYSTEMD" = "1" ] && printf '    systemctl status astra astra-webui\n    journalctl -u astra -f\n')    tail -f /var/log/astra/astra.log

  Recuerda cambiar la contraseña de admin en Usuarios tras el primer acceso.

EOF
