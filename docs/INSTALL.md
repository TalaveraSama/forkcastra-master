# Instalación de Astra WebUI

Guía completa para Ubuntu 20.04 / 22.04 / 24.04 y Debian 11/12 (amd64 y arm64).

---

## 1. Requisitos

**En el servidor donde se compila el paquete** (puede ser el mismo servidor de streaming):

```bash
sudo apt update
sudo apt install -y build-essential python3 git
```

- `build-essential` trae `gcc`, `make` y `linux-libc-dev` (los headers `linux/dvb/*.h`
  que necesita el módulo DVB del fork).
- `python3` (>= 3.8) es todo lo que necesita el panel: **cero dependencias de terceros**.

**En el servidor de streaming** (si compilas en otra máquina): sólo `python3`, `libc6` y
`adduser`, que ya vienen en cualquier Ubuntu.

Puertos usados por defecto:

| Puerto | Servicio | Configurable en |
|---|---|---|
| 8000/tcp | panel web | `/etc/astra-webui/panel.json` → `port` |
| 8001/tcp | streaming MPEG-TS | `panel.json` → `stream.port` y `agent.json` → `stream.port` |
| 9977/tcp | API de control del agente (sólo 127.0.0.1) | `agent.json` → `control.port` |

---

## 2. Construir el paquete .deb

```bash
git clone https://github.com/TalaveraSama/forkcastra-master.git
cd astra-webui
./packaging/build-deb.sh --source ../forkcastra-master
```

Salida: `dist/astra-webui_4.0.282-1_amd64.deb`.

Opciones útiles:

| Opción | Para qué |
|---|---|
| `--source DIR` | ruta del código del fork (por defecto busca `repo/tree` o el directorio padre) |
| `--core-bin FILE` | reutilizar un binario `astra` ya compilado (omite la compilación) |
| `--no-core` | generar sólo el paquete del panel (`astra-webui-panel`, `arch: all`) |
| `--arch arm64` | forzar la arquitectura (cross-compilación) |
| `--jobs 4` | paralelismo de `make` |
| `--out DIR` | directorio de salida |
| `-v` | mostrar el contenido del paquete al terminar |
| `--keep` | conservar `build/pkgroot` para inspeccionarlo |

El script:

1. lee la versión de `version.h` (`4.0.282` → paquete `4.0.282-1`);
2. compila el núcleo (`./configure.sh && make -j`);
3. prepara el árbol con `tools/deb_stage.py` (permissions exactos, enlaces, conffiles);
4. empaqueta con `dpkg-deb` (o con `tools/build_deb.py` si no está disponible);
5. verifica el `.deb` (estructura, control, conffiles, permisos).

> **Compatibilidad**: el binario queda enlazado contra la glibc de la máquina donde se
> compila. Para servidores antiguos, compila el `.deb` en la versión más vieja que vayas
> a soportar (por ejemplo 20.04) o usa Docker:
> `docker run --rm -v "$PWD:/w" -w /w ubuntu:20.04 bash -c "apt update && apt install -y build-essential python3 && ./packaging/build-deb.sh --source /w/repo/tree"`

---

## 3. Instalar

```bash
sudo apt install -y ./dist/astra-webui_4.0.282-1_amd64.deb
```

`postinst` hace todo esto y lo muestra por pantalla:

- crea el usuario y grupo de sistema `astra` (y lo añade a `video` y `audio` para DVB);
- crea `/var/lib/astra-webui` (datos), `/var/log/astra` y `/var/log/astra-webui`;
- genera `/etc/astra-webui/agent.token` (secreto panel ↔ agente, `0640 root:astra`);
- crea los ficheros de datos vacíos (`channels.json`, `auth.json`, `users.json`, `dvb.json`);
- genera la contraseña inicial de `admin` en `/etc/astra-webui/initial-admin-password`;
- conserva un `/etc/astra/astra.conf` previo como `astra.conf.pre-webui`;
- habilita y arranca `astra.service` y `astra-webui.service`.

Al final imprime algo así:

```
============================================================
 Astra WebUI instalado
------------------------------------------------------------
 Panel:      http://<IP-del-servidor>:8000
 Usuario:    admin
 Contraseña: 3f9a1c7d2b84e05f
...
```

---

## 4. Primer acceso

> El panel usa JavaScript moderno (módulos ES, `?.`, `??`, SSE): necesitas un navegador
> de 2021 o posterior (Chrome/Edge 90+, Firefox 90+, Safari 15+). No hay soporte para
> Internet Explorer.

1. Abre `http://IP:8000` y entra con `admin` + la contraseña mostrada.
2. Cambia la contraseña en **Ajustes → Mi contraseña**.
3. Borra el fichero con la contraseña temporal:
   ```bash
   sudo shred -u /etc/astra-webui/initial-admin-password
   ```
4. Crea tus canales en **Canales → Nuevo**:
   - *HTTP*: `http://origen:8080/canal.ts` (acepta `usuario:clave@host`).
   - *UDP*: `239.10.0.1:1234` (marca RTP si la fuente lo usa).
   - *Fichero*: ruta absoluta a un `.ts` (opción de bucle para pruebas).
   - *DVB*: adaptador, sistema de entrega (S2/T2/C…), frecuencia y parámetros del
     transponder; `pnr` para elegir el programa dentro del mux.
5. Añade una salida **HTTP** (por defecto `/nombre-del-canal`) y guarda. El canal arranca
   en caliente: no se reinicia el proceso ni se cortan los demás.
6. En **Usuarios**, crea un cliente, copia su URL M3U o la URL directa
   `http://IP:8001/live/<token>/<id_canal>.ts` y pruébala con VLC/ffprobe.

---

## 5. Dónde queda todo

| Ruta | Contenido |
|---|---|
| `/usr/bin/astra-4.0` (+ enlace `/usr/bin/astra`) | núcleo compilado del fork |
| `/etc/astra/scripts-4.0/` | `stream.lua`, `analyze.lua`, `dvbls.lua`, `xproxy.lua` |
| `/usr/share/astra-webui/agent/` | agente Lua (script principal de astra) |
| `/usr/share/astra-webui/panel/` | backend Python |
| `/usr/share/astra-webui/web/` | interfaz web |
| `/etc/astra-webui/panel.json` | configuración del panel (**conffile**) |
| `/etc/astra-webui/agent.json` | configuración del agente (**conffile**) |
| `/etc/astra-webui/agent.token` | secreto panel ↔ agente |
| `/etc/default/astra-webui` | variables de entorno del servicio (**conffile**) |
| `/var/lib/astra-webui/` | `channels.json`, `auth.json`, `users.json`, `dvb.json`, auditoría, grabaciones |
| `/var/log/astra/astra.log` | log del núcleo |
| `/var/log/astra-webui/panel.log` | log del panel |

Comandos habituales:

```bash
systemctl status astra astra-webui
journalctl -u astra -f
journalctl -u astra-webui -f
tail -f /var/log/astra/astra.log

# utilidades del panel
astra-webui --check                     # valida la configuración
astra-webui --reset-admin-password      # nueva contraseña para admin (la imprime)
astra-webui --print-version
```

---

## 6. Ajustes de red y TLS

**Cambiar la IP/puerto público del streaming** (para que las listas M3U apunten a la
dirección correcta cuando el panel se ve por otro nombre):

`/etc/astra-webui/panel.json`

```json
"stream": { "host": "tv.midominio.com", "port": 8001, "public_base": "https://tv.midominio.com" }
```

`public_base` se usa tal cual en las URLs (con o sin TLS). Si lo dejas vacío se usa el
`Host` de la petición.

Al cambiar `stream.port` desde la interfaz, el panel **sincroniza ese puerto también en
`/etc/astra-webui/agent.json`** (el agente y el panel leen ficheros distintos). El nuevo
puerto no se aplica hasta reiniciar el núcleo: usa el botón *Reiniciar astra* en
*Ajustes → Servicio* (o `sudo systemctl restart astra`).

**nginx como frontal con TLS** (recomendado si el panel sale a Internet):

```nginx
server {
    listen 443 ssl http2;
    server_name panel.midominio.com;

    ssl_certificate     /etc/letsencrypt/live/panel.midominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.midominio.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # el panel usa SSE: hay que desactivar el buffering
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

Si usas proxy inverso, activa en `panel.json` → `"security": {"trust_proxy": true}` para
que el panel lea la IP real de `X-Forwarded-For` (y la use en la auditoría y en la lista
de redes permitidas). Además, pon `"base_url": "https://panel.midominio.com"` para que la
cookie de sesión se marque `Secure`.

El puerto de streaming (8001) conviene dejarlo directo (nginx no mejora nada y consume
CPU); si necesitas TLS también para los clientes, añade otro `server` con
`proxy_pass http://127.0.0.1:8001;` y `proxy_buffering off;` y pon esa URL en `public_base`.

**Cortafuegos**:

```bash
sudo ufw allow 8000/tcp      # panel  (o sólo desde tu red: ufw allow from 192.168.1.0/24 to any port 8000)
sudo ufw allow 8001/tcp      # streaming
```

Mejor aún, en `panel.json` → `"security": {"allowed_networks": ["192.168.1.0/24", "10.0.0.0/8"]}`
para limitar quién puede llegar al panel.

---

## 7. DVB: preparación del sistema

1. **Firmware y drivers**: instala `linux-firmware` y, si usas tarjetas USB, comprueba
   `dmesg | grep -i dvb`. Los adaptadores deben aparecer en `/dev/dvb/adapterN/`.
2. **Permisos**: el usuario `astra` ya está en el grupo `video`. Verifica:
   ```bash
   ls -l /dev/dvb/adapter0/
   groups astra
   ```
3. **Listado de adaptadores**: en el panel, **DVB → Adaptadores** (equivale a
   `astra-webui` + API `/dvb/adapters`, que usa `dvbls()` del core). Muestra el frontend,
   la MAC del adaptador de red asociado y si está ocupado.
4. **Escaneo**: un adaptador sólo admite un lector a la vez. Si hay canales emitiendo
   desde ese adaptador, el escaneo fallará (frontend ocupado): para el canal o usa otro
   adaptador. Cada transponder se sintoniza durante el tiempo indicado (8 s por defecto);
   en DVB-S con LNB universal usa `9750:10600:11700`.
5. **Crear canales desde el escaneo**: selecciona los servicios encontrados y pulsa
   *Crear canales*; se generan con su `pnr` y una salida HTTP con el nombre del servicio.

---

## 8. Actualizar

```bash
git -C ../forkcastra-master pull
./packaging/build-deb.sh --source ../forkcastra-master
sudo apt install -y ./dist/astra-webui_*_amd64.deb
```

`prerm` no detiene los servicios en una actualización; `postinst` los reinicia y conserva
`panel.json`, `agent.json` y todos los datos de `/var/lib/astra-webui`.

---

## 9. Desinstalar

```bash
sudo apt remove astra-webui          # quita el software, CONSERVA los datos
sudo apt purge astra-webui           # quita también /var/lib/astra-webui, logs, usuario y token
```

Tras `purge` quedan intactos los ficheros del núcleo que no gestiona el paquete
(`/etc/astra/astra.conf.pre-webui`, por ejemplo) y `/var/log/astra` (se borra a mano si
no lo usa nada más).

---

## 10. Instalación manual (sin .deb)

Si prefieres no usar paquetes:

```bash
# 1) núcleo
cd forkcastra-master
./configure.sh && make -j"$(nproc)"
sudo make install          # /usr/bin/astra-4.0 y /etc/astra/scripts-4.0
sudo make link             # enlaces /usr/bin/astra y /usr/bin/astra-analyze

# 2) agente + panel + web
sudo install -d /usr/share/astra-webui/{agent,panel,web}
sudo cp -a agent/.        /usr/share/astra-webui/agent/
sudo cp -a panel/astra_webui/. /usr/share/astra-webui/panel/astra_webui/
sudo cp -a panel/web/.    /usr/share/astra-webui/web/

# 3) usuario, directorios y token
sudo adduser --system --group --home /var/lib/astra-webui --no-create-home astra
sudo adduser astra video
sudo install -d -o astra -g astra /var/lib/astra-webui/recordings /var/log/astra /var/log/astra-webui
sudo install -d -o root -g astra -m 0750 /etc/astra-webui
sudo install -m 0640 -o root -g astra packaging/config/panel.json /etc/astra-webui/panel.json
sudo install -m 0640 -o root -g astra packaging/config/agent.json /etc/astra-webui/agent.json
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | sudo tee /etc/astra-webui/agent.token >/dev/null
sudo chown root:astra /etc/astra-webui/agent.token && sudo chmod 0640 /etc/astra-webui/agent.token

# 4) servicios
sudo install -m 0644 packaging/common/*.service /lib/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astra astra-webui
```

La contraseña inicial de `admin` se imprime en `journalctl -u astra-webui -n 20`.

---

## 11. Migrar desde un `astra.conf` existente

El agente sustituye a `astra.conf` como script principal: los canales se declaran en
`/var/lib/astra-webui/channels.json` (que gestiona el panel). Tu configuración antigua se
conserva en `/etc/astra/astra.conf.pre-webui` para consultarla; para pasarla al panel,
crea los canales equivalentes desde la interfaz (o importa la lista M3U de orígenes con
*Canales → Importar M3U*).

Si quieres volver al comportamiento anterior:

```bash
sudo systemctl stop astra
sudo sed -i 's#/usr/share/astra-webui/agent/agent.lua /etc/astra-webui/agent.json#/etc/astra/astra.conf#' \
    /lib/systemd/system/astra.service
sudo systemctl daemon-reload && sudo systemctl start astra
```

## Desinstalar

```bash
# quita el panel, el agente y el núcleo, guardando copia de canales y softcams
curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/desinstalar.sh | sudo bash

# ver lo que haría, sin tocar nada
sudo bash desinstalar.sh --dry-run

# borrar también datos y configuración (empezar de cero)
sudo bash desinstalar.sh --purge
```

Antes de tocar nada guarda una copia en `/var/backups/astra-webui/<fecha>/` con
`etc-astra-webui.tar.gz` y `var-lib-astra-webui.tar.gz` (canales, CAM, softcams).
No toca ncam, oscam, pcscd ni el driver TBS.
