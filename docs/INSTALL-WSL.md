# Astra WebUI en Windows (WSL)

El panel y el núcleo de Astra son software Linux, así que en Windows se ejecutan
**dentro de WSL** (Subsistema de Windows para Linux). La ventaja respecto a una máquina
virtual es que comparte el sistema de ficheros y la red con Windows; el límite es que
**DVB sólo funciona en WSL2 y con el sintonizador pasado por USB**.

---

## 1. Estado de WSL en este equipo (diagnóstico)

Comprobado el 2026-10-10 en un Windows 10 22H2 (19045), x64, con AMD Ryzen 7 4800H:

| Comprobación | Resultado |
|---|---|
| Virtualización en CPU/firmware | **Sí** (`VirtualizationFirmwareEnabled: True`, SLAT: True) |
| Característica `Microsoft-Windows-Subsystem-Linux` | **Habilitada** |
| Característica `VirtualMachinePlatform` | **NO EXISTE** en la imagen (`dism` → error `0x800f080c`) |
| `Get-WindowsOptionalFeature` (Hyper-V/Virtual*) | **ninguna** disponible |
| `wsl.exe --version` / `--status` | no existen (CLI de 1903: solo `--install`, `--list`, `--help`) |
| `wsl -l -o` (catálogo de distribuciones) | **vacío** (no puede bajar nada de la Store) |

**Conclusión**: en este equipo **WSL2 no se puede activar** porque el *payload* de
`VirtualMachinePlatform` falta en el almacén de componentes de Windows, y **WSL1 tampoco
se puede aprovisionar** porque el `wsl.exe` instalado es demasiado antiguo (no tiene
`--import`, así que no admite un rootfs manual).

### Cómo se arregla (requiere un medio de instalación de Windows)

```powershell
# 1) reparar el almacén de componentes con un ISO de la MISMA versión (22H2)
#    (monta el ISO y usa la ruta real a install.wim)
dism /online /cleanup-image /restorehealth /source:D:\sources\install.wim /index:1 /limitaccess

# 2) habilitar las dos características
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

# 3) REINICIAR Windows y actualizar WSL
wsl --update          # o: winget install --id Microsoft.WSL

# 4) instalar Ubuntu
wsl --install -d Ubuntu-22.04
```

Si `wsl --update` tampoco está disponible, instala el paquete moderno a mano:
`winget install --id Microsoft.WSL` o el MSI de <https://github.com/microsoft/WSL/releases>.

También sirve un **Windows 11** o un equipo donde WSL2 ya funcione.

---

## 2. Instalación automática (una vez WSL funcione)

Con WSL2 + Ubuntu ya instalados, todo el proceso está automatizado:

```powershell
# PowerShell COMO ADMINISTRADOR, en la carpeta del proyecto
cd C:\ruta\a\astra-webui
.\packaging\wsl\install-wsl.ps1 -InstallDistro -ExposeToLan -AutoStart
```

El script:

1. comprueba los requisitos (y **te dice exactamente qué falta**, como en la tabla de arriba);
2. instala la distribución de Ubuntu si no está;
3. copia el proyecto a `/root/astra-webui` dentro de WSL;
4. ejecuta `packaging/wsl/setup-in-wsl.sh`, que instala dependencias, **compila el núcleo
   del fork**, construye el `.deb`, lo instala con `apt`, arranca los servicios y hace una
   **prueba end-to-end** creando un canal con un MPEG-TS generado y descargando el TS por
   HTTP con el token;
5. muestra la URL, el usuario y la contraseña inicial, y (si lo pides) publica los puertos
   hacia Windows/LAN y crea una tarea programada de arranque.

Opciones útiles:

| Opción | Para qué |
|---|---|
| `-Check` | sólo diagnostica, sin cambiar nada |
| `-Distro Ubuntu-24.04` | otra distribución |
| `-SkipDeps` | no ejecutar `apt-get` (dependencias ya instaladas) |
| `-ExposeToLan` | `netsh portproxy` + regla de firewall para abrir el panel desde Windows/LAN |
| `-AutoStart` | tarea programada que arranca astra y el panel al iniciar Windows |
| `-WhatIf` | mostrar qué haría sin hacerlo |

### Paso a paso manual (equivalente)

```powershell
wsl --install -d Ubuntu-22.04          # y crea tu usuario cuando lo pida
wsl -d Ubuntu-22.04                    # entra en Linux
```

Dentro de WSL:

```bash
sudo apt update && sudo apt install -y build-essential python3 git

# copia el proyecto (desde Windows está en /mnt/c/...)
mkdir -p ~/astra-webui
cp -a /mnt/c/ruta/a/astra-webui/. ~/astra-webui/

cd ~/astra-webui
bash packaging/wsl/setup-in-wsl.sh --project ~/astra-webui
```

Ese script deja todo instalado y comprobado. Para repetir sólo el arranque:
`bash packaging/wsl/setup-in-wsl.sh --no-deps --no-demo`.

---

## 3. Uso diario

```bash
# dentro de WSL
systemctl status astra astra-webui          # (WSL2 con systemd)
journalctl -u astra -f
tail -f /var/log/astra/astra.log
```

Desde Windows:

| Qué | Cómo |
|---|---|
| Abrir el panel | <http://localhost:8000> (con `-ExposeToLan`) o `http://<ip-de-wsl>:8000` |
| Ver el TS con VLC | pega la URL `http://localhost:8001/live/<token>/<canal>.ts` |
| Entrar en Linux | `wsl -d Ubuntu-22.04` |
| Ver logs | `wsl -d Ubuntu-22.04 -u root -- tail -f /var/log/astra/astra.log` |
| Parar todo | `wsl -d Ubuntu-22.04 -u root -- systemctl stop astra astra-webui` |
| Apagar WSL | `wsl --shutdown` |

**Red**: WSL2 usa NAT, por eso el panel no se ve desde Windows con solo arrancarlo. Dos
soluciones:

1. `netsh interface portproxy` (lo hace `-ExposeToLan`):
   ```powershell
   netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 `
       connectport=8000 connectaddress=<ip-de-wsl>
   New-NetFirewallRule -DisplayName "Astra WebUI" -Direction Inbound `
       -LocalPort 8000,8001 -Protocol TCP -Action Allow
   ```
2. Red en modo espejo (WSL 2.0+ en Windows 11): en `%UserProfile%\.wslconfig`
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   y `wsl --shutdown`. Con esto `http://localhost:8000` funciona directamente.

**Arranque automático**: la tarea programada que crea `-AutoStart` ejecuta
`wsl -d <distro> -u root -- systemctl start astra astra-webui` al iniciar Windows.

---

## 4. DVB en WSL

El módulo `dvb` del fork necesita `/dev/dvb`, así que:

* **WSL2**: se pasa el sintonizador USB con [usbipd-win](https://github.com/dorssel/usbipd-win):
  ```powershell
  winget install usbipd
  usbipd list
  usbipd bind --busid <BUSID>          # como administrador, una sola vez
  usbipd attach --wsl --busid <BUSID>  # cada vez que arranques WSL
  ```
  Después, dentro de WSL: `ls /dev/dvb/` y el panel lo detecta en **DVB → Adaptadores**.
* **WSL1**: no hay soporte de dispositivos, así que **no hay DVB** (el resto sí funciona).
* Alternativa: panel dentro del servidor Linux real (lo normal en producción).

---

## 5. Diferencias respecto a un servidor Ubuntu normal

| Tema | Servidor real | WSL |
|---|---|---|
| systemd | sí | WSL2 sí (con `systemd=true` en `/etc/wsl.conf`); WSL1 no |
| `astra-webui.service` | lo gestiona systemd | igual en WSL2; en WSL1 el script arranca los procesos a mano |
| DVB | directo (`/dev/dvb`) | sólo WSL2 + `usbipd` |
| Puertos | accesibles desde la LAN | hay que publicarlos (`portproxy` / modo espejo) |
| Rendimiento de red | nativo | NAT con algo más de latencia (suficiente para gestionar y ver TS) |
| Persistencia | arranca al encender | la tarea programada arranca WSL al iniciar Windows |
| GPU/transcodificación | nativa | no aplica (Astra no transcodifica) |

---

## 6. Problemas habituales

| Síntoma | Causa / solución |
|---|---|
| `dism ... VirtualMachinePlatform` → `0x800f080c` | falta el *payload*: repara el almacén con un ISO (sección 1) |
| `wsl --install` no encuentra distribuciones | catálogo/Store no disponible: usa `wsl --update` o el MSI moderno |
| `systemctl: System has not been booted with systemd` | WSL1 o WSL2 sin `systemd=true`; el script arranca en modo manual |
| El panel funciona dentro de WSL pero no en Windows | falta el `portproxy`/firewall o el modo espejo (sección 3) |
| `apt-get install` del `.deb` falla en el postinst | mira `/tmp/astra-webui-install.log`; normalmente es `systemctl` ausente (WSL1), que el paquete tolera |
| El agente no responde en `127.0.0.1:9977` | `tail -40 /var/log/astra/astra.log`; revisa el token en `/etc/astra-webui/agent.token` |
| DVB no aparece tras `usbipd attach` | el panel cachea la lista: recarga **DVB → Adaptadores**; comprueba `ls /dev/dvb/` |
| Quiero empezar de cero | `wsl --unregister Ubuntu-22.04` (borra la distribución completa) |

Diagnóstico rápido equivalente al de un servidor:

```bash
wsl -d Ubuntu-22.04 -u root -- bash -lc '
  systemctl status astra astra-webui --no-pager;
  curl -s http://127.0.0.1:8000/api/health;
  curl -s -H "X-Astra-Token: $(cat /etc/astra-webui/agent.token)" http://127.0.0.1:9977/status;
  tail -n 20 /var/log/astra/astra.log'
```

---

## 7. Alternativas si no puedes reparar WSL

| Opción | Ventajas | Inconvenientes |
|---|---|---|
| **Servidor Ubuntu real** (o una VM en la nube) | es el destino previsto; DVB directo; systemd | hace falta una máquina |
| **Máquina virtual** (VirtualBox/Hyper-V/UTM) | Linux completo e independiente | también necesita virtualización (en este equipo está disponible en BIOS, no en Windows) |
| **Compilación nativa para Windows (MSYS2 + mingw-w64)** | `astra.exe` nativo, sin virtualización; el fork ya trae soporte `mingw` en `configure.sh` | ~1,5 GB de toolchain; **sin DVB**; el panel correría en el mismo Windows |
| **Sólo el panel en Windows** contra el agente simulado | útil para desarrollar la interfaz | no emite vídeo real |

El panel también se puede ejecutar directamente en Windows para **desarrollar** (sin
vídeo real):

```powershell
python tools\mock_agent.py --data-dir var --log-file var\astra.log
$env:PYTHONPATH='panel'; python -m astra_webui --dev --port 8000 --data-dir var
python tools\astra_demo.py --password <clave>     # prueba end-to-end contra el simulado
```
