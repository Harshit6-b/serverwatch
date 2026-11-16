"""Detection engine: turns raw WMI readings into OK / WARNING / CRITICAL / UNREACHABLE.

Rules (case study, Chapter 5):
  * warning and critical thresholds per metric
  * a metric escalates only after N consecutive breaches (default 3): one spike is ignored
  * hysteresis: a metric clears only once it is a margin back inside the limit
  * a stopped service or an unreachable WMI session is raised at once
  * the worst status across all items wins

On top of that the engine keeps the information that makes an alert useful:
  * incidents, with detection latency (fault injected -> alert raised)
  * time-to-critical prediction for memory and disk (linear trend)
  * silent-reboot detection (LastBootUpTime changed between polls)
  * the processes behind a CPU or memory problem
"""
import copy
import threading
import time
from collections import deque

OK, WARNING, CRITICAL, UNREACHABLE = 0, 1, 2, 3
LABELS = ["OK", "WARNING", "CRITICAL", "UNREACHABLE"]

DEFAULT_THRESHOLDS = {
    "cpu":  {"name": "CPU",     "unit": "% used",    "warn": 75, "crit": 90, "high_is_bad": True},
    "mem":  {"name": "Memory",  "unit": "% used",    "warn": 80, "crit": 92, "high_is_bad": True},
    "disk": {"name": "Disk",    "unit": "% free",    "warn": 15, "crit": 5,  "high_is_bad": False},
    "net":  {"name": "Network", "unit": "% of link", "warn": 70, "crit": 90, "high_is_bad": True},
}
PREDICT = ("mem", "disk")      # metrics that fail gradually, worth a trend line
FAULT_KINDS = ("cpu", "spike", "mem", "disk", "net", "svc", "reach")


def level_for(value, t, margin=0.0):
    if value is None:
        return None
    if t["high_is_bad"]:
        if value >= t["crit"] - margin:
            return CRITICAL
        if value >= t["warn"] - margin:
            return WARNING
    else:
        if value <= t["crit"] + margin:
            return CRITICAL
        if value <= t["warn"] + margin:
            return WARNING
    return OK


def service_level(state):
    if state == "Running":
        return OK
    if state in ("StartPending", "StopPending", "ContinuePending", "PausePending"):
        return WARNING
    return CRITICAL


def fmt_duration(s):
    if s is None:
        return "–"
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


class MetricState:
    """Confirmed level of one metric: consecutive-breach rule + hysteresis.

    Breaches are counted per level, so a metric that is critical for N readings in a row
    becomes CRITICAL straight away, without first sitting at WARNING for N more readings.
    """

    def __init__(self):
        self.level = OK
        self.raw = OK
        self.streak = 0             # breaches in a row above the confirmed level (for display)
        self.onset_t = None         # first abnormal reading of the confirmed episode
        self._count = {WARNING: 0, CRITICAL: 0}
        self._since = {WARNING: None, CRITICAL: None}

    def update(self, value, t, consec, hyst, now=None):
        now = time.time() if now is None else now
        raw = level_for(value, t)
        if raw is None:
            return self.level
        self.raw = raw
        for lv in (WARNING, CRITICAL):
            if raw >= lv:
                if self._count[lv] == 0:
                    self._since[lv] = now
                self._count[lv] += 1
            else:
                self._count[lv] = 0
        target = max((lv for lv in (WARNING, CRITICAL) if self._count[lv] >= consec), default=OK)
        if target > self.level:
            if self.level == OK:
                self.onset_t = self._since[WARNING]
            self.level = target
        elif raw <= self.level:
            sticky = level_for(value, t, margin=hyst)      # clear only once well inside the limit
            if sticky < self.level:
                self.level = sticky
        self.streak = self._count[raw] if raw > self.level else 0
        return self.level


def trend_eta(points, t, min_points=8):
    """Seconds until the critical limit, from a least-squares line through recent points."""
    pts = [(a, b) for a, b in points if b is not None]
    if len(pts) < min_points:
        return None
    x0 = pts[0][0]
    xs = [a - x0 for a, _ in pts]
    ys = [b for _, b in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    r2 = sxy * sxy / (sxx * syy)
    if r2 < 0.6:                                  # not a clear trend, just noise
        return None
    v = ys[-1]
    if t["high_is_bad"]:
        if slope <= 0.005 or v >= t["crit"]:
            return None
        eta = (t["crit"] - v) / slope
    else:
        if slope >= -0.005 or v <= t["crit"]:
            return None
        eta = (v - t["crit"]) / -slope
    return eta if eta < 3 * 3600 else None


class Engine:
    def __init__(self, services, consec=3, hyst=5.0, interval=2.0,
                 history=1800, notifier=None, csv_logger=None):
        self.lock = threading.RLock()
        self.thr = copy.deepcopy(DEFAULT_THRESHOLDS)
        self.consec, self.hyst, self.interval = consec, hyst, interval
        self.metrics = {k: MetricState() for k in self.thr}
        self.services = {n: "Unknown" for n in services}
        self.host = "?"
        self.info = {}                   # OS, model, CPUs, RAM (static facts)
        self.values = {k: None for k in self.thr}
        self.disks, self.nic, self.wmi_ms = [], None, None
        self.top = {"cpu": [], "mem": []}
        self.boot = None
        self.reach_error = None
        self.down_since = None
        self.last_ok = None
        self.started = time.time()
        self.history = deque(maxlen=history)
        self.alerts = deque(maxlen=500)
        self.incidents = deque(maxlen=300)   # newest first
        self.open = {}                       # item -> open incident
        self.marks = deque(maxlen=100)       # fault-injection markers from the demo tool
        self.items = {}
        self.alert_seq = self.ack_seq = 0
        self._inc_id = 0
        self.notifier = notifier
        self.csv_logger = csv_logger

    # ------------------------------------------------------------------ inputs
    def ingest(self, s, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.host = s.get("host", self.host)
            if s.get("info"):
                self.info = s["info"]
            if self.reach_error:
                self.reach_error = None
            self.last_ok = now
            self.disks = s.get("disks") or []
            self.nic = s.get("nic")
            self.wmi_ms = s.get("wmi_ms")
            if s.get("top") is not None:
                self.top = s["top"]
            self.values = {
                "cpu": s.get("cpu"), "mem": s.get("mem"), "net": s.get("net"),
                "disk": min((d["free"] for d in self.disks), default=None),
            }
            for k, st in self.metrics.items():
                st.update(self.values[k], self.thr[k], self.consec, self.hyst, now)
            for name, state in (s.get("services") or {}).items():
                self.services[name] = state
            self._check_reboot(s.get("boot"), now)
            self._evaluate(now)
            row = {"t": now, **{k: _r(v) for k, v in self.values.items()}, "overall": self._overall(now)}
            self.history.append(row)
            if self.csv_logger:
                self.csv_logger.reading(row, self.services)

    def ingest_failure(self, err, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if not self.reach_error:
                self.down_since = now
            self.reach_error = str(err) or err.__class__.__name__
            self._evaluate(now)
            self.history.append({"t": now, "cpu": None, "mem": None, "disk": None, "net": None,
                                 "overall": UNREACHABLE})

    def service_event(self, name, state):
        """From the WMI event subscription: evaluated at once, no waiting for a poll."""
        with self.lock:
            if name in self.services and self.services[name] != state:
                self.services[name] = state
                self._evaluate(time.time(), via="event")

    def mark(self, fault, text=""):
        """The demo tool says 'I am injecting this fault now', so latency can be measured."""
        if fault not in FAULT_KINDS:
            raise ValueError(fault)
        with self.lock:
            m = {"t": time.time(), "fault": fault, "text": text or fault, "claimed": None}
            self.marks.append(m)
            self._log_action(f"Fault injected: {m['text']}")

    def set_thresholds(self, upd):
        with self.lock:
            for k, v in upd.items():
                if k in self.thr:
                    for f in ("warn", "crit"):
                        if f in v:
                            self.thr[k][f] = float(v[f])
            self._log_action(f"Thresholds changed: {upd}")

    def ack(self, who="dashboard"):
        with self.lock:
            self.ack_seq = self.alert_seq
            self._log_action(f"Alarm acknowledged from {who}")

    def action(self, text, notify=False):
        with self.lock:
            self._log_action(text)
        if notify and self.notifier:
            self.notifier.send(f"🔧 {self.host}: {text}")

    # ------------------------------------------------------------------ evaluation
    def _stale(self, now):
        return now - (self.last_ok or self.started) > max(10.0, 4 * self.interval)

    def _current_items(self):
        items = {k: st.level for k, st in self.metrics.items()}
        for n, state in self.services.items():
            if state != "Unknown":
                items["svc:" + n] = service_level(state)
        items["reach"] = UNREACHABLE if self.reach_error else OK
        return items

    def _overall(self, now=None):
        now = time.time() if now is None else now
        if self.reach_error or self._stale(now):
            return UNREACHABLE
        return max(self._current_items().values(), default=OK)

    def _culprit(self, item):
        lst = self.top.get(item) or []
        if not lst:
            return ""
        p = lst[0]
        return f" · top: {p['name']} {p['cpu']:.0f}%" if item == "cpu" else f" · top: {p['name']} {p['mem_mb']:.0f} MB"

    def _describe(self, item, level):
        if item == "reach":
            return f"WMI session failed: {self.reach_error}" if level else "WMI session restored"
        if item.startswith("svc:"):
            return f"Service {item[4:]} is {self.services.get(item[4:])}"
        t, v = self.thr[item], self.values.get(item)
        vtxt = "n/a" if v is None else f"{v:.1f}"
        lim = {CRITICAL: t["crit"], WARNING: t["warn"]}.get(level)
        if lim is None:
            return f"{t['name']} back to normal ({vtxt}{t['unit']})"
        cmp_ = ">=" if t["high_is_bad"] else "<="
        extra = self._culprit(item) if item in ("cpu", "mem") else ""
        return f"{t['name']} {vtxt}{t['unit']} {cmp_} {lim:g}{extra}"

    @staticmethod
    def _kind(item):
        return "svc" if item.startswith("svc:") else item

    def _claim_mark(self, kind, now):
        for m in reversed(self.marks):
            if m["claimed"] is None and m["fault"] == kind and now - m["t"] < 600:
                return m
        return None

    def _evaluate(self, now, via="poll"):
        cur = self._current_items()
        for item, lvl in cur.items():
            old = self.items.get(item, OK)
            if lvl == old:
                continue
            text = self._describe(item, lvl)
            rec = {"t": now, "host": self.host, "item": item, "from": LABELS[old], "to": LABELS[lvl],
                   "level": lvl, "text": text, "via": via}
            self.alerts.appendleft(rec)
            if lvl > old:
                self.alert_seq += 1
            self._track_incident(item, old, lvl, text, now)
            if self.csv_logger:
                self.csv_logger.alert(rec)
            if self.notifier and (lvl >= CRITICAL or old >= CRITICAL):
                icon = "🔴" if lvl >= CRITICAL else ("🟠" if lvl == WARNING else "🟢")
                self.notifier.send(f"{icon} {LABELS[lvl]} on {self.host}: {text} "
                                   f"({time.strftime('%H:%M:%S', time.localtime(now))})")
        self.items = cur

    def _track_incident(self, item, old, lvl, text, now):
        if old == OK and lvl > OK:
            onset = now
            if item in self.metrics and self.metrics[item].onset_t:
                onset = self.metrics[item].onset_t
            elif item == "reach" and self.down_since:
                onset = self.down_since
            m = self._claim_mark(self._kind(item), now)
            self._inc_id += 1
            inc = {"id": self._inc_id, "item": item, "text": text, "peak": lvl,
                   "injected": m["t"] if m else None, "fault": m["text"] if m else None,
                   "onset": onset, "detected": now, "cleared": None}
            if m:
                m["claimed"] = inc["id"]
            self.open[item] = inc
            self.incidents.appendleft(inc)
        elif item in self.open:
            inc = self.open[item]
            if lvl > inc["peak"]:
                inc["peak"], inc["text"] = lvl, text
            if lvl == OK:
                inc["cleared"] = now
                del self.open[item]

    def _check_reboot(self, boot, now):
        if not boot:
            return
        if self.boot and boot != self.boot:
            self._inc_id += 1
            text = f"Server restarted (new boot time {boot})"
            self.incidents.appendleft({"id": self._inc_id, "item": "reboot", "text": text,
                                       "peak": WARNING, "injected": None, "fault": None,
                                       "onset": now, "detected": now, "cleared": now})
            self.alerts.appendleft({"t": now, "host": self.host, "item": "reboot", "from": "",
                                    "to": "WARNING", "level": WARNING, "text": text, "via": "poll"})
            self.alert_seq += 1
            if self.notifier:
                self.notifier.send(f"🟠 {self.host}: {text}")
        self.boot = boot

    def _log_action(self, text):
        rec = {"t": time.time(), "host": self.host, "item": "action", "from": "", "to": "",
               "level": -1, "text": text, "via": "user"}
        self.alerts.appendleft(rec)
        if self.csv_logger:
            self.csv_logger.alert(rec)

    # ------------------------------------------------------------------ outputs
    def etas(self):
        hist = list(self.history)[-30:]
        return {k: trend_eta([(r["t"], r[k]) for r in hist], self.thr[k]) for k in PREDICT}

    def headline(self, now=None):
        now = time.time() if now is None else now
        if self.reach_error:
            return "WMI unreachable: " + self.reach_error[:80]
        if self._stale(now):
            return "No WMI data (collector stalled)"
        items = self._current_items()
        worst = max(items.items(), key=lambda kv: kv[1], default=("", OK))
        if worst[1] > OK:
            return self._describe(*worst)
        etas = {k: v for k, v in self.etas().items() if v is not None}
        if etas:
            k = min(etas, key=etas.get)
            return f"{self.thr[k]['name']} trending to critical in ~{fmt_duration(etas[k])}"
        return "All checks OK"

    def snapshot(self):
        with self.lock:
            now = time.time()
            overall = self._overall(now)
            etas = self.etas()
            metrics = {}
            for k, st in self.metrics.items():
                t = self.thr[k]
                metrics[k] = {"name": t["name"], "unit": t["unit"], "value": _r(self.values[k]),
                              "level": st.level, "raw": st.raw, "streak": st.streak,
                              "warn": t["warn"], "crit": t["crit"], "high_is_bad": t["high_is_bad"],
                              "eta": _r(etas.get(k), 0)}
            return {
                "host": self.host, "info": self.info, "time": now,
                "age": None if self.last_ok is None else round(now - self.last_ok, 1),
                "overall": overall, "label": LABELS[overall],
                "acked": self.ack_seq == self.alert_seq,
                "message": self.headline(now),
                "metrics": metrics,
                "disks": [{**d, "free": _r(d["free"]), "size_gb": _r(d.get("size_gb"))} for d in self.disks],
                "nic": self.nic,
                "top": self.top,
                "boot": self.boot,
                "services": [{"name": n, "state": s, "level": service_level(s) if s != "Unknown" else 0}
                             for n, s in self.services.items()],
                "wmi_ms": _r(self.wmi_ms),
                "rules": {"consec": self.consec, "hyst": self.hyst, "interval": self.interval},
                "stats": self.stats(),
            }

    def beacon(self):
        """Compact form for the ESP32-S3: short keys, short strings."""
        s = self.snapshot()
        m = s["metrics"]
        bad_svc = [x for x in s["services"] if x["level"] > 0]
        hint = ""
        if bad_svc:
            hint = f"{bad_svc[0]['name']}:{bad_svc[0]['state']}"
        elif m["cpu"]["level"] > 0 and s["top"]["cpu"]:
            p = s["top"]["cpu"][0]
            hint = f"top {p['name'][:10]} {p['cpu']:.0f}%"
        elif m["mem"]["level"] > 0 and s["top"]["mem"]:
            p = s["top"]["mem"][0]
            hint = f"top {p['name'][:10]} {p['mem_mb'] / 1024:.1f}G"
        else:
            etas = [(m[k]["eta"], k) for k in PREDICT if m[k]["eta"] is not None]
            if etas:
                eta, k = min(etas)
                hint = f"{'DSK' if k == 'disk' else 'MEM'} crit in {fmt_duration(eta)}"
        return {
            "host": s["host"][:12], "o": s["overall"], "lbl": s["label"], "ack": s["acked"],
            "age": s["age"], "fix": bool(bad_svc),
            "m": [[k, m[k]["value"], m[k]["level"]] for k in ("cpu", "mem", "disk", "net")],
            "hint": hint[:21], "msg": s["message"][:60],
        }

    # ------------------------------------------------------------------ incident report
    def incident_rows(self):
        with self.lock:
            out = []
            for inc in self.incidents:
                ref = inc["injected"] or inc["onset"]
                out.append({**inc,
                            "latency": None if inc["item"] == "reboot" else round(inc["detected"] - ref, 1),
                            "latency_from": "injected" if inc["injected"] else "first abnormal reading",
                            "duration": None if inc["cleared"] is None else round(inc["cleared"] - inc["onset"], 1),
                            "peak_label": LABELS[inc["peak"]]})
            return out

    def mark_rows(self):
        with self.lock:
            out = []
            for m in self.marks:
                outcome = "detected" if m["claimed"] else "pending"
                if m["fault"] == "spike":
                    # a false alarm = a CPU alert within 30 s that no other injected fault explains
                    cpu_alert = any(i["item"] == "cpu" and i["injected"] is None
                                    and m["t"] <= i["detected"] <= m["t"] + 30 for i in self.incidents)
                    if time.time() - m["t"] > 30 or cpu_alert:
                        outcome = "false alarm" if cpu_alert else "filtered (correct)"
                elif not m["claimed"] and time.time() - m["t"] > 120:
                    outcome = "missed"
                out.append({**m, "outcome": outcome})
            return out

    def stats(self):
        incs = self.incident_rows()
        timed = [i for i in incs if i["injected"] and i["latency"] is not None]
        by = {}
        for i in timed:
            by.setdefault(self._kind(i["item"]), []).append(i["latency"])
        marks = self.mark_rows()
        return {
            "incidents": len(incs),
            "open": len(self.open),
            "injected": len(marks),
            "detected": sum(1 for m in marks if m["outcome"] == "detected"),
            "spikes_filtered": sum(1 for m in marks if m["outcome"] == "filtered (correct)"),
            "false_alarms": sum(1 for m in marks if m["outcome"] == "false alarm"),
            "correct": sum(1 for m in marks if m["outcome"] in ("detected", "filtered (correct)")),
            "mean_latency": round(sum(i["latency"] for i in timed) / len(timed), 1) if timed else None,
            "latency_by_kind": {k: round(sum(v) / len(v), 1) for k, v in by.items()},
            "uptime_watch": round(time.time() - self.started),
        }

    def report_markdown(self):
        s = self.snapshot()
        st = s["stats"]
        L = [f"# ServerWatch detection report: {s['host']}", ""]
        if s["info"]:
            i = s["info"]
            L.append(f"{i.get('os', '')} · {i.get('model', '')} · {i.get('cpus', '?')} logical CPUs · "
                     f"{i.get('ram_gb', '?')} GB RAM")
            L.append("")
        L += [f"Watched for {fmt_duration(st['uptime_watch'])}, poll every {self.interval:g} s, "
              f"alert after {self.consec} breaches, hysteresis {self.hyst:g} points.", "",
              f"* Faults injected: **{st['injected']}**, handled correctly: **{st['correct']}** "
              f"({st['detected']} detected, {st['spikes_filtered']} harmless spikes ignored), "
              f"false alarms: **{st['false_alarms']}**",
              f"* Mean detection latency: **{st['mean_latency'] if st['mean_latency'] is not None else '–'} s**",
              "", "| # | Item | Fault injected | Peak | Detected after | Duration | Detail |",
              "|---|---|---|---|---|---|---|"]
        for i in reversed(self.incident_rows()):
            L.append(f"| {i['id']} | {i['item']} | {i['fault'] or '–'} | {i['peak_label']} | "
                     f"{'–' if i['latency'] is None else str(i['latency']) + ' s'} | "
                     f"{fmt_duration(i['duration']) if i['duration'] is not None else 'open'} | {i['text']} |")
        L.append("")
        return "\n".join(L)

    def history_rows(self, n=None):
        with self.lock:
            rows = list(self.history)
        return rows[-n:] if n else rows

    def alert_rows(self, n=100):
        with self.lock:
            return list(self.alerts)[:n]


def _r(v, nd=1):
    return None if v is None else round(float(v), nd)
