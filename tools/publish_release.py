#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publica una release en GitHub con los ficheros de la carpeta `release/`.

Usa la API REST (no necesita git ni gh). El token se pasa por variable de entorno
para que no quede en el historial ni en la lista de procesos:

    GITHUB_TOKEN=github_pat_xxx python3 tools/publish_release.py --tag v4.0.282-10
    python3 tools/publish_release.py --tag v4.0.282-10 --dry-run   # sólo muestra qué haría

Comprueba antes:
  * que el token es válido y a qué usuario pertenece (GET /user),
  * que el repositorio es accesible y el usuario tiene permiso de escritura
    (GET /repos/... → campo permissions.push),
  * qué ficheros de `release/` se van a subir y sus tamaños.

Con `--draft` la release se crea como borrador (no visible hasta que la publiques).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
REPO_DEFAULT = "TalaveraSama/forkcastra-master"
ASSET_SUFFIXES = (".deb", ".tar.gz", ".zip", ".rpm", ".AppImage")
ASSET_FILES = ("SHA256SUMS", "SHA256SUMS.txt")


def log(message):
    print("\033[1m[release]\033[0m %s" % message)


def info(message):
    print("  · %s" % message)


def die(message, code=2):
    print("\033[31m[release] ERROR:\033[0m %s" % message, file=sys.stderr)
    sys.exit(code)


def api(method, url, token, body=None, content_type="application/json", raw=False):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("Authorization", "Bearer %s" % token)
    request.add_header("User-Agent", "astra-webui-release")
    if data is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
            return response.status, (payload if raw else json.loads(payload.decode("utf-8") or "{}"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            message = parsed.get("message", detail[:200])
            if parsed.get("errors"):
                message += " (%s)" % "; ".join(str(e.get("message", e)) for e in parsed["errors"])
        except ValueError:
            message = detail[:200]
        return exc.code, {"message": message}


def release_body(repo_root, tag, core, version, asset_names):
    source = os.path.join(repo_root, "docs", "RELEASE-NOTES.md")
    body = ""
    if os.path.isfile(source):
        body = open(source, encoding="utf-8").read()
        body = (body.replace("@@TAG@@", tag).replace("@@CORE@@", core)
                    .replace("@@VERSION@@", version))
    names = sorted(asset_names)
    aviso = [
        "",
        "## Instalación en una línea",
        "",
        "```sh",
        "curl -fsSL https://github.com/TalaveraSama/forkcastra-master/releases/latest/download/instalar.sh | sudo bash",
        "```",
        "",
        "## Ficheros adjuntos en esta release",
        "",
    ]
    for name in names:
        aviso.append("* `%s`" % name)
    if not any(name.startswith("astra-webui_") and name.endswith(".deb") for name in names):
        aviso += [
            "",
            "> **Falta el paquete completo** (`astra-webui_%s-1_amd64.deb`, con el núcleo"
            " compilado): sólo puede construirse en Linux. Se añade lanzando el workflow"
            " **Actions → release** con la etiqueta `%s`." % (core, tag),
        ]
    return body.rstrip() + "\n" + "\n".join(aviso) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Publica una release con los ficheros de release/")
    parser.add_argument("--repo", default=REPO_DEFAULT, help="propietario/repositorio")
    parser.add_argument("--tag", required=True, help="etiqueta de la release (p. ej. v4.0.282-10)")
    parser.add_argument("--title", default="", help="título (por defecto: Astra WebUI <etiqueta>)")
    parser.add_argument("--dir", default=os.path.join(ROOT, "release"),
                        help="carpeta con los ficheros a subir")
    parser.add_argument("--core", default="", help="versión del núcleo (por defecto, la de la etiqueta)")
    parser.add_argument("--version", default="", help="versión del panel (por defecto, la del .deb)")
    parser.add_argument("--draft", action="store_true", help="crear la release como borrador")
    parser.add_argument("--only", action="append", default=[],
                        help="subir sólo estos ficheros (ruta o nombre); repetible")
    parser.add_argument("--no-body", action="store_true", help="no tocar el texto de la release")
    parser.add_argument("--dry-run", action="store_true", help="no publicar: sólo comprobar y mostrar")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token and not args.dry_run:
        die("falta el token: exporta GITHUB_TOKEN=... (o usa --dry-run)")

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        die("no existe la carpeta %s (usa: python3 tools/make_release.py)" % directory)

    assets = []
    only = {os.path.basename(item) for item in args.only}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if only and name not in only:
            continue
        if name.endswith(ASSET_SUFFIXES) or name in ASSET_FILES or name in only:
            assets.append(path)
    if not assets:
        die("no hay ficheros que subir en %s" % directory)

    core = args.core or (os.path.basename(args.tag).split("-")[0].lstrip("v") or "4.0.282")
    version = args.version
    for path in assets:
        match = re.match(r"astra-webui-panel_([0-9.]+)-", os.path.basename(path))
        if match:
            version = match.group(1)
    version = version or "1.0.0"
    title = args.title or "Astra WebUI %s" % args.tag

    log("release %s en %s" % (args.tag, args.repo))
    info("título: %s" % title)
    info("núcleo: %s · panel: %s" % (core, version))
    info("ficheros:")
    for path in assets:
        print("      %-46s %8.1f KiB" % (os.path.basename(path), os.path.getsize(path) / 1024.0))

    body = release_body(ROOT, args.tag, core, version, assets)
    info("cuerpo de la release: %d caracteres" % len(body))

    if args.dry_run:
        log("simulación (no se ha publicado nada)")
        print("\n----- primeras líneas del cuerpo -----")
        print("\n".join(body.splitlines()[:12]))
        print("...\n----- últimas líneas -----")
        print("\n".join(body.splitlines()[-8:]))
        return 0

    log("comprobando el token")
    status, user = api("GET", "%s/user" % API, token)
    if status != 200:
        die("el token no es válido (HTTP %s: %s)" % (status, user.get("message")))
    login = user.get("login", "?")
    info("token válido del usuario %s" % login)

    status, repo = api("GET", "%s/repos/%s" % (API, args.repo), token)
    if status != 200:
        die("no se puede leer %s (HTTP %s: %s)" % (args.repo, status, repo.get("message")))
    permissions = repo.get("permissions") or {}
    info("repositorio: %s (%s)" % (repo.get("full_name"), "público" if not repo.get("private") else "privado"))
    if not (permissions.get("push") or permissions.get("admin")):
        die("el token no tiene permiso de escritura en %s (permisos: %s)" % (args.repo, permissions))
    info("permiso de escritura: sí")

    status, existing = api("GET", "%s/repos/%s/releases/tags/%s" % (API, args.repo, args.tag), token)
    release = None
    if status == 200:
        release = existing
        info("la release %s ya existe: se reutiliza (id %s)" % (args.tag, release.get("id")))
    elif status not in (404,):
        die("no se pudo comprobar si la release existe (HTTP %s: %s)" % (status, existing.get("message")))

    # el texto final lista los ficheros que ya tenía la release más los que se suben ahora
    nombres = {os.path.basename(path) for path in assets}
    nombres |= {asset.get("name") for asset in (release or {}).get("assets") or []}
    body = release_body(ROOT, args.tag, core, version, nombres)
    if args.no_body and release is not None:
        body = release.get("body") or body

    if release is None:
        log("creando la release %s" % args.tag)
        payload = {
            "tag_name": args.tag,
            "name": title,
            "body": body,
            "draft": bool(args.draft),
            "prerelease": False,
        }
        status, release = api("POST", "%s/repos/%s/releases" % (API, args.repo), token, payload)
        if status not in (200, 201):
            die("no se pudo crear la release (HTTP %s: %s)" % (status, release.get("message")))
        info("release creada: %s" % release.get("html_url"))
    else:
        status, release = api("PATCH", "%s/repos/%s/releases/%s" % (API, args.repo, release["id"]),
                              token, {"name": title, "body": body, "draft": bool(args.draft)})
        if status != 200:
            die("no se pudo actualizar la release (HTTP %s: %s)" % (status, release.get("message")))
        info("release actualizada: %s" % release.get("html_url"))

    uploaded = {asset.get("name") for asset in (release.get("assets") or [])}
    for path in assets:
        name = os.path.basename(path)
        if name in uploaded:
            info("%s ya estaba subido: se reemplaza" % name)
            for asset in release["assets"]:
                if asset.get("name") == name:
                    api("DELETE", "%s/repos/%s/releases/assets/%s" % (API, args.repo, asset["id"]), token)
        data = open(path, "rb").read()
        url = "%s/repos/%s/releases/%s/assets?name=%s" % (UPLOADS, args.repo, release["id"], name)
        status, result = api("POST", url, token, data, content_type="application/octet-stream")
        if status not in (200, 201):
            die("fallo al subir %s (HTTP %s: %s)" % (name, status, result.get("message")))
        info("subido %s (%.1f KiB)" % (name, len(data) / 1024.0))

    status, final = api("GET", "%s/repos/%s/releases/%s" % (API, args.repo, release["id"]), token)
    print()
    log("listo: %s" % final.get("html_url"))
    print("      etiqueta: %s" % final.get("tag_name"))
    print("      ficheros: %s" % ", ".join(a.get("name") for a in (final.get("assets") or [])))
    print("      estado:   %s" % ("borrador" if final.get("draft") else "publicada"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
