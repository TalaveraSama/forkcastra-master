#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: run this installer as root" >&2
    exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PREFIX=${PREFIX:-/usr}
SYSCONFDIR=${SYSCONFDIR:-/etc}
LOCALSTATEDIR=${LOCALSTATEDIR:-/var}

if [ ! -x "$ROOT/astra" ]; then
    echo "Error: astra is not built. Run ./configure.sh && make first." >&2
    exit 1
fi

install -d -m 0755 "$PREFIX/bin" "$PREFIX/share/astra" "$SYSCONFDIR/astra"
install -m 0755 "$ROOT/astra" "$PREFIX/bin/astra"
install -m 0644 "$ROOT/scripts/stream.lua" "$PREFIX/share/astra/stream.lua"
install -m 0644 "$ROOT/scripts/analyze.lua" "$PREFIX/share/astra/analyze.lua"

if [ ! -f "$SYSCONFDIR/astra/astra.lua" ]; then
    install -m 0640 "$ROOT/deploy/astra.lua.example" "$SYSCONFDIR/astra/astra.lua"
fi

if command -v systemctl >/dev/null 2>&1; then
    if ! getent group astra >/dev/null 2>&1; then
        groupadd --system astra
    fi
    if ! getent passwd astra >/dev/null 2>&1; then
        useradd --system --gid astra --home-dir "$LOCALSTATEDIR/lib/astra" \
            --shell /usr/sbin/nologin --comment "Forkcastra service" astra
    fi
    install -d -o astra -g astra -m 0750 "$LOCALSTATEDIR/lib/astra" "$LOCALSTATEDIR/log/astra"
    install -m 0644 "$ROOT/deploy/astra.service" /etc/systemd/system/astra.service
    systemctl daemon-reload
    systemctl enable astra.service
    echo "Installed. Review $SYSCONFDIR/astra/astra.lua, then run: systemctl start astra"
else
    echo "Installed without systemd integration."
fi
