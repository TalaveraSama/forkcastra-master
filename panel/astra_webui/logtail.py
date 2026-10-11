# -*- coding: utf-8 -*-
"""Lectura de logs (astra, panel) con filtros y seguimiento en vivo."""

from __future__ import annotations

import os
import re
import time

from . import util

LEVELS = ("error", "warning", "info", "debug", "other")
_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2, "debug": 3, "other": 4}

# "Oct  2 08:28:11: INFO: mensaje"
_LINE_RE = re.compile(r"^(?P<ts>[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}):\s+(?P<level>[A-Z]+):\s?(?P<msg>.*)$")
# "2024-05-01 12:00:00,123 INFO mensaje" (log del panel)
_PANEL_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})[.,]?\d*\s+(?P<level>[A-Z]+)\s+(?P<msg>.*)$")


def parse_line(line, source="astra"):
    line = (line or "").rstrip("\r\n")
    if source == "panel":
        match = _PANEL_RE.match(line)
    else:
        match = _LINE_RE.match(line)
    if match:
        level = match.group("level").lower()
        if level not in LEVELS:
            level = "other"
        return {
            "timestamp": match.group("ts"),
            "level": level,
            "message": match.group("msg").strip(),
            "raw": line,
            "source": source,
        }
    return {"timestamp": "", "level": "other", "message": line, "raw": line, "source": source}


def read(path, lines=200, level=None, query=None, source="astra"):
    """Últimas `lines` líneas, filtradas por nivel y texto."""
    raw_lines = util.tail_lines(path, max_lines=util.to_int(lines, 200, minimum=1, maximum=5000))
    entries = [parse_line(line, source) for line in raw_lines]

    if level and level != "all":
        wanted = util.sanitize_text(level, 16).lower()
        if wanted in LEVELS:
            entries = [e for e in entries if e["level"] == wanted]

    if query:
        needle = util.sanitize_text(query, 128).lower()
        if needle:
            entries = [e for e in entries if needle in e["raw"].lower()]

    return entries


def stats(path):
    size = util.file_size(path)
    lines = util.tail_lines(path, max_lines=2000)
    counts = {level: 0 for level in LEVELS}
    for line in lines:
        entry = parse_line(line)
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        mtime = 0
    return {"size": size, "mtime": mtime, "sampled_lines": len(lines), "levels": counts}


class Follower:
    """Sigue un fichero de log y va entregando las líneas nuevas (para SSE)."""

    def __init__(self, path, source="astra", level=None, query=None, start_at_end=True, max_idle=1800):
        self.path = path
        self.source = source
        self.level = (level or "all").lower()
        self.query = util.sanitize_text(query or "", 128).lower()
        self.offset = 0
        self.inode = None
        self.max_idle = max_idle
        self._started = util.now()
        self._primed = False
        self._start_at_end = start_at_end

    def _prime(self):
        try:
            stat = os.stat(self.path)
            self.inode = stat.st_ino
            self.offset = stat.st_size if self._start_at_end else 0
        except OSError:
            self.inode = None
            self.offset = 0
        self._primed = True

    def _matches(self, entry):
        if self.level != "all" and entry["level"] != self.level:
            return False
        if self.query and self.query not in entry["raw"].lower():
            return False
        return True

    def poll(self):
        """Devuelve la lista de entradas nuevas (puede estar vacía)."""
        if not self._primed:
            self._prime()
            return []
        try:
            stat = os.stat(self.path)
        except OSError:
            return []
        if self.inode is not None and stat.st_ino != self.inode:
            self.offset = 0  # rotación del fichero
            self.inode = stat.st_ino
        if stat.st_size < self.offset:
            self.offset = 0  # truncado
        if stat.st_size == self.offset:
            return []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                data = handle.read()
                self.offset = handle.tell()
        except OSError:
            return []
        out = []
        for line in data.split("\n"):
            if not line.strip():
                continue
            entry = parse_line(line, self.source)
            if self._matches(entry):
                out.append(entry)
        return out

    def expired(self):
        return (util.now() - self._started) > self.max_idle


def follow_iter(follower, interval=0.7, max_seconds=1800, stop_flag=None):
    """Generador que hace polling y cede listas de entradas nuevas."""
    started = time.time()
    while True:
        if stop_flag is not None and stop_flag():
            return
        if time.time() - started > max_seconds:
            return
        entries = follower.poll()
        if entries:
            yield entries
        else:
            yield []
        time.sleep(interval)
