#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ejecuta comandos por SSH (con contraseña) y muestra la salida.

Pensado para diagnosticar y reparar una instalación de Astra WebUI en un servidor.

    python3 tools/ssh_exec.py --host 192.168.1.21 --user ruben --password-env PASS \
        "systemctl status astra --no-pager"
    python3 tools/ssh_exec.py --host H --user U --password-env PASS --sudo \
        "journalctl -u astra -n 50 --no-pager"

Opciones:
  --sudo            ejecuta el comando con sudo (la contraseña se pasa por stdin)
  --script FICHERO  ejecuta un script local (se copia por stdin)
  --timeout N       segundos por comando (por defecto 60)
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import paramiko
except ImportError:  # pragma: no cover
    print("ERROR: falta paramiko →  pip install paramiko", file=sys.stderr)
    sys.exit(2)


def connect(host, user, password, port=22, timeout=20):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=port, username=user, password=password,
                   timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                   look_for_keys=False, allow_agent=False)
    return client


def run(client, command, password, use_sudo=False, timeout=60):
    if use_sudo:
        command = "sudo -S -p '' bash -lc %s" % shlex_quote(command)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=False)
    if use_sudo:
        stdin.write(password + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def shlex_quote(text):
    return "'" + text.replace("'", "'\"'\"'") + "'"


def main():
    parser = argparse.ArgumentParser(description="Ejecuta un comando por SSH")
    parser.add_argument("command", nargs="*", help="comando a ejecutar (entre comillas)")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--password", default="", help="contraseña (mejor usar --password-env)")
    parser.add_argument("--password-env", default="", help="variable de entorno con la contraseña")
    parser.add_argument("--sudo", action="store_true", help="ejecutar con sudo")
    parser.add_argument("--script", default="", help="script local que se ejecuta en el servidor")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--quiet", action="store_true", help="no mostrar la cabecera del comando")
    args = parser.parse_args()

    password = args.password
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    if not password:
        print("ERROR: falta la contraseña (--password o --password-env)", file=sys.stderr)
        return 2

    if args.script:
        with open(args.script, encoding="utf-8") as handle:
            command = handle.read()
    else:
        command = " ".join(args.command)
    if not command.strip():
        parser.error("indica un comando o --script")

    client = connect(args.host, args.user, password, args.port)
    try:
        code, out, err = run(client, command, password, args.sudo, args.timeout)
    finally:
        client.close()

    if not args.quiet:
        print("$ %s%s" % ("sudo " if args.sudo else "", command[:160]))
    if out:
        print(out.rstrip())
    if err:
        print("--- stderr ---")
        print(err.rstrip())
    if code not in (0, None):
        print("[salida: %s]" % code)
    return 0 if code in (0, None) else 1


if __name__ == "__main__":
    sys.exit(main())
