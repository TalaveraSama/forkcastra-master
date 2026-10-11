#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sube una carpeta a una rama del repositorio usando la API de GitHub (sin git).

Sirve para publicar el proyecto desde un equipo sin git ni GitHub Desktop.

    GITHUB_TOKEN=... python3 tools/github_push.py --branch webui-panel \
        --dir release/entrega --message "Añadir panel Astra WebUI"
    python3 tools/github_push.py --branch webui-panel --dir release/entrega --dry-run

Si la rama no existe se crea a partir de la rama base (por defecto, la del repositorio).
Los ficheros se envían como texto (todos los de la entrega lo son).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO_DEFAULT = "TalaveraSama/forkcastra-master"
EXCLUDE_DIRS = {"__pycache__", ".git", "var", "build", "dist", "release", "node_modules"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".deb", ".tar.gz", ".zip", ".o", ".so")


def log(message):
    print("\033[1m[push]\033[0m %s" % message)


def info(message):
    print("  · %s" % message)


def warn(message):
    print("\033[33m[push] AVISO:\033[0m %s" % message)


def die(message, code=2):
    print("\033[31m[push] ERROR:\033[0m %s" % message, file=sys.stderr)
    sys.exit(code)


def api(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("Authorization", "Bearer %s" % token)
    request.add_header("User-Agent", "astra-webui-push")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read()
            return response.status, (json.loads(payload.decode("utf-8") or "{}") if payload else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            message = parsed.get("message", detail[:300])
            for error in parsed.get("errors") or []:
                message += " | %s" % (error if isinstance(error, str) else error.get("message", error))
        except ValueError:
            message = detail[:300]
        return exc.code, {"message": message}


def mode_for(relative):
    """Permisos que debe tener el fichero en el repositorio.

    Los scripts tienen que ser ejecutables (100755): si se suben como 100644, el
    workflow falla con «exit code 126» al ejecutar ./packaging/build-deb.sh.
    """
    if relative.endswith(".sh"):
        return "100755"
    if relative.startswith("packaging/scripts/"):
        return "100755"          # postinst, prerm, postrm
    if "/" not in relative and relative.endswith(".sh"):
        return "100755"
    return "100644"


def collect(directory):
    """Lista los ficheros a subir (rutas relativas con / y contenido de texto)."""
    files = []
    skipped = []
    for base, dirs, names in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in sorted(names):
            full = os.path.join(base, name)
            relative = os.path.relpath(full, directory).replace(os.sep, "/")
            if name.endswith(EXCLUDE_SUFFIXES):
                skipped.append(relative)
                continue
            try:
                with open(full, "r", encoding="utf-8") as handle:
                    content = handle.read()
            except UnicodeDecodeError:
                skipped.append(relative + " (binario)")
                continue
            files.append({"path": relative, "mode": mode_for(relative),
                          "type": "blob", "content": content})
    return files, skipped


def main():
    parser = argparse.ArgumentParser(description="Sube una carpeta a una rama del repositorio (API)")
    parser.add_argument("--repo", default=REPO_DEFAULT)
    parser.add_argument("--branch", required=True, help="rama de destino (se crea si no existe)")
    parser.add_argument("--base", default="", help="rama base (por defecto, la del repositorio)")
    parser.add_argument("--dir", required=True, help="carpeta local que se sube")
    parser.add_argument("--message", default="Añadir panel administrativo web Astra WebUI")
    parser.add_argument("--tag", default="", help="crear también una etiqueta (dispara el workflow release)")
    parser.add_argument("--dry-run", action="store_true", help="no subir: sólo mostrar el resumen")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token and not args.dry_run:
        die("falta el token: exporta GITHUB_TOKEN=... (o usa --dry-run)")

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        die("no existe la carpeta %s" % directory)

    files, skipped = collect(directory)
    total = sum(len(item["content"].encode("utf-8")) for item in files)
    log("carpeta %s: %d ficheros de texto (%.2f MiB)" % (directory, len(files), total / 1048576))
    for name in skipped[:10]:
        info("se omite %s" % name)
    if len(files) > 90:
        warn("%d ficheros en un solo commit: la API lo admite, pero puede tardar" % len(files))

    if args.dry_run:
        log("simulación: se subirían estos ficheros (primeros 15)")
        for item in files[:15]:
            print("      %s" % item["path"])
        print("      … y %d más" % max(0, len(files) - 15))
        return 0

    log("comprobando el repositorio y la rama")
    status, repo = api("GET", "%s/repos/%s" % (API, args.repo), token)
    if status != 200:
        die("no se puede leer %s (HTTP %s: %s)" % (args.repo, status, repo.get("message")))
    base = args.base or repo.get("default_branch") or "main"
    info("repositorio %s · rama base %s" % (repo.get("full_name"), base))

    status, ref = api("GET", "%s/repos/%s/git/ref/heads/%s" % (API, args.repo, base), token)
    if status != 200:
        die("no encuentro la rama %s (HTTP %s: %s)" % (base, status, ref.get("message")))
    base_commit = ref["object"]["sha"]
    status, commit = api("GET", "%s/repos/%s/git/commits/%s" % (API, args.repo, base_commit), token)
    if status != 200:
        die("no puedo leer el commit base (HTTP %s)" % status)
    base_tree = commit["tree"]["sha"]
    info("commit base %s · árbol %s" % (base_commit[:8], base_tree[:8]))

    log("creando el árbol con %d ficheros" % len(files))
    status, tree = api("POST", "%s/repos/%s/git/trees" % (API, args.repo), token,
                       {"base_tree": base_tree, "tree": files})
    if status not in (200, 201):
        if "workflow" in str(tree.get("message", "")).lower():
            die("el token no tiene permiso para crear ficheros de workflow:\n  %s\n"
                "Vuelve a intentarlo con un token que incluya el permiso 'workflow' "
                "(o pega los ficheros de .github/workflows a mano en la web)." % tree.get("message"))
        die("no se pudo crear el árbol (HTTP %s: %s)" % (status, tree.get("message")))
    info("árbol creado: %s" % tree["sha"][:8])

    log("creando el commit")
    status, new_commit = api("POST", "%s/repos/%s/git/commits" % (API, args.repo), token,
                             {"message": args.message, "tree": tree["sha"], "parents": [base_commit]})
    if status not in (200, 201):
        die("no se pudo crear el commit (HTTP %s: %s)" % (status, new_commit.get("message")))
    info("commit %s" % new_commit["sha"][:8])

    log("actualizando la rama %s" % args.branch)
    status, result = api("POST", "%s/repos/%s/git/refs" % (API, args.repo), token,
                         {"ref": "refs/heads/%s" % args.branch, "sha": new_commit["sha"]})
    if status in (200, 201):
        info("rama creada: %s" % args.branch)
    else:
        status, result = api("PATCH", "%s/repos/%s/git/refs/heads/%s" % (API, args.repo, args.branch),
                             token, {"sha": new_commit["sha"], "force": True})
        if status != 200:
            die("no se pudo actualizar la rama (HTTP %s: %s)" % (status, result.get("message")))
        info("rama actualizada: %s" % args.branch)

    print()
    log("listo: %d ficheros en la rama %s" % (len(files), args.branch))
    print("      commit:  %s" % new_commit["sha"])
    print("      rama:    https://github.com/%s/tree/%s" % (args.repo, args.branch))
    print("      comparar: https://github.com/%s/compare/%s...%s?expand=1"
          % (args.repo, base, args.branch))

    if args.tag:
        log("creando la etiqueta %s" % args.tag)
        status, result = api("POST", "%s/repos/%s/git/refs" % (API, args.repo), token,
                             {"ref": "refs/tags/%s" % args.tag, "sha": new_commit["sha"]})
        if status in (200, 201):
            info("etiqueta creada; el workflow 'release' se lanzará solo")
            print("      https://github.com/%s/releases/tag/%s" % (args.repo, args.tag))
        elif "already exists" in str(result.get("message", "")):
            warn("la etiqueta %s ya existía: no se ha tocado" % args.tag)
        else:
            warn("no se pudo crear la etiqueta (HTTP %s: %s)" % (status, result.get("message")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
