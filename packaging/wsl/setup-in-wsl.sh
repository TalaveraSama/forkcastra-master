#!/usr/bin/env bash
# Astra WebUI — instalación y verificación automática DENTRO de WSL (o en cualquier
# Ubuntu/Debian): instala dependencias, compila el núcleo del fork, construye el
# .deb, lo instala, arranca los servicios y hace una prueba end-to-end real.
#
#   # dentro de WSL (como root o con sudo):
#   bash packaging/wsl/setup-in-wsl.sh --project /root/astra-webui
#   bash packaging/wsl/setup-in-wsl.sh --check          # sólo comprueba requisitos
#
#   --project DIR   raíz del proyecto astra-webui (por defecto, dos niveles arriba)
#   --fork DIR      código del fork (por defecto $PROJECT/repo/tree o se clona)
#   --out DIR       salida de los .deb (por defecto $PROJECT/dist)
#   --port N        puerto del panel (por defecto, el de panel.json: 8000)
#   --skip-deps     no ejecutar apt-get
#   --no-demo       no lanzar la prueba end-to-end
#   --no-start      construir e instalar, pero no arrancar los servicios
#   --check         comprobar requisitos y salir

set -euo pipefail

PROJECT_DIR=""
FORK_DIR=""
OUT_DIR=""
PANEL_PORT=""
SKIP_DEPS=0
WITH_DEMO=1
DO_START=1
CHECK_ONLY=0

log()  { printf '\033[1m[astra-webui]\033[0m %s\n' "$*"; }
info() { printf '  · %s\n' "$*"; }
warn() { printf '\033[33m[astra-webui] AVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[astra-webui] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --project)   PROJECT_DIR="$2"; shift 2 ;;
        --project=*) PROJECT_DIR="${1#*=}"; shift ;;
        --fork)      FORK_DIR="$2"; shift 2 ;;
        --fork=*)    FORK_DIR="${1#*=}"; shift ;;
        --out)       OUT_DIR="$2"; shift 2 ;;
        --out=*)     OUT_DIR="${1#*=}"; shift ;;
        --port)      PANEL_PORT="$2"; shift 2 ;;
        --port=*)    PANEL_PORT="${1#*=}"; shift ;;
        --skip-deps) SKIP_DEPS=1; shift ;;
        --no-demo)   WITH_DEMO=0; shift ;;
        --no-start)  DO_START=0; shift ;;
        --check)     CHECK_ONLY=1; shift ;;
        -h|--help)   usage ;;
        *) die "opción desconocida: $1 (usa --help)" ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -n "$PROJECT_DIR" ] || PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
[ -n "$OUT_DIR" ] || OUT_DIR="$PROJECT_DIR/dist"

# ---------------------------------------------------------------------------
# 1. requisitos
# ---------------------------------------------------------------------------

log "comprobando el entorno"
[ "$(uname -s)" = "Linux" ] || die "este script es para Linux/WSL (detectado: $(uname -s))"

if grep -qi microsoft /proc/version 2>/dev/null; then
    info "WSL detectado: $(grep -o 'microsoft.*' /proc/version 2>/dev/null | head -1)"
else
    info "Linux nativo: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -r)"
fi

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    die "se necesita root o sudo (ejecuta: su -c 'bash $0')"
fi
info "usuario: $(id -un)${SUDO:+ (con sudo)}"

if [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" = "systemd" ]; then
    HAS_SYSTEMD=1
    info "systemd activo: los servicios se gestionarán con systemctl"
else
    HAS_SYSTEMD=0
    info "systemd NO activo (habitual en WSL1): los procesos se arrancarán a mano"
fi

[ -f "$PROJECT_DIR/packaging/build-deb.sh" ] || \
    die "no encuentro el proyecto en $PROJECT_DIR (usa --project /ruta/astra-webui)"
[ -f "$PROJECT_DIR/tools/make_test_ts.py" ] || warn "falta tools/make_test_ts.py (la demo no podrá generar el TS)"

if [ -n "$FORK_DIR" ]; then
    [ -f "$FORK_DIR/version.h" ] || die "--fork $FORK_DIR no parece el código del fork"
elif [ -f "$PROJECT_DIR/repo/tree/version.h" ]; then
    FORK_DIR="$PROJECT_DIR/repo/tree"
elif [ -f "$PROJECT_DIR/../forkcastra-master/version.h" ]; then
    FORK_DIR="$(cd "$PROJECT_DIR/../forkcastra-master" && pwd)"
fi

if [ "$CHECK_ONLY" = "1" ]; then
    log "modo comprobación: no se cambia nada"
    info "proyecto:   $PROJECT_DIR"
    info "fork:       ${FORK_DIR:-<se clonará al construir>}"
    info "salida:     $OUT_DIR"
    info "dependencias a instalar: build-essential python3 ca-certificates curl git"
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. dependencias
# ---------------------------------------------------------------------------

if [ "$SKIP_DEPS" = "0" ]; then
    log "instalando dependencias (build-essential, python3, git…)"
    $SUDO apt-get update -qq
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y --no-install-recommends \
        build-essential python3 ca-certificates curl git file >/dev/null
    info "dependencias listas"
else
    for tool in gcc make python3; do
        command -v "$tool" >/dev/null 2>&1 || die "falta '$tool' y se pidió --skip-deps"
    done
fi

# ---------------------------------------------------------------------------
# 3. código del fork
# ---------------------------------------------------------------------------

if [ -z "$FORK_DIR" ]; then
    FORK_DIR="$HOME/forkcastra-master"
    if [ -f "$FORK_DIR/version.h" ]; then
        info "usando el fork ya clonado en $FORK_DIR"
    else
        log "clonando el fork en $FORK_DIR"
        git clone --depth 1 https://github.com/TalaveraSama/forkcastra-master.git "$FORK_DIR" >/dev/null 2>&1 || \
            die "no se pudo clonar el fork (sin red?): copia el código y usa --fork DIR"
    fi
fi
[ -f "$FORK_DIR/version.h" ] || die "el fork en $FORK_DIR no tiene version.h"
VERSION="$(sed -n 's/.*ASTRA_VERSION_MAJOR \([0-9]*\).*/\1/p' "$FORK_DIR/version.h").$(sed -n 's/.*ASTRA_VERSION_MINOR \([0-9]*\).*/\1/p' "$FORK_DIR/version.h").$(sed -n 's/.*ASTRA_VERSION_DEV \([0-9]*\).*/\1/p' "$FORK_DIR/version.h")"
info "Astra $VERSION en $FORK_DIR"

# ---------------------------------------------------------------------------
# 4. construir el .deb
# ---------------------------------------------------------------------------

log "compilando el núcleo y construyendo el paquete (puede tardar un par de minutos)"
"$PROJECT_DIR/packaging/build-deb.sh" --source "$FORK_DIR" --out "$OUT_DIR" >/tmp/astra-webui-build.log 2>&1 || {
    tail -30 /tmp/astra-webui-build.log >&2
    die "la construcción falló (log completo en /tmp/astra-webui-build.log)"
}
DEB="$(ls -t "$OUT_DIR"/astra-webui_*.deb 2>/dev/null | head -1)"
[ -n "$DEB" ] || die "no se generó ningún .deb en $OUT_DIR"
info "paquete: $DEB"

# ---------------------------------------------------------------------------
# 5. instalar
# ---------------------------------------------------------------------------

log "instalando $DEB"
$SUDO apt-get install -y "$DEB" >/tmp/astra-webui-install.log 2>&1 || {
    tail -30 /tmp/astra-webui-install.log >&2
    die "la instalación falló (log completo en /tmp/astra-webui-install.log)"
}
info "instalado: $(dpkg-query -W -f='${Package} ${Version}' astra-webui 2>/dev/null || echo astra-webui)"

[ -n "$PANEL_PORT" ] || PANEL_PORT="$(python3 - <<'PY' 2>/dev/null || echo 8000
import json
try:
    print(json.load(open('/etc/astra-webui/panel.json')).get('port', 8000))
except Exception:
    print(8000)
PY
)"

# ---------------------------------------------------------------------------
# 6. arrancar
# ---------------------------------------------------------------------------

if [ "$DO_START" = "1" ]; then
    if [ "$HAS_SYSTEMD" = "1" ]; then
        log "arrancando los servicios con systemd"
        $SUDO systemctl enable --now astra.service astra-webui.service >/dev/null 2>&1 || \
            warn "systemctl falló; revisa: systemctl status astra astra-webui"
        sleep 2
    else
        log "arrancando astra y el panel en segundo plano (sin systemd)"
        $SUDO pkill -f 'astra-webui' >/dev/null 2>&1 || true
        $SUDO pkill -f 'agent.lua' >/dev/null 2>&1 || true
        sleep 1
        $SUDO mkdir -p /var/log/astra /var/log/astra-webui
        $SUDO chown astra:astra /var/log/astra /var/log/astra-webui 2>/dev/null || true
        $SUDO bash -c 'nohup /usr/bin/astra /usr/share/astra-webui/agent/agent.lua /etc/astra-webui/agent.json >>/var/log/astra/astra.log 2>&1 & echo $! > /run/astra.pid'
        $SUDO bash -c 'PYTHONPATH=/usr/share/astra-webui/panel nohup python3 -m astra_webui --config /etc/astra-webui/panel.json >>/var/log/astra-webui/panel.log 2>&1 & echo $! > /run/astra-webui.pid'
        sleep 3
        info "astra pid $(cat /run/astra.pid 2>/dev/null || echo '?'), panel pid $(cat /run/astra-webui.pid 2>/dev/null || echo '?')"
    fi
fi

# ---------------------------------------------------------------------------
# 7. comprobar
# ---------------------------------------------------------------------------

log "comprobando el panel en http://127.0.0.1:$PANEL_PORT"
HEALTH=""
for _ in $(seq 1 30); do
    HEALTH="$(curl -fsS "http://127.0.0.1:$PANEL_PORT/api/health" 2>/dev/null || true)"
    [ -n "$HEALTH" ] && break
    sleep 1
done
if [ -n "$HEALTH" ]; then
    info "el panel responde: $HEALTH"
else
    warn "el panel no responde. Revisa los logs:"
    warn "  tail -n 40 /var/log/astra-webui/panel.log"
    warn "  tail -n 40 /var/log/astra/astra.log"
    [ "$HAS_SYSTEMD" = "1" ] && warn "  systemctl status astra astra-webui"
fi

PASSWORD=""
if [ -r /etc/astra-webui/initial-admin-password ]; then
    PASSWORD="$(cat /etc/astra-webui/initial-admin-password)"
elif [ -n "$SUDO" ]; then
    PASSWORD="$($SUDO cat /etc/astra-webui/initial-admin-password 2>/dev/null || true)"
fi

AGENT_HEALTH="$(TOKEN="$($SUDO cat /etc/astra-webui/agent.token 2>/dev/null || true)" \
    curl -fsS -H "X-Astra-Token: $TOKEN" http://127.0.0.1:9977/health 2>/dev/null || true)"
if [ -n "$AGENT_HEALTH" ]; then
    info "el agente Lua responde: $AGENT_HEALTH"
else
    warn "el agente Lua (127.0.0.1:9977) no responde; el panel funcionará en modo degradado"
fi

# ---------------------------------------------------------------------------
# 8. prueba end-to-end (canal de prueba + streaming real)
# ---------------------------------------------------------------------------

if [ "$WITH_DEMO" = "1" ] && [ -n "$PASSWORD" ] && [ -n "$HEALTH" ]; then
    log "prueba end-to-end: canal de prueba con un MPEG-TS generado y descarga del TS"
    if python3 "$PROJECT_DIR/tools/astra_demo.py" --panel "http://127.0.0.1:$PANEL_PORT" \
            --password "$PASSWORD" --ts /var/lib/astra-webui/demo.ts; then
        info "prueba end-to-end correcta"
    else
        warn "la prueba end-to-end falló (mira el detalle arriba)"
    fi
elif [ "$WITH_DEMO" = "1" ]; then
    warn "no se pudo lanzar la prueba end-to-end (falta la contraseña inicial o el panel no responde)"
fi

# ---------------------------------------------------------------------------
# 9. resumen
# ---------------------------------------------------------------------------

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if [ "$HAS_SYSTEMD" = "1" ]; then
    SERVICE_HELP="    systemctl status astra astra-webui
    systemctl restart astra astra-webui
    journalctl -u astra -f
    tail -f /var/log/astra/astra.log"
else
    SERVICE_HELP="    tail -f /var/log/astra/astra.log
    tail -f /var/log/astra-webui/panel.log
    pkill -f agent.lua ; pkill -f astra-webui        # parar
    bash packaging/wsl/setup-in-wsl.sh --no-deps     # volver a arrancar"
fi

cat <<EOF

$(printf '\033[32m[astra-webui] Instalación terminada\033[0m')

  Panel:      http://127.0.0.1:$PANEL_PORT   (desde WSL: http://${IP:-<ip-wsl>}:$PANEL_PORT)
  Usuario:    admin
  Contraseña: ${PASSWORD:-<mira /etc/astra-webui/initial-admin-password>}
  Streaming:  http://${IP:-<ip-wsl>}:8001/live/<token>/<canal>.ts

  Servicios:
$SERVICE_HELP

  Para abrir el panel desde el navegador de Windows (WSL2 usa red NAT),
  ejecuta esto en PowerShell COMO ADMINISTRADOR:

    netsh interface portproxy add v4tov4 listenport=$PANEL_PORT listenaddress=0.0.0.0 \`
        connectport=$PANEL_PORT connectaddress=${IP:-<ip-wsl>}
    New-NetFirewallRule -DisplayName "Astra WebUI" -Direction Inbound \`
        -LocalPort $PANEL_PORT,8001 -Protocol TCP -Action Allow

  y abre http://localhost:$PANEL_PORT

  DVB: con WSL2 se pasa un sintonizador USB con usbipd-win
       (winget install usbipd; usbipd list; usbipd attach --wsl --busid X-Y).
       En WSL1 no hay DVB (el módulo dvb del fork es Linux con /dev/dvb).

EOF

