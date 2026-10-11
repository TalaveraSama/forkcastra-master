# -*- coding: utf-8 -*-
"""Astra WebUI — panel administrativo para Astra 4 (fork forkcastra-master).

Módulos:
    config        configuración (panel.json)
    store         persistencia (canales, usuarios, DVB, auditoría)
    validators    validación/normalización del modelo
    security      contraseñas, sesiones, CSRF, límite de intentos
    agentclient   cliente de la API del agente Lua
    metrics       métricas del host y del proceso astra
    logtail       lectura y seguimiento de logs
    m3u           listas de reproducción
    httpd         servidor HTTP (rutas, estáticos, SSE)
    api_*         rutas de la API REST
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
