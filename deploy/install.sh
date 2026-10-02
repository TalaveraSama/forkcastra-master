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

install -d -m 0755 "$PREFIX/bin" "$PREFIX/share/forkcastra" "$SYSCONFDIR/forkcastra"
install -m 0755 "$ROOT/astra" "$PREFIX/bin/forkcastra"
install -m 0644 "$ROOT/scripts/stream.lua" "$PREFIX/share/forkcastra/stream.lua"
install -m 0644 "$ROOT/scripts/analyze.lua" "$PREFIX/share/forkcastra/analyze.lua"

if [ ! -f "$SYSCONFDIR/forkcastra/forkcastra.lua" ]; then
    install -m 0640 "$ROOT/deploy/forkcastra.lua.example" "$SYSCONFDIR/forkcastra/forkcastra.lua"
fi

if command -v systemctl >/dev/null 2>&1; then
    if ! getent group forkcastra >/dev/null 2>&1; then
        groupadd --system forkcastra
    fi
    if ! getent passwd forkcastra >/dev/null 2>&1; then
        useradd --system --gid forkcastra --home-dir "$LOCALSTATEDIR/lib/forkcastra" \
            --shell /usr/sbin/nologin --comment "Forkcastra service" forkcastra
    fi
    install -d -o forkcastra -g forkcastra -m 0750 "$LOCALSTATEDIR/lib/forkcastra" "$LOCALSTATEDIR/log/forkcastra"
    install -m 0644 "$ROOT/deploy/forkcastra.service" /etc/systemd/system/forkcastra.service
    systemctl daemon-reload
    systemctl enable forkcastra.service
    echo "Installed. Review $SYSCONFDIR/forkcastra/forkcastra.lua, then run: systemctl start forkcastra"
else
    echo "Installed without systemd integration."
fi
