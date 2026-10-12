#!/usr/bin/env bash
# =============================================================================
#  Astra WebUI — desinstalador
# -----------------------------------------------------------------------------
#  Quita el panel, el agente y el núcleo que instaló el paquete astra-webui,
#  dejando siempre una copia de seguridad de los canales y los softcams.
#
#  Uso:
#     sudo bash desinstalar.sh                 # desinstala y guarda copia de datos
#     sudo bash desinstalar.sh --purge         # además borra datos y configuración
#     sudo bash desinstalar.sh --dry-run       # enseña lo que haría, sin tocar nada
#     sudo bash desinstalar.sh --backup /ruta  # copia de seguridad en otra ruta
#     sudo bash desinstalar.sh --sin-copia     # no hace copia (bajo tu cuenta y riesgo)
#
#  NO toca: ncam, oscam, pcscd, el driver TBS ni ninguna configuración de otros
#  programas. Sólo lo que pertenece a astra-webui.
#
#     curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/desinstalar.sh | sudo bash
# =============================================================================

set -uo pipefail

PAQUETE="astra-webui"
USUARIO="astra"
GRUPO="astra"
DIR_DATOS="/var/lib/astra-webui"
DIR_CONF="/etc/astra-webui"
DIR_INSTALADO="/usr/share/astra-webui"
SUDOERS="/etc/sudoers.d/astra-webui"
UNIDADES=(
  "/etc/systemd/system/astra-webui.service"
  "/etc/systemd/system/astra.service"
  "/etc/systemd/system/astra-webui.service.d"
  "/etc/systemd/system/astra.service.d"
  "/etc/systemd/system/multi-user.target.wants/astra-webui.service"
  "/etc/systemd/system/multi-user.target.wants/astra.service"
)

PURGAR=0
COPIA=1
RUTA_COPIA="/var/backups/astra-webui"
SECO=0
SI=0

rojo()  { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
aviso() { printf '\033[33m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }

ayuda() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --purge|--purgar)   PURGAR=1 ;;
    --keep-data|--conservar-datos) PURGAR=0 ;;
    --backup|--copia)   RUTA_COPIA="${2:-}"; shift ;;
    --backup=*)         RUTA_COPIA="${1#*=}" ;;
    --sin-copia)        COPIA=0 ;;
    --dry-run|--simular) SECO=1 ;;
    -y|--yes|--si)      SI=1 ;;
    -h|--help|--ayuda)  ayuda ;;
    *) rojo "opción no reconocida: $1"; exit 2 ;;
  esac
  shift
done

if [ "$(id -u)" != "0" ]; then
  rojo "hay que ejecutarlo como root:  sudo bash desinstalar.sh"
  exit 1
fi

if [ -n "$RUTA_COPIA" ] && [ "${RUTA_COPIA#/}" = "$RUTA_COPIA" ]; then
  rojo "la ruta de la copia debe ser absoluta: $RUTA_COPIA"
  exit 2
fi

echo
echo "  Desinstalador de Astra WebUI"
echo "  ============================"
echo

# ---------------------------------------------------------------- qué hay
VERSION="(no instalado)"
if dpkg -s "$PAQUETE" >/dev/null 2>&1; then
  VERSION="$(dpkg -s "$PAQUETE" 2>/dev/null | awk '/^Version:/ {print $2}')"
fi
info "paquete:            $VERSION"
info "datos:              $DIR_DATOS $([ -d "$DIR_DATOS" ] && echo "(existe)" || echo "(no está)")"
info "configuración:      $DIR_CONF $([ -d "$DIR_CONF" ] && echo "(existe)" || echo "(no está)")"
info "copia de seguridad: $([ "$COPIA" = 1 ] && echo "${RUTA_COPIA}/<fecha>.tar.gz" || echo "desactivada")"
info "modo:               $([ "$PURGAR" = 1 ] && echo "PURGAR (borra datos y configuración)" || echo "conservar datos y configuración")"
echo

PLAN=()
[ "$COPIA" = 1 ]  && PLAN+=("copia de $DIR_CONF y $DIR_DATOS en $RUTA_COPIA")
PLAN+=("parar y deshabilitar: astra-webui, astra")
PLAN+=("desinstalar el paquete $PAQUETE")
[ "$PURGAR" = 1 ] && PLAN+=("borrar $DIR_DATOS, $DIR_CONF, $SUDOERS, la unidad y el usuario $USUARIO")
[ "$PURGAR" = 0 ] && PLAN+=("dejar intactos los datos y la configuración")
PLAN+=("no tocar: ncam, oscam, pcscd, driver TBS")

echo "  Se va a hacer:"
for paso in "${PLAN[@]}"; do info "- $paso"; done
echo

if [ "$SECO" = 1 ]; then
  aviso "  --dry-run: no se ha tocado nada."
  exit 0
fi

if [ "$SI" != 1 ]; then
  printf '  ¿Seguimos? [s/N] '
  read -r respuesta < /dev/tty || respuesta="n"
  case "$respuesta" in
    s|S|si|SI|y|Y) ;;
    *) aviso "  cancelado."; exit 0 ;;
  esac
fi

# ---------------------------------------------------------------- copia
if [ "$COPIA" = 1 ]; then
  marca="$(date +%Y%m%d-%H%M%S)"
  destino="$RUTA_COPIA/$marca"
  mkdir -p "$destino"
  algo=0
  if [ -d "$DIR_CONF" ]; then
    tar -czf "$destino/etc-astra-webui.tar.gz" -C "$(dirname "$DIR_CONF")" "$(basename "$DIR_CONF")" 2>/dev/null && algo=1
  fi
  if [ -d "$DIR_DATOS" ]; then
    tar -czf "$destino/var-lib-astra-webui.tar.gz" -C "$(dirname "$DIR_DATOS")" "$(basename "$DIR_DATOS")" 2>/dev/null && algo=1
  fi
  if [ "$algo" = 1 ]; then
    verde "  copia guardada en $destino"
    ls -1 "$destino" | sed 's/^/    /'
  else
    aviso "  no había nada que copiar"
  fi
fi

# ---------------------------------------------------------------- servicios
for unidad in astra-webui astra; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^$unidad.service"; then
    info "parando $unidad…"
    systemctl stop "$unidad" 2>/dev/null || true
    systemctl disable "$unidad" 2>/dev/null || true
  fi
done
systemctl daemon-reload 2>/dev/null || true

# ---------------------------------------------------------------- paquete
if dpkg -s "$PAQUETE" >/dev/null 2>&1; then
  info "desinstalando el paquete…"
  if [ "$PURGAR" = 1 ]; then
    DEBIAN_FRONTEND=noninteractive apt-get -y purge "$PAQUETE" >/dev/null 2>&1 || \
      dpkg --purge "$PAQUETE" >/dev/null 2>&1 || true
  else
    DEBIAN_FRONTEND=noninteractive apt-get -y remove "$PAQUETE" >/dev/null 2>&1 || \
      dpkg --remove "$PAQUETE" >/dev/null 2>&1 || true
  fi
  DEBIAN_FRONTEND=noninteractive apt-get -y autoremove --purge >/dev/null 2>&1 || true
else
  info "el paquete no está instalado; se limpian los restos"
fi

# ---------------------------------------------------------------- restos
borrar() {
  # Sólo se borra lo que empieza por una de las rutas permitidas
  local objetivo="$1" permitido
  case "$objetivo" in
    "$DIR_DATOS"|"$DIR_DATOS"/*|"$DIR_CONF"|"$DIR_CONF"/*|"$SUDOERS"|\
    "$DIR_INSTALADO"|"$DIR_INSTALADO"/*|/etc/systemd/system/*astra*|\
    /var/backups/astra-webui|"$RUTA_COPIA") permitido=1 ;;
    *) permitido=0 ;;
  esac
  if [ "$permitido" != 1 ]; then
    aviso "  (omitido por seguridad: $objetivo)"
    return
  fi
  if [ -e "$objetivo" ] || [ -L "$objetivo" ]; then
    rm -rf --one-file-system "$objetivo"
    info "borrado: $objetivo"
  fi
}

if [ "$PURGAR" = 1 ]; then
  for unidad in "${UNIDADES[@]}"; do borrar "$unidad"; done
  borrar "$SUDOERS"
  borrar "$DIR_DATOS"
  borrar "$DIR_CONF"
  borrar "$DIR_INSTALADO"
  systemctl daemon-reload 2>/dev/null || true
  systemctl reset-failed 2>/dev/null || true

  if id "$USUARIO" >/dev/null 2>&1; then
    if [ -z "$(pgrep -u "$USUARIO" 2>/dev/null)" ]; then
      userdel "$USUARIO" 2>/dev/null && info "usuario $USUARIO borrado" || aviso "no se pudo borrar el usuario $USUARIO"
    else
      aviso "el usuario $USUARIO tiene procesos vivos; no se borra"
    fi
  fi
  if getent group "$GRUPO" >/dev/null 2>&1; then
    groupdel "$GRUPO" 2>/dev/null && info "grupo $GRUPO borrado" || true
  fi
else
  # Sin purgar: fuera sólo lo que no son datos del usuario
  for unidad in "${UNIDADES[@]}"; do borrar "$unidad"; done
  if [ -f "$SUDOERS" ]; then borrar "$SUDOERS"; fi
  systemctl daemon-reload 2>/dev/null || true
fi

# ---------------------------------------------------------------- resumen
echo
verde "  Desinstalación terminada."
echo
if [ "$PURGAR" = 1 ]; then
  info "datos y configuración borrados"
else
  info "se han conservado:"
  [ -d "$DIR_CONF" ]  && info "  $DIR_CONF  (configuración del panel)"
  [ -d "$DIR_DATOS" ] && info "  $DIR_DATOS (canales, CAM, softcams)"
  info "  para volver a empezar de cero:  sudo bash desinstalar.sh --purge"
fi
if [ "$COPIA" = 1 ]; then
  info "copia de seguridad: $RUTA_COPIA"
  info "  restaurar a mano:  tar -xzf <copia>/var-lib-astra-webui.tar.gz -C /var/lib"
fi
echo
info "no se ha tocado: ncam, oscam, pcscd, driver TBS ni otros programas"
info "para volver a instalar:"
info "  curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/instalar.sh | sudo bash"
echo
