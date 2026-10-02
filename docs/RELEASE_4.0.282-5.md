# Forkcastra 4.0.282-5 — versión instalable inicial

Esta versión está destinada a pruebas controladas en Ubuntu Server 20.04, 22.04 y 24.04 amd64.

## Instalación paralela

No reemplaza Astra 5.x. Usa nombres y rutas independientes:

| Componente | Forkcastra |
|---|---|
| Binario | `/usr/bin/forkcastra` |
| Configuración | `/etc/forkcastra/forkcastra.lua` |
| Servicio | `forkcastra.service` |
| Datos | `/var/lib/forkcastra` |
| Logs auxiliares | `/var/log/forkcastra` |
| Usuario | `forkcastra` |

Astra y Forkcastra pueden estar instalados al mismo tiempo, pero no deben usar simultáneamente el mismo adaptador DVB, puerto HTTP/TCP, puerto UDP de escucha o destino multicast.

## Instalar

```sh
sudo apt install ./forkcastra_4.0.282-5_amd64.deb
sudoedit /etc/forkcastra/forkcastra.lua
sudo systemctl start forkcastra
sudo systemctl status forkcastra --no-pager
```

El instalador habilita el servicio para futuros reinicios, pero no lo inicia automáticamente.

## Desinstalar

```sh
sudo apt remove forkcastra
```

La configuración y los datos se conservan intencionalmente. Para eliminarlos después de revisar su contenido:

```sh
sudo rm -rf /etc/forkcastra /var/lib/forkcastra /var/log/forkcastra
sudo deluser forkcastra
sudo delgroup forkcastra
```

## Limitaciones

- Primera versión de validación; incluye panel autenticado, gestión de canales UDP/HTTP/file/DVB, generación segura de Lua y aplicación controlada mediante systemd.
- El HTTP heredado no debe utilizarse como panel administrativo expuesto a Internet.
- Debe probarse con los adaptadores DVB y flujos multicast reales antes de producción.
- El módulo `newcamd` sólo se incluye cuando el entorno de compilación tiene cabeceras OpenSSL compatibles.
