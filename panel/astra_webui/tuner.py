"""Señal del sintonizador DVB (S, Q, BER, UNC) en modo consulta.

Usa `femon` (paquete dvb-apps) porque el driver del TBS 6904se no acepta los ioctl
antiguos (FE_READ_*, devuelven ENOTTY) y sí las propiedades DVBv5 que emplea femon.
Es SÓLO LECTURA: abrir el frontend para consultar no ocupa el sintonizador, así que
convive con el streaming en marcha.

El panel corre como usuario `astra`, que pertenece al grupo `video`; por eso puede
abrir /dev/dvb/adapterN/frontend0 sin root.

Salida ejemplo de femon -a 0 -H -c 1:
    FE: TurboSight TBS 6904se DVB-S/S2/S2x  (DVBS)
    status SCVYL | signal  74% | snr  65% | ber 0 | unc -1384439808 | FE_HAS_LOCK
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

# Caché corta: el panel refresca cada 2 s, no hace falta lanzar femon en cada petición.
CACHE_MS = 1500
TIMEOUT_S = 3.0

_BINARIOS = ("/usr/bin/femon", "/usr/local/bin/femon", "/sbin/femon")

# Bits de estado del frontend (SCVYL)
_BITS = (("S", "signal"), ("C", "carrier"), ("V", "fec"), ("Y", "sync"), ("L", "lock"))

_RE_ESTADO = re.compile(r"status\s+([A-Z]+)")
_RE_SENAL = re.compile(r"signal\s+(-?\d+)%")
_RE_SNR = re.compile(r"snr\s+(-?\d+)%")
_RE_BER = re.compile(r"ber\s+(-?\d+)")
_RE_UNC = re.compile(r"unc\s+(-?\d+)")
_RE_FE = re.compile(r"^FE:\s*(.+?)\s*$", re.M)

_lock = threading.Lock()
_cache: dict = {}


def _binario():
    for ruta in _BINARIOS:
        if os.path.exists(ruta) and os.access(ruta, os.X_OK):
            return ruta
    return None


def _entero(valor):
    """Los contadores se imprimen a veces sin signo; un negativo es basura del driver."""
    if valor is None:
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero >= 0 else None


def _consultar(adapter):
    binario = _binario()
    if not binario:
        return {"adapter": adapter, "available": False,
                "reason": "femon no está instalado (paquete dvb-apps)"}
    try:
        proceso = subprocess.run(
            [binario, "-a", str(int(adapter)), "-H", "-c", "1"],
            capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"adapter": adapter, "available": False, "reason": "femon no respondió"}
    except OSError as exc:
        return {"adapter": adapter, "available": False, "reason": str(exc)}

    texto = "\n".join(parte for parte in (proceso.stdout, proceso.stderr) if parte)
    estado = _RE_ESTADO.search(texto)
    if not estado:
        return {"adapter": adapter, "available": False,
                "reason": "sin lectura del frontend (¿adaptador sin cable o ocupado?)"}

    letras = estado.group(1)
    senal = _RE_SENAL.search(texto)
    snr = _RE_SNR.search(texto)
    ber = _RE_BER.search(texto)
    unc = _RE_UNC.search(texto)
    nombre = _RE_FE.search(texto)

    datos = {
        "adapter": adapter,
        "available": True,
        "signal": int(senal.group(1)) if senal else None,
        "snr": int(snr.group(1)) if snr else None,
        "ber": _entero(ber.group(1) if ber else None),
        "unc": _entero(unc.group(1) if unc else None),
        "locked": "L" in letras,
        "frontend": nombre.group(1).strip() if nombre else "",
    }
    # OJO: los indicadores van con prefijo para no pisar signal (porcentaje) ni snr.
    for letra, clave in _BITS:
        datos["has_" + clave] = letra in letras
    return datos


def leer(adapter, forzar=False):
    """Estado del sintonizador `adapter`, con caché de CACHE_MS milisegundos."""
    try:
        adapter = int(adapter)
    except (TypeError, ValueError):
        adapter = 0
    ahora = time.time()
    with _lock:
        guardado = _cache.get(adapter)
        if guardado and not forzar and (ahora - guardado[0]) * 1000 < CACHE_MS:
            return guardado[1]
    datos = _consultar(adapter)
    with _lock:
        _cache[adapter] = (ahora, datos)
    return datos


def de_canales(canales):
    """Adaptadores que usan de verdad los canales, en orden."""
    adaptadores = set()
    for canal in canales or []:
        for entrada in (canal.get("inputs") or []):
            if not isinstance(entrada, dict):
                continue
            if str(entrada.get("type", "")).lower() != "dvb":
                continue
            try:
                adaptadores.add(int(entrada.get("adapter") or 0))
            except (TypeError, ValueError):
                adaptadores.add(0)
    return sorted(adaptadores)


def presentes():
    """Números de adaptador que existen en /dev/dvb (aunque no los use ningún canal)."""
    import glob
    import re
    vistos = set()
    for ruta in glob.glob("/dev/dvb/adapter*/frontend*"):
        coincidencia = re.search(r"/adapter(\d+)/", ruta)
        if coincidencia:
            vistos.add(int(coincidencia.group(1)))
    return sorted(vistos)
