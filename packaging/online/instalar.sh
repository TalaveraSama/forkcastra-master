#!/bin/bash
# Astra WebUI — instalador en línea (descarga el paquete desde GitHub y lo instala).
#
#   curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/instalar.sh | sudo bash
#
#   sudo ./instalar.sh                     # última release
#   sudo ./instalar.sh --tag v4.0.282-10   # una release concreta
#   sudo ./instalar.sh --deb /tmp/x.deb    # un .deb ya descargado
#   sudo ./instalar.sh --offline paquete.tar.gz   # paquete sin Internet
#   ./instalar.sh --check                  # sólo comprobar (no requiere root)
#
# Hace lo mismo que el instalador de Astra/Cesbo: detecta el sistema, descarga el
# paquete, comprueba la suma SHA-256, instala con dpkg/apt, arranca los servicios y
# muestra la dirección y la contraseña inicial.

set -u

REPO="TalaveraSama/forkcastra-master"
TAG=""
DEB_FILE=""
OFFLINE=""
DO_START=1
CHECK_ONLY=0
KEEP=0

log()  { printf '\033[1m[astra]\033[0m %s\n' "$*"; }
info() { printf '  · %s\n' "$*"; }
warn() { printf '\033[33m[astra] AVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[astra] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag)      TAG="$2"; shift 2 ;;
        --tag=*)    TAG="${1#*=}"; shift ;;
        --repo)     REPO="$2"; shift 2 ;;
        --repo=*)   REPO="${1#*=}"; shift ;;
        --deb)      DEB_FILE="$2"; shift 2 ;;
        --deb=*)    DEB_FILE="${1#*=}"; shift ;;
        --offline)  OFFLINE="$2"; shift 2 ;;
        --offline=*) OFFLINE="${1#*=}"; shift ;;
        --no-start) DO_START=0; shift ;;
        --check)    CHECK_ONLY=1; shift ;;
        --keep)     KEEP=1; shift ;;
        -h|--help)  usage ;;
        *) die "opción desconocida: $1 (usa --help)" ;;
    esac
done

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if [ "$CHECK_ONLY" = "1" ]; then SUDO=""
    elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"
    else die "hay que ejecutarlo como root: sudo $0"; fi
fi

# ---------------------------------------------------------------------------
# 1. sistema
# ---------------------------------------------------------------------------

log "comprobando el sistema"
[ "$(uname -s)" = "Linux" ] || die "esto es un paquete Linux (detectado $(uname -s))"
command -v dpkg >/dev/null 2>&1 || die "no existe dpkg: este paquete es para Ubuntu/Debian"

if [ -r /etc/os-release ]; then
    . /etc/os-release
    info "${PRETTY_NAME:-$NAME}"
fi
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
info "arquitectura: $ARCH"

for tool in curl wget; do
    command -v "$tool" >/dev/null 2>&1 && { DOWNLOADER="$tool"; break; }
done
[ -n "${DOWNLOADER:-}" ] || die "hace falta curl o wget para descargar el paquete"
info "descargador: $DOWNLOADER"

WORK="$(mktemp -d)"
cleanup() { [ "$KEEP" = "1" ] || rm -rf "$WORK"; }
trap cleanup EXIT
info "carpeta de trabajo: $WORK"

fetch() {
    # $1 = url, $2 = destino
    if [ "$DOWNLOADER" = "curl" ]; then
        curl -fsSL --retry 3 -o "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

BASE_URL="https://github.com/$REPO/releases"
if [ -n "$TAG" ]; then
    REL_URL="$BASE_URL/download/$TAG"
    info "release: $TAG"
else
    REL_URL="$BASE_URL/latest/download"
    info "release: la última publicada"
fi

# ---------------------------------------------------------------------------
# 2. paquete sin Internet (si se indica)
# ---------------------------------------------------------------------------

if [ -n "$OFFLINE" ]; then
    [ -f "$OFFLINE" ] || die "no existe $OFFLINE"
    log "usando el paquete sin Internet $OFFLINE"
    tar -xzf "$OFFLINE" -C "$WORK" || die "no se pudo descomprimir $OFFLINE"
    INSTALLER="$(find "$WORK" -maxdepth 2 -name instalar-offline.sh | head -1)"
    [ -n "$INSTALLER" ] || die "el paquete no trae instalar-offline.sh"
    [ "$CHECK_ONLY" = "1" ] && { info "se ejecutaría: $INSTALLER --check"; exit 0; }
    log "ejecutando el instalador sin Internet"
    if [ "$DO_START" = "1" ]; then
        $SUDO bash "$INSTALLER"
    else
        $SUDO bash "$INSTALLER" --no-start
    fi
    exit $?
fi

# ---------------------------------------------------------------------------
# 3. descargar el .deb
# ---------------------------------------------------------------------------

if [ -z "$DEB_FILE" ]; then
    log "buscando el paquete para $ARCH en la release"
    DEB_NAME="astra-webui_${ARCH}.deb"
    # el nombre real lleva la versión del núcleo: se consulta la API pública
    API_URL="https://api.github.com/repos/$REPO/releases"
    if [ -n "$TAG" ]; then
        API_URL="$API_URL/tags/$TAG"
    else
        API_URL="$API_URL/latest"
    fi
    if command -v python3 >/dev/null 2>&1; then
        fetch "$API_URL" "$WORK/release.json" || die "no se pudo consultar la release"
        DEB_NAME="$(python3 - "$WORK/release.json" "$ARCH" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
arch = sys.argv[2]
# la más específica primero: astra-webui_<versión>_<arch>.deb
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if name.startswith("astra-webui_") and name.endswith("_%s.deb" % arch):
        print(name)
        break
else:
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("astra-webui-panel_") and name.endswith("_all.deb"):
            print(name)
            break
PY
)"
        [ -n "$DEB_NAME" ] || die "la release no tiene paquete para $ARCH (¿ya se publicó?)"
        info "paquete elegido: $DEB_NAME"
        if [ "$DEB_NAME" = "astra-webui-panel_1.0.0-1_all.deb" ]; then
            warn "sólo hay paquete del panel: instala el panel pero NO el binario astra"
            warn "lanza el workflow 'release' en GitHub para que se genere el completo"
        fi
        DEB_FILE="$WORK/$DEB_NAME"
        fetch "$REL_URL/$DEB_NAME" "$DEB_FILE" || die "no se pudo descargar $DEB_NAME"
        if fetch "$REL_URL/SHA256SUMS" "$WORK/SHA256SUMS" 2>/dev/null; then
            log "comprobando la suma SHA-256"
            expected="$(grep " $DEB_NAME\$" "$WORK/SHA256SUMS" | awk '{print $1}')"
            if [ -n "$expected" ] && command -v sha256sum >/dev/null 2>&1; then
                real="$(sha256sum "$DEB_FILE" | awk '{print $1}')"
                [ "$expected" = "$real" ] || die "la suma no coincide (esperado $expected, obtenido $real)"
                info "suma correcta"
            else
                warn "no se pudo comprobar la suma"
            fi
        fi
    else
        # sin python3: se descarga el nombre del paquete del panel (el completo lleva versión)
        warn "no hay python3: se descarga el paquete del panel"
        DEB_NAME="astra-webui-panel_1.0.0-1_all.deb"
        DEB_FILE="$WORK/$DEB_NAME"
        fetch "$REL_URL/$DEB_NAME" "$DEB_FILE" || die "no se pudo descargar $DEB_NAME"
    fi
else
    [ -f "$DEB_FILE" ] || die "no existe $DEB_FILE"
    info "usando el paquete local $DEB_FILE"
fi

log "paquete: $(basename "$DEB_FILE") ($(du -h "$DEB_FILE" | cut -f1))"

if [ "$CHECK_ONLY" = "1" ]; then
    log "modo comprobación: no se instala nada"
    dpkg-deb -I "$DEB_FILE" | sed -n '1,12p'
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. instalar
# ---------------------------------------------------------------------------

log "instalando"
if ! $SUDO dpkg -i "$DEB_FILE" > "$WORK/install.log" 2>&1; then
    warn "faltan dependencias: se intentan resolver con apt"
    $SUDO apt-get -f install -y >> "$WORK/install.log" 2>&1 || true
    if ! $SUDO dpkg -i "$DEB_FILE" >> "$WORK/install.log" 2>&1; then
        tail -25 "$WORK/install.log" >&2
        die "no se pudo instalar (log en $WORK/install.log; usa --keep para conservarlo)"
    fi
fi
info "instalado $(dpkg-query -W -f='${Package} ${Version}' astra-webui 2>/dev/null \
    || dpkg-query -W -f='${Package} ${Version}' astra-webui-panel 2>/dev/null || echo '')"

PORT="8000"
if [ -r /etc/astra-webui/panel.json ] && command -v python3 >/dev/null 2>&1; then
    PORT="$(python3 -c "
import json
try:
    print(json.load(open('/etc/astra-webui/panel.json')).get('port', 8000))
except Exception:
    print(8000)
")"
fi

# ---------------------------------------------------------------------------
# 5. arrancar y resumir
# ---------------------------------------------------------------------------

if [ "$DO_START" = "1" ] && [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" = "systemd" ]; then
    log "arrancando los servicios"
    $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
    $SUDO systemctl enable --now astra.service astra-webui.service >/dev/null 2>&1 || \
        warn "systemctl falló; revisa: systemctl status astra astra-webui"
    for _ in $(seq 1 30); do
        health="$(curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)"
        [ -n "$health" ] && break
        sleep 1
    done
    [ -n "${health:-}" ] && info "el panel responde: $health" || warn "el panel no responde todavía"
elif [ "$DO_START" = "1" ]; then
    warn "systemd no está activo: arranca astra y el panel a mano (mira docs/INSTALL.md)"
fi

PASSWORD=""
if [ -r /etc/astra-webui/initial-admin-password ]; then
    PASSWORD="$(cat /etc/astra-webui/initial-admin-password)"
elif [ -n "$SUDO" ]; then
    PASSWORD="$($SUDO cat /etc/astra-webui/initial-admin-password 2>/dev/null || true)"
fi
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<EOF

$(printf '\033[32m[astra] Instalación terminada\033[0m')

  Panel:      http://${IP:-<ip-del-servidor>}:$PORT
  Usuario:    admin
  Contraseña: ${PASSWORD:-<mira /etc/astra-webui/initial-admin-password>}
  Streaming:  http://${IP:-<ip-del-servidor>}:8001/live/<token>/<canal>.ts

  systemctl status astra astra-webui
  journalctl -u astra -f
  tail -f /var/log/astra/astra.log

  Cambia la contraseña de admin en Usuarios tras el primer acceso.

EOF
