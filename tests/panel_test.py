#!/usr/bin/python3
import importlib.util
import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("panel", ROOT / "panel/forkcastra_panel.py")
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)

with tempfile.TemporaryDirectory() as tmp:
    panel.DB = Path(tmp) / "panel.db"
    panel.STATIC = ROOT / "panel/static"
    salt = b"0123456789abcdef"
    with panel.connect() as db:
        db.execute("INSERT INTO users VALUES (?, ?, ?)", ("admin", salt, panel.password_digest("a secure test password", salt)))

    server = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_port
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
        with opener.open(req) as response:
            return response.status, json.loads(response.read())

    assert opener.open(base + "/").status == 200
    assert request("/api/session")[1]["authenticated"] is False
    try:
        request("/api/login", {"username": "admin", "password": "wrong"})
        raise AssertionError("invalid login accepted")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    assert request("/api/login", {"username": "admin", "password": "a secure test password"})[1]["ok"]
    session = request("/api/session")[1]
    assert session["authenticated"] is True
    assert request("/api/status")[0] == 200
    created = request("/api/channels", {"csrf": session["csrf"], "name": "News", "input": "udp://239.0.0.1:1234", "output": "udp://239.0.0.2:1234"})[1]
    channels = request("/api/channels")[1]["channels"]
    assert created["id"] == channels[0]["id"] and channels[0]["name"] == "News"
    rendered = panel.render_config([(1, "News", "udp://239.0.0.1:1234", "udp://239.0.0.2:1234", 1, "url", None, None, None, None, None, None, None)])
    assert 'name = "News"' in rendered and "make_channel" in rendered
    dvb = panel.render_config([(2, "DVB S2", "dvb://adapter0", "udp://239.1.1.1:1234", 1, "dvb", 0, "S2", 11538, "H", 30000, 101, "9750:10600:11700")])
    assert 'type = "S2"' in dvb and "adapter = 0" in dvb and "pnr = 101" in dvb
    delete = urllib.request.Request(base + "/api/channels/%d" % created["id"], method="DELETE", headers={"X-CSRF-Token": session["csrf"]})
    assert json.loads(opener.open(delete).read())["ok"]
    assert request("/api/logout", {"csrf": session["csrf"]})[1]["ok"]
    server.shutdown()

print("panel tests passed")
