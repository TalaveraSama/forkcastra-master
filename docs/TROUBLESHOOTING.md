# Diagnóstico de problemas

Orden recomendado de comprobación:

```bash
systemctl status astra astra-webui
journalctl -u astra -n 50 --no-pager
journalctl -u astra-webui -n 50 --no-pager
sudo tail -50 /var/log/astra/astra.log
curl -s http://127.0.0.1:8000/api/health
TOKEN=$(sudo cat /etc/astra-webui/agent.token)
curl -s -H "X-Astra-Token: $TOKEN" http://127.0.0.1:9977/status
```

---

## El panel no arranca

**Síntoma**: `systemctl status astra-webui` en `failed` o el puerto 8000 no responde.

| Causa | Diagnóstico | Solución |
|---|---|---|
| Puerto ocupado | `sudo ss -ltnp \| grep 8000` | cambia `port` en `/etc/astra-webui/panel.json` y `systemctl restart astra-webui` |
| `panel.json` inválido | `sudo -u astra astra-webui --check` | corrige el JSON (el log dice la línea); también vale `python3 -m json.tool` |
| Faltan directorios | `astra-webui --check` los lista | `sudo mkdir -p` con `-o astra -g astra`, o reinstala el paquete |
| Permisos de datos | `ls -l /var/lib/astra-webui` | deben ser de `astra:astra` (0750 dir, 0640 ficheros) |
| Interfaz web ausente | el log dice `no existe el directorio web` | revisa `/usr/share/astra-webui/web/index.html` |
| Python antiguo | `python3 -V` | se necesita Python **3.8** o superior |

```bash
sudo -u astra PYTHONPATH=/usr/share/astra-webui/panel \
     python3 -m astra_webui --config /etc/astra-webui/panel.json --check
```

---

## El panel dice «no se puede contactar con el agente» (502)

El panel funciona (puedes editar, ver logs y métricas del host), pero no puede hablar con
`127.0.0.1:9977`:

1. ¿Está vivo `astra`? `systemctl status astra`.
2. ¿Escucha en 9977? `sudo ss -ltnp | grep 9977`.
3. ¿Coincide el token?
   ```bash
   sudo cat /etc/astra-webui/agent.token
   # debe ser el mismo fichero que lee el agente (agent.json → control.token_file)
   ```
   Si rotaste el token a mano, reinicia ambos: `sudo systemctl restart astra astra-webui`.
4. ¿Hay otro proceso en 9977? El agente aborta si no puede hacer *bind* (mira
   `journalctl -u astra`): cambia `control.port` en `agent.json` **y** `agent.url` en
   `panel.json`.

Mientras el agente esté caído, el panel sigue escribiendo `channels.json`; en cuanto el
agente arranque aplicará los cambios (vigila el fichero cada 3 s).

---

## `astra` no arranca o se reinicia en bucle

| Mensaje en el log | Significado |
|---|---|
| `no se pudo cargar stream.lua del core` | falta `/etc/astra/scripts-4.0/stream.lua`: reinstala el paquete o ajusta `streams_script` en `agent.json` |
| `[http_server 0.0.0.0:8001]` + abort | el puerto de streaming está ocupado → cambia `stream.port` en `agent.json` **y** en `panel.json` |
| `module dvbls is required` | el binario se compiló sin el módulo `dvb` (revisa `configure.sh --with-modules`) |
| `option 'x' is required` | configuración de canal inválida que se coló hasta el núcleo |
| `abort execution. Lua backtrace:` | error Lua: las líneas siguientes indican el fichero y la línea |

El agente **valida todos los canales antes** de llamar al núcleo para que un canal mal
formado no tumbe el proceso; si aun así ves un abort, copia el *backtrace* completo y el
canal implicado.

### `code=dumped, signal=ILL`: instrucción ilegal (binario no portable)

`systemctl status astra` muestra `Result: core-dump` con `signal=ILL` y el servicio se
reinicia cada pocos segundos. Es un problema **del binario**, no de la configuración: se
compiló con optimizaciones de **la CPU que compiló** (`-march=...`, que añade `cpucheck` de
`configure.sh`) y tu procesador no tiene esos juegos de instrucciones. Pasa cuando el
`.deb` se construye en otra máquina (por ejemplo, un runner de CI).

```sh
# 1) comprueba si el binario instalado usa AVX (0 = portable)
objdump -d /usr/bin/astra-4.0 | grep -cE '%[yz]mm'

# 2) reconstruye en tu propia máquina: la compilación por defecto ya es genérica
sudo apt install -y build-essential python3 git
git clone --depth 1 https://github.com/TalaveraSama/forkcastra-master.git
cd forkcastra-master
bash packaging/build-deb.sh --source . --out dist --revision 2   # portable
sudo apt install -y ./dist/astra-webui_4.0.282-2_amd64.deb
```

`packaging/build-deb.sh --native` sólo debe usarse si el paquete se queda en esa misma
máquina. Para ver con qué flags se compiló: `grep -E '^CFLAGS' Makefile` (no debe aparecer
`-march`).

### `apt` rechaza el paquete: «Cabecera de miembro del archivo inválida»

El `.deb` se generó con `dpkg-deb` de Ubuntu 20.04 (dpkg 1.19) y `--uniform-compression`:
ese archivo lo lee `dpkg-deb` pero **`apt` lo rechaza**. `packaging/build-deb.sh` usa por
defecto el empaquetador en Python (`--builder python`), que produce `control.tar.gz` +
`data.tar.xz` aceptados por cualquier apt/dpkg.

```sh
python3 tools/check_package.py astra-webui_4.0.282-3_amd64.deb   # incluye esa comprobación
apt-get install -y --reinstall ./astra-webui_4.0.282-3_amd64.deb # prueba real con apt
```

### El log no se escribe: `failed to open /var/log/astra/astra.log (Permission denied)`

Los ficheros de log quedaron con otro dueño (típicamente `root`, por haber ejecutado astra
a mano). Se arregla con:

```sh
sudo install -d -o astra -g astra -m 0750 /var/log/astra /var/log/astra-webui
sudo touch /var/log/astra/astra.log
sudo chown astra:astra /var/log/astra/astra.log && sudo chmod 0640 /var/log/astra/astra.log
sudo systemctl restart astra
```

### El panel no puede reiniciar astra: `sudo: The "no new privileges" flag is set`

El panel arranca/para/reinicia `astra.service` con
`sudo -n /usr/bin/systemctl restart astra.service` (regla de `/etc/sudoers.d/astra-webui`).
Ese `sudo` no puede funcionar si el servicio del panel tiene activo **NoNewPrivileges**, y
systemd lo activa **automáticamente** en cuanto se usa cualquiera de estas opciones de
endurecimiento:

`ProtectKernelTunables`, `ProtectControlGroups`, `RestrictSUIDSGID`, `RestrictNamespaces`,
`RestrictRealtime`, `LockPersonality`, `MemoryDenyWriteExecute`, `ProtectClock`,
`ProtectKernelLogs`, `ProtectKernelModules`, `PrivateDevices`.

Por eso `astra-webui.service` **no** las usa (aunque `systemctl show` diga
`NoNewPrivileges=no`, lo efectivo se ve en `grep NoNewPrivs /proc/$(systemctl show -p
MainPID --value astra-webui)/status`). Comprobación completa:

```sh
grep NoNewPrivs /proc/$(systemctl show -p MainPID --value astra-webui)/status   # debe ser 0
sudo -u astra sudo -n /usr/bin/systemctl restart astra.service                  # debe funcionar
```

Además, la unidad del panel **no** lleva `PartOf=astra.service`: si lo llevara, al reiniciar
astra desde el panel systemd reiniciaría también el panel y la petición HTTP se cortaría
antes de responder (`Remote end closed connection without response`).

### El escaneo DVB se lleva por delante el streaming (y el panel pierde el agente)

Si al escanear un transponder desaparece el agente («El agente Astra no responde») y astra
se reinicia, es porque el módulo `dvb` del núcleo llama a `astra_abort()` cuando el
sintonizador rechaza el tune:

```
ERROR: [dvb_input 0:0] FE_SET_PROPERTY DTV_TUNE failed [Input/output error]
ERROR: [main] abort execution. Lua backtrace: ... aw_dvb.lua ...
```

Desde la versión con `agent/aw_scan.lua` el escaneo se ejecuta en un **proceso hijo**: si el
tune falla, muere sólo el hijo y el panel muestra
`no se pudo sintonizar (sin señal o el sintonizador rechazó los parámetros)`, mientras astra
y los canales siguen emitiendo. Los ficheros de cada transponder se dejan en
`/var/lib/astra-webui/scan/` (`*.json` con lo encontrado y `*.log` con la salida del hijo)
para poder revisarlos.

Si **todos** los transponders fallan, el problema está en el sintonizador y no en el panel:

```sh
ls /dev/dvb/                      # ¿aparecen los adaptadores?
sudo femon -a 0                   # ¿signal/snr > 0? ¿errores?
sudo dmesg | grep -iE 'tbs|i2c|dvb' | tail -20
```

Señales de problema de driver/hardware (no del panel):

| Síntoma | Qué significa |
|---|---|
| `TBSECP3 driver ...: i2c xfer timeout` | el driver no puede hablar con el chip del sintonizador |
| `femon`: `signal 0000`, `snr 0000`, `Problem retrieving frontend information: Unknown error 524` | el sintonizador no responde |
| `DTV_TUNE failed [Input/output error]` en el log de astra | el driver rechaza la sintonía (tuner mudo o parámetros fuera de rango) |

Pasos que suelen resolverlo:

```sh
# 1) reconstruir el driver tal y como lo dejó DKMS (el aviso «Diff between built and
#    installed module!» indica una instalación sucia)
sudo apt install -y linux-headers-$(uname -r) build-essential
sudo dkms status
sudo dkms remove tbs-dvb-driver/<versión> --all
sudo dkms install tbs-dvb-driver/<versión> --force
sudo depmod -a
# 2) reiniciar para que se cargue el módulo nuevo (no se puede descargar si está en uso)
sudo reboot
# 3) tras el reinicio
sudo femon -a 0
```

Si sigue igual: prueba la tarjeta en otro slot PCIe, en otro equipo, o consulta a TBS (puede
ser una incompatibilidad de esa revisión de tarjeta con el driver, o la propia tarjeta).

### La tarjeta desaparece: `ls /dev/dvb` vacío y no sale en `lspci`

Si tras descargar el módulo (`modprobe -r tbsecp3`) o tras un reinicio la tarjeta ya no
aparece **ni en el bus PCI**, un `reboot` puede no bastar: hay tarjetas TBS que solo vuelven
a enumerarse con un **apagado completo**.

```bash
lspci -nn | grep -i tbs      # ¿aparece "TBS Technologies DVB Tuner PCIe Card"?
ls /dev/dvb                  # ¿adapters?
lsmod | grep tbsecp3         # ¿está el módulo cargado?

sudo shutdown -h now         # APAGAR del todo (no reboot)
# esperar ~30 s (o quitar el cable de corriente) y volver a encender
```

Si aparece en `lspci` pero marca `(rev ff)` o el log dice `i2c xfer timeout`, la tarjeta no
responde: reinstala el driver con `drivers/tbs/instalar-tbs-ubuntu.sh` (ver
[drivers/tbs/README.md](../drivers/tbs/README.md)), prueba en otro slot PCIe y, si sigue
igual, consulta a TBS.

---

## Un canal no arranca

Mira `GET /status` → campo `failed`, o en el panel el aviso rojo sobre el canal.

| Error | Causa / solución |
|---|---|
| `entrada N: 'addr' debe ser una IPv4` | la fuente UDP no tiene dirección válida |
| `entrada N: el fichero no existe` | la ruta del fichero de entrada no está en el servidor |
| `salida N: ruta de grabación no permitida` | las salidas `file` sólo pueden vivir en `/var/lib/astra-webui/`, `/srv/`, `/media/` o `/mnt/` |
| `salida N: el directorio no existe` | crea el directorio (el panel lo intenta al guardar) y que sea escribible por `astra` |
| `no se pudo abrir el adaptador DVB` | frontend ocupado (otro proceso o canal en el mismo adaptador) o permisos (`groups astra`) |
| `make_channel falló` | configuración rechazada por `stream.lua`; revisa el log del núcleo |

**Verificar el TS**: `ffprobe -v error -show_streams "http://IP:8001/live/<token>/<canal>.ts"`
o `curl -s -o /dev/null -w '%{size_download}\n' --max-time 5 "http://IP:8001/<uri>"` (para
salidas públicas).

**El bitrate llega a 0 o `on_air:false`**: la fuente no envía datos (usa `tcpdump -ni any
port 1234`), el PID/`pnr` no existe en el mux, o hay *scrambling* (`sc_error > 0` en
`/status` → hace falta CAM o clave BISS).

**Con entrada `file` el canal no entra en emisión** y en el log de astra aparece:

```
ERROR: [file_intput /ruta/demo.ts] first PCR is not found
ERROR: [canal] Bitrate:0Kbit/s
ERROR: [canal] No active input
```

El módulo `file` necesita **PCR** en el flujo (lo usa para sincronizar y medir el bitrate).
Un TS grabado sin PCR (o un TS de prueba generado a mano) no vale. Para probar canales sin
fuente real usa el generador incluido, que sí lo lleva:

```sh
python3 tools/make_test_ts.py /var/lib/astra-webui/demo.ts --seconds 60 --bitrate 4000
sudo chown astra:astra /var/lib/astra-webui/demo.ts
# en el panel: canal con entrada file = /var/lib/astra-webui/demo.ts, loop activado
```

`tools/test_media.py` verifica que el generador produce PCR monótono, CRC correctos en
PAT/PMT/SDT, PES válidos y contadores de continuidad sin saltos.

---

## DVB

| Síntoma | Diagnóstico | Solución |
|---|---|---|
| No aparecen adaptadores | `ls /dev/dvb/adapter*/` | faltan drivers/firmware: `sudo apt install linux-firmware`, `dmesg \| grep -i dvb` |
| `busy: true` | `/dev/dvb/adapter0/frontend0` abierto por otro proceso | para el otro proceso (`fuser -v /dev/dvb/adapter0/*`) |
| El escaneo no encuentra servicios | el log muestra `frontend locked`? | parámetros del transponder (frecuencia/polarización/symbolrate/LNB), antena, o `stream_id` en multistream |
| DVB-S sin señal | `lof1/lof2/slof` mal | LNB universal: `9750:10600:11700`; usa `diseqc`/`tone` si hay conmutador |
| DVB-T sin servicios | falta `bandwidth`/`guardinterval` | prueba `AUTO` o `8MHZ` + `1/8` + `8K` |
| El escaneo se corta al empezar | hay canales emitiendo en ese adaptador | detén esos canales (o usa otro adaptador): el DVR es exclusivo |

---

## Streaming: el cliente no ve nada

| Código | Significado | Solución |
|---|---|---|
| `403 token desconocido` | token que no está en `auth.json` | copia el token actual del usuario (Usuarios → token) |
| `403 token expirado` | `expires_at` superado | amplía la caducidad o quítala |
| `403 sin permiso para ese canal` | el usuario no tiene ese canal y no es `all_channels` | marca «todos los canales» o añádelo |
| `403 esta salida requiere token` | pediste la URI directa de un canal no público | usa `/live/<token>/<id>.ts` o marca la salida como pública |
| `404 canal no disponible` | el canal está parado o no tiene salida HTTP | arráncalo y añade una salida HTTP |
| `404 el canal no está emitiendo` | está en marcha pero sin datos | revisa la fuente y `on_air` |
| `429` | límite de conexiones del token | sube `max_connections` del usuario |
| El reproductor se corta | saturación de red o del disco de grabación | revisa `on_air`/errores `cc` y el ancho de banda (`/api/system/status`) |

Comprobar sin reproductor:

```bash
curl -sI "http://IP:8001/live/<token>/<canal>.ts"      # debe dar 200 y Content-Type: video/mp2t
curl -s -o /dev/null -w '%{http_code}\n' "http://IP:8001/live/malo/<canal>.ts"   # 403
```

---

## Acceso al panel

| Problema | Solución |
|---|---|
| Contraseña olvidada | `sudo -u astra astra-webui --reset-admin-password` (la imprime) |
| Bloqueado por «demasiados intentos» | espera 5 minutos o reinicia el panel; ajusta `security.max_login_attempts` |
| «tu propia IP bloquearía» al guardar redes | añade tu red a la lista (`192.168.1.0/24`) o déjala vacía |
| Te quedaste fuera por `allowed_networks` | edita `/etc/astra-webui/panel.json` a mano, vacía la lista y `systemctl restart astra-webui` |
| «token CSRF inválido» | recarga la página (la sesión pudo caducar) |
| La sesión caduca muy pronto | sube `session_hours` en `panel.json` |
| Tras el proxy inverso todas las IPs son 127.0.0.1 | activa `security.trust_proxy` y manda `X-Forwarded-For` |

---

## Logs

* El núcleo escribe en `/var/log/astra/astra.log` (formato `Nov 12 10:33:02: INFO: mensaje`).
* El panel escribe en `/var/log/astra-webui/panel.log`.
* Para más detalle: `"debug": true` en `agent.json` → `log.debug`, y `--log-level DEBUG`
  (o `ASTRA_WEBUI_LOG_LEVEL=DEBUG` en `/etc/default/astra-webui`) en el panel.
* El visor del panel permite filtrar por nivel y por texto, y seguir el log en vivo.
* `logrotate` gira ambos ficheros y envía `SIGHUP` para que el núcleo reabra el suyo.
  Si ves que el log deja de crecer tras un giro: `sudo systemctl reload-or-restart astra`.

---

## Rendimiento

| Síntoma | Qué mirar |
|---|---|
| CPU alta con pocos canales | `--debug` en el agente genera mucho log; revisa `configure.sh` (optimizaciones `-O3`) |
| Errores `cc` (>0) crecientes | pérdida de paquetes en la red de entrada: sube `socket_size` en la entrada UDP, revisa el switch |
| `pes_error` > 2 | la fuente envía mal el mux o hay corrupción |
| Cortes con muchos espectadores | `LimitNOFILE` (65536 en el servicio), ancho de banda de salida, y evita pasar el TS por el panel |
| El panel consume CPU | sube `ui.refresh_ms` (2000 → 5000) y `history.interval` (5 → 15) |

---

## Recopilar información para pedir ayuda

```bash
{
  echo "== versión";            astra-webui --print-version
  echo "== sistema";            lsb_release -a 2>/dev/null || cat /etc/os-release
  echo "== servicios";          systemctl status astra astra-webui --no-pager
  echo "== salud del panel";    curl -s http://127.0.0.1:8000/api/health
  echo "== estado del agente";  curl -s -H "X-Astra-Token: $(sudo cat /etc/astra-webui/agent.token)" \
                                     http://127.0.0.1:9977/status
  echo "== log astra (50)";     sudo tail -50 /var/log/astra/astra.log
  echo "== log panel (50)";     sudo tail -50 /var/log/astra-webui/panel.log
} > /tmp/astra-webui-diagnostico.txt
```

Revisa el fichero antes de enviarlo: puede contener nombres de canales, URLs de origen con
credenciales y tokens de cliente.
