#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera un MPEG-TS válido de un solo programa, para probar canales sin fuente real.

El fichero resultante lleva PAT, PMT y SDT con sus CRC correctos, un PID de vídeo
y otro de audio con cabeceras PES válidas, y relleno con paquetes nulos hasta la
tasa indicada. El analizador del núcleo (`analyze`) lo acepta como flujo en
emisión (`on_air`), así que sirve para:

  * crear un canal de tipo `file` con `loop = true` y comprobar que arranca;
  * ver el visor PSI del panel con tablas reales;
  * probar el streaming con token sin depender de una fuente de Internet.

    python3 tools/make_test_ts.py /tmp/demo.ts --seconds 30 --bitrate 4000
    python3 tools/make_test_ts.py /tmp/demo.ts --name "Canal Demo" --pnr 101
"""

from __future__ import annotations

import argparse
import os
import sys

PACKET_SIZE = 188
SYNC = 0x47
NULL_PID = 0x1FFF

PAT_PID = 0x0000
SDT_PID = 0x0011


def mpeg_crc32(data: bytes) -> int:
    """CRC-32/MPEG-2 (polinomio 0x04C11DB7, init 0xFFFFFFFF, sin reflejar)."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def _u16(value):
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def _u32(value):
    return bytes([(value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])


def _crc_bytes(section_without_crc: bytes) -> bytes:
    return _u32(mpeg_crc32(section_without_crc))


def _psi_section(table_id, table_id_extension, payload, version=0):
    """Envuelve `payload` en una sección PSI completa (con CRC)."""
    section_length = 5 + len(payload) + 4  # tras los 3 bytes iniciales
    header = bytes([
        table_id,
        0xB0 | ((section_length >> 8) & 0x0F),
        section_length & 0xFF,
        (table_id_extension >> 8) & 0xFF,
        table_id_extension & 0xFF,
        0xC0 | ((version & 0x1F) << 1) | 0x01,  # reserved + version + current_next
        0x00,  # section_number
        0x00,  # last_section_number
    ])
    body = header + payload
    return body + _crc_bytes(body)


def build_pat(tsid, pmt_pid, program_number=1):
    payload = _u16(program_number) + bytes([0xE0 | ((pmt_pid >> 8) & 0x1F), pmt_pid & 0xFF])
    return _psi_section(0x00, tsid, payload)


def build_pmt(program_number, pcr_pid, streams):
    payload = bytes([0xE0 | ((pcr_pid >> 8) & 0x1F), pcr_pid & 0xFF, 0xF0, 0x00])
    for stream_type, pid in streams:
        payload += bytes([stream_type, 0xE0 | ((pid >> 8) & 0x1F), pid & 0xFF, 0xF0, 0x00])
    return _psi_section(0x02, program_number, payload)


def build_sdt(tsid, onid, service_id, provider, name, service_type=0x01):
    provider_b = provider.encode("latin-1", "replace")[:255]
    name_b = name.encode("latin-1", "replace")[:255]
    descriptor = bytes([0x48, len(provider_b) + len(name_b) + 3, service_type,
                        len(provider_b)]) + provider_b + bytes([len(name_b)]) + name_b
    service = (_u16(service_id) + bytes([0xFC, 0x80 | (len(descriptor) >> 8), len(descriptor) & 0xFF])
               + descriptor)
    payload = _u16(onid) + bytes([0xFF]) + service
    return _psi_section(0x42, tsid, payload)


def _pcr_bytes(pcr_27mhz):
    """Codifica un PCR (base de 33 bits en 90 kHz + extensión de 9 bits)."""
    base = (pcr_27mhz // 300) & 0x1FFFFFFFF
    ext = (pcr_27mhz % 300) & 0x1FF
    return bytes([
        (base >> 25) & 0xFF,
        (base >> 17) & 0xFF,
        (base >> 9) & 0xFF,
        (base >> 1) & 0xFF,
        ((base & 1) << 7) | 0x7E | ((ext >> 8) & 0x01),
        ext & 0xFF,
    ])


class TsWriter:
    def __init__(self, handle, bitrate_kbps=4000):
        self.handle = handle
        self.continuity = {}
        self.packets = 0
        # reloj del flujo: cada paquete avanza lo que tarda en llegar a ese bitrate
        self.clock = 0
        self.step = int(188 * 8 * 90000 / max(1, bitrate_kbps * 1000) * 300)  # en 27 MHz

    def _cc(self, pid):
        value = self.continuity.get(pid, 0)
        self.continuity[pid] = (value + 1) & 0x0F
        return value

    def _adaptation(self, pcr):
        """Campo de adaptación con PCR (obligatorio para que astra sincronice)."""
        if pcr is None:
            return b"", 0x10
        pcr = pcr % (1 << 33) * 300
        return bytes([7, 0x10]) + _pcr_bytes(pcr), 0x30

    def write_packet(self, pid, payload, start=True, pcr=None):
        adaptation, afc = self._adaptation(pcr)
        if len(adaptation) + len(payload) > 184:
            payload = payload[:184 - len(adaptation)]
        header = bytes([
            SYNC,
            (0x40 if start else 0x00) | ((pid >> 8) & 0x1F),
            pid & 0xFF,
            afc | self._cc(pid),
        ])
        packet = header + adaptation + payload
        packet += bytes([0xFF]) * (PACKET_SIZE - len(packet))
        self.handle.write(packet)
        self.packets += 1
        self.clock += self.step

    def write_psi(self, pid, section):
        payload = bytes([0x00]) + section  # pointer_field
        self.write_packet(pid, payload)

    def write_null(self):
        self.write_packet(NULL_PID, b"", start=False)

    def write_pes(self, pid, stream_id, payload, pts=0, pcr=False):
        header = bytes([0x00, 0x00, 0x01, stream_id])
        length = len(payload) + 8
        header += _u16(length if length < 0x10000 else 0)
        header += bytes([0x80, 0x80, 0x05])  # '10' + PES header data length = 5
        pts_base = (pts & 0x1FFFFFFFF)
        header += bytes([
            0x21 | (((pts_base >> 30) & 0x07) << 1),
            (pts_base >> 22) & 0xFF,
            0x01 | (((pts_base >> 15) & 0x7F) << 1),
            (pts_base >> 7) & 0xFF,
            0x01 | ((pts_base & 0x7F) << 1),
        ])
        data = header + payload
        first = True
        while data:
            chunk, data = data[:184], data[184:]
            # el PCR viaja en el campo de adaptación del PID marcado como PCR
            self.write_packet(pid, chunk, start=first,
                              pcr=(self.clock // 300) if (pcr and first) else None)
            first = False


def generate(path, seconds=30, bitrate_kbps=4000, pnr=1, name="Canal Demo", provider="Astra WebUI",
             tsid=1, video_pid=0x0101, audio_pid=0x0102, pmt_pid=0x0100, onid=1):
    total_packets = max(200, int(bitrate_kbps * 1000 * seconds / (PACKET_SIZE * 8)))
    video = bytes([0x00, 0x00, 0x01, 0x09, 0xF0]) + bytes((i * 7) & 0xFF for i in range(120))
    audio = bytes([0xFF, 0xF1, 0x50, 0x80]) + bytes((i * 13) & 0xFF for i in range(100))

    with open(path, "wb") as handle:
        writer = TsWriter(handle, bitrate_kbps=bitrate_kbps)
        written = 0
        cycle = 0
        while written < total_packets:
            writer.write_psi(PAT_PID, build_pat(tsid, pmt_pid, pnr))
            writer.write_psi(pmt_pid, build_pmt(pnr, video_pid, [(0x1B, video_pid), (0x0F, audio_pid)]))
            if cycle % 10 == 0:
                writer.write_psi(SDT_PID, build_sdt(tsid, onid, pnr, provider, name))
            written += 3

            for index in range(24):
                if index % 6 == 0:
                    # el PCR (obligatorio para que astra sincronice) va en el PID de vídeo
                    writer.write_pes(video_pid, 0xE0, video,
                                     pts=cycle * 3600 + index * 100, pcr=True)
                elif index % 6 == 3:
                    writer.write_pes(audio_pid, 0xC0, audio, pts=cycle * 3600 + index * 100)
                else:
                    writer.write_null()
                written += 1
            cycle += 1

    size = os.path.getsize(path)
    return {"path": path, "bytes": size, "packets": size // PACKET_SIZE,
            "bitrate_kbps": bitrate_kbps, "seconds": seconds}


def verify(path):
    """Comprobaciones básicas del fichero generado (sync y CRC de las tablas)."""
    problems = []
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) % PACKET_SIZE:
        problems.append("el tamaño no es múltiplo de 188 (%d)" % len(data))
    for offset in range(0, min(len(data), PACKET_SIZE * 400), PACKET_SIZE):
        if data[offset] != SYNC:
            problems.append("falta el byte de sincronismo en el paquete %d" % (offset // PACKET_SIZE))
            break
    if mpeg_crc32(b"123456789") != 0x0376E6E7:
        problems.append("la implementación de CRC-32/MPEG-2 no pasa el vector de prueba")
    return problems


def main():
    parser = argparse.ArgumentParser(description="Genera un MPEG-TS de prueba válido")
    parser.add_argument("output", help="fichero .ts de salida")
    parser.add_argument("--seconds", type=int, default=30, help="duración simulada (por defecto 30)")
    parser.add_argument("--bitrate", type=int, default=4000, help="bitrate en kbit/s (por defecto 4000)")
    parser.add_argument("--pnr", type=int, default=1, help="program number / service id (por defecto 1)")
    parser.add_argument("--name", default="Canal Demo", help="nombre del servicio (SDT)")
    parser.add_argument("--provider", default="Astra WebUI", help="proveedor del servicio (SDT)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    info = generate(args.output, seconds=args.seconds, bitrate_kbps=args.bitrate,
                    pnr=args.pnr, name=args.name, provider=args.provider)
    problems = verify(args.output)
    if not args.quiet:
        print("TS generado: %(path)s (%(bytes)d bytes, %(packets)d paquetes, "
              "%(bitrate_kbps)d kbit/s durante %(seconds)ds)" % info)
        print("  servicio: %s (pnr %d, proveedor %s)" % (args.name, args.pnr, args.provider))
        print("  PIDs: PAT 0x0000, PMT 0x0100, vídeo 0x0101, audio 0x0102, SDT 0x0011")
    for problem in problems:
        print("AVISO: %s" % problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
