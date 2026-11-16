"""Inject real failures into this Windows PC and let ServerWatch time how fast it notices.

    python inject.py spike              3-second CPU burst (should be filtered, no alert)
    python inject.py cpu [seconds]      saturate every core (default 40 s)
    python inject.py mem [pct] [hold]   leak RAM until pct% is in use (default 94%, hold 20 s)
    python inject.py disk [drive] [pct] fill a drive until pct% free (default S: 3%)
    python inject.py disk --clean       remove the filler files
    python inject.py service [name]     stop a Windows service (default Spooler)
    python inject.py tour               all of the above in order, then print the results

Each fault first tells the agent "fault X injected now" (POST /api/mark), so the agent can
measure detection latency from the real start of the fault, not from the first bad reading.
Run it in an Administrator prompt on the machine the agent is watching.
"""
import argparse
import ctypes
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = "http://127.0.0.1:8080"


# ------------------------------------------------------------------ talking to the agent
def api(path, body=None, key=False):
    req = urllib.request.Request(AGENT + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if key:
        try:
            with open(os.path.join(HERE, "..", "agent", ".serverwatch-key")) as f:
                req.add_header("X-Key", f.read().strip())
        except OSError:
            pass
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def mark(fault, text):
    r = api("/api/mark", {"fault": fault, "text": text})
    if "error" in r:
        print(f"  (agent not reachable at {AGENT}: latency will not be measured)")


def wait_for(cond, timeout, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(every)
    return False


def overall():
    return api("/api/status").get("overall")


# ------------------------------------------------------------------ the faults
def _burn(until):
    x = 0
    while time.time() < until:
        x = (x * 31 + 7) % 1000003


def cpu(seconds=40, fault="cpu"):
    mark(fault, f"CPU burn {seconds:g}s on {mp.cpu_count()} cores")
    print(f"burning {mp.cpu_count()} cores for {seconds:g} s")
    until = time.time() + seconds
    procs = [mp.Process(target=_burn, args=(until,)) for _ in range(mp.cpu_count())]
    for p in procs:
        p.start()
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


class _MEM(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
               [(n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "aev")]


def mem_used():
    m = _MEM()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return 100 - m.ap / m.tp * 100


def mem(target=94.0, hold=20):
    mark("mem", f"memory leak to {target:g}%")
    step, leak = 100 * 2 ** 20, []
    try:
        while mem_used() < target:
            b = bytearray(step)
            b[::4096] = b"\x01" * len(range(0, step, 4096))   # touch every page
            leak.append(b)
            print(f"\r  leaked {len(leak) * 100:>6} MB, memory in use {mem_used():5.1f}%", end="")
            time.sleep(0.4)
        print(f"\n  holding for {hold:g} s")
        time.sleep(hold)
    except KeyboardInterrupt:
        pass
    leak.clear()
    print("  memory released")


def disk(drive="S:", target=3.0, clean=False):
    root = drive.rstrip("\\") + "\\"
    folder = os.path.join(root, "serverwatch_fill")
    if clean:
        shutil.rmtree(folder, ignore_errors=True)
        print(f"  filler removed from {root}")
        return
    u = shutil.disk_usage(root)
    need = u.free - u.total * target / 100
    if need > 20 * 2 ** 30:
        sys.exit(f"That would write {need / 2 ** 30:.0f} GB to {root}. "
                 "Create the 1 GB demo drive first: .\\make_demo_disk.ps1")
    mark("disk", f"filling {root} to {target:g}% free")
    os.makedirs(folder, exist_ok=True)
    chunk, i = b"\0" * (24 * 2 ** 20), 0
    while True:
        u = shutil.disk_usage(root)
        free = u.free / u.total * 100
        print(f"\r  {root} free {free:5.1f}%", end="")
        if free <= target or u.free < 2 * len(chunk):
            break
        with open(os.path.join(folder, f"app-{i:04d}.log"), "wb") as f:
            f.write(chunk)
        i += 1
        time.sleep(0.6)
    print()


def service(name="Spooler"):
    mark("svc", f"Stop-Service {name}")
    r = subprocess.run(["sc", "stop", name], capture_output=True, text=True)
    print(f"  sc stop {name}: {'ok' if r.returncode == 0 else r.stdout.strip() or r.stderr.strip()}")


# ------------------------------------------------------------------ the tour
def step(title):
    print(f"\n=== {title}")


def settle():
    print("  waiting for the server to return to OK ...")
    wait_for(lambda: overall() == 0, 120, 2)


def tour(args):
    if "error" in api("/api/status"):
        sys.exit(f"Start the agent first (expected at {AGENT}).")
    step("1/5  short CPU spike: must NOT raise an alert")
    cpu(3, fault="spike")
    time.sleep(8)
    step("2/5  CPU overload")
    cpu(30)
    settle()
    step("3/5  memory leak")
    mem(args.mem_target, 15)
    settle()
    step("4/5  disk filling up")
    if os.path.exists(args.drive.rstrip("\\") + "\\"):
        disk(args.drive, 3)
        time.sleep(8)
        disk(args.drive, clean=True)
        settle()
    else:
        print(f"  {args.drive} not found, skipped (run make_demo_disk.ps1)")
    step("5/5  service crash, then FIX through WMI")
    service(args.service)
    time.sleep(5)
    print("  FIX:", api("/api/fix?from=inject.py", {}, key=True).get("msg"))
    settle()
    report()


def report():
    r = api("/api/incidents")
    if "error" in r:
        sys.exit(r["error"])
    st = r["stats"]
    print("\n=== Results")
    print(f"{'fault':34} {'outcome':20} {'detected after':>14}")
    by_id = {i["id"]: i for i in r["incidents"]}
    for m in r["marks"]:
        inc = by_id.get(m["claimed"])
        lat = f"{inc['latency']:.1f} s" if inc and inc["latency"] is not None else "–"
        print(f"{m['text'][:34]:34} {m['outcome']:20} {lat:>14}")
    print(f"\n{st['correct']}/{st['injected']} faults handled correctly ({st['detected']} detected, "
          f"{st['spikes_filtered']} spikes ignored), {st['false_alarms']} false alarms, "
          f"mean time to detect {st['mean_latency']} s")
    print(f"full report: {AGENT}/api/report.md")


def main():
    global AGENT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fault", choices=["spike", "cpu", "mem", "disk", "service", "tour", "report"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--clean", action="store_true", help="disk: remove filler files")
    ap.add_argument("--agent", default=AGENT)
    ap.add_argument("--drive", default="S:")
    ap.add_argument("--service", default="Spooler")
    ap.add_argument("--mem-target", type=float, default=94)
    a = ap.parse_args()
    AGENT = a.agent.rstrip("/")
    x = a.args
    if a.fault == "spike":
        cpu(3, fault="spike")
    elif a.fault == "cpu":
        cpu(float(x[0]) if x else 40)
    elif a.fault == "mem":
        mem(float(x[0]) if x else 94, float(x[1]) if len(x) > 1 else 20)
    elif a.fault == "disk":
        disk(x[0] if x else a.drive, float(x[1]) if len(x) > 1 else 3, a.clean)
    elif a.fault == "service":
        service(x[0] if x else a.service)
    elif a.fault == "tour":
        tour(a)
    else:
        report()


if __name__ == "__main__":
    main()
