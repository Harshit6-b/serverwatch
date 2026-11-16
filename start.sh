#!/usr/bin/env bash
# ServerWatch on Linux / macOS: sets up a virtualenv on first run, then starts the agent.
#   ./start.sh                 simulator (no Windows needed), dashboard on http://localhost:8080
#   ./start.sh --port 9000     extra arguments go to agent/serverwatch.py
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3.9 or newer." >&2; exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    echo "Python 3.9 or newer is needed (found $("$PY" --version 2>&1))." >&2; exit 1
fi
if [ ! -x .venv/bin/python ]; then
    echo "Creating .venv ..."
    if ! "$PY" -m venv .venv; then
        rm -rf .venv
        echo "Could not create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv" >&2; exit 1
    fi
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r agent/requirements.txt

# WMI only exists on Windows, so default to the simulator unless a target was given.
case " $* " in
    *" --target "*|*" --sim "*) exec .venv/bin/python agent/serverwatch.py "$@" ;;
    *) exec .venv/bin/python agent/serverwatch.py --sim "$@" ;;
esac
