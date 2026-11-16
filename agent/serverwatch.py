"""ServerWatch agent: detect a server failure using WBEM/WMI.

Run on Windows (real WMI):      python serverwatch.py --services Spooler
Run anywhere (simulated):       python serverwatch.py --sim

Then open http://<this-pc>:8080 for the dashboard. The ESP32-S3 beacon finds the agent
by itself through a UDP broadcast on port 47800.
"""
import argparse
import csv
import getpass
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

from engine import FAULT_KINDS, Engine
from sources import QUERIES, SimSource, WmiSource

HERE = os.path.dirname(os.path.abspath(__file__))
DISCOVERY_PORT = 47800


class Telegram:
    def __init__(self, token, chat):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat = chat

    def send(self, text):
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text):
        try:
            data = urllib.parse.urlencode({"chat_id": self.chat, "text": text}).encode()
            urllib.request.urlopen(self.url, data, timeout=10).read()
        except Exception as e:
            print(f"[telegram] send failed: {e}")


class CsvLogger:
    """Real readings for the report's observation table and graphs."""

    def __init__(self, folder):
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.rpath = os.path.join(folder, f"readings-{stamp}.csv")
        self.apath = os.path.join(folder, f"alerts-{stamp}.csv")
        self.lock = threading.Lock()
        self._rhead = False
        with open(self.apath, "w", newline="") as f:
            csv.writer(f).writerow(["time", "host", "item", "from", "to", "text", "via"])

    def reading(self, row, services):
        with self.lock, open(self.rpath, "a", newline="") as f:
            w = csv.writer(f)
            if not self._rhead:
                w.writerow(["time", "cpu_pct", "mem_pct", "disk_free_pct", "net_pct", "status"]
                           + [f"svc_{n}" for n in services])
                self._rhead = True
            w.writerow([time.strftime("%H:%M:%S", time.localtime(row["t"])), row["cpu"], row["mem"],
                        row["disk"], row["net"], row["overall"]] + list(services.values()))

    def alert(self, rec):
        with self.lock, open(self.apath, "a", newline="") as f:
            csv.writer(f).writerow([time.strftime("%H:%M:%S", time.localtime(rec["t"])), rec["host"],
                                    rec["item"], rec["from"], rec["to"], rec["text"], rec["via"]])


def broadcaster(port, stop):
    """Tell beacons on the LAN where the agent is: 'SERVERWATCH <port>'."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    msg = f"SERVERWATCH {port}".encode()
    while not stop.is_set():
        try:
            s.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            pass
        stop.wait(2)


def load_or_make_key():
    """A random key, created on first run and kept next to the agent (never committed)."""
    path = os.path.join(HERE, ".serverwatch-key")
    try:
        with open(path) as f:
            k = f.read().strip()
            if k:
                return k
    except OSError:
        pass
    k = secrets.token_urlsafe(9)
    with open(path, "w") as f:
        f.write(k)
    return k


def local_ips():
    ips = set()
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.connect(("10.255.255.255", 1))
        ips.add(u.getsockname()[0])
    except OSError:
        pass
    return sorted(ips) or ["127.0.0.1"]


def main():
    ap = argparse.ArgumentParser(description="ServerWatch: WBEM/WMI server-failure detector")
    ap.add_argument("--sim", action="store_true", help="simulated server (no Windows needed)")
    ap.add_argument("--target", default=".", help="server to monitor ('.' = this PC)")
    ap.add_argument("--user", help="account for a remote target, e.g. LAB-PC\\admin")
    ap.add_argument("--services", default="Spooler",
                    help="comma-separated service names to watch (default: Spooler)")
    ap.add_argument("--interval", type=float, default=2.0, help="poll period in seconds")
    ap.add_argument("--consec", type=int, default=3, help="breaches in a row before alerting")
    ap.add_argument("--hyst", type=float, default=5.0, help="hysteresis margin in percent points")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--key", default=os.environ.get("SW_KEY"),
                    help="shared key for FIX / threshold changes (default: generated once, kept in .serverwatch-key)")
    a = ap.parse_args()

    a.key = a.key if a.key is not None else load_or_make_key()
    services = [s.strip() for s in a.services.split(",") if s.strip()]
    for s in services:
        if not re.fullmatch(r"[A-Za-z0-9_.\-$]+", s):
            sys.exit(f"bad service name: {s!r}")

    if a.sim:
        source = SimSource(services)
    else:
        if sys.platform != "win32" and a.target == ".":
            sys.exit("WMI only exists on Windows. Use --sim here, or run this on the Windows laptop.")
        pw = getpass.getpass(f"Password for {a.user}: ") if a.user else None
        source = WmiSource(services, a.target, a.user, pw)

    tg_token, tg_chat = os.environ.get("SW_TG_TOKEN"), os.environ.get("SW_TG_CHAT")
    notifier = Telegram(tg_token, tg_chat) if tg_token and tg_chat else None

    engine = Engine(services, consec=a.consec, hyst=a.hyst, interval=a.interval,
                    notifier=notifier, csv_logger=CsvLogger(os.path.join(HERE, "logs")))
    engine.host = source.host

    stop, wake = threading.Event(), threading.Event()

    def poll_loop():
        while not stop.is_set():
            t0 = time.time()
            try:
                engine.ingest(source.sample())
            except Exception as e:
                engine.ingest_failure(e)
            wake.wait(max(0.1, a.interval - (time.time() - t0)))
            wake.clear()

    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=source.watch_services, args=(engine.service_event, stop), daemon=True).start()
    threading.Thread(target=broadcaster, args=(a.port, stop), daemon=True).start()

    # ---------------- web API ----------------
    app = Flask(__name__, static_folder=None)

    def key_ok():
        return not a.key or request.headers.get("X-Key") == a.key

    @app.get("/")
    def index():
        return send_from_directory(os.path.join(HERE, "static"), "index.html")

    @app.get("/api/status")
    def status():
        s = engine.snapshot()
        s["mode"] = source.mode
        s["scenario"] = getattr(source, "scenario", None)
        s["scenarios"] = getattr(source, "SCENARIOS", None)
        s["telegram"] = notifier is not None
        s["queries"] = QUERIES
        return jsonify(s)

    @app.get("/api/incidents")
    def incidents():
        return jsonify({"incidents": engine.incident_rows(), "marks": engine.mark_rows(),
                        "stats": engine.stats()})

    @app.get("/api/report.md")
    def report_md():
        return Response(engine.report_markdown(), mimetype="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=serverwatch-report.md"})

    @app.post("/api/mark")
    def mark():
        body = request.get_json(force=True, silent=True) or {}
        try:
            engine.mark(body.get("fault", ""), str(body.get("text", ""))[:80])
        except ValueError:
            return jsonify(ok=False, msg=f"fault must be one of {FAULT_KINDS}"), 400
        return jsonify(ok=True)

    @app.get("/api/beacon")
    def beacon():
        return jsonify(engine.beacon())

    @app.get("/api/history")
    def history():
        return jsonify(engine.history_rows(request.args.get("n", 300, type=int)))

    @app.get("/api/history.csv")
    def history_csv():
        rows = engine.history_rows()
        out = ["time,cpu_pct,mem_pct,disk_free_pct,net_pct,status"]
        for r in rows:
            out.append(",".join([time.strftime("%H:%M:%S", time.localtime(r["t"]))] +
                                ["" if r[k] is None else str(r[k]) for k in ("cpu", "mem", "disk", "net")] +
                                [str(r["overall"])]))
        return Response("\n".join(out) + "\n", mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=serverwatch.csv"})

    @app.get("/api/alerts")
    def alerts():
        return jsonify(engine.alert_rows())

    @app.get("/api/evidence")
    def evidence():
        try:
            return jsonify(source.evidence(request.args.get("days", 7, type=int)))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/ack")
    def ack():
        engine.ack(request.args.get("from", "dashboard"))
        return jsonify(ok=True, msg="Alarm acknowledged")

    @app.post("/api/fix")
    def fix():
        if not key_ok():
            return jsonify(ok=False, msg="wrong key"), 403
        who = request.args.get("from", "dashboard")
        snap = engine.snapshot()
        todo = [s["name"] for s in snap["services"] if s["level"] > 0]
        if not todo:
            return jsonify(ok=True, msg="Nothing to fix")
        results = []
        for name in todo:
            ok, msg = source.start_service(name)
            results.append(f"{name}: {msg}")
            engine.action(f"FIX from {who}: StartService({name}) -> {msg}", notify=True)
        wake.set()  # re-poll now so the result shows straight away
        return jsonify(ok=True, msg="; ".join(results))

    @app.post("/api/thresholds")
    def thresholds():
        if not key_ok():
            return jsonify(ok=False, msg="wrong key"), 403
        engine.set_thresholds(request.get_json(force=True) or {})
        return jsonify(ok=True)

    @app.post("/api/sim")
    def sim():
        if source.mode != "sim":
            return jsonify(ok=False, msg="not in simulator mode"), 400
        name = (request.get_json(force=True) or {}).get("scenario", "normal")
        if name not in SimSource.SCENARIOS:
            return jsonify(ok=False, msg="unknown scenario"), 400
        label, fault = SimSource.SCENARIOS[name]
        if fault:                       # record the injection first, then cause it
            engine.mark(fault, f"{label} (simulator)")
        else:
            engine.action(f"Simulator: {label}")
        source.set_scenario(name)
        wake.set()
        return jsonify(ok=True)

    print(f"ServerWatch agent ({source.mode}) watching {source.host}, services={services}")
    for ip in local_ips():
        print(f"  dashboard:  http://{ip}:{a.port}/")
    print(f"  agent key (for FIX, put it in the beacon's menuconfig): {a.key or '(none)'}")
    print(f"  telegram alerts: {'on' if notifier else 'off (set SW_TG_TOKEN and SW_TG_CHAT)'}")
    try:
        app.run(host="0.0.0.0", port=a.port, threaded=True)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
