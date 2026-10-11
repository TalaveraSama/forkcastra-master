# Astra WebUI @@TAG@@ — panel administrativo web

Panel administrativo web (estilo Astra/Cesbo) para el fork **Forkcastra/Astra 4.0
dev:282**, con agente Lua embebido en el núcleo, backend en Python 3 (sólo biblioteca
estándar) y paquete `.deb` para **Ubuntu 22.04 y superiores**.

## Qué incluye

| Componente | Descripción |
|---|---|
| **Núcleo** | `astra-4.0` compilado desde el fork (GPLv3), con enlace `/usr/bin/astra` |
| **Agente Lua** | se instala como script principal de astra; CRUD de canales en caliente, estadísticas, PSI, escaneo DVB y servidor de streaming con tokens (`:8001`) |
| **Panel** | API REST + SSE en `:8000`, login multiusuario con roles (viewer/operator/admin), auditoría, visor de logs, métricas del host, listas M3U/EPG |
| **WebUI** | interfaz en español/inglés sin dependencias externas: dashboard, canales, usuarios, DVB, logs, auditoría y ajustes |

## Requisitos

* Ubuntu 22.04, 24.04 o superior, **amd64** (el paquete se compila en Ubuntu 20.04, así
  que también funciona en 20.04).
* `systemd` (los servicios `astra.service` y `astra-webui.service`).
* Puertos libres: **8000** (panel), **8001** (streaming) y **9977** (API del agente,
  sólo en `127.0.0.1`).
* Para DVB: sintonizador en `/dev/dvb` (el módulo `dvb` del fork es Linux).

## Instalación

**En una línea** (descarga el paquete de esta release y lo instala, como el instalador de Astra):

```sh
curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/instalar.sh | sudo bash
```

O a mano, con el paquete ya descargado:

```sh
sudo apt install ./astra-webui_@@CORE@@-1_amd64.deb
```

Al terminar, la contraseña inicial del usuario `admin` está en:

```sh
sudo cat /etc/astra-webui/initial-admin-password
```

Abre `http://IP-DEL-SERVIDOR:8000`, entra con `admin` y esa contraseña, y cámbiala en
**Usuarios**. El fichero se borra solo tras el primer cambio de contraseña.

Si ya tienes el núcleo de Astra 4.0 compilado y sólo quieres el panel + agente:

```sh
sudo apt install ./astra-webui-panel_@@VERSION@@-1_all.deb
```

Comprobaciones rápidas:

```sh
systemctl status astra astra-webui
curl -s http://127.0.0.1:8000/api/health
curl -s -H "X-Astra-Token: $(sudo cat /etc/astra-webui/agent.token)" http://127.0.0.1:9977/status
```

## Novedades de esta versión

* Empaquetado `.deb` único que **compila el núcleo del fork** e instala agente + panel +
  WebUI, con servicios systemd, logrotate, regla polkit y respaldo por `sudoers`.
* CRUD de canales **en caliente** (sin reiniciar astra ni cortar los demás canales) con
  inputs `http`, `udp`, `file` y `dvb`, y salidas `http`, `udp` y `file`.
* Streaming con **token por usuario** (`/live/<token>/<canal>.ts`) y listas M3U por
  usuario, con permisos por canal o acceso total.
* Analizador **PSI** (PAT/PMT/SDT/NIT) en vivo y visor de logs del núcleo con seguimiento
  por SSE.
* **DVB**: adaptadores, transponders, satélites y escaneo con creación masiva de canales
  desde los servicios encontrados.
* **Auditoría** de todas las acciones y usuarios/roles (viewer, operator, admin).
* Pruebas: 117 comprobaciones del agente Lua contra el `stream.lua` real, 147 del panel,
  157 del empaquetado y 44 en un Chrome real sin interfaz.

## Ficheros de la release

| Fichero | Para qué |
|---|---|
| `instalar.sh` | instalador de una línea: descarga el paquete y lo instala |
| `astra-webui_@@CORE@@-1_amd64.deb` | paquete completo (núcleo + agente + panel + WebUI) |
| `astra-webui-panel_@@VERSION@@-1_all.deb` | sólo agente + panel + WebUI (sin núcleo) |
| `astra-webui-offline_@@VERSION@@_amd64.tar.gz` | **instalación sin Internet**: los `.deb`, el código fuente, las dependencias y un instalador que no usa la red |
| `astra-webui-@@VERSION@@-src.tar.gz` | código del panel y del agente para revisar o compilar |
| `SHA256SUMS` | sumas SHA-256 de todo lo anterior |

```sh
sha256sum -c SHA256SUMS
```

## Instalación sin Internet

```sh
# lleva el paquete al servidor (USB, scp…) y allí:
tar -xzf astra-webui-offline_@@VERSION@@_amd64.tar.gz
cd astra-webui-offline_@@VERSION@@_amd64
sudo ./instalar-offline.sh          # comprueba sumas, dependencias e instala y arranca
```

El instalador no descarga nada: usa los `.deb` incluidos, revisa las dependencias con
`dpkg-query` e informa de las que falten (y las instala si están en `deps/`). Detalles en
`docs/OFFLINE.md`.

## Desinstalación

```sh
sudo apt remove astra-webui        # conserva la configuración
sudo apt purge astra-webui         # borra también /etc/astra-webui y los datos
```

## Notas

* Paquete GPLv3 (igual que el fork). El núcleo se compila desde
  <https://github.com/TalaveraSama/forkcastra-master>.
* Documentación completa en el repositorio: `docs/INSTALL.md`, `docs/ARCHITECTURE.md`,
  `docs/API.md`, `docs/TROUBLESHOOTING.md` y `docs/INSTALL-WSL.md` (Windows con WSL).
* Las releases anteriores (`v4.0.282-1` … `v4.0.282-9`) **no llevaban ficheros
  adjuntos**; esta es la primera con paquetes instalables generados por integración
  continua sobre Ubuntu.
