# Auditoría inicial y plan de modernización

Fecha: 2026-10-01

## Conclusión

El motor puede reutilizarse como base de un fork para IPTV, pero **no debe exponerse directamente a Internet ni considerarse listo para producción sin modernización**. Compila en Linux x86_64 actual y conserva módulos para DVB, UDP, HTTP, MPEG-TS, archivos y softcam. El panel debe construirse como un servicio separado que genere configuración y controle el proceso Astra mediante systemd.

## Licencia

El repositorio incluye `COPYING` con GNU GPL versión 3 y las cabeceras principales declaran GPL-3.0-or-later. Se puede usar, modificar y distribuir, incluso comercialmente, respetando la GPL: conservar avisos, entregar el código fuente correspondiente de las versiones distribuidas y licenciar las modificaciones derivadas bajo GPL compatible. Esto no sustituye asesoría legal. Antes de publicar conviene revisar por separado la procedencia y licencia de cada dependencia incluida, especialmente FFdecsa/libdvbcsa y cualquier parche del fork.

## Estado técnico observado

- C99, Lua integrado y sistema de compilación propio basado en shell/make.
- Versión reportada por el binario: `Astra 4.0 dev:282`.
- Compilación verificada en Linux x86_64 con GCC; el binario inicia y muestra su ayuda.
- El script no tenía permiso ejecutable; se corrigió.
- `modules/softcam/module.mk` usaba `+=`, no válido en `/bin/sh` de Ubuntu (dash). Esto omitía softcam y producía un error de preprocesador. Se corrigió y el módulo compila.
- Hay advertencias del compilador y código pendiente de revisión en HTTP, JSON y MPEG-TS.
- Incluye Lua antiguo y primitivas MD5/SHA-1. No deben emplearse para contraseñas, sesiones ni autenticación del panel.
- El HTTP interno tiene comentarios pendientes sobre capacidad, envíos parciales, WebSocket y desbordamientos; no es una frontera de seguridad apropiada para administración pública.
- No hay pruebas automatizadas, CI, paquetes Debian, unidad systemd, migraciones ni panel administrativo en este repositorio.

## Compatibilidad objetivo

Objetivo recomendado:

- Ubuntu Server 20.04 LTS: soporte de compatibilidad hasta el fin acordado por el proyecto.
- Ubuntu Server 22.04 LTS: soportado.
- Ubuntu Server 24.04 LTS: plataforma principal.
- Arquitecturas iniciales: amd64; arm64 después de eliminar supuestos SSE y probar DVB/controladores.

La compilación local demuestra viabilidad, no certifica todavía las tres versiones. Deben probarse con matrices limpias (contenedores para compilación y máquinas/VM para DVB y red multicast).

## Arquitectura del panel

No conviene reescribir el motor antes de validar flujos reales. Se recomienda:

1. **Motor Astra** sin privilegios, administrado por systemd.
2. **API de control separada**, con validación estricta, base de datos y escritura atómica de configuración.
3. **Interfaz web responsive** inspirada en la organización de Astra/Cesbo, pero con diseño y recursos propios.
4. Proxy inverso Caddy o Nginx con TLS; la API escucha sólo en socket Unix o loopback.
5. Autenticación Argon2id, cookies `HttpOnly/Secure/SameSite`, CSRF, limitación de intentos y registro de auditoría.
6. Acciones permitidas explícitamente; nunca aceptar Lua, comandos shell o rutas arbitrarias desde el navegador.
7. Copia, validación y rollback de cada configuración antes de reiniciar el motor.

## MVP propuesto

- Inicio de sesión y usuario administrador inicial.
- Dashboard: estado, uptime, CPU, RAM, tráfico y versión.
- CRUD de entradas y salidas UDP/HTTP/DVB.
- Canales, grupos y búsqueda.
- Analizador MPEG-TS y métricas de continuidad/bitrate.
- Aplicar configuración, reiniciar, ver logs y volver a la versión anterior.
- Configuración de red y multicast sólo mediante operaciones seguras predefinidas.
- Instalador Debian, unidad systemd y desinstalación limpia.

## Fases

1. **Base:** CI para 20.04/22.04/24.04, compilación reproducible, sanitizers y pruebas mínimas.
2. **Empaquetado:** layout `/usr/lib`, `/etc`, `/var/lib`, usuario de servicio, systemd y logrotate/journald.
3. **API:** modelo de configuración versionado, validación, health checks y control del proceso.
4. **Panel MVP:** dashboard, streams, configuración y logs.
5. **Endurecimiento:** auditoría del parser HTTP/MPEG-TS, fuzzing, pruebas de carga, permisos y actualización segura.

## Criterios antes de producción

- Builds verdes en las tres LTS y pruebas amd64.
- ASan/UBSan sin fallos en corpus representativo.
- Fuzzing de entradas HTTP y MPEG-TS.
- Ningún proceso web como root.
- TLS y autenticación obligatorios.
- Backups y rollback verificados.
- Pruebas reales de multicast, DVB y reconexión por al menos 72 horas.
