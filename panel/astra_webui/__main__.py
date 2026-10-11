# -*- coding: utf-8 -*-
"""Punto de entrada del panel:  python3 -m astra_webui [opciones]"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, config, httpd, logs, security, util
from .app import App


def build_parser():
    parser = argparse.ArgumentParser(
        prog="astra-webui",
        description="Panel administrativo web para Astra 4 (fork forkcastra-master)",
    )
    parser.add_argument("-c", "--config", default=None,
                        help="ruta de panel.json (por defecto /etc/astra-webui/panel.json)")
    parser.add_argument("--listen", default=None, help="dirección de escucha (0.0.0.0)")
    parser.add_argument("--port", default=None, type=int, help="puerto HTTP del panel")
    parser.add_argument("--web-dir", default=None, help="directorio de la interfaz web")
    parser.add_argument("--data-dir", default=None,
                        help="directorio de datos (canales, usuarios, auditoría)")
    parser.add_argument("--dev", action="store_true", help="modo desarrollo (rutas locales)")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING o ERROR")
    parser.add_argument("--check", action="store_true", help="valida la configuración y sale")
    parser.add_argument("--reset-admin-password", action="store_true",
                        help="genera una contraseña nueva para 'admin' y la imprime")
    parser.add_argument("--print-version", action="store_true", help="muestra la versión y sale")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.print_version:
        print("astra-webui %s" % __version__)
        return 0

    overrides = {}
    if args.listen:
        overrides["listen"] = args.listen
    if args.port:
        overrides["port"] = util.to_int(args.port, 8000)
    if args.web_dir:
        overrides["web_dir"] = args.web_dir
    if args.dev:
        overrides["dev"] = True
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = args.data_dir or os.path.join(here, "var")
        overrides.setdefault("paths", {})
        overrides["paths"].update({
            "channels": os.path.join(data_dir, "channels.json"),
            "auth": os.path.join(data_dir, "auth.json"),
            "users": os.path.join(data_dir, "users.json"),
            "sessions": os.path.join(data_dir, "sessions.json"),
            "dvb": os.path.join(data_dir, "dvb.json"),
            "audit": os.path.join(data_dir, "audit.jsonl"),
            "history": os.path.join(data_dir, "history.json"),
            "panel_log": os.path.join(data_dir, "panel.log"),
            "recordings": os.path.join(data_dir, "recordings"),
        })
        overrides["web_dir"] = os.path.join(here, "web")
        overrides["service_control"] = {"enabled": False}
        overrides["agent"] = {"token_file": os.path.join(data_dir, "agent.token")}
    elif args.data_dir:
        data_dir = args.data_dir
        overrides.setdefault("paths", {})
        for key, name in (("channels", "channels.json"), ("auth", "auth.json"), ("users", "users.json"),
                          ("sessions", "sessions.json"), ("dvb", "dvb.json"), ("history", "history.json")):
            overrides["paths"][key] = os.path.join(data_dir, name)
        overrides["paths"]["audit"] = os.path.join(data_dir, "audit.jsonl")
        overrides["paths"]["recordings"] = os.path.join(data_dir, "recordings")

    try:
        cfg = config.load(args.config, overrides)
    except SystemExit as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    logs.setup(cfg.get("paths", {}).get("panel_log"), args.log_level, to_stdout=True)
    logger = logs.get()
    logger.info("Astra WebUI %s (config=%s)", __version__, cfg.get("_config_path"))

    if args.check:
        problems = []
        for key in ("channels", "auth", "users", "sessions", "dvb", "history"):
            path = config.get_path(cfg, "paths.%s" % key)
            directory = os.path.dirname(path)
            if not os.path.isdir(directory):
                problems.append("falta el directorio %s" % directory)
        if not os.path.isdir(cfg.get("web_dir", "")):
            problems.append("no existe el directorio web: %s" % cfg.get("web_dir"))
        if problems:
            for problem in problems:
                print("AVISO: %s" % problem, file=sys.stderr)
            return 1
        print("configuración correcta: escucha %s:%s, web %s"
              % (cfg.get("listen"), cfg.get("port"), cfg.get("web_dir")))
        return 0

    app = App(cfg)

    if args.reset_admin_password:
        admin = app.users.find(username="admin")
        if not admin:
            print("ERROR: no existe el usuario 'admin'", file=sys.stderr)
            return 1
        password = util.random_secret(16)
        _user, errors = app.users.change_password(admin.get("id"), password, "cli")
        if errors:
            print("ERROR: %s" % "; ".join(errors), file=sys.stderr)
            return 1
        print(password)
        return 0

    admin_password = os.environ.get("ASTRA_WEBUI_ADMIN_PASSWORD") or None
    if not admin_password:
        initial_file = os.path.join(os.path.dirname(cfg.get("_config_path") or "/etc/astra-webui/panel.json"),
                                    "initial-admin-password")
        if os.path.exists(initial_file):
            admin_password = util.sanitize_text(util.read_text(initial_file, ""), 128) or None
    created = app.bootstrap(admin_password)
    if created:
        # la contraseña inicial sólo vive en el fichero del postinst
        logger.warning("usuario inicial '%s' con contraseña temporal %s",
                       created["username"], created["password"])

    app.start_background()
    try:
        return httpd.serve(app)
    finally:
        app.stop_background()


if __name__ == "__main__":
    sys.exit(main())
