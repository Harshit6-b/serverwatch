"""Where the readings come from.

WmiSource  talks to the WMI service (Microsoft's WBEM implementation, the CIM Object Manager)
           through the SWbemLocator COM object and runs plain WQL queries.
SimSource  replays scripted failures so everything can be developed without Windows.
"""
import random
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

WBEM_E_TIMED_OUT = -2147209215  # 0x80043001, SWbemEventSource.NextEvent timed out

START_RC = {
    0: "start requested", 1: "not supported", 2: "access denied (run the agent as Administrator)",
    3: "dependent services running", 7: "service did not respond in time", 8: "unknown failure",
    10: "already running", 14: "service is disabled", 15: "service logon failed",
}

EVIDENCE_IDS = {
    41: "Rebooted without a clean shutdown (Kernel-Power)",
    6008: "Previous shutdown was unexpected",
    1001: "Recovered from a blue screen (BugCheck)",
    7031: "Service terminated unexpectedly (will be restarted)",
    7034: "Service terminated unexpectedly",
}

# The WQL the agent runs, shown on the dashboard so people can see there is no magic.
QUERIES = {
    "cpu": "SELECT LoadPercentage FROM Win32_Processor",
    "mem": "SELECT FreePhysicalMemory, TotalVisibleMemorySize, LastBootUpTime FROM Win32_OperatingSystem",
    "disk": "SELECT DeviceID, Size, FreeSpace FROM Win32_LogicalDisk WHERE DriveType=3",
    "net": "SELECT Name, BytesTotalPersec, CurrentBandwidth FROM Win32_PerfFormattedData_Tcpip_NetworkInterface",
    "procs": "SELECT Name, IDProcess, PercentProcessorTime, WorkingSetPrivate "
             "FROM Win32_PerfFormattedData_PerfProc_Process",
    "svc_event": "SELECT * FROM __InstanceModificationEvent WITHIN 2 WHERE TargetInstance ISA 'Win32_Service'",
}


def _is_timeout(e):
    a = getattr(e, "args", ())
    if a and a[0] == WBEM_E_TIMED_OUT:
        return True
    return len(a) > 2 and a[2] and len(a[2]) > 5 and a[2][5] == WBEM_E_TIMED_OUT


def _wmi_time(s):
    """'20261116093012.500000+330' -> '2026-11-16 09:30:12'"""
    try:
        return datetime.strptime(str(s)[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(s)


def _top(procs, ncpu):
    by_cpu = sorted(procs, key=lambda p: p["cpu"], reverse=True)[:5]
    by_mem = sorted(procs, key=lambda p: p["mem_mb"], reverse=True)[:5]
    return {"cpu": [p for p in by_cpu if p["cpu"] >= 0.5], "mem": by_mem}


class WmiSource:
    mode = "wmi"

    def __init__(self, services, target=".", user=None, password=None):
        self.services = list(services)
        self.target = target or "."
        self.user, self.password = user, password
        self.host = self.target if self.target != "." else socket.gethostname()
        self._tls = threading.local()     # COM objects belong to the thread that created them
        self._info = None
        self._n = 0

    def _connect(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        loc = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        if self.target in (".", "localhost", "127.0.0.1"):
            svc = loc.ConnectServer(".", r"root\cimv2")
        else:
            svc = loc.ConnectServer(self.target, r"root\cimv2", self.user or "", self.password or "")
        svc.Security_.ImpersonationLevel = 3  # impersonate
        return svc

    def _svc(self):
        s = getattr(self._tls, "svc", None)
        if s is None:
            s = self._tls.svc = self._connect()
        return s

    def _q(self, wql):
        return list(self._svc().ExecQuery(wql))

    def _canon(self, name):
        for s in self.services:
            if s.lower() == name.lower():
                return s
        return name

    def _static_info(self):
        os_ = self._q("SELECT Caption FROM Win32_OperatingSystem")[0]
        cs = self._q("SELECT Manufacturer, Model, NumberOfLogicalProcessors, TotalPhysicalMemory "
                     "FROM Win32_ComputerSystem")[0]
        return {"os": str(os_.Caption).replace("Microsoft ", ""),
                "model": f"{cs.Manufacturer} {cs.Model}".strip(),
                "cpus": int(cs.NumberOfLogicalProcessors or 1),
                "ram_gb": round(int(cs.TotalPhysicalMemory) / 2 ** 30, 1)}

    def sample(self):
        t0 = time.perf_counter()
        self._n += 1
        try:
            if self._info is None:
                self._info = self._static_info()
            loads = [p.LoadPercentage for p in self._q(QUERIES["cpu"])]
            loads = [float(v) for v in loads if v is not None]
            if loads:
                cpu = sum(loads) / len(loads)
            else:  # LoadPercentage is sometimes empty; fall back to the perf counter class
                r = self._q("SELECT PercentProcessorTime FROM Win32_PerfFormattedData_PerfOS_Processor "
                            "WHERE Name='_Total'")
                cpu = float(r[0].PercentProcessorTime) if r else None

            os_ = self._q("SELECT CSName, FreePhysicalMemory, TotalVisibleMemorySize, LastBootUpTime "
                          "FROM Win32_OperatingSystem")[0]
            self.host = str(os_.CSName)
            mem = 100.0 - int(os_.FreePhysicalMemory) / int(os_.TotalVisibleMemorySize) * 100.0

            disks = []
            for d in self._q(QUERIES["disk"]):
                if d.Size and int(d.Size) > 0:
                    disks.append({"id": str(d.DeviceID), "free": int(d.FreeSpace) / int(d.Size) * 100.0,
                                  "size_gb": int(d.Size) / 2 ** 30})

            net, nic = None, None
            for n in self._q(QUERIES["net"]):
                bw = int(n.CurrentBandwidth or 0)
                if bw > 0:
                    u = int(n.BytesTotalPersec or 0) * 8 / bw * 100.0
                    if net is None or u > net:
                        net, nic = u, str(n.Name)

            svcs = {}
            if self.services:
                cond = " OR ".join(f"Name='{s}'" for s in self.services)
                for s in self._q(f"SELECT Name, State FROM Win32_Service WHERE {cond}"):
                    svcs[self._canon(str(s.Name))] = str(s.State)
                for s in self.services:
                    svcs.setdefault(s, "NotFound")

            # Who is using it? Only when it matters (or every 5th poll), the query is heavier.
            top = None
            if (cpu or 0) >= 50 or mem >= 70 or self._n % 5 == 1:
                ncpu = self._info["cpus"]
                procs = []
                for p in self._q(QUERIES["procs"]):
                    name = str(p.Name)
                    if name in ("_Total", "Idle", "System", "Memory Compression"):
                        continue
                    procs.append({"name": name, "pid": int(p.IDProcess),
                                  "cpu": int(p.PercentProcessorTime or 0) / ncpu,
                                  "mem_mb": int(p.WorkingSetPrivate or 0) / 2 ** 20})
                top = _top(procs, ncpu)
        except Exception:
            self._tls.svc = None
            self._info = None
            raise
        return {"host": self.host, "info": self._info, "cpu": cpu, "mem": mem, "disks": disks,
                "net": net, "nic": nic, "services": svcs, "top": top,
                "boot": _wmi_time(os_.LastBootUpTime), "wmi_ms": (time.perf_counter() - t0) * 1000}

    def start_service(self, name):
        """CIM InvokeMethod: Win32_Service.StartService()"""
        try:
            r = self._q(f"SELECT * FROM Win32_Service WHERE Name='{name}'")
            if not r:
                return False, "service not found"
            rc = int(r[0].StartService())
            return rc in (0, 10), START_RC.get(rc, f"return code {rc}")
        except Exception as e:
            self._tls.svc = None
            return False, f"WMI error: {e}"

    def watch_services(self, callback, stop):
        """CIM indication: told when a watched service changes state."""
        if not self.services:
            return
        cond = " OR ".join(f"TargetInstance.Name='{s}'" for s in self.services)
        wql = f"{QUERIES['svc_event']} AND ({cond})"
        while not stop.is_set():
            try:
                src = self._connect().ExecNotificationQuery(wql)
                while not stop.is_set():
                    try:
                        ev = src.NextEvent(1000)
                    except Exception as e:
                        if _is_timeout(e):
                            continue
                        raise
                    ti = ev.Properties_("TargetInstance").Value
                    callback(self._canon(str(ti.Name)), str(ti.State))
            except Exception as e:
                print(f"[watcher] {e}; retrying in 5 s")
                stop.wait(5)

    def evidence(self, days=7):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d%H%M%S.000000+000")
        ids = " OR ".join(f"EventCode={i}" for i in EVIDENCE_IDS)
        wql = ("SELECT EventCode, TimeGenerated, SourceName, Message FROM Win32_NTLogEvent "
               f"WHERE Logfile='System' AND ({ids}) AND TimeGenerated > '{since}'")
        out = []
        for e in self._q(wql)[:30]:
            tg = str(e.TimeGenerated)
            try:
                when = datetime.strptime(tg[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) \
                    .astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                when = tg
            code = int(e.EventCode)
            out.append({"time": when, "id": code, "source": str(e.SourceName),
                        "meaning": EVIDENCE_IDS.get(code, ""),
                        "message": str(e.Message or "").strip()[:200]})
        out.sort(key=lambda x: x["time"], reverse=True)
        return out


class SimSource:
    """Scripted failures (the observation table of the case study)."""
    mode = "sim"
    SCENARIOS = {
        "normal": ("Idle", None),
        "spike": ("3-second CPU spike", "spike"),
        "heavy": ("CPU overload", "cpu"),
        "memleak": ("Memory leak", "mem"),
        "diskfill": ("Disk filling", "disk"),
        "netflood": ("Network flood", "net"),
        "service": ("Service crash", "svc"),
        "unreachable": ("Server down", "reach"),
        "reboot": ("Silent reboot", None),
    }
    BASE_PROCS = [("chrome", 3.0, 820), ("MsMpEng", 1.2, 260), ("explorer", 0.6, 140),
                  ("python", 1.0, 60), ("svchost", 0.8, 45)]

    def __init__(self, services):
        self.services = list(services)
        self.host = "LAB-SERVER-01"
        self.info = {"os": "Windows 11 Pro (simulated)", "model": "Simulated host", "cpus": 8, "ram_gb": 16.0}
        self.svc = {n: "Running" for n in self.services}
        self.boot = (datetime.now() - timedelta(hours=26)).strftime("%Y-%m-%d %H:%M:%S")
        self._cb = None
        self.set_scenario("normal")

    def set_scenario(self, name):
        if name not in self.SCENARIOS:
            raise ValueError(name)
        self.scenario, self.t0, self.n = name, time.time(), 0
        if name == "normal":
            self.svc = {n: "Running" for n in self.services}
        if name == "service" and self.services:
            self._set_svc(self.services[0], "Stopped")

    def _set_svc(self, n, state):
        self.svc[n] = state
        if self._cb:
            self._cb(n, state)

    def sample(self):
        sc, t = self.scenario, time.time() - self.t0
        self.n += 1
        if sc == "unreachable" or (sc == "reboot" and t < 6):
            time.sleep(0.3)
            raise ConnectionError("The RPC server is unavailable (simulated)")
        if sc == "reboot" and self.n > 0 and not getattr(self, "_rebooted", False):
            self.boot = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._rebooted = True
        if sc != "reboot":
            self._rebooted = False
        j = lambda a: random.uniform(-a, a)
        cpu, mem, disk, net = 12 + j(4), 48 + j(1.0), 46 + j(0.05), 2 + j(1.5)
        procs = [{"name": n, "pid": 1000 + i * 4, "cpu": max(0, c + j(c / 2)), "mem_mb": m}
                 for i, (n, c, m) in enumerate(self.BASE_PROCS)]
        if sc == "spike" and self.n == 1:
            cpu = 97
        if sc == "heavy":
            cpu = min(98, 55 + 3.0 * t) + j(2)
            procs.append({"name": "cpu_burn", "pid": 4242, "cpu": cpu - 8, "mem_mb": 30})
        if sc == "memleak":
            mem = min(97, 55 + 1.2 * t) + j(0.3)
            procs.append({"name": "leaky_app", "pid": 5150, "cpu": 4, "mem_mb": (mem - 48) / 100 * 16384})
        if sc == "diskfill":
            disk = max(1.5, 40 - 2.0 * t)
        if sc == "netflood":
            net = min(97, 30 + 4 * t) + j(2)
        return {"host": self.host, "info": self.info, "cpu": max(0, cpu), "mem": mem,
                "disks": [{"id": "C:", "free": disk, "size_gb": 476.0}],
                "net": max(0, net), "nic": "Wi-Fi (simulated)", "services": dict(self.svc),
                "top": _top(procs, 8), "boot": self.boot, "wmi_ms": 40 + j(15)}

    def start_service(self, name):
        if name not in self.svc:
            return False, "service not found"
        if self.svc[name] == "Running":
            return True, "already running"
        self._set_svc(name, "Running")
        if self.scenario == "service":
            self.scenario = "normal"
        return True, "start requested (simulated)"

    def watch_services(self, callback, stop):
        self._cb = callback

    def evidence(self, days=7):
        now = datetime.now()
        return [
            {"time": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"), "id": 7034,
             "source": "Service Control Manager", "meaning": EVIDENCE_IDS[7034],
             "message": "The Print Spooler service terminated unexpectedly. (simulated)"},
            {"time": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"), "id": 41,
             "source": "Microsoft-Windows-Kernel-Power", "meaning": EVIDENCE_IDS[41],
             "message": "The system has rebooted without cleanly shutting down first. (simulated)"},
        ]
