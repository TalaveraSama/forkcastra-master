# -*- coding: utf-8 -*-
"""Registro de actividad del panel (fichero + stdout)."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from . import util

LOGGER_NAME = "astra-webui"


def setup(path=None, level="INFO", to_stdout=True):
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    if path:
        try:
            util.ensure_dir(os.path.dirname(path) or ".", mode=0o750)
            handler = logging.handlers.WatchedFileHandler(path, encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except OSError:
            to_stdout = True

    if to_stdout:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    return logger


def get():
    return logging.getLogger(LOGGER_NAME)
