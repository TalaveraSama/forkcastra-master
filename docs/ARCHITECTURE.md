# Astra WebUI — Arquitectura y contrato de APIs

Panel administrativo estilo Astra/Cesbo para el fork `TalaveraSama/forkcastra-master`
(Astra `4.0 dev:282`, GPLv3, Lua 5.2.2 embebido).

---

## 1. Visión general

El fork **no tiene panel web**. El binario `astra` sólo ejecuta un script Lua
(`astra /etc/astra/astra.conf`) y ofrece primitivas:

- `http_server{addr, port, callback}` → servidor HTTP embebido (con WebSocket).
- `http_request{...}` → cliente HTTP.
- `json.encode/decode`, `base64`, `md5`, `sha1`, `utils.*`, `log.*`, `timer{...}`.
- Módulos de streaming: `transmit`, `analyze`, `channel`, `decrypt`, `dvb_input`,
  `dvbls`, `ddci`, `udp_input/output`, `file_input/output`.
- `scripts/stream.lua` define el modelo de canales: `make_channel{...}`,
  `kill_channel(cd)`, `find_channel(k,v)`, `channel_list`.

La solución se compone de **tres piezas**:

```
                 ┌──────────────────────────────────────────┐
   navegador ──▶ │  astra-webui  (backend Python 3 stdlib)  │
   :8000 HTTP    │  · UI estática (SPA)                     │
                 │  · API REST + SSE                        │
                 │  · usuarios/roles/auditoría              │
                 │  · métricas /proc, logs, M3U             │
                 └───────────────┬──────────────────────────┘
                                 │ HTTP 127.0.0.1:9977 (X-Astra-Token)
                 ┌───────────────▼──────────────────────────┐
                 │  astra 4.0  (proceso de streaming)       │
                 │  · agente Lua (agent.lua) = plano control│
                 │  · stream.lua  = plano de datos (TS)     │
                 │  · http_server :8001 → TS con token      │
                 └──────────────────────────────────────────┘
```

* **Plano de datos**: `astra` mueve el MPEG-TS. El panel **nunca** proxya vídeo.
* **Plano de control**: el agente Lua expone una API local que el panel consume.
* **Fuente de verdad**: ficheros JSON en disco. El panel escribe, el agente aplica
  en caliente (diff) sin reiniciar el proceso ni cortar los demás canales.

Modo degradado: si el agente no responde, el panel sigue funcionando para editar
configuración, mostrar métricas del host y logs, y ofrece reinicio de `astra` vía
`systemctl` (sudoers controlado).

---

## 2. Layout en disco (instalado por el .deb)

| Ruta | Contenido |
|---|---|
| `/usr/bin/astra-4.0` + symlink `/usr/bin/astra` | núcleo compilado del fork |
| `/etc/astra/scripts-4.0/stream.lua`, `analyze.lua`, `dvbls.lua`, `xproxy.lua` | scripts upstream |
| `/usr/share/astra-webui/agent/*.lua` | agente Lua (script principal de astra) |
| `/usr/share/astra-webui/panel/*.py` | backend Python |
| `/usr/share/astra-webui/web/*` | SPA estática |
| `/etc/astra-webui/panel.json` | configuración del panel (**conffile**) |
| `/etc/astra-webui/agent.token` | secreto compartido panel↔agente (`0600 astra:astra`) |
| `/etc/astra-webui/README.Debian` | notas de instalación |
| `/var/lib/astra-webui/channels.json` | base de datos de canales (**fuente de verdad**) |
| `/var/lib/astra-webui/auth.json` | tokens de streaming que valida el agente |
| `/var/lib/astra-webui/users.json` | usuarios del panel |
| `/var/lib/astra-webui/sessions.json` | sesiones |
| `/var/lib/astra-webui/dvb.json` | satélites, transponders y resultados de escaneo |
| `/var/lib/astra-webui/audit.jsonl` | auditoría (append-only) |
| `/var/lib/astra-webui/history.json` | histórico de métricas (ring buffer) |
| `/var/log/astra/astra.log` | log del núcleo (escrito por el agente) |
| `/var/log/astra-webui/panel.log` | log del panel |
| `/var/lib/astra-webui/recordings/` | salidas `file` |

Servicios systemd: `astra.service` (núcleo+agente) y `astra-webui.service` (panel).
Usuario de sistema `astra`, grupos `video` (DVB) y `audio`.

---

## 3. Modelo de datos

### 3.1 `channels.json`

```json
{
  "version": 1,
  "updated_at": 1730000000,
  "channels": [
    {
      "id": "ch_7f3a91",
      "name": "Canal 1 HD",
      "group": "General",
      "logo": "https://…/logo.png",
      "epg_id": "canal1.example",
      "enabled": true,
      "tags": ["hd", "deportes"],
      "notes": "",
      "inputs": [
        {
          "type": "http",
          "url": "http://origen:8080/canal1.ts",
          "auth": "",                      
          "options": { "pnr": 101, "set_pnr": 1, "no_analyze": false, "biss": "", "cam": "" }
        },
        {
          "type": "udp",
          "addr": "239.10.0.1", "port": 1234, "localaddr": "", "rtp": false, "socket_size": 0,
          "options": { "pnr": 0, "set_pnr": 0 }
        },
        {
          "type": "dvb",
          "delivery": "S2",
          "adapter": 0, "device": 0,
          "frequency": 11500, "polarization": "H", "symbolrate": 27500,
          "lof1": 9750, "lof2": 10600, "slof": 11700,
          "diseqc": 0, "tone": 0, "budget": 0, "fec": "AUTO", "stream_id": 0,
          "options": { "pnr": 101, "set_pnr": 0 }
        },
        {
          "type": "file",
          "filename": "/srv/media/loop.ts", "loop": true,
          "options": { "pnr": 0 }
        }
      ],
      "outputs": [
        { "type": "http", "uri": "/canal1", "public": false, "cors": true },
        { "type": "udp", "addr": "239.20.0.1", "port": 1234, "ttl": 32, "localaddr": "", "rtp": false, "sync": 0 },
        { "type": "file", "filename": "/var/lib/astra-webui/recordings/canal1.ts", "m2ts": false }
      ],
      "analyze": { "enabled": true },
      "created_at": 1730000000,
      "updated_at": 1730000000
    }
  ]
}
```

`inputs[].type` ∈ `http | udp | file | dvb`, `outputs[].type` ∈ `http | udp | file`.
Para las entradas DVB, `inputs[].delivery` ∈ `S | S2 | T | T2 | C`.
El **orden** de `inputs` define la prioridad de respaldo (reserva automática de
`stream.lua`). Reglas de validación (panel + agente):

* `name` obligatorio, único salvo duplicados explícitos.
* `dvb` requiere `adapter`, `frequency` y `delivery`; `S/S2` además `polarization`,
  `symbolrate` y `lof1`; `T/T2` usa `bandwidth`/`guardinterval`/`transmitmode`;
  `C` requiere `symbolrate`.
* `udp` requiere `addr` IPv4 multicast y `port` 1..65535.
* `file` requiere ruta absoluta bajo `/srv` o `/var/lib/astra-webui`.
* `outputs[].type=http` requiere `uri` empezando por `/`.
* Puerto de streaming reservado: ver `settings.stream`.

### 3.2 `auth.json` (tokens de consumo)

```json
{
  "version": 1,
  "enabled": true,
  "tokens": {
    "8f14e45fceea167a5a36dedd4bea2543": {
      "username": "cliente1",
      "display_name": "Cliente Uno",
      "enabled": true,
      "channels": ["ch_7f3a91"],
      "all_channels": false,
      "expires_at": 0,
      "max_connections": 2,
      "allow_public": false
    }
  }
}
```

`all_channels: true` ⇒ acceso a todos los canales habilitados (ignora `channels`).
`expires_at: 0` ⇒ sin caducidad (epoch en segundos).

### 3.3 `users.json` (panel)

```json
{
  "version": 1,
  "users": [
    {
      "id": "u_1a2b3c",
      "username": "admin",
      "display_name": "Administrador",
      "role": "admin",
      "enabled": true,
      "password": { "algo": "pbkdf2_sha256", "iterations": 260000, "salt": "…", "hash": "…" },
      "stream_token": "8f14e45fceea167a5a36dedd4bea2543",
      "allowed_channels": [],
      "all_channels": true,
      "expires_at": 0,
      "max_connections": 1,
      "created_at": 1730000000,
      "last_login": 1730000000,
      "failed_logins": 0
    }
  ]
}
```

Roles: `admin` (todo), `operator` (canales, DVB, logs, inicio/parada; no usuarios ni
ajustes globales), `viewer` (sólo lectura).
`stream_token` es el token que se copia a `auth.json` (se sincroniza en cada cambio).

### 3.4 `panel.json`

```json
{
  "listen": "0.0.0.0",
  "port": 8000,
  "base_url": "",
  "session_hours": 12,
  "stream": { "host": "", "port": 8001, "public_base": "" },
  "agent": { "url": "http://127.0.0.1:9977", "token_file": "/etc/astra-webui/agent.token", "timeout": 4 },
  "paths": {
    "astra_bin": "/usr/bin/astra",
    "astra_log": "/var/log/astra/astra.log",
    "panel_log": "/var/log/astra-webui/panel.log",
    "channels": "/var/lib/astra-webui/channels.json",
    "auth": "/var/lib/astra-webui/auth.json",
    "users": "/var/lib/astra-webui/users.json",
    "sessions": "/var/lib/astra-webui/sessions.json",
    "dvb": "/var/lib/astra-webui/dvb.json",
    "audit": "/var/lib/astra-webui/audit.jsonl",
    "history": "/var/lib/astra-webui/history.json"
  },
  "service_control": { "enabled": true, "unit": "astra.service" },
  "security": { "max_login_attempts": 5, "login_window": 300, "trust_proxy": false },
  "ui": { "title": "Astra WebUI", "language": "es", "refresh_ms": 2000, "theme": "dark" },
  "history": { "points": 720, "interval": 5 }
}
```

---

## 4. API del **agente Lua** (`127.0.0.1:9977`)

Todas las peticiones llevan la cabecera `X-Astra-Token: <secreto>`; el agente la
compara con `--token-file`. Sin token válido → `401`.
Respuestas: JSON UTF-8. Envoltorio uniforme: `{"ok":true,…}` o `{"ok":false,"error":"…"}`.
**Nunca** se emiten objetos vacíos (el `json.encode` del core los convierte en `[]`)
ni números decimales (el `json.decode` del core trunca decimales).

| Método | Ruta | Cuerpo | Respuesta |
|---|---|---|---|
| GET | `/health` | – | `{ok,version,pid,uptime,channels_running,agents:{stream}` |
| GET | `/status` | – | estado completo (ver 4.1) |
| GET | `/metrics` | – | `{ok,uptime,channels:[{id,bitrate,viewers,on_air}]}` |
| POST | `/reload` | `{}` | relee `channels.json`+`auth.json` y aplica diff: `{ok,added,updated,removed,running,failed:[…]}` |
| POST | `/channel/start` | `{id}` | `{ok,running:true}` |
| POST | `/channel/stop` | `{id}` | `{ok,running:false}` |
| POST | `/channel/restart` | `{id}` | `{ok}` |
| GET | `/channel/psi?id=…` | – | `{ok,id,tables:[…],stats:{bitrate,on_air}}` |
| GET | `/dvb/adapters` | – | `{ok,adapters:[{adapter,device,frontend,mac,busy,error}]}` |
| POST | `/dvb/scan` | `{adapter,device,type,duration,transponders:[…]}` | `{ok,scan_id}` |
| GET | `/dvb/scan?id=…` | – | `{ok,scan:{state,index,total,current,services:[…],results:[…]}}` |
| POST | `/dvb/scan/stop` | `{id}` | `{ok}` |
| GET | `/log/tail?lines=200` | – | `{ok,path,lines:["…"]}` |
| POST | `/auth/reload` | `{}` | `{ok,tokens:n}` |
| POST | `/exit` | `{}` | `{ok}` y termina el proceso (sólo para mantenimiento) |

### 4.1 `GET /status`

```json
{
  "ok": true,
  "version": "4.0 dev:282",
  "pid": 1234,
  "uptime": 3600,
  "channels_total": 12,
  "channels_running": 10,
  "viewers": 34,
  "bitrate_total": 48210,
  "channels": [
    {
      "id": "ch_7f3a91",
      "name": "Canal 1 HD",
      "enabled": true,
      "running": true,
      "on_air": true,
      "bitrate": 5340,
      "viewers": 3,
      "inputs": [
        { "index": 1, "type": "http", "on_air": true, "bitrate": 5340,
          "cc_error": 0, "sc_error": 0, "pes_error": 0 }
      ],
      "outputs": [ { "type": "http", "target": "/canal1", "clients": 3 } ],
      "errors": { "cc": 0, "sc": 0, "pes": 0 }
    }
  ]
}
```

`bitrate` en **kbit/s** (entero). `uptime` en segundos.

### 4.2 Servidor de streaming (`0.0.0.0:8001`, mismo proceso)

| Ruta | Descripción |
|---|---|
| `GET /live/<token>/<channel_id>.ts` | TS del canal si el token lo permite |
| `GET /live/<token>/<channel_id>/index.ts` | alias |
| `GET /live/<token>/playlist.m3u` (o `.m3u8`) | lista M3U de los canales permitidos |
| `GET /live/<token>` | información JSON de la cuenta |
| `GET <outputs.uri>` | salida directa del canal (sólo si `public: true`) |
| `OPTIONS *` | preflight CORS `204` |

Códigos: `200` OK, `403` token inválido/expira/límite de conexiones, `404` canal
inexistente o deshabilitado, `429` demasiadas conexiones.
Contadores de espectadores por canal y por token (`max_connections`).

---

## 5. API del **panel** (`/api`, cookie `astra_session`)

Autenticación: `POST /api/auth/login` → cookie `HttpOnly; SameSite=Lax` + `csrf`
en el cuerpo. Toda mutación exige `X-CSRF-Token`. Sesión caducada → `401`.

| Método | Ruta | Notas |
|---|---|---|
| POST | `/api/auth/login` | `{username,password}` → `{ok,user,csrf}` |
| POST | `/api/auth/logout` | |
| GET | `/api/auth/me` | `{ok,user,csrf,version,hostname,server_time,ui,stream,capabilities}` |
| POST | `/api/auth/password` | `{current,password}` |
| GET | `/api/auth/sessions` · POST `/api/auth/sessions/purge` | sesiones activas (rol admin) |
| GET | `/api/system/status` | host + astra + agente + canales + panel |
| GET | `/api/system/history?minutes=60` | serie temporal del host |
| GET | `/api/stats/bitrate?minutes=60` | serie de bitrate por canal |
| GET | `/api/channels` | canales + estado en vivo (`{ok,channels,agent}`) |
| POST | `/api/channels` | crear |
| GET/PUT/DELETE | `/api/channels/{id}` | leer/editar/borrar |
| POST | `/api/channels/{id}/action` | `{action:start\|stop\|restart}` |
| POST | `/api/channels/{id}/duplicate` | |
| POST | `/api/channels/reorder` | `{ids:[…]}` |
| POST | `/api/channels/import` | `{text,format:"m3u",group}` |
| GET | `/api/channels/{id}/psi` | proxy del agente |
| GET | `/api/channels/{id}/clients` | espectadores de ese canal |
| GET | `/api/channels/export.m3u?token=` | exportar la configuración como M3U |
| GET/POST/PUT/DELETE | `/api/users[/{id}]` | rol admin |
| POST | `/api/users/{id}/token/rotate` | nuevo token de streaming |
| POST | `/api/users/{id}/password` | restablecer contraseña |
| GET | `/api/playlists/{token}.m3u` (o `.m3u8`) | texto M3U (público: el token es la credencial) |
| GET | `/api/playlists/public.m3u` | canales con salida pública |
| GET | `/api/dvb/adapters` | proxy del agente |
| GET | `/api/dvb/transponders` | lista aplanada (con `satellite_id`/`satellite_name`) |
| GET/POST/PUT/DELETE | `/api/dvb/satellites[/{id}]` | alta/edición/borrado de satélites |
| POST | `/api/dvb/satellites/{id}/transponders` | alta masiva (descarta duplicados) |
| DELETE | `/api/dvb/satellites/{id}/transponders/{tp}` | borrar un transponder |
| POST | `/api/dvb/scan` | `{satellite_id}` \| `{band:"uhf",channel_first,channel_last}` \| `{transponders:[…]}` |
| GET | `/api/dvb/scan/{id}` · POST `/api/dvb/scan/{id}/stop` | progreso / cancelar |
| POST | `/api/dvb/scan/apply` | crear canales desde los servicios encontrados |
| GET | `/api/logs/astra` · `/api/logs/panel` | `lines,level,q` |
| GET | `/api/logs/astra/stream` | **SSE** de líneas nuevas (el log del panel va por polling) |
| GET | `/api/logs/audit?limit=200` | auditoría (cada entrada lleva `iso`) |
| GET | `/api/events` | **SSE** estado global (dashboard en vivo) |
| GET/PUT | `/api/settings` | `panel.json` (`PUT` añade `restart_required` y `agent_config`) |
| POST | `/api/service/{start\|stop\|restart\|status}-astra` | systemd (polkit o `sudo -n`) |
| GET | `/api/export` · POST `/api/import` | respaldo/restauración |
| GET | `/api/enums` | valores válidos para los formularios |
| GET | `/api/health` | sin sesión: `{ok,version,agent,astra}` |

Códigos: `200/201` OK, `400` validación, `401` sin sesión, `403` rol/CSRF/red no
permitida, `404` inexistente, `413` cuerpo grande, `429` demasiados intentos de acceso,
`500` error interno, `502` agente caído, `503` systemctl no disponible.

**Degradación elegante**: `GET /api/system/status` y `GET /api/channels` responden `200`
aunque el agente esté caído, con `agent:{ok:false,error:"…"}`; sólo los endpoints que
necesitan datos en vivo del agente (`/psi`, `/dvb/adapters`, `/dvb/scan`, acciones de
canal) devuelven `502`.

---

## 6. Seguridad

* Contraseñas: PBKDF2-HMAC-SHA256, 260 000 iteraciones, salt de 16 bytes,
  comparación en tiempo constante.
* Sesiones: token aleatorio de 32 bytes (`secrets.token_urlsafe`), guardado con
  hash SHA-256 en `sessions.json`, cookie `HttpOnly`, `SameSite=Lax`, `Secure`
  cuando `base_url` es HTTPS.
* CSRF: token por sesión en cabecera `X-CSRF-Token`.
* Anti fuerza bruta: `max_login_attempts` por IP+usuario en `login_window` s.
* Auditoría append-only de toda mutación: usuario, IP, acción, objetivo, resultado.
* El agente sólo escucha en `127.0.0.1` y exige `X-Astra-Token`.
* Control de servicios: drop-in sudoers con comandos exactos (`systemctl
  start|stop|restart|is-active astra.service`), nunca shell libre.
* Sin `eval`, sin `shell=True`, rutas normalizadas contra traversal
  (`file://` outputs limitados a directorios permitidos).

---

## 7. Empaquetado

`packaging/build-deb.sh`:

1. Compila el core del fork (`configure.sh && make -j`) — o usa `--core-bin` para
   reutilizar un binario ya compilado.
2. Construye el árbol `pkgroot/` con binario, scripts, agente, panel y web.
3. Genera el `.deb` con `dpkg-deb` si existe, o con `tools/build_deb.py`
   (implementación pura en Python del formato `ar`+`tar.gz`, sin debhelper).
4. `postinst`: usuario/grupos, directorios, token del agente, contraseña inicial
   aleatoria de `admin` (mostrada e impresa al final), `systemctl enable --now`.
5. Paquete final: `astra-webui_<version>_<arch>.deb`
   (`Depends: libc6, python3 (>=3.8), adduser`, `Recommends: sudo, ffmpeg`).

Requisitos de compilación en el servidor: `build-essential` y, para DVB, los
headers de `linux-libc-dev`. El `.deb` resultante incluye el binario ya compilado.

---

## 8. Compatibilidad

* Ubuntu 20.04 (Python 3.8), 22.04 (3.10), 24.04 (3.12), Debian 11/12.
* Python 3.8-compatible: sin `match`, sin `X | Y` en runtime, sin `tomllib`.
* Lua 5.2.2 del core: sin operadores bit a bit, sin `//`, sin `utf8`.
* El panel arranca también en Windows/macOS en modo desarrollo
  (`tools/mock_agent.py`) para probar sin DVB ni astra.
