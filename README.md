# ServerWatch

[![CI](https://github.com/Harshit6-b/serverwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Harshit6-b/serverwatch/actions/workflows/ci.yml)

**Find out a Windows server is failing before your users do, using nothing but the
management interface that is already built into Windows (WMI / WBEM).**

A small Python agent reads CPU, memory, disk, network and service state over WMI, decides
when a reading is really a problem, and tells you which process is to blame. An ESP32-S3
"beacon" on your desk turns red, shows what broke, and has a button that restarts a crashed
service over WMI.

<p align="center">
  <img src="docs/img/beacon/01-ok.png" width="260" alt="Beacon: all OK">
  <img src="docs/img/beacon/03-cpu-critical.png" width="260" alt="Beacon: CPU critical, names the process">
  <img src="docs/img/beacon/07-server-down.png" width="260" alt="Beacon: server down">
</p>
<p align="center"><sub>The beacon's actual screen code, rendered on a PC (<code>firmware/tools/render_screens.c</code>).</sub></p>

## Why

"Does it answer ping?" misses most real failures. A server can reply to ping while its disk
is full, its memory is leaking or its web service has crashed. Windows already exposes all of
that through WMI, Microsoft's implementation of the DMTF's WBEM standard, so no agent has to be
installed on the server you watch.

## What it does

- **Reads** the five health signals every 2 s with plain WQL (`SELECT LoadPercentage FROM Win32_Processor`, …)
- **Decides** with warning/critical thresholds, an *N readings in a row* rule so a 3-second
  spike does not page anyone, and hysteresis so alerts don't flap
- **Reacts at once** to a stopped service (WMI event subscription) and to a server that stops answering
- **Explains**: names the process behind a CPU or memory problem, predicts *"disk critical in ~3 min"*
  from the trend, notices a silent reboot, and pulls past crash evidence from the event log
- **Measures itself**: the fault injector stamps the moment each fault starts, so every
  incident shows a real *time to detect*
- **Fixes**: hold the beacon's button and the agent calls `Win32_Service.StartService()`
- **Alerts** on the dashboard, on the beacon, and on your phone (Telegram)

## Results

Output of `demo/inject.py report` after one run of every fault in the **simulator**.
Real hardware numbers will go here after the lab run.

| Fault | Outcome | Time to detect |
|---|---|---|
| 3-second CPU spike | ignored (correct) | – |
| CPU overload | CRITICAL, culprit `cpu_burn` | 12.0 s |
| Memory leak (rising 1.2 %/s) | WARNING, culprit `leaky_app` | 26.0 s |
| Disk filling (2 %/s) | CRITICAL | 18.0 s |
| Service crash | CRITICAL | 0.0 s |
| Server unreachable | UNREACHABLE | 0.3 s |

6/6 faults handled correctly, 0 false alarms. Time to detect counts from the moment the
fault *started*. For a slow leak that includes the time the leak takes to cross the
threshold, so it is honest rather than flattering.

## How it works

<p align="center"><img src="docs/img/architecture.svg" alt="Architecture" width="820"></p>

| Step | WBEM concept | In ServerWatch |
|---|---|---|
| Read | `ExecQuery` (WQL) | five queries every poll, see `agent/sources.py` → `QUERIES` |
| Be told | Indication | `__InstanceModificationEvent WITHIN 2 … ISA 'Win32_Service'` |
| Act | `InvokeMethod` | `Win32_Service.StartService()` from the FIX button |
| Explain | – | `Win32_PerfFormattedData_PerfProc_Process`, `LastBootUpTime`, `Win32_NTLogEvent` |

Detection rules (`agent/engine.py`):

| Metric | Warning | Critical |
|---|---|---|
| CPU | ≥ 75 % | ≥ 90 % |
| Memory in use | ≥ 80 % | ≥ 92 % |
| Disk free | ≤ 15 % | ≤ 5 % |
| Network | ≥ 70 % of link | ≥ 90 % of link |
| Service | – | not Running |
| WMI session | – | fails → UNREACHABLE |

A metric escalates only after 3 readings in a row at that level, and clears only 5 points
back inside the limit. All of this can be changed live from the dashboard.

## Quick start

### 1. Try it anywhere (simulator)

Needs Python 3.9+ and git. Works on Linux and macOS.

```bash
git clone https://github.com/Harshit6-b/serverwatch.git
cd serverwatch
./start.sh
```

Open <http://localhost:8080>. The yellow bar injects failures, and the beacon works against
the simulator too. (On Debian/Ubuntu you may first need `sudo apt install python3-venv`.)

### 2. Watch a real Windows PC

Needs Python 3.9+ from [python.org](https://www.python.org/downloads/) (tick *Add python.exe
to PATH*). Clone the repo or use *Code → Download ZIP*, then in an **Administrator**
PowerShell inside the folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start.ps1
```

The first run creates `.venv`, installs Flask and pywin32, and opens port 8080 in the firewall.
On first start it prints the dashboard address and an **agent key**. FIX needs that key.

- More services: `--services Spooler,W32Time`
- Another machine: `--target LAB-PC --user LAB-PC\admin` (DCOM; the target must allow the WMI firewall rules)
- Phone alerts: create a bot with `@BotFather`, then set `$env:SW_TG_TOKEN` and `$env:SW_TG_CHAT` before starting

### 3. Build the beacon

Two hardware options. Both use an ESP32-S3 and the same firmware; pick one in `idf.py menuconfig` → *ServerWatch Beacon → Board*.

#### Option A: SRA VJTI Wall-E 2026 board (no wiring)

The [Wall-E board](https://github.com/SRA-VJTI/sra-board-component) already has the OLED
(SDA 14 / SCL 21) and an 8×8 LED matrix (MAX7219), so you only need a USB cable. The matrix
shows big status icons you can read from across the room:

<p align="center"><img src="docs/img/walle-matrix.png" width="640" alt="Wall-E matrix icons: OK, warning, critical, server down, no WiFi, searching"></p>

The board has no user buttons, so the **BOOT** button is used: short press = acknowledge,
hold 1.5 s = FIX.

```bash
cd firmware
idf.py -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.walle" set-target esp32s3
idf.py menuconfig        # ServerWatch Beacon: WiFi name/password, agent key
idf.py build flash monitor
```

#### Option B: any ESP32-S3 DevKit + a 0.96" OLED

| Part | Notes |
|---|---|
| ESP32-S3 dev board | DevKitC-1 or similar, with its onboard WS2812 RGB LED |
| 0.96" SSD1306 OLED, I2C | 128×64 |
| 1–2 push buttons | optional: the BOOT button works too |

| From | To |
|---|---|
| OLED VCC / GND | 3V3 / GND |
| OLED SDA / SCL | GPIO 8 / GPIO 9 |
| Button 1 | GPIO 4 ↔ GND (short = acknowledge, hold 1.5 s = FIX) |
| Button 2 | GPIO 5 ↔ GND (FIX), optional |

```bash
cd firmware
idf.py set-target esp32s3
idf.py menuconfig        # ServerWatch Beacon: WiFi name/password, agent key, DevKit pins
idf.py build flash monitor
```

Both options need ESP-IDF v5.5.

Nothing secret is committed: WiFi details live in `sdkconfig`, which git ignores. The beacon
finds the agent on its own (UDP broadcast on port 47800). Both must be on the same 2.4 GHz
network. A phone hotspot works well; many campus networks block device-to-device traffic.

| DevKit LED | Wall-E matrix | Meaning |
|---|---|---|
| green | ✓ | all checks pass |
| amber | ! | warning |
| red, blinking | ✗ blinking | critical, not acknowledged (screen flashes too) |
| red, steady | ✗ steady | critical, acknowledged |
| magenta, blinking | sad face | server not answering |
| blue | WiFi icon | the beacon itself has no WiFi |

All the screens are in [`docs/img/beacon/`](docs/img/beacon/).

## Run the demo

`demo/inject.py` causes real failures on the watched PC and tells the agent when each one
starts:

```powershell
.\demo\make_demo_disk.ps1        # once: a 1 GB virtual drive S: so the disk test never touches C:
python demo\inject.py tour       # spike, CPU, memory, disk, service crash + FIX, then the results table
```

Or one at a time: `inject.py spike | cpu 40 | mem 94 | disk S: 3 | service Spooler | report`.

The dashboard's **Report (.md)** and **Readings (.csv)** links export everything.

## Project layout

```
agent/        serverwatch.py (agent + web API), engine.py (rules), sources.py (WMI + simulator),
              static/index.html (dashboard), test_engine.py
firmware/     ESP-IDF project for the beacon (main/; board.h has the pin maps), tools/ renders the screens on a PC
demo/         inject.py fault injector (standard library only), demo-disk scripts
start.sh      one-command start on Linux/macOS (simulator)
start.ps1     one-command start on Windows (real WMI)
docs/img/     diagram and beacon screens
```

Tests: `cd agent && python -m unittest test_engine`

## Limits and security

- Windows only for live data, because WMI is Windows' WBEM. On Linux the same model is available
  through OpenPegasus / SBLIM with `pywbem`; adding a source for it is a good next step.
- Polling every 2 s can miss shorter events, which is why services also use an event subscription.
- A powered-off remote target makes DCOM wait about 20 s before failing.
- The dashboard has no login. Run it on a trusted network. FIX and threshold changes need the agent
  key, FIX only touches services on the watch list, and every action is logged.
- Thresholds are starting points. Tune them per server.

## Background

Built as the practical part of a *Telecommunication Networks and Management* case study,
"How to detect a server failure using WBEM and WMI", at VJTI Mumbai.

## License

MIT, see [LICENSE](LICENSE).
