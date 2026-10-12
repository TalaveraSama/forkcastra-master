# Instalación sin Internet (offline)

Para servidores de cabecera sin salida a Internet: todo lo necesario viaja en un único
fichero `tar.gz`.

## Qué hay dentro

```
astra-webui-offline_1.0.0_amd64/
  instalar-offline.sh                    instalador (no usa la red)
  LEEME.txt                              instrucciones de un minuto
  VERSION                                versiones del núcleo y del panel
  SHA256SUMS                             sumas de comprobación de todo el paquete
  debs/astra-webui_4.0.282-1_amd64.deb   paquete completo (núcleo + agente + panel + WebUI)
  debs/astra-webui-panel_1.0.0-1_all.deb panel + agente + WebUI (sin núcleo)
  deps/*.deb                             dependencias de sistema (opcional)
  src/astra-webui-1.0.0-src.tar.gz       código del panel
  src/forkcastra-master.tar.gz           código del fork (para compilar el núcleo)
  docs/                                  INSTALL, OFFLINE, TROUBLESHOOTING…
  plantillas/nginx-astra-webui.conf      ejemplo de proxy inverso con TLS
```

## Instalación

```sh
# 1) copia el paquete al servidor (USB, scp desde otra máquina, etc.)
scp astra-webui-offline_1.0.0_amd64.tar.gz administrador@servidor:/tmp/

# 2) en el servidor
cd /tmp
tar -xzf astra-webui-offline_1.0.0_amd64.tar.gz
cd astra-webui-offline_1.0.0_amd64
sudo ./instalar-offline.sh
```

El instalador:

1. comprueba las sumas SHA-256 del paquete entero;
2. elige el paquete completo de `debs/`; si no lo hay, **compila el núcleo** desde
   `src/` (necesita `gcc` y `make`, es decir `build-essential` ya instalado);
3. revisa las dependencias con `dpkg-query` y, si faltan, intenta instalarlas desde
   `deps/`; nunca ejecuta `apt-get update` ni descarga nada;
4. instala con `dpkg -i` (y `apt-get -f install --no-download` si hay dependencias
   pendientes en la caché local);
5. arranca `astra` y `astra-webui` (systemd, o en segundo plano si no hay systemd) y
   muestra la URL y la contraseña inicial de `admin`.

### Opciones

| Opción | Para qué |
|---|---|
| `--check` | comprobar el paquete y el sistema sin instalar nada |
| `--no-start` | instalar sin arrancar los servicios |
| `--build` | compilar el núcleo aunque haya un `.deb` completo |
| `--deps-dir DIR` | usar otra carpeta de dependencias |
| `--port N` | puerto del panel (por defecto 8000) |

## Dependencias: cómo evitar sorpresas

El paquete declara `Depends: python3 (>= 3.8), adduser` (y `libc6` en el paquete completo).
En un **Ubuntu Server** normal ya están instaladas. Si tu servidor es muy mínimo, tienes
dos caminos sin Internet:

1. **Llevar los `.deb` de esas dependencias**: en una máquina con Internet de la misma
   versión de Ubuntu:
   ```sh
   apt-get install --download-only -y python3 adduser
   cp /var/cache/apt/archives/*.deb /ruta/al/paquete/deps/
   ```
   y vuelve a montar el paquete con `--deps /ruta/al/paquete/deps` (o copia los ficheros
   directamente en `deps/` del paquete ya descomprimido y ejecuta el instalador).
2. **Instalarlas a mano** con `dpkg -i` antes de ejecutar el instalador.

Si falta algo, el instalador lo dice con el nombre exacto y la orden que haría falta en un
equipo con red (`apt-get install -y <paquetes>`), sin bloquearse.

## Compilar el núcleo sin Internet

El paquete completo (`astra-webui_<núcleo>-1_amd64.deb`) ya trae el binario compilado, así
que **no hace falta compilar** si lo tienes. Si sólo llevas el paquete del panel y el
servidor tiene `build-essential`, el instalador compila el núcleo desde
`src/forkcastra-master.tar.gz` + `src/astra-webui-1.0.0-src.tar.gz` (todo local, sin red).
Sin `build-essential` no hay compilación posible: consigue su `.deb` (y el de sus
dependencias) o copia el paquete completo.

## Verificación manual

```sh
sha256sum -c SHA256SUMS                 # dentro del paquete descomprimido
dpkg-deb -I debs/astra-webui_*.deb      # versión, dependencias y arquitectura
dpkg-deb -c debs/astra-webui_*.deb | head
```

## Montar el paquete tú mismo

```sh
python3 tools/make_offline_bundle.py                        # usa release/ y dist/
python3 tools/make_offline_bundle.py --deps /tmp/debs --arch amd64
python3 tools/make_offline_bundle.py --no-fork              # sin el código del núcleo
```

En GitHub, el workflow **release** añade este paquete a cada release automáticamente
(junto a los `.deb`), así que también puedes descargarlo ya montado.

## Notas

* El paquete no incluye el binario de Astra 5.x ni lo reemplaza.
* Igual que en la instalación normal, para DVB el usuario `astra` necesita acceso a
  `/dev/dvb/*` (el paquete instala la regla udev `50-astra-webui.rules`).
* La contraseña inicial está en `/etc/astra-webui/initial-admin-password` y se borra sola
  al cambiarla desde **Usuarios**.
