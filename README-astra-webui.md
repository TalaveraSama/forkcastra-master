# Astra WebUI

Panel administrativo web (estilo Astra/Cesbo) para el fork
[`TalaveraSama/forkcastra-master`](https://github.com/TalaveraSama/forkcastra-master) —
Astra `4.0 dev:282`, GPLv3, con el intérprete Lua 5.2.2 embebido.

Astra no trae interfaz de administración: sólo ejecuta un script Lua. Este proyecto
añade **tres piezas** que se instalan juntas con un único `.deb` para Ubuntu 20.04+:

| Pieza | Qué es | Dónde corre |
|---|---|---|
| **Agente Lua** | plano de control: gestiona los canales en caliente (`make_channel`/`kill_channel`), sirve el TS con tokens y escanea DVB | dentro del propio proceso `astra` |
| **Panel Python 3** | API REST + SSE, login multiusuario, auditoría, métricas, logs, M3U | `astra-webui.service` (sólo biblioteca estándar) |
| **WebUI** | SPA estática estilo Astra (dashboard, canales, usuarios, DVB, logs) | servida por el panel |

```
navegador ──:8000──▶ astra-webui (Python)  ──127.0.0.1:9977──▶ astra + agente Lua
                              │                                      │
                              └── ficheros JSON en /var/lib/astra-webui ──┘
                                                                     │
reproductor ──────────:8001/live/<token>/<canal>.ts──────────────────┘
```

El panel **nunca** proxya el vídeo: el MPEG-TS lo mueve siempre el núcleo `astra`.

## Funciones

- **Canales en vivo**: alta, edición, duplicado, borrado, orden y activación/desactivación
  sin reiniciar el proceso (diff en caliente). Entradas `http`, `udp`, `file` y `dvb`
  (S, S2, T, T2, C) con varias entradas por canal en orden de respaldo, y salidas
  `http` (con token), `udp` y `file`.
- **Dashboard**: CPU, memoria, red, disco, uptime del host y de astra, bitrate total,
  espectadores, canales en marcha y gráficas en vivo (SSE).
- **Usuarios y acceso**: roles `admin`/`operator`/`viewer`, sesiones con cookie + CSRF,
  tokens de streaming por usuario, canales permitidos, caducidad, límite de conexiones,
  listas M3U listas para VLC/TiviMate y auditoría de todas las acciones.
- **Logs y PSI**: visor del log del núcleo con filtros y seguimiento en vivo, más
  analizador PSI por canal (PAT/PMT/SDT/CAT, PIDs, servicios, cifrado).
- **DVB**: adaptadores, satélites con LNB, transponders, escaneo (satélite, rango UHF o
  lista manual) y creación masiva de canales desde los servicios encontrados.
- **Operación**: servicios systemd, logrotate, permisos endurecidos, control de
  `astra.service` desde el panel (polkit o sudoers), respaldo/restauración en JSON.

## Instalación rápida (Ubuntu 20.04 / 22.04 / 24.04)

En el servidor, con el código del fork a mano:

```bash
sudo apt update
sudo apt install -y build-essential python3 git

# 1) trae el fork (o copia tu copia local)
git clone https://github.com/TalaveraSama/forkcastra-master.git

# 2) construye el .deb (compila el núcleo + empaqueta agente, panel y WebUI)
cd astra-webui
./packaging/build-deb.sh --source ../forkcastra-master

# 3) instala
sudo apt install -y ./dist/astra-webui_4.0.282-1_amd64.deb

# 4) abre el panel y entra con admin + la contraseña que mostró la instalación
#    http://IP-DEL-SERVIDOR:8000
```

La contraseña inicial también queda en `/etc/astra-webui/initial-admin-password`
(bórrala tras el primer acceso y cámbiala en *Ajustes*).

Detalles, DVB, TLS con nginx y desinstalación: **[docs/INSTALL.md](docs/INSTALL.md)**.

## Instalar desde una release

**En una línea** (como el instalador de Astra: descarga el paquete y lo instala):

```bash
curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/instalar.sh | sudo bash
```

O descargando el `.deb` a mano:

```bash
# descarga astra-webui_<núcleo>-1_amd64.deb y SHA256SUMS de la release
sha256sum -c SHA256SUMS
sudo apt install ./astra-webui_4.0.282-1_amd64.deb
sudo cat /etc/astra-webui/initial-admin-password
```

`instalar.sh` acepta `--tag <etiqueta>`, `--deb <fichero>`, `--offline <paquete.tar.gz>`,
`--no-start` y `--check`.

`.deb` que lleva cada release:

| Fichero | Contenido |
|---|---|
| `instalar.sh` | instalador de una línea (descarga y verifica el paquete) |
| `astra-webui_<núcleo>-1_amd64.deb` | núcleo del fork + agente Lua + panel + WebUI (Ubuntu 20.04/22.04/24.04) |
| `astra-webui-panel_<panel>-1_all.deb` | sólo agente + panel + WebUI (si ya tienes el núcleo) |
| `astra-webui-offline_<panel>_amd64.tar.gz` | **instalación sin Internet**: los `.deb`, el código fuente y un instalador que no usa la red |
| `astra-webui-<panel>-src.tar.gz` | código del panel y del agente |
| `SHA256SUMS` | sumas SHA-256 de todo lo anterior |

### Publicar una release nueva

```bash
python3 tools/make_release.py --tag v4.0.282-10        # entrega + .deb del panel + notas
python3 tools/make_offline_bundle.py                   # paquete de instalación sin Internet
```

* **[docs/COMO-SUBIRLO.md](docs/COMO-SUBIRLO.md)** explica paso a paso cómo subir la
  carpeta `release/entrega` con **GitHub Desktop**.
* El workflow `.github/workflows/release.yml` compila el `.deb` **completo en Ubuntu** y lo
  adjunta a la release (junto al paquete offline): basta con lanzarlo desde *Actions* (o
  empujar una etiqueta nueva). No hace falta compilador en tu equipo.
* Para publicar desde aquí con la API: `GITHUB_TOKEN=... python3 tools/publish_release.py --tag v4.0.282-11`.
* **[docs/RELEASE.md](docs/RELEASE.md)** tiene el detalle del proceso, las versiones y las
  comprobaciones previas.

## Documentación

| Documento | Contenido |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | instalación paso a paso, compilación manual, DVB, nginx/TLS, actualización, desinstalación |
| [docs/OFFLINE.md](docs/OFFLINE.md) | **instalación sin Internet**: qué lleva el paquete, instalador, dependencias y compilación local |
| [docs/INSTALL-WSL.md](docs/INSTALL-WSL.md) | ejecutarlo en **Windows con WSL** (diagnóstico, instalación automática, red, DVB con usbipd, alternativas) |
| [docs/RELEASE.md](docs/RELEASE.md) | cómo se publica una release, qué ficheros lleva, comprobaciones e instalación |
| [docs/COMO-SUBIRLO.md](docs/COMO-SUBIRLO.md) | guía con GitHub Desktop para subir el proyecto y crear la release |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | diseño, modelo de datos, layout en disco, contrato de APIs |
| [docs/API.md](docs/API.md) | referencia de la API del agente y del panel con ejemplos `curl` |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | problemas habituales y cómo diagnosticarlos |

## Estructura del repositorio

```
agent/                 agente Lua (se instala como script principal de astra)
  agent.lua            arranque, configuración y vigilancia de ficheros
  aw_channels.lua      canales: validación, diff en caliente, estadísticas
  aw_stream.lua        servidor de streaming con tokens y contador de espectadores
  aw_control.lua       API de control en 127.0.0.1:9977
  aw_dvb.lua           adaptadores y escaneo de transponders
  aw_auth.lua          tokens de consumo (auth.json)
  aw_http.lua          ayuda HTTP sobre http_server (enrutador, respuestas)
  aw_json.lua          envoltorio del json del core (con sus limitaciones)
  aw_log.lua           configuración del log y lectura del fichero
  aw_util.lua          utilidades comunes

panel/astra_webui/     backend del panel (Python 3, sólo biblioteca estándar)
  app.py               contexto: BDs, agente, métricas, rutas
  api_auth.py          login, sesiones, contraseñas
  api_core.py          estado, histórico, SSE, logs, ajustes, respaldo
  api_channels.py      canales, usuarios, listas M3U
  api_dvb.py           DVB: adaptadores, satélites, escaneo
  httpd.py             servidor HTTP, enrutador, estáticos, SSE
  store.py             persistencia JSON (fuente de verdad para el agente)
  validators.py        validación del modelo (evita abortar el núcleo)
  security.py          PBKDF2, sesiones, CSRF, límite de intentos
  metrics.py           /proc, histórico en memoria
  logtail.py           lectura y seguimiento de logs
  agentclient.py       cliente de la API del agente
  m3u.py               listas de reproducción

panel/web/             WebUI estática (HTML + CSS + JS, sin build step)

packaging/
  build-deb.sh         script principal de empaquetado
  common/              systemd, logrotate, sudoers, polkit, /etc/default
  config/              panel.json y agent.json de ejemplo (conffiles)
  scripts/             postinst, prerm, postrm

tools/
  deb_stage.py            prepara el árbol del paquete (lógica en Python)
  build_deb.py            constructor/verificador de .deb en Python puro
  mock_agent.py           agente simulado para desarrollar sin astra
  make_test_ts.py         genera un MPEG-TS válido para probar canales sin fuente real
  astra_demo.py           prueba end-to-end (alta de canal, emisión, streaming, PSI)
  make_release.py         prepara release/ (entrega, .deb del panel, sumas y notas)
  make_offline_bundle.py  monta el paquete de instalación sin Internet
  publish_release.py      publica la release con la API de GitHub (sin git ni gh)
  github_push.py          sube la carpeta al repositorio por la API (crea rama/etiqueta)
  github_workflow.py      lanza o vigila un workflow de Actions por la API
  check_webui.py          comprobaciones estáticas de la WebUI
  webui_browser_check.py  pruebas de la WebUI en Chrome/Edge headless (CDP)
  lua_selftest.py         pruebas del agente Lua (con stream.lua real)
  test_panel.py           pruebas end-to-end del panel contra el agente simulado
  test_packaging.py       pruebas del empaquetado .deb
  run_tests.py            ejecuta todas las suites y resume
  agent_stubs.lua         stubs de los módulos del core para lua_selftest.py

.github/workflows/     CI (pruebas en cada push) y release (compila y publica el .deb)
packaging/online/      instalar.sh: instalador de una línea desde la release
packaging/offline/     instalar-offline.sh: instalador sin Internet
packaging/templates/   ejemplo de nginx con TLS
packaging/wsl/         instalación en Windows: install-wsl.ps1 + setup-in-wsl.sh
dist/                  paquete .deb de ejemplo (variante sólo panel)
release/               entrega y ficheros de la release (lo genera make_release.py)
docs/                  documentación
```

## Desarrollo y pruebas

No hace falta astra ni DVB para trabajar en el panel: hay un agente simulado.

```bash
# agente simulado (API + streaming de mentira)
python3 tools/mock_agent.py --data-dir var --log-file var/astra.log

# panel en modo desarrollo (usa panel/web y var/)
PYTHONPATH=panel python3 -m astra_webui --dev --port 8000 --data-dir var
```

Pruebas automatizadas (todas se pueden lanzar en cualquier sistema):

```bash
python3 tools/run_tests.py        # todas las suites + resumen
```

| Suite | Qué cubre | Comprobaciones |
|---|---|---|
| `tools/check_webui.py` | estáticos de la WebUI: rutas usadas ↔ rutas del backend, i18n es/en, CSP (sin scripts en línea ni CDN), sintaxis de los módulos ES | — |
| `tools/lua_selftest.py` | agente Lua con el `stream.lua` **real** del fork y stubs del core; incluye integración panel → agente | 117 |
| `tools/test_panel.py` | API REST completa contra el agente simulado (auth, canales, usuarios, DVB, logs, ajustes, SSE, canal de fichero + streaming con token) | 147 |
| `tools/test_packaging.py` | estructura del `.deb` (permisos, conffiles, systemd, symlinks, ambas variantes) | 155 |
| `tools/webui_browser_check.py` | **Chrome/Edge headless por CDP**: recorre las 7 rutas, hace login y mutaciones reales, PSI, M3U, escaneo DVB, cambio de idioma y modo degradado con el agente apagado | 44 |

En GitHub estas suites se ejecutan solas en cada push (`.github/workflows/ci.yml`).

Además, `tools/astra_demo.py` es la prueba manual de extremo a extremo contra un astra
real (o simulado): genera un MPEG-TS, crea el canal, espera a que emita y descarga el TS
por HTTP con el token.

```bash
python3 tools/astra_demo.py --password <clave-admin>     # 14 comprobaciones
```

`lua_selftest.py` necesita `pip install lupa`; `check_webui.py` aprovecha `pip install esprima`
(opcional) para validar la sintaxis. El comprobador de navegador necesita el panel y el
agente simulado en marcha:

```bash
python3 tools/mock_agent.py --data-dir var --log-file var/astra.log &
PYTHONPATH=panel python3 -m astra_webui --dev --port 8000 --data-dir var &
python3 tools/webui_browser_check.py --password <contraseña-de-admin>
```

En `dist/` hay un `astra-webui-panel_1.0.0-1_all.deb` de ejemplo (agente + panel + WebUI,
sin el núcleo) generado con el constructor en Python; el paquete completo con el binario
compilado se construye en el servidor con `packaging/build-deb.sh`.

## Licencia

GPLv3 o posterior, igual que Astra (ver `COPYING` del fork). El paquete `.deb` incluye
el binario compilado del fork, por lo que distribuye la licencia en
`/usr/share/doc/astra-webui/`.

Astra es obra de Andrey Dyldin (`and@cesbo.com`, <http://cesbo.com/astra>).
