# -*- coding: utf-8 -*-
"""Métricas del sistema (host + proceso astra) e histórico en memoria.

Lee /proc en Linux y degrada con elegancia en otros sistemas (el panel también
se puede ejecutar en macOS/Windows en modo desarrollo).
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import struct
import threading
import time

from . import util

IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# lecturas básicas
# ---------------------------------------------------------------------------

def _read(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return default


def cpu_times():
    """(idle, total) en unidades de jiffies, sumando todos los núcleos."""
    line = _read("/proc/stat").split("\n", 1)[0]
    if not line.startswith("cpu "):
        return None
    parts = [util.to_int(p, 0) for p in line.split()[1:]]
    if len(parts) < 4:
        return None
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    return idle, total


def cpu_count():
    try:
        return os.cpu_count() or 1
    except Exception:  # noqa: BLE001
        return 1


def cpu_model():
    if not IS_LINUX:
        return platform.processor() or platform.machine()
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.machine()


def memory():
    if not IS_LINUX:
        try:
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError, AttributeError):
            total = 0
        return {"total": total, "used": 0, "available": total, "percent": 0,
                "swap_total": 0, "swap_used": 0, "swap_percent": 0}
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        info[key.strip()] = util.to_int(value.strip().split()[0] if value.strip() else 0) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(0, total - available)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": int(round(used * 100.0 / total)) if total else 0,
        "swap_total": swap_total,
        "swap_used": swap_used,
        "swap_percent": int(round(swap_used * 100.0 / swap_total)) if swap_total else 0,
    }


def uptime():
    text = _read("/proc/uptime").split()
    if text:
        return util.to_int(util.to_float(text[0]))
    try:
        return util.to_int(time.time() - os.path.getmtime("/"))
    except OSError:
        return 0


def boot_time():
    return util.now() - uptime()


def loadavg():
    parts = _read("/proc/loadavg").split()
    if len(parts) >= 3:
        return [round(util.to_float(p), 2) for p in parts[:3]]
    return [0.0, 0.0, 0.0]


def net_counters():
    """{iface: {"rx": bytes, "tx": bytes}} desde /proc/net/dev."""
    result = {}
    for line in _read("/proc/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, _, rest = line.partition(":")
        iface = iface.strip()
        fields = rest.split()
        if len(fields) < 9:
            continue
        result[iface] = {"rx": util.to_int(fields[0]), "tx": util.to_int(fields[8])}
    return result


def primary_interface():
    best, best_tx = None, -1
    for iface, counters in net_counters().items():
        if iface == "lo":
            continue
        if counters["tx"] > best_tx:
            best, best_tx = iface, counters["tx"]
    return best or "eth0"


def interface_addresses():
    """Lista de interfaces con su IPv4 (Linux: ioctl SIOCGIFADDR)."""
    out = []
    names = list(net_counters().keys())
    if not names:
        names = ["lo"]
    for name in names:
        addr = _iface_ipv4(name)
        if addr:
            out.append({"name": name, "ipv4": addr})
    return out


def _iface_ipv4(name):
    if not IS_LINUX:
        return ""
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:
        return ""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack("256s", name[:15].encode("utf-8"))
        result = fcntl.ioctl(sock.fileno(), 0x8915, packed)  # SIOCGIFADDR
        return socket.inet_ntoa(result[20:24])
    except OSError:
        return ""
    finally:
        sock.close()


def disk(path="/"):
    try:
        usage = shutil.disk_usage(path)
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": int(round(usage.used * 100.0 / usage.total)) if usage.total else 0,
        }
    except OSError:
        return {"total": 0, "used": 0, "free": 0, "percent": 0}


def hostname():
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return "localhost"


def os_release():
    pretty = ""
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
            break
    return {
        "name": pretty or platform.system(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
    }


def find_process(name="astra", binary="/usr/bin/astra"):
    """Busca un proceso por nombre/ejecutable en /proc."""
    if not IS_LINUX:
        return None
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            cmdline = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if not cmdline:
            continue
        first = cmdline.split(" ", 1)[0]
        if os.path.basename(first) == name or first == binary:
            return process_info(pid, cmdline)
    return None


def process_info(pid, cmdline=""):
    pid = util.to_int(pid)
    if not pid:
        return None
    stat = _read("/proc/%d/stat" % pid)
    info = {"pid": pid, "cmdline": util.sanitize_text(cmdline, 256), "rss_kb": 0, "started": 0, "threads": 0}
    if stat:
        try:
            fields = stat.rsplit(")", 1)[1].split()
            info["threads"] = util.to_int(fields[17], 0)
            info["rss_kb"] = util.to_int(fields[21], 0) * (os.sysconf("SC_PAGE_SIZE") // 1024)
            starttime = util.to_int(fields[19], 0)
            hz = os.sysconf("SC_CLK_TCK") or 100
            info["started"] = util.to_int(boot_time() + starttime / float(hz))
        except (IndexError, ValueError, OSError):
            pass
    status = _read("/proc/%d/status" % pid)
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            info["rss_kb"] = util.to_int(line.split()[1], 0)
    return info


# ---------------------------------------------------------------------------
# muestreo e histórico
# ---------------------------------------------------------------------------

class Metrics:
    def __init__(self, cfg, agent=None):
        self.cfg = cfg
        self.agent = agent
        self._lock = threading.Lock()
        self._samples = []
        self._prev_cpu = None
        self._prev_net = None
        self._prev_ts = None
        self._stop = threading.Event()
        self._thread = None
        self._dirty = False
        self._last_flush = 0
        self.points = util.to_int(cfg.get("history", {}).get("points"), 720)
        self.interval = util.to_int(cfg.get("history", {}).get("interval"), 5, minimum=1)

    # -- muestreo ----------------------------------------------------------

    def sample(self, include_agent=True):
        current = util.now()
        cpu_percent = 0
        net_rx_rate = 0
        net_tx_rate = 0

        times = cpu_times()
        if times:
            if self._prev_cpu:
                idle_diff = times[0] - self._prev_cpu[0]
                total_diff = times[1] - self._prev_cpu[1]
                if total_diff > 0:
                    cpu_percent = int(round(100.0 * (total_diff - idle_diff) / total_diff))
            self._prev_cpu = times

        counters = net_counters()
        iface = primary_interface()
        if counters:
            if self._prev_net and self._prev_ts:
                elapsed = max(1, current - self._prev_ts)
                for name, values in counters.items():
                    prev = self._prev_net.get(name)
                    if not prev:
                        continue
                    rx = max(0, values["rx"] - prev["rx"]) // elapsed
                    tx = max(0, values["tx"] - prev["tx"]) // elapsed
                    if name == iface:
                        net_rx_rate, net_tx_rate = rx, tx
            self._prev_net = counters
        self._prev_ts = current

        mem = memory()
        sample = {
            "t": current,
            "cpu": cpu_percent,
            "mem": mem["percent"],
            "mem_used": mem["used"],
            "net_rx": net_rx_rate,
            "net_tx": net_tx_rate,
            "bitrate": 0,
            "viewers": 0,
            "channels": 0,
            "bitrates": {},
        }

        if include_agent and self.agent is not None:
            try:
                metrics = self.agent.metrics()
                sample["bitrate"] = util.to_int(metrics.get("bitrate_total"), 0)
                sample["viewers"] = util.to_int(metrics.get("viewers"), 0)
                sample["channels"] = len(metrics.get("channels") or [])
                for item in metrics.get("channels") or []:
                    if isinstance(item, dict) and item.get("id"):
                        value = util.to_int(item.get("bitrate"), 0)
                        if value:
                            sample["bitrates"][item["id"]] = value
            except Exception:  # noqa: BLE001 - el agente puede estar caído
                pass

        with self._lock:
            self._samples.append(sample)
            while len(self._samples) > self.points:
                self._samples.pop(0)
            self._dirty = True
        return sample

    def history(self, minutes=60):
        limit = max(2, util.to_int(minutes, 60, minimum=1, maximum=10080) * 60 // max(1, self.interval))
        with self._lock:
            samples = self._samples[-limit:]
        return list(samples)

    def latest(self):
        with self._lock:
            return self._samples[-1] if self._samples else None

    # -- persistencia ------------------------------------------------------

    def load_history(self):
        path = self.cfg.get("paths", {}).get("history")
        data = util.loads(util.read_text(path), None)
        if isinstance(data, dict) and isinstance(data.get("samples"), list):
            with self._lock:
                self._samples = data["samples"][-self.points:]
        return len(self._samples)

    def flush(self):
        with self._lock:
            if not self._dirty:
                return False
            samples = list(self._samples)
            self._dirty = False
            self._last_flush = util.now()
        path = self.cfg.get("paths", {}).get("history")
        try:
            util.write_atomic(path, util.dumps({"version": 1, "interval": self.interval,
                                                "samples": samples}), mode=0o640)
        except OSError:
            return False
        return True

    # -- hilo --------------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.load_history()
        self.sample()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="metrics", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.flush()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.sample()
                if util.now() - self._last_flush >= 60:
                    self.flush()
            except Exception:  # noqa: BLE001 - nunca debe morir el hilo
                time.sleep(1)

    # -- vista completa ----------------------------------------------------

    def system_status(self, agent_client=None, agent_status=None, agent_error=None):
        mem = memory()
        net = net_counters()
        iface = primary_interface()
        with self._lock:
            latest = self._samples[-1] if self._samples else None
        counters = net.get(iface, {"rx": 0, "tx": 0})

        astra = {"running": False, "pid": 0, "version": "", "uptime": 0,
                 "channels_running": 0, "channels_total": 0, "viewers": 0,
                 "bitrate_total": 0, "rss_kb": 0}
        proc = find_process("astra", self.cfg.get("paths", {}).get("astra_bin") or "/usr/bin/astra")
        if proc:
            astra.update({
                "running": True,
                "pid": proc.get("pid", 0),
                "rss_kb": proc.get("rss_kb", 0),
                "started": proc.get("started", 0),
                "uptime": max(0, util.now() - util.to_int(proc.get("started"), util.now())),
            })
        if isinstance(agent_status, dict):
            astra.update({
                "running": True,
                "pid": util.to_int(agent_status.get("pid"), astra.get("pid", 0)),
                "version": agent_status.get("version", ""),
                "uptime": util.to_int(agent_status.get("uptime"), 0),
                "channels_running": util.to_int(agent_status.get("channels_running"), 0),
                "channels_total": util.to_int(agent_status.get("channels_total"), 0),
                "viewers": util.to_int(agent_status.get("viewers"), 0),
                "bitrate_total": util.to_int(agent_status.get("bitrate_total"), 0),
                "rss_kb": util.to_int(agent_status.get("rss_kb"), astra.get("rss_kb", 0)),
                "config_error": util.sanitize_text(agent_status.get("config_error"), 256),
            })

        latency = 0
        agent_state = {"ok": False, "url": "", "latency_ms": 0, "error": ""}
        if agent_client is not None:
            agent_state = {
                "ok": agent_error is None,
                "url": agent_client.base_url,
                "latency_ms": util.to_int(agent_client.last_latency_ms, 0),
                "error": util.sanitize_text(agent_error, 256) if agent_error else "",
                "last_ok": util.to_int(agent_client.last_ok, 0),
            }
            latency = agent_state["latency_ms"]

        return {
            "ok": True,
            "host": {
                "hostname": hostname(),
                "uptime": uptime(),
                "load": loadavg(),
                "cpu": {"count": cpu_count(), "model": cpu_model(),
                        "usage": util.to_int(latest.get("cpu"), 0) if latest else 0},
                "mem": {"total": mem["total"], "used": mem["used"], "available": mem["available"],
                        "percent": mem["percent"], "swap_total": mem["swap_total"],
                        "swap_used": mem["swap_used"], "swap_percent": mem["swap_percent"]},
                "net": {"interface": iface, "rx": counters["rx"], "tx": counters["tx"],
                        "rx_rate": util.to_int(latest.get("net_rx"), 0) if latest else 0,
                        "tx_rate": util.to_int(latest.get("net_tx"), 0) if latest else 0,
                        "interfaces": interface_addresses()},
                "disk": disk(self.cfg.get("paths", {}).get("recordings") or "/"),
                "os": os_release(),
            },
            "astra": astra,
            "agent": agent_state,
            "server_time": util.now(),
        }
