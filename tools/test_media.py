#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valida el generador de MPEG-TS de prueba (tools/make_test_ts.py).

Comprueba lo que el núcleo astra necesita de verdad: CRC de las tablas PSI, cabeceras
PES válidas, contadores de continuidad y **PCR** (el módulo `file` de astra rechaza el
flujo con «first PCR is not found» si falta, y entonces el canal nunca entra en emisión).

    python3 tools/test_media.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from make_test_ts import generate, mpeg_crc32, verify, PACKET_SIZE, SYNC  # noqa: E402

FAILURES = []
CHECKS = [0]


def check(condition, label, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  \033[32mOK\033[0m   %s" % label)
    else:
        FAILURES.append(label)
        print("  \033[31mFALLO\033[0m %s %s" % (label, detail))
    return bool(condition)


def parse(path):
    raw = open(path, "rb").read()
    packets = []
    for offset in range(0, len(raw) - PACKET_SIZE + 1, PACKET_SIZE):
        packet = raw[offset:offset + PACKET_SIZE]
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        afc = (packet[3] >> 4) & 0x03
        # con campo de adaptación (afc 2 o 3) el payload empieza después de él
        payload_offset = 4 + (1 + packet[4]) if afc in (2, 3) else 4
        has_af = afc in (2, 3)
        packets.append({
            "pid": pid,
            "start": bool(packet[1] & 0x40),
            "afc": afc,
            "cc": packet[3] & 0x0F,
            "pcr": (packet[5] & 0x10) if has_af and packet[4] >= 7 else 0,
            "pcr_value": ((packet[6] << 25) | (packet[7] << 17) | (packet[8] << 9)
                          | (packet[9] << 1) | (packet[10] >> 7))
                         if has_af and packet[4] >= 7 and (packet[5] & 0x10) else None,
            "payload": packet[payload_offset:],
        })
    return raw, packets


def main():
    tmp = tempfile.mkdtemp(prefix="astra-ts-")
    path = os.path.join(tmp, "demo.ts")
    print("Generando %s" % path)
    info = generate(path, seconds=4, bitrate_kbps=4000, pnr=101, name="Canal Demo")

    print("\n== estructura")
    raw, packets = parse(path)
    check(len(raw) % PACKET_SIZE == 0, "el tamaño es múltiplo de 188",
          "%d bytes" % len(raw))
    check(all(raw[i * PACKET_SIZE] == SYNC for i in range(len(packets))),
          "todos los paquetes empiezan por 0x47")
    check(not verify(path), "la verificación del generador pasa")

    print("\n== CRC de las tablas")
    check(mpeg_crc32(b"123456789") == 0x0376E6E7, "CRC-32/MPEG-2 correcto (vector de prueba)")
    tables = {}
    for packet in packets:
        if packet["start"] and packet["pid"] in (0x0000, 0x0011, 0x0100) \
                and packet["pid"] not in tables:
            section = packet["payload"][1:]
            length = ((section[1] & 0x0F) << 8) | section[2]
            section = section[:3 + length]
            stored = int.from_bytes(section[-4:], "big")
            tables[packet["pid"]] = (section[0], stored, mpeg_crc32(section[:-4]), section)
    for pid, (table_id, stored, calculated, _) in sorted(tables.items()):
        check(stored == calculated, "CRC válido en el PID 0x%04X (table_id 0x%02X)"
              % (pid, table_id), "0x%08X != 0x%08X" % (stored, calculated))
    check(0x0000 in tables and 0x0100 in tables and 0x0011 in tables,
          "están la PAT, la PMT y la SDT")

    print("\n== PCR (obligatorio para el módulo file de astra)")
    with_pcr = [p for p in packets if p["pcr"]]
    check(bool(with_pcr), "el flujo lleva PCR", "%d paquetes" % len(with_pcr))
    if with_pcr:
        primer_pid = with_pcr[0]["pid"]
        check(primer_pid == 0x0101, "el PCR viaja en el PID de vídeo declarado como PCR",
              "0x%04X" % primer_pid)
        check(with_pcr[0]["pcr_value"] is not None, "el primer PCR se puede decodificar")
        valores = [p["pcr_value"] for p in with_pcr if p["pcr_value"] is not None]
        check(all(b >= a for a, b in zip(valores, valores[1:])),
              "el PCR es monótono (nunca retrocede)")
        check(with_pcr[0]["pcr_value"] < 90000, "el primer PCR está al principio del flujo",
              "%d" % with_pcr[0]["pcr_value"])
        separacion = max(
            (b - a) for a, b in zip(valores, valores[1:])
        ) if len(valores) > 1 else 0
        check(separacion <= 90000 * 0.5, "los PCR no están separados más de 0,5 s",
              "%d ticks" % separacion)

    print("\n== PES y continuidad")
    video = [p for p in packets if p["pid"] == 0x0101]
    pes_start = [p for p in video if p["start"]]
    check(bool(pes_start), "hay paquetes de inicio de PES en el PID de vídeo")
    if pes_start:
        check(pes_start[0]["payload"][:3] == b"\x00\x00\x01",
              "el PES empieza por el código de inicio 0x000001")
    for pid in (0x0000, 0x0100, 0x0101, 0x0102):
        secuencia = [p["cc"] for p in packets if p["pid"] == pid]
        saltos = sum(1 for a, b in zip(secuencia, secuencia[1:]) if (a + 1) % 16 != b)
        check(saltos == 0, "contador de continuidad correcto en el PID 0x%04X" % pid,
              "%d saltos" % saltos)

    print("\n== PIDs")
    pids = {p["pid"] for p in packets}
    for pid, nombre in ((0x0000, "PAT"), (0x0011, "SDT"), (0x0100, "PMT"),
                        (0x0101, "vídeo"), (0x0102, "audio"), (0x1FFF, "relleno")):
        check(pid in pids, "el PID 0x%04X (%s) aparece" % (pid, nombre))

    check(info["packets"] == len(packets), "el número de paquetes coincide con el resumen",
          "%s vs %s" % (info["packets"], len(packets)))

    print("\n--------------------------------------------------------------")
    if FAILURES:
        print("\033[31m%d fallos de %d comprobaciones\033[0m" % (len(FAILURES), CHECKS[0]))
        return 1
    print("\033[32mTodas las comprobaciones pasaron (%d)\033[0m" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
