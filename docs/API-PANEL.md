# API del panel (para el desarrollador frontend)

Todo sale del panel en `http://<servidor>:8000`. Sesión por cookie tras el login;
las escrituras necesitan la cabecera `X-CSRF-Token` que devuelve el propio login.

## 1. Entrar

```http
POST /api/auth/login
Content-Type: application/json

{"username": "admin", "password": "<la del fichero /etc/astra-webui/initial-admin-password>"}
```

Devuelve `{"ok": true, "csrf": "…", "user": {…}}` y deja la cookie de sesión.

## 2. Los datos que pide el grid

| Método | Ruta | Rol | Para qué |
|---|---|---|---|
| `GET` | `/api/channels` | user | channels |
| `GET` | `/api/channels/` | user | channels |
| `POST` | `/api/channels` | operator | channels |
| `PUT` | `/api/channels/` | user | channels |
| `DELETE` | `/api/channels/` | user | channels |
| `POST` | `/api/channels/outputs` | operator | channels |
| `POST` | `/api/channels` | user | channels |
| `POST` | `/api/channels/import` | operator | channels |
| `POST` | `/api/channels/` | user | channels |
| `POST` | `/api/channels/` | user | channels |
| `GET` | `/api/channels/` | user | channels |
| `GET` | `/api/channels/` | user | channels |
| `GET` | `/api/users` | admin | channels |
| `POST` | `/api/users` | admin | channels |
| `GET` | `/api/users/` | user | channels |
| `PUT` | `/api/users/` | user | channels |
| `DELETE` | `/api/users/` | user | channels |
| `POST` | `/api/users/` | user | channels |
| `POST` | `/api/users/` | user | channels |
| `GET` | `/api/playlists/` | user | channels |
| `GET` | `/api/playlists/public.m3u8?` | user | channels |
| `GET` | `/api/channels/export.m3u8?` | operator | channels |
| `GET` | `/api/enums` | user | channels |
| `GET` | `/api/dvb/adapters` | user | dvb |
| `GET` | `/api/dvb/transponders` | user | dvb |
| `GET` | `/api/dvb/satellites` | user | dvb |
| `POST` | `/api/dvb/satellites` | operator | dvb |
| `PUT` | `/api/dvb/satellites/` | user | dvb |
| `DELETE` | `/api/dvb/satellites/` | user | dvb |
| `POST` | `/api/dvb/satellites/` | user | dvb |
| `DELETE` | `/api/dvb/satellites/` | user | dvb |
| `PUT` | `/api/dvb/satellites/` | user | dvb |
| `POST` | `/api/dvb/scan` | operator | dvb |
| `GET` | `/api/dvb/scan/` | user | dvb |
| `POST` | `/api/dvb/scan/` | user | dvb |
| `POST` | `/api/dvb/scan/apply` | operator | dvb |
| `GET` | `/api/cam` | admin | cam |
| `POST` | `/api/cam` | admin | cam |
| `GET` | `/api/softcams` | admin | cam |
| `POST` | `/api/softcams` | admin | cam |
| `GET` | `/api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})/full` | admin | cam |
| `POST` | `/api/channels/softcam` | admin | cam |
| `POST` | `/api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})/apply` | admin | cam |
| `DELETE` | `/api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})` | admin | cam |
| `DELETE` | `/api/cam` | admin | cam |
| `GET` | `/api/health` | user | core |
| `GET` | `/api/system/status` | user | core |
| `GET` | `/api/system/history` | user | core |
| `GET` | `/api/stats/bitrate` | user | core |
| `GET` | `/api/events` | user | core |
| `GET` | `/api/logs/astra` | user | core |
| `GET` | `/api/logs/panel` | user | core |
| `GET` | `/api/logs/astra/stream` | user | core |
| `GET` | `/api/logs/audit` | user | core |
| `GET` | `/api/settings` | admin | core |
| `PUT` | `/api/settings` | admin | core |
| `POST` | `/api/service/(?P<action>restart|start|stop|status)-astra` | admin | core |
| `GET` | `/api/export` | admin | core |
| `POST` | `/api/import` | admin | core |
| `POST` | `/api/auth/login` | user | auth |
| `POST` | `/api/auth/logout` | user | auth |
| `GET` | `/api/auth/me` | user | auth |
| `POST` | `/api/auth/password` | user | auth |
| `GET` | `/api/auth/sessions` | admin | auth |
| `POST` | `/api/auth/sessions/purge` | admin | auth |

## channels

- `GET /api/channels` (user)
- `GET /api/channels/` (user)
- `POST /api/channels` (operator)
- `PUT /api/channels/` (user)
- `DELETE /api/channels/` (user)
- `POST /api/channels/outputs` (operator)
- `POST /api/channels` (user)
- `POST /api/channels/import` (operator)
- `POST /api/channels/` (user)
- `POST /api/channels/` (user)
- `GET /api/channels/` (user)
- `GET /api/channels/` (user)
- `GET /api/users` (admin)
- `POST /api/users` (admin)
- `GET /api/users/` (user)
- `PUT /api/users/` (user)
- `DELETE /api/users/` (user)
- `POST /api/users/` (user)
- `POST /api/users/` (user)
- `GET /api/playlists/` (user)
- `GET /api/playlists/public.m3u8?` (user)
- `GET /api/channels/export.m3u8?` (operator)
- `GET /api/enums` (user)

## dvb

- `GET /api/dvb/adapters` (user)
- `GET /api/dvb/transponders` (user)
- `GET /api/dvb/satellites` (user)
- `POST /api/dvb/satellites` (operator)
- `PUT /api/dvb/satellites/` (user)
- `DELETE /api/dvb/satellites/` (user)
- `POST /api/dvb/satellites/` (user)
- `DELETE /api/dvb/satellites/` (user)
- `PUT /api/dvb/satellites/` (user)
- `POST /api/dvb/scan` (operator)
- `GET /api/dvb/scan/` (user)
- `POST /api/dvb/scan/` (user)
- `POST /api/dvb/scan/apply` (operator)

## cam

- `GET /api/cam` (admin)
- `POST /api/cam` (admin)
- `GET /api/softcams` (admin)
- `POST /api/softcams` (admin)
- `GET /api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})/full` (admin)
- `POST /api/channels/softcam` (admin)
- `POST /api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})/apply` (admin)
- `DELETE /api/softcams/(?P<softcam_id>[A-Za-z0-9_\-]{3,32})` (admin)
- `DELETE /api/cam` (admin)

## 3. Qué trae `/api/channels` (lo que necesita cada celda del grid)

```json
{
  "ok": true,
  "channels": [
    {
      "id": "ch_jvu0lr",
      "name": "Servicio 4",
      "group": "70w 11170",
      "enabled": true,
      "bitrate": 980,
      "on_air": false,
      "running": true,
      "viewers": 0,
      "errors": { "cc": 0, "pes": 0 },
      "inputs": [ { "type": "dvb", "options": { "cam": { "host": "127.0.0.1", "port": 5000 } } } ],
      "inputs_config": [ "..." ],
      "outputs": [ { "type": "http", "uri": "/servicio-4" } ],
      "outputs_config": [ "..." ],
      "logo": "", "epg_id": "", "tags": [], "updated_at": 1760000000
    }
  ],
  "agent": { "ok": true, "error": "" }
}
```

- `group` es el **mux/frecuencia**: es la clave para los encabezados del grid.
  En este servidor hay dos: `70w 11170` (36 canales) y `70w 10970` (21).
- `bitrate` viene en **Kbit/s** (980 = 980 Kbit/s). El total que enseña el panel, 47,5 Mbit/s,
  es la suma de esos valores.
- `on_air: true` = verde (descifrando y en el aire). `running: true` con `on_air: false` =
  arrancado pero sin datos. `enabled: false` = gris, deshabilitado.
- `viewers` es el número de conexiones activas y `errors` los contadores por canal
  (`cc` = continuidad, `pes` = PES). **No hay campo `status`**: el estado se deduce de
  `on_air` / `running` / `errors`.
- `inputs_config` y `outputs_config` son las versiones legibles (editables) de `inputs` y
  `outputs`; para pintar basta `inputs`/`outputs`, para editar se usan las `_config`.
- La lista trae **57 canales** y 13 en el aire ahora mismo (el límite lo pone la tarjeta).

## 4. Frecuencia de refresco

No hay WebSocket abierto al navegador: el panel **sondea** `/api/channels` cada
**2 s** (el pliego pide 1-2 s). Es una única llamada para todo el grid, no una por canal.

## 5. Señal del sintonizador (S / Q / BER / UNC)

Estas métricas **no son por canal**, son del **adaptador/mux**. El núcleo de Astra las
publica en su interfaz de control como evento de adaptador con estos campos:

```json
{ "dvb_id": "dvb0", "status": 18, "signal": 81, "snr": 67, "ber": 0, "unc": 0,
  "signal_db": -2022, "snr_db": 1269, "bitrate": 47500 }
```

- `signal`/`snr` en porcentaje (si el driver los da en 0-65535, dividir: `x * 99 / 65535`).
- `signal_db`/`snr_db` vienen en centésimas (-2022 = -20.22 dBm, 1269 = 12.69 dB).
- `status` es una máscara: 1 SEÑAL · 2 PORTADORA · 4 FEC · 8 SINCRONISMO · 16 LOCK.

**Ya está resuelto**: `GET /api/tuners` devuelve la señal de cada adaptador en uso.

```json
{ "ok": true, "tuners": {
    "0": { "adapter": 0, "available": true, "signal": 74, "snr": 65, "ber": 0,
           "unc": null, "locked": true, "has_signal": true, "has_carrier": true,
           "has_fec": true, "has_sync": true, "has_lock": true,
           "frontend": "TurboSight TBS 6904se DVB-S/S2/S2x  (DVBS)" } } }
```

- `signal` y `snr` en porcentaje (0-100), listos para las barras del pliego.
- `ber` en bits erróneos; `unc` puede venir `null` porque el driver del TBS no rellena
  ese contador (imprime basura negativa, que se descarta).
- `locked` es el bit L de SCVYL (LOCK). Los `has_*` indican qué bits
  vinieron activos; `signal` y `snr` son porcentajes numéricos.
- Los adaptadores que se consultan salen de los canales (los que usa cada mux), no de
  una lista fija.

Se lee con `femon` (paquete `dvb-apps`) en modo consulta: abrir el frontend para leer
estado **no ocupa el sintonizador**, así que convive con el streaming. El panel corre
como `astra`, que está en el grupo `video`. Si `femon` no está, la respuesta trae
`available: false` con el motivo en vez de fallar.

## 6. Ver el vídeo

```
http://<servidor>:8001/live/<token>/<id-del-canal>.ts
```

Es MPEG-TS en directo; se ve en VLC, en un `<video>` no (necesita HLS o un reproductor
de TS). El token de emisión lo da `/api/cam` y `/api/channels`.
