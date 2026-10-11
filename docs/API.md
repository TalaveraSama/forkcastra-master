# Referencia de la API

Dos APIs independientes:

* **Agente Lua** — `http://127.0.0.1:9977` (sólo local). La usa el panel. Exige la
  cabecera `X-Astra-Token` con el contenido de `/etc/astra-webui/agent.token`.
* **Panel** — `http://IP:8000/api`. La usa la WebUI (y puedes usarla tú). Sesión por
  cookie `astra_session` + cabecera `X-CSRF-Token` en las mutaciones.

Además, el agente sirve el TS en `http://IP:8001`.

Todas las respuestas son JSON con un envoltorio uniforme: `{"ok":true,…}` o
`{"ok":false,"error":"…"}`. Códigos: `200/201` correcto, `400` validación, `401` sin
sesión, `403` permisos/CSRF, `404` no existe, `413` cuerpo demasiado grande,
`429` demasiados intentos, `500` error interno, `502` agente no disponible.

---

## 1. API del agente

### `GET /health`

```console
$ curl -s -H "X-Astra-Token: $(sudo cat /etc/astra-webui/agent.token)" \
       http://127.0.0.1:9977/health
{"ok":true,"version":"4.0 dev:282","agent_version":"1.0.0","pid":1534,"rss_kb":24880,
 "uptime":3600,"running":4,"config_error":""}
```

### `GET /status`

Estado completo: canales en marcha, bitrate por entrada, errores, espectadores, PSI
resumido y los fallos de arranque.

```json
{
  "ok": true,
  "version": "4.0 dev:282",
  "pid": 1534,
  "rss_kb": 24880,
  "uptime": 3600,
  "channels_total": 5,
  "channels_running": 4,
  "viewers": 7,
  "bitrate_total": 21340,
  "config_error": "",
  "failed": [ { "id": "ch_9d1", "name": "Canal roto", "error": "entrada 1: 'addr' debe ser una IPv4" } ],
  "channels": [
    {
      "id": "ch_7f3a91",
      "name": "Canal Uno HD",
      "group": "General",
      "enabled": true,
      "running": true,
      "on_air": true,
      "bitrate": 5340,
      "viewers": 3,
      "errors": { "cc": 0, "sc": 0, "pes": 0 },
      "started": 1730000000,
      "inputs": [
        { "index": 1, "type": "http", "on_air": true, "bitrate": 5340,
          "cc_error": 0, "sc_error": 0, "pes_error": 0, "error": "" }
      ],
      "outputs": [ { "type": "http", "target": "/uno", "public": false } ]
    }
  ]
}
```

`bitrate` va en **kbit/s** (entero). Si `config_error` no está vacío, el agente mantiene
la configuración anterior porque el fichero no se pudo leer.

### `GET /metrics`

Versión reducida, pensada para muestrear cada pocos segundos:

```json
{"ok":true,"uptime":3600,"bitrate_total":21340,"viewers":7,
 "channels":[{"id":"ch_7f3a91","name":"Canal Uno HD","enabled":true,"running":true,
              "on_air":true,"bitrate":5340,"viewers":3}]}
```

### `POST /reload`

Vuelve a leer `channels.json` y `auth.json` y aplica el **diff en caliente**: arranca los
canales nuevos, reinicia los que cambiaron de configuración y para los que ya no están.

```console
$ curl -s -X POST -H "X-Astra-Token: $TOKEN" -d '{}' http://127.0.0.1:9977/reload
{"ok":true,"changed":true,"added":1,"updated":0,"removed":2,"running":4,
 "failed":[],"auth_ok":true,"auth_error":""}
```

### `POST /channel/start` · `/channel/stop` · `/channel/restart`

Cuerpo: `{"id":"ch_7f3a91"}`. `start` exige que el canal esté habilitado en la
configuración; `stop` lo detiene hasta la siguiente recarga.

### `GET /channel/psi?id=<canal>&input=<n>`

Últimas tablas PSI capturadas por el analizador del canal:

```json
{"ok":true,"id":"ch_7f3a91","stats":{"bitrate":5340,"on_air":true},
 "tables":[
   {"psi":"sdt","tsid":1001,"crc32":3735928559,"input_index":1,
    "services":[{"sid":101,"descriptors":[{"type_name":"service",
       "service_name":"Canal Uno","service_provider":"Proveedor"}]}]},
   {"psi":"pmt","pnr":101,"pid":100,"pcr":101,"crc32":1234,
    "streams":[{"pid":101,"type_name":"video","type_id":2},
               {"pid":102,"type_name":"audio","type_id":3}],
    "descriptors":[{"type_name":"cas","caid":2816,"pid":500}]}
 ]}
```

### DVB

| Petición | Descripción |
|---|---|
| `GET /dvb/adapters` | `{"ok":true,"adapters":[{"adapter":0,"device":0,"frontend":"Montage M88DS3103","mac":"00:11:22:33:44:55","busy":false,"error":""}]}` |
| `POST /dvb/scan` | `{"adapter":0,"device":0,"type":"S2","duration":8,"transponders":[{"delivery":"S2","frequency":11500,"polarization":"H","symbolrate":27500,"lof1":9750,"lof2":10600,"slof":11700}]}` → `{"ok":true,"scan_id":"scan_1a2b3c"}` |
| `GET /dvb/scan?id=<scan_id>` | `{"ok":true,"scan":{"state":"running|done|stopped","index":2,"total":12,"current":{…},"services":[…],"results":[…]}}` |
| `POST /dvb/scan/stop` | `{"id":"scan_1a2b3c"}` |

Cada servicio lleva `pnr`, `sid`, `pmt_pid`, `name`, `provider`, `scrambled`,
`video_pid`, `audio_pid` y el `transponder` del que salió (útil para crear canales).

### Otros

| Petición | Descripción |
|---|---|
| `GET /log/tail?lines=200` | últimas líneas del log clasificadas (`timestamp`, `level`, `message`) |
| `POST /auth/reload` | recarga `auth.json` sin tocar los canales |
| `POST /exit` | termina el proceso `astra` (mantenimiento) |

---

## 2. Servidor de streaming (puerto 8001)

| Ruta | Descripción |
|---|---|
| `GET /live/<token>/<canal>.ts` | TS del canal si el token lo permite |
| `GET /live/<token>/<canal>/index.ts` | alias |
| `GET /live/<token>/playlist.m3u` (o `.m3u8`) | lista M3U de los canales permitidos |
| `GET /live/<token>` | información JSON de la cuenta |
| `GET <uri-de-la-salida>` | salida directa, sólo si está marcada como pública |

```console
$ ffprobe -v error -show_entries stream=codec_name \
      "http://IP:8001/live/8f14e45fceea167a5a36dedd4bea2543/ch_7f3a91.ts"
h264
mp2
```

Códigos: `403` token inválido/expirado/sin permiso o límite de conexiones alcanzado
(`429`), `404` canal inexistente o sin emisión.

---

## 3. API del panel

### Autenticación

```console
$ curl -s -c cookies.txt -H 'Content-Type: application/json' \
       -d '{"username":"admin","password":"…"}' \
       http://IP:8000/api/auth/login
{"ok":true,"user":{"id":"u_ab12cd","username":"admin","role":"admin",…},
 "csrf":"1bZ…","version":"1.0.0","hostname":"tv01","server_time":1730000000}

$ CSRF=$(python3 -c "import json;print(json.load(open('login.json'))['csrf'])")
```

A partir de ahí:

```console
$ curl -s -b cookies.txt http://IP:8000/api/channels
$ curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
       -d '{"name":"Nuevo","inputs":[{"type":"udp","addr":"239.1.1.1","port":1234}],
            "outputs":[{"type":"http","uri":"/nuevo","public":false}]}' \
       http://IP:8000/api/channels
```

### Endpoints

| Método | Ruta | Rol mínimo | Notas |
|---|---|---|---|
| POST | `/api/auth/login` | — | `{username,password}` → cookie + `csrf` |
| POST | `/api/auth/logout` | — | |
| GET | `/api/auth/me` | sesión | usuario, `csrf`, capacidades, `ui` |
| POST | `/api/auth/password` | sesión | `{current,password}` |
| GET | `/api/auth/sessions` · POST `/api/auth/sessions/purge` | admin | sesiones activas |
| GET | `/api/health` | — | latido (sin sesión) |
| GET | `/api/system/status` | viewer | host + astra + agente + canales (`200` aunque el agente esté caído) |
| GET | `/api/system/history?minutes=60` | viewer | serie temporal |
| GET | `/api/stats/bitrate?minutes=60` | viewer | bitrate por canal |
| GET | `/api/events` | viewer | **SSE** con el estado en vivo |
| GET | `/api/channels` | viewer | canales + estado del agente |
| POST | `/api/channels` | operator | crear |
| GET/PUT/DELETE | `/api/channels/{id}` | viewer / operator | |
| POST | `/api/channels/{id}/action` | operator | `{action:start\|stop\|restart}` |
| POST | `/api/channels/{id}/duplicate` | operator | |
| POST | `/api/channels/reorder` | operator | `{ids:[…]}` |
| POST | `/api/channels/import` | operator | `{text,format:"m3u",group}` |
| GET | `/api/channels/{id}/psi` | operator | proxy del agente (`502` si está caído) |
| GET | `/api/channels/{id}/clients` | viewer | espectadores |
| GET | `/api/channels/export.m3u?token=` | operator | exportar lista |
| GET/POST/PUT/DELETE | `/api/users[/{id}]` | admin | |
| POST | `/api/users/{id}/token/rotate` | admin | nuevo token |
| POST | `/api/users/{id}/password` | admin | restablecer |
| GET | `/api/playlists/{token}.m3u` (o `.m3u8`) | — | **público** (el token es la credencial) |
| GET | `/api/playlists/public.m3u` | — | canales con salida pública |
| GET | `/api/enums` | viewer | valores válidos de los formularios |
| GET | `/api/dvb/adapters` | viewer | proxy del agente |
| GET | `/api/dvb/transponders` | viewer | lista aplanada |
| GET/POST/PUT/DELETE | `/api/dvb/satellites[/{id}]` | viewer / operator | |
| POST | `/api/dvb/satellites/{id}/transponders` | operator | alta masiva |
| DELETE | `/api/dvb/satellites/{id}/transponders/{tp}` | operator | baja |
| POST | `/api/dvb/scan` | operator | `{satellite_id}` o `{band:"uhf",channel_first,channel_last}` o `{transponders:[…]}` |
| GET | `/api/dvb/scan/{id}` · POST `/api/dvb/scan/{id}/stop` | operator | progreso |
| POST | `/api/dvb/scan/apply` | operator | `{services:[…],group,adapter,device}` |
| GET | `/api/logs/astra` · `/api/logs/panel` | viewer | `lines,level,q` |
| GET | `/api/logs/astra/stream` | viewer | **SSE** de líneas nuevas (el log del panel se consulta por polling) |
| GET | `/api/logs/audit?limit=200` | viewer | auditoría (con `iso`) |
| GET/PUT | `/api/settings` | admin | `panel.json` |
| POST | `/api/service/{start\|stop\|restart\|status}-astra` | admin | systemd |
| GET | `/api/export` · POST `/api/import` | admin | respaldo/restauración |

Las mutaciones exigen `X-CSRF-Token` con el valor devuelto por `/api/auth/login` o
`/api/auth/me`. Sin él → `403`.

Si el agente no responde: `/api/system/status` y `/api/channels` siguen devolviendo `200`
con `agent:{ok:false,error:"…"}` y las mutaciones incluyen `agent_error`; en cambio
`/psi`, `/dvb/adapters`, `/dvb/scan` y las acciones de canal devuelven `502`.

`PUT /api/settings` responde además con `restart_required` (cambió `listen`, `port` o
`stream`) y `agent_config:{ok,path,error}` — el puerto de streaming se sincroniza en
`agent.json`, que es lo que lee el núcleo.

### Server-Sent Events

`GET /api/events` emite un evento `status` con el resumen en vivo:

```
event: status
data: {"t":1730000000,"astra":{"running":true,"bitrate_total":21340,"viewers":7,…},
       "agent":{"ok":true,"error":""},
       "channels":[{"id":"ch_7f3a91","name":"Canal Uno HD","running":true,"on_air":true,
                    "bitrate":5340,"viewers":3,"errors":{…}}],
       "host":{"cpu":12,"mem":41,"net_rx":523411,"net_tx":1200344}}
```

`GET /api/logs/astra/stream?level=error&q=canal` emite eventos `log` con las líneas
nuevas que cumplen el filtro.

### Ejemplos de respuestas

`GET /api/system/status`:

```json
{
  "ok": true,
  "host": {
    "hostname": "tv01", "uptime": 86400, "load": [0.4, 0.5, 0.6],
    "cpu": {"count": 4, "model": "Intel…", "usage": 12},
    "mem": {"total": 8322383872, "used": 3412341248, "available": 4910042624,
            "percent": 41, "swap_total": 0, "swap_used": 0, "swap_percent": 0},
    "net": {"interface": "eth0", "rx": 123456789, "tx": 987654321,
            "rx_rate": 523411, "tx_rate": 1200344,
            "interfaces": [{"name": "eth0", "ipv4": "192.168.1.10"}]},
    "disk": {"total": 500107862016, "used": 12003456789, "free": 488104405227, "percent": 3},
    "os": {"name": "Ubuntu 22.04.4 LTS", "kernel": "5.15.0-105-generic",
           "arch": "x86_64", "python": "3.10.12"}
  },
  "astra": {"running": true, "pid": 1534, "version": "4.0 dev:282", "uptime": 3600,
            "channels_running": 4, "channels_total": 5, "viewers": 7,
            "bitrate_total": 21340, "rss_kb": 24880},
  "agent": {"ok": true, "url": "http://127.0.0.1:9977", "latency_ms": 3, "error": "", "last_ok": 1730000000},
  "panel": {"version": "1.0.0", "uptime": 1800, "sessions": 1, "channels": 5, "users": 3,
            "service_control": true},
  "channels": [ … ],
  "server_time": 1730000000
}
```

`GET /api/channels` (un canal, resumido):

```json
{"ok":true,"agent":{"ok":true,"error":""},
 "channels":[{
   "id":"ch_7f3a91","name":"Canal Uno HD","group":"General","logo":"","epg_id":"uno.tvg",
   "enabled":true,"tags":["hd"],"running":true,"on_air":true,"bitrate":5340,"viewers":3,
   "errors":{"cc":0,"sc":0,"pes":0},
   "inputs":[{"index":1,"type":"http","on_air":true,"bitrate":5340,
              "cc_error":0,"sc_error":0,"pes_error":0,"error":""}],
   "outputs":[{"type":"http","target":"/uno","public":false}],
   "inputs_config":[{"type":"http","url":"http://origen:8080/uno.ts",
                     "options":{"pnr":101,"set_pnr":1}}],
   "outputs_config":[{"type":"http","uri":"/uno","public":false,"cors":true}],
   "updated_at":1730000000}]}
```

Notas de forma:

* `inputs`/`outputs` son el **estado en vivo** que informa el agente; `inputs_config`
  y `outputs_config` son la **configuración** guardada en `channels.json` (lo que edita
  el formulario).
* `agent.ok=false` indica que el agente no responde: el resto de campos siguen siendo
  válidos (configuración y métricas del host).
* El PSI de un canal llega como `{ok,id,tables:[…],stats:{bitrate,on_air}}`, donde cada
  entrada de `tables` es una tabla del core (`psi:"pat"|"pmt"|"sdt"|"cat"`) con sus
  `programs`/`streams`/`services`/`descriptors` tal cual los entrega `analyze()`.
* Las rutas de lista aceptan `.m3u` y `.m3u8` (`/api/playlists/<token>.m3u8` es válido).
