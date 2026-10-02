#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
OUT=${1:-"$ROOT/packages"}
ARCH=${ARCH:-$(dpkg --print-architecture)}
MAJOR=$(sed -n 's/^#define ASTRA_VERSION_MAJOR \([0-9][0-9]*\)$/\1/p' "$ROOT/version.h")
MINOR=$(sed -n 's/^#define ASTRA_VERSION_MINOR \([0-9][0-9]*\)$/\1/p' "$ROOT/version.h")
DEV=$(sed -n 's/^#define ASTRA_VERSION_DEV \([0-9][0-9]*\)$/\1/p' "$ROOT/version.h")
VERSION=${VERSION:-"$MAJOR.$MINOR.$DEV-3"}
PKG="forkcastra_${VERSION}_${ARCH}"
STAGE="$OUT/.stage-$PKG"
DEB="$OUT/$PKG.deb"

command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required" >&2; exit 1; }

if [ "${SKIP_BUILD:-0}" != 1 ]; then
    (cd "$ROOT" && { make distclean >/dev/null 2>&1 || true; } && ./configure.sh --portable && make -j"${JOBS:-2}" && ./tests/smoke.sh)
fi
[ -x "$ROOT/astra" ] || { echo "astra build output is missing" >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/forkcastra" "$STAGE/usr/share/forkcastra/panel" \
    "$STAGE/usr/share/doc/forkcastra" "$STAGE/etc/forkcastra" \
    "$STAGE/lib/systemd/system" "$STAGE/var/lib/forkcastra" "$STAGE/var/log/forkcastra" "$OUT"

install -m 0755 "$ROOT/astra" "$STAGE/usr/bin/forkcastra"
install -m 0755 "$ROOT/panel/forkcastra_panel.py" "$STAGE/usr/bin/forkcastra-panel"
install -m 0644 "$ROOT/scripts/stream.lua" "$STAGE/usr/share/forkcastra/stream.lua"
install -m 0644 "$ROOT/scripts/analyze.lua" "$STAGE/usr/share/forkcastra/analyze.lua"
install -m 0640 "$ROOT/deploy/forkcastra.lua.example" "$STAGE/etc/forkcastra/forkcastra.lua"
install -m 0644 "$ROOT/deploy/forkcastra.service" "$STAGE/lib/systemd/system/forkcastra.service"
install -m 0644 "$ROOT/deploy/forkcastra-panel.service" "$STAGE/lib/systemd/system/forkcastra-panel.service"
install -m 0644 "$ROOT/panel/static/"* "$STAGE/usr/share/forkcastra/panel/"
install -m 0644 "$ROOT/COPYING" "$STAGE/usr/share/doc/forkcastra/copyright"
install -m 0644 "$ROOT/README.md" "$STAGE/usr/share/doc/forkcastra/README.md"
install -m 0644 "$ROOT/docs/AUDITORIA_MODERNIZACION.md" "$STAGE/usr/share/doc/forkcastra/AUDITORIA_MODERNIZACION.md"
install -m 0644 "$ROOT/docs/RELEASE_4.0.282-3.md" "$STAGE/usr/share/doc/forkcastra/RELEASE.md"

INSTALLED_SIZE=$(du -sk "$STAGE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: forkcastra
Version: $VERSION
Section: net
Priority: optional
Architecture: $ARCH
Maintainer: Forkcastra contributors
Depends: libc6 (>= 2.31), python3 (>= 3.8), adduser, systemd | systemd-sysv
Installed-Size: $INSTALLED_SIZE
Homepage: https://github.com/TalaveraSama/forkcastra-master
Description: IPTV MPEG-TS processing engine based on Astra
 Forkcastra processes DVB, UDP, HTTP, file and MPEG-TS streams and includes
 a restricted systemd service suitable for Ubuntu Server.
EOF

printf '%s\n' '/etc/forkcastra/forkcastra.lua' > "$STAGE/DEBIAN/conffiles"
install -m 0755 "$ROOT/packaging/postinst" "$STAGE/DEBIAN/postinst"
install -m 0755 "$ROOT/packaging/prerm" "$STAGE/DEBIAN/prerm"
install -m 0755 "$ROOT/packaging/postrm" "$STAGE/DEBIAN/postrm"

find "$STAGE" -type d -exec chmod 0755 {} +
dpkg-deb --root-owner-group --build "$STAGE" "$DEB"
rm -rf "$STAGE"
echo "$DEB"
