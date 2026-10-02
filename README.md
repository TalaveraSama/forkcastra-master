# Description

Astra is a high-customizable software to processing IPTV streams.
Source code resides here is a fork with custom patches.
Original upstream version can be pulled from here:
https://bitbucket.org/cesbo/astra/

Astra consists of the following components:

*   Core is an API to communicate with the operation system. Astra is a
    cross-platform software that supports:
    OS X, Linux (any distributives), BSD, Windows
*   Modules is a set of high-performance units that
    carries out specific functions
*   Lua is a scripting language to build a business logic for applications

## Build on Ubuntu Server

Supported targets are Ubuntu 20.04, 22.04 and 24.04 on amd64. The CI workflow
builds and smoke-tests all three releases.

```sh
sudo apt-get update
sudo apt-get install build-essential libssl-dev
./configure.sh
make -j"$(nproc)"
./tests/smoke.sh
```

The OpenSSL development package is optional; without it the legacy `newcamd`
module is disabled. To install the engine and its hardened systemd service:

```sh
sudo ./deploy/install.sh
sudoedit /etc/forkcastra/forkcastra.lua
sudo systemctl start forkcastra
sudo systemctl status forkcastra
```

To create an installable `.deb` after building:

```sh
./packaging/build-deb.sh
sudo apt install ./packages/forkcastra_*.deb
sudoedit /etc/forkcastra/forkcastra.lua
sudo systemctl start forkcastra
```

The package installs as `/usr/bin/forkcastra`, uses
`/etc/forkcastra/forkcastra.lua` and runs as `forkcastra.service`. It can
therefore coexist with a vendor Astra 5.x installation. Do not configure both
services to claim the same DVB adapter, TCP/UDP port or multicast output.

The package enables the services for the next boot but deliberately does not
start them during installation. Create the first panel administrator and start
the services:

```sh
sudo -u forkcastra forkcastra-panel --create-admin admin
sudo systemctl start forkcastra forkcastra-panel
```

The panel listens only on `127.0.0.1:8088` until a TLS reverse proxy is
configured. For an initial remote test, use an SSH tunnel from your computer:

```sh
ssh -L 8088:127.0.0.1:8088 user@server
```

Then open `http://127.0.0.1:8088`. The example configuration does not start any
stream. Do not expose the panel or legacy HTTP module directly on an untrusted
network. See
[`docs/AUDITORIA_MODERNIZACION.md`](docs/AUDITORIA_MODERNIZACION.md) for the
panel architecture and security plan.
