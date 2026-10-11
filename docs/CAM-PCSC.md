# CAM con tarjeta (lector PC/SC + OSCam)

Cómo usar una tarjeta de abonado (lector USB **PC/SC**, tipo HID Omnikey 3021) para
descifrar canales: la tarjeta se lee con **OSCam**, OSCam devuelve las claves (CW) y el
agente se las pide por **newcamd** desde el panel.

```
  tarjeta ──▶ lector PC/SC ──▶ pcscd ──▶ OSCam ──newcamd──▶ astra (decrypt) ──▶ VLC
```

## 1. Lector y tarjeta

```sh
sudo apt install pcscd pcsc-tools libccid opensc
sudo systemctl enable --now pcscd
opensc-tool --list-readers         # debe aparecer el lector
opensc-tool -a                     # ATR de la tarjeta
pcsc_scan                          # estado (Card inserted / Unresponsive card)
```

Si `pcsc_scan` dice **`Unresponsive card`**, la tarjeta se ha quedado colgada (suele pasar
si se prueba con un reloj distinto): **sácala y vuelve a insertarla**. Un reinicio del
lector por software no siempre basta:

```sh
# reinicio del lector sin tocarlo (equivale a desenchufar/reinsertar el USB)
for d in /sys/bus/usb/devices/*/idVendor; do
  [ "$(cat "$d")" = "076b" ] && DEV=$(basename "$(dirname "$d")")
done
echo "$DEV" | sudo tee /sys/bus/usb/drivers/usb/unbind
sleep 3
echo "$DEV" | sudo tee /sys/bus/usb/drivers/usb/bind
```

## 2. OSCam (servidor de tarjeta)

OSCam no está en los repositorios de Ubuntu: se compila en un minuto.

```sh
sudo apt install build-essential cmake libpcsclite-dev libusb-1.0-0-dev libssl-dev git
cd /usr/src
sudo git clone --depth 1 https://git.streamboard.tv/common/oscam.git
cd oscam && make -j"$(nproc)" USE_PCSC=1 USE_LIBUSB=1
sudo install -m 0755 Distribution/oscam-*[!g] /usr/local/bin/oscam   # (el binario, no el .debug)
```

`/etc/oscam/oscam.server` (ejemplo real de una tarjeta **Nagra 1802**; el `boxkey` y el
`rsakey` van con la tarjeta):

```ini
[reader]
label        = tv_box4_1802
protocol     = pcsc
device       = 0
autospeed    = 0
caid         = 1802
boxkey       = <boxkey de la tarjeta>
rsakey       = <rsakey de la tarjeta>
readtiers    = 2
detect       = cd
nagra_read   = 1
ident        = 1802:000000,FFFFFF
group        = 3
dropbadcws   = 1
```

`/etc/oscam/oscam.conf` — el servidor **newcamd** que consume astra:

```ini
[global]
logfile      = /var/log/oscam/oscam.log
maxlogsize   = 1000
waitforcards = 1
preferlocalcards = 1
[newcamd]
port         = 5000@1802:000000
key          = 0102030405060708091011121314
[webif]
httpport     = 8888
httpuser     = admin
httppwd      = astra
```

`/etc/oscam/oscam.user`:

```ini
[account]
user  = astra
pwd   = astra
group = 3
caid  = 1802
au    = 1
```

Servicio systemd (`/etc/systemd/system/oscam.service`):

```ini
[Unit]
Description=OSCam (servidor de tarjeta PC/SC)
After=network.target pcscd.service
Wants=pcscd.service
[Service]
ExecStart=/usr/local/bin/oscam -c /etc/oscam -f
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

Comprobar que la tarjeta se lee:

```sh
sudo systemctl restart oscam
sudo tail -f /var/log/oscam/oscam.log      # "card detected", ATR, CAID 1802…
sudo ss -ltnp | grep -E ':(5000|8888)'     # newcamd y webif escuchando
```

## 3. CAM en el panel

En la entrada DVB del canal, dentro de `options`, se añade el bloque `cam` (el mismo que en
`oscam.conf`):

```json
"options": {
  "pnr": 264,
  "cam": {
    "host": "127.0.0.1",
    "port": 5000,
    "user": "astra",
    "pass": "astra",
    "key": "0102030405060708091011121314"
  },
  "ecm_pid": 4095
}
```

- `host`, `port`, `user`, `pass`, `key` — servidor newcamd de OSCam (`key` = la clave DES
  del `[newcamd]`, 14 bytes = 28 caracteres hexadecimales).
- `ecm_pid` — opcional; si no se pone, el núcleo busca el ECM por CAID en la PMT.
- `cas_data`, `ecm_swap_time` — opcionales.
- Para **hardware CAM** (CI del sintonizador) se usa `"cam": true` en vez del bloque.

El agente crea la instancia `newcamd` (una por servidor) y la pasa al módulo `decrypt`
manteniéndola referenciada: si el recolector de basura de Lua la libera mientras el núcleo
la usa, el proceso se va a *segmentation fault* (mismo problema que con el analizador del
escaneo).

## 4. La tarjeta en el panel

En el panel: **Ajustes → Tarjeta / CAM**. Ahí está todo sin tocar la consola:

- **Estado**: OSCam alcanzable (y su URL), **tarjeta** (`CARDOK`, `CARDNOK`, `NO CARD`…),
  el enlace newcamd con astra, y los canales: *descifrando / con CAM / sin claves / total*,
  con la lista de los que están abiertos en ese momento.
- **Configuración**: host, puerto, usuario, contraseña y clave DES del `[newcamd]`. El
  botón **«Guardar y aplicar a todos los canales»** escribe el bloque `cam` en la entrada
  DVB de todos los canales y recarga el agente.

Por debajo: `GET /api/cam` (estado), `POST /api/cam` (guardar + aplicar), `DELETE /api/cam`.
La configuración se guarda en `/var/lib/astra-webui/cam.json` — **no** en
`/etc/astra-webui/panel.json`, porque ese directorio es de root y el panel corre como
`astra`. Si en `panel.json` hay una sección `cam`, se usa como valor por defecto de sólo
lectura.

El estado de la tarjeta se lee del panel web de **OSCam**, que conviene dejar accesible
sólo en la red local:

```ini
[webif]
httpport    = 8888
; sin usuario/clave: el acceso lo limita httpallowed
httpallowed = 127.0.0.1,192.168.0.0-192.168.255.255,10.0.0.0-10.255.255.255
```

En `http://<servidor>:8888` ves el lector, `CARDOK`, los ECM resueltos y los usuarios; ahí
se comprueba de un vistazo si el problema es la tarjeta o la suscripción.

## 5. Comprobación

```sh
# ¿astra se conecta al newcamd? (OSCam lo registra al iniciar sesión el cliente)
sudo grep -iE 'newcamd|client|login' /var/log/oscam/oscam.log | tail

# ¿la tarjeta responde?  (Exclusive Mode = OSCam la tiene abierta y hablando)
pcsc_scan | head

# ¿resuelve ECM?  'found (X ms) by <reader>' = sí; 'not found' = canal fuera de la
# suscripción/tiers de la tarjeta (no es un problema de configuración)
sudo grep -a '(ecm)' /var/log/oscam/oscam.log | tail

# ¿bajan los errores de descifrado?  'sc' debe bajar en los canales que sí decodifica
TOKEN=$(sudo cat /etc/astra-webui/agent.token)
curl -s -H "X-Astra-Token: $TOKEN" http://127.0.0.1:9977/status | grep -o '"sc":[0-9]*'
```

### ⚠️ NO fijes `ecm_pid` en el canal (deja que astra lea la PMT)

En `modules/softcam/decrypt.c`:

```c
if(mod->ecm_pid) // skip descriptors checking
{
    mod->stream[mod->ecm_pid] = MPEGTS_PACKET_ECM;
    ...
}
```

Si se fija `ecm_pid`, astra **se salta el recorrido de descriptores de la PMT** y con él el
descubrimiento del **CAID**; entonces no encuentra el handler del CAS y en el log aparece:

```
ERROR: [decrypt <canal>] ECM:0x80 size:0 Not Found.
```

ese mensaje es la **respuesta del CAM** (el texto que devuelve OSCam), no un fallo de astra:
si OSCam dice `found (X ms) by <reader>` las claves llegan y el canal se descifra; si dice
`not found`, ese servicio no está en la tarjeta (tiers/suscripción).

Por eso el escaneo **guarda** el `ecm_pid` y el CAID como información del servicio (los
verás en las notas del canal: `CAID 1802 / ECM 674`), pero el canal se crea **sin**
`ecm_pid`.

## 6. Requisito del binario: módulo `newcamd`

El módulo `newcamd` lo compila `modules/softcam/module.mk` **sólo si encuentra
`libssl-dev`** (`#include <openssl/des.h>`):

```sh
dpkg -l libssl-dev || sudo apt install -y libssl-dev
```

Con `libssl-dev` presente, `configure.sh` añade `cam/newcamd.c` y registra el módulo
(`MODULES="decrypt newcamd"`). Si el binario se compiló sin él, el panel mostrará en el log:

```
ERROR: [astra-webui] CAM: este binario no incluye el módulo newcamd
```

Reconstruye el paquete con `libssl-dev` instalado:

```sh
sudo apt install -y libssl-dev
bash packaging/build-deb.sh --source . --out dist --revision N
sudo apt install -y --reinstall ./dist/astra-webui_*.deb
```

Y comprueba dentro de astra que el módulo existe:

```sh
cat > /tmp/t.lua <<'EOF'
local f = io.open("/tmp/t.txt", "w"); f:write(tostring(newcamd) .. "\n"); f:close(); os.exit(0)
EOF
/usr/bin/astra /tmp/t.lua && cat /tmp/t.txt    # debe ser: table: 0x...
```
