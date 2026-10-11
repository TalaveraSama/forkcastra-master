# Driver DVB Card (TBS) — guardado desde [irtec/Driver_DVB_Card](https://github.com/irtec/Driver_DVB_Card)

Copia del repositorio `irtec/Driver_DVB_Card` (README + `drv-tbs.sh`), más un instalador
actualizado para Ubuntu 20.04 / 22.04 / 24.04.

| Fichero | Qué es |
|---|---|
| `drv-tbs.sh` | script original, tal cual (Ubuntu 14.04/16.04 y CentOS) |
| `instalar-tbs-ubuntu.sh` | versión actualizada: comprueba cabeceras, sube el límite de adaptadores, usa `tbsdtv` y si falla `ljalves`, instala los firmwares y verifica |
| `README.md` | este fichero |

## Instalación (Ubuntu 20.04+)

```bash
sudo bash instalar-tbs-ubuntu.sh              # compila e instala (tarda 15-40 min)
sudo bash instalar-tbs-ubuntu.sh --j 8        # con más paralelismo
sudo bash instalar-tbs-ubuntu.sh --source ljalves   # si el árbol de tbsdtv falla
sudo bash instalar-tbs-ubuntu.sh --no-firmware      # sin descargar firmwares
sudo reboot
```

Después del reinicio:

```bash
ls /dev/dvb                      # deben aparecer los adaptadores (la 6904se da 4)
sudo femon -a 0                  # signal/snr > 0 cuando sintoniza
```

## Manual (como en el README original)

1. Preparar el sistema e instalar utilidades:

```bash
sudo -s
apt-get install build-essential patchutils libproc-processtable-perl \
                linux-headers-$(uname -r) git linux-source
```

2. Quitar drivers de media antiguos (opcional; el instalador nuevo **no** lo hace por
   defecto porque los módulos de `updates/` tienen prioridad sobre los del kernel):

```bash
rm -rf /lib/modules/$(uname -r)/extra
rm -rf /lib/modules/$(uname -r)/kernel/drivers/media
rm -rf /lib/modules/$(uname -r)/kernel/drivers/staging/media
```

3. Subir el límite de adaptadores DVB (por defecto 8; una 6904se con 4 sintonizadores y
   varias tarjetas se queda corta):

```bash
sed -i.bak -e 's/^\(CONFIG_DVB_MAX_ADAPTERS\)=.*/\1=48/g' /lib/modules/$(uname -r)/build/.config
```

4. Compilar e instalar:

```bash
cd /usr/src
git clone https://github.com/tbsdtv/media_build.git
git clone --depth=1 https://github.com/tbsdtv/linux_media.git -b latest ./media
cd media_build
make dir DIR=../media
make allyesconfig
make -j4
sudo make install
```

   Alternativa (fork de ljalves, útil cuando el árbol de tbsdtv no compila):

```bash
mkdir -p /usr/src/ljalves-tbsdriver && cd /usr/src/ljalves-tbsdriver
git clone https://github.com/ljalves/media_build.git
git clone --depth=1 https://github.com/ljalves/linux_media.git -b latest ./media
cd media_build
make dir DIR=../media
make distclean
make
sudo make install
```

5. Firmware de los adaptadores:

```bash
cd /usr/src
wget http://www.tbsdtv.com/download/document/linux/tbs-tuner-firmwares_v1.0.tar.bz2
sudo tar jxvf tbs-tuner-firmwares_v1.0.tar.bz2 -C /lib/firmware/
```

6. Reiniciar y comprobar:

```bash
sudo reboot
ls /dev/dvb
```

## Instalación automática (script original)

```bash
curl -fsSL https://raw.githubusercontent.com/irtec/Driver_DVB_Card/master/drv-tbs.sh | sudo bash
```

## ⚠️ NO descargues el módulo de esta tarjeta (`modprobe -r tbsecp3` / `rmmod tbsecp3`)

Con la **TBS 6904se** de este servidor está comprobado que al descargar el módulo la tarjeta
**desaparece del bus PCI** y ya **no vuelve con un `reboot` normal**: ni con varios reinicios,
ni con `echo 1 > /sys/bus/pci/rescan`, ni reescaneando el puente PCIe. Solo vuelve con un
**apagado completo** (cortar la corriente).

Cronología real de lo que pasó (kernel 5.15.0-198-generic):

| Arranque | Menciones de la tarjeta en el kernel |
|---|---|
| 03:19 → 06:29 UTC | **41** (tarjeta detectada, 4 adaptadores en `/dev/dvb`) |
| justo al descargar el módulo (~05:45) | la tarjeta deja de responder y desaparece del bus |
| 06:29 → 07:30 UTC (4 reinicios) | **0** en todos |

Por tanto:

- **Nunca** hagas `modprobe -r tbsecp3` / `rmmod tbsecp3` si no puedes apagar el equipo.
- Para recargar el driver, **apaga** el servidor, no lo reinicies.
- Si has descargado el módulo y la tarjeta no aparece: `sudo shutdown -h now`, esperar ~30 s
  (o cortar la corriente) y encender. Luego `lspci -nn | grep -i 0480` y `ls /dev/dvb`.
- Un `modprobe tbsecp3` posterior carga el módulo pero **no** recupera la tarjeta si el
  enlace PCIe ya se perdió.

### Cómo comprobar en cada caso

```bash
lspci -nn | grep -i 0480     # ¿está la tarjeta en el bus? (0480 = tarjeta DVB)
ls /dev/dvb                  # adapters 0..3
lsmod | grep tbsecp3         # ¿módulo cargado?
sudo femon -a 0              # signal/snr > 0 cuando sintoniza
journalctl -b -1 -k | grep -ciE 'TBSECP3|registering adapter'   # ¿se detectó en el arranque anterior?
```

Si aparece en `lspci` pero con `(rev ff)` y el log dice `i2c xfer timeout`, el interfaz de la
tarjeta no responde: reinstala el driver, prueba otro slot PCIe y, si sigue igual, consulta a
TBS.

## Notas de este servidor (homeserver)

- Tarjeta: **TBS 6904se** (4 sintonizadores DVB-S/S2/S2x), PCIe `544d:6178`, driver
  `tbsecp3` + `gx1133` + `tas2101` + `m88rs6060` (firmware `dvb-demod-m88rs6060.fw`).
- Instalación actual: módulos en `/lib/modules/$(uname -r)/updates/extra/media/`
  (compilados con `media_build`, aviso «experimental version of the media stack»).
- Satélite configurado en el panel: **70°W (KU)**, LNB 9750/10600, transponder
  **11170 V 27900**.
