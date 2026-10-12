#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lanza un workflow de GitHub Actions y espera el resultado (sin git ni gh).

    GITHUB_TOKEN=... python3 tools/github_workflow.py --workflow release.yml \
        --ref webui-panel --input tag=v4.0.282-10 --wait
    python3 tools/github_workflow.py --workflow release.yml --ref main --dry-run

Con --wait sigue la ejecuciÃ³n hasta que termina e imprime el registro si falla.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO_DEFAULT = "TalaveraSama/forkcastra-master"


def log(message):
    print("\033[1m[actions]\033[0m %s" % message)


def info(message):
    print("  Â· %s" % message)


def die(message, code=2):
    print("\033[31m[actions] ERROR:\033[0m %s" % message, file=sys.stderr)
    sys.exit(code)


def api(method, url, token, body=None, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("Authorization", "Bearer %s" % token)
    request.add_header("User-Agent", "astra-webui-actions")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read()
            if raw:
                return response.status, payload
            return response.status, (json.loads(payload.decode("utf-8") or "{}") if payload else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if raw:
            return exc.code, detail.encode()
        try:
            parsed = json.loads(detail)
            return exc.code, {"message": parsed.get("message", detail[:300])}
        except ValueError:
            return exc.code, {"message": detail[:300]}


def fetch_logs(repo, token, job_id):
    """Descarga el registro de un job.

    El endpoint responde con un 302 a una URL firmada de S3, y el token NO debe
    viajar en esa segunda peticiÃ³n (S3 responde InvalidAuthenticationInfo).
    """
    url = "%s/repos/%s/actions/jobs/%s/logs" % (API, repo, job_id)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(url)
    request.add_header("Authorization", "Bearer %s" % token)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "astra-webui-actions")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location")
        if location:
            plain = urllib.request.Request(location)
            plain.add_header("User-Agent", "astra-webui-actions")
            try:
                with urllib.request.urlopen(plain, timeout=180) as response:
                    return response.read()
            except (urllib.error.URLError, OSError) as inner:
                return ("no se pudo descargar el registro: %s" % inner).encode()
        return exc.read()


def report(repo, token, run):
    """Imprime el resultado de una ejecuciÃ³n y su registro si fallÃ³."""
    conclusion = run.get("conclusion")
    print()
    log("resultado: %s" % conclusion)
    print("      %s" % run.get("html_url"))
    if conclusion == "success":
        return 0
    status, jobs = api("GET", "%s/repos/%s/actions/runs/%s/jobs" % (API, repo, run["id"]), token)
    for job in (jobs.get("jobs") or []):
        print("\n--- %s: %s ---" % (job.get("name"), job.get("conclusion")))
        for step in job.get("steps") or []:
            mark = "OK " if step.get("conclusion") == "success" else ">> "
            print("  %s %s (%s)" % (mark, step.get("name"), step.get("conclusion")))
        if job.get("conclusion") not in ("success", "skipped"):
            logs = fetch_logs(repo, token, job["id"])
            text = logs.decode("utf-8", "replace") if isinstance(logs, bytes) else str(logs)
            print("\n--- registro (Ãºltimas 60 lÃ­neas) ---")
            print("\n".join(text.splitlines()[-int(os.environ.get("LOG_LINES", "60")):]))
    return 1


def wait_for_run(repo, token, workflow, ref, timeout, started=0):
    """Espera a la ejecuciÃ³n mÃ¡s reciente del workflow (por su ruta)."""
    log("esperando el resultadoâ€¦")
    run = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, runs = api("GET", "%s/repos/%s/actions/runs?per_page=30" % (API, repo), token)
        if status == 200:
            for item in runs.get("workflow_runs", []):
                if not str(item.get("path", "")).endswith(workflow):
                    continue
                if ref and item.get("head_branch") not in (ref, None) and \
                        ref not in str(item.get("head_branch")):
                    continue
                if started:
                    created = time.mktime(time.strptime(item["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
                    if created < started - 60:
                        continue
                run = item
                break
        if run and run.get("status") == "completed":
            break
        if run:
            info("estado: %s (%s)" % (run.get("status"), run.get("conclusion") or "en curso"))
        time.sleep(10)
    if not run:
        die("no encuentro ninguna ejecuciÃ³n de %s" % workflow)
    if run.get("status") != "completed":
        die("la ejecuciÃ³n sigue en curso: %s" % run.get("html_url"), code=3)
    return run


def main():
    parser = argparse.ArgumentParser(description="Lanza un workflow de GitHub Actions")
    parser.add_argument("--repo", default=REPO_DEFAULT)
    parser.add_argument("--workflow", required=True, help="fichero del workflow (p. ej. release.yml)")
    parser.add_argument("--ref", default="", help="rama donde ejecutarlo (por defecto, la principal)")
    parser.add_argument("--input", action="append", default=[], help="entrada clave=valor (repetible)")
    parser.add_argument("--wait", action="store_true", help="esperar al resultado")
    parser.add_argument("--watch", action="store_true",
                        help="no lanzar nada: seguir la Ãºltima ejecuciÃ³n de ese workflow")
    parser.add_argument("--timeout", type=int, default=1800, help="espera mÃ¡xima en segundos")
    parser.add_argument("--dry-run", action="store_true", help="no lanzar nada")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token and not args.dry_run:
        die("falta el token: exporta GITHUB_TOKEN=... (o usa --dry-run)")

    inputs = {}
    for item in args.input:
        if "=" not in item:
            die("--input necesita clave=valor (recibido: %s)" % item)
        key, value = item.split("=", 1)
        inputs[key] = value

    if args.watch:
        log("siguiendo la Ãºltima ejecuciÃ³n de %s" % args.workflow)
        run = wait_for_run(args.repo, token, args.workflow, args.ref, args.timeout)
        return report(args.repo, token, run)

    log("workflow %s en %s" % (args.workflow, args.repo))
    if inputs:
        info("entradas: %s" % ", ".join("%s=%s" % kv for kv in inputs.items()))

    if args.dry_run:
        info("simulaciÃ³n: no se lanza nada")
        return 0

    status, repo = api("GET", "%s/repos/%s" % (API, args.repo), token)
    if status != 200:
        die("no se puede leer %s (HTTP %s)" % (args.repo, status))
    ref = args.ref or repo.get("default_branch") or "main"
    info("rama: %s" % ref)

    status, flows = api("GET", "%s/repos/%s/actions/workflows" % (API, args.repo), token)
    if status != 200:
        die("no puedo listar los workflows (HTTP %s: %s)" % (status, flows.get("message")))
    known = {os.path.basename(item.get("path", "")): item for item in flows.get("workflows", [])}
    if args.workflow not in known:
        die("el workflow %s no estÃ¡ en el repositorio. Hay: %s"
            % (args.workflow, ", ".join(sorted(known)) or "ninguno"))
    info("workflow encontrado: %s" % known[args.workflow].get("name"))

    started = int(time.time())
    url = "%s/repos/%s/actions/workflows/%s/dispatches" % (API, args.repo, args.workflow)
    status, result = api("POST", url, token, {"ref": ref, "inputs": inputs})
    if status not in (200, 204):
        die("no se pudo lanzar el workflow (HTTP %s: %s)" % (status, result.get("message")))
    log("workflow lanzado en la rama %s" % ref)

    if not args.wait:
        print("      ver: https://github.com/%s/actions/workflows/%s" % (args.repo, args.workflow))
        return 0

    run = wait_for_run(args.repo, token, args.workflow, ref, args.timeout, started=started)
    return report(args.repo, token, run)


if __name__ == "__main__":
    sys.exit(main())
