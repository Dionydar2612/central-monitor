#!/usr/bin/env bash
# win-poc.sh - run scripts/win-poc.py (PoC tester for agentless Windows monitoring) from central.
# First run creates a small Python venv with pypsrp in ~/.cm-win-poc (about 15 MB). Nothing else is installed.
#   scripts/win-poc.sh --host 192.168.181.101 --thumbprint <from setup-cm-monitor.bat> --once
#   scripts/win-poc.sh --host 192.168.181.101 --thumbprint <...> --hours 24
# No venv support on this machine? -> CM_WIN_POC_DOCKER=1 scripts/win-poc.sh ...  (uses python:3.12-slim)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${CM_WIN_POC_VENV:-$HOME/.cm-win-poc}"

if [ "${CM_WIN_POC_DOCKER:-0}" = "1" ]; then
    if [ -z "${CM_WIN_PASSWORD:-}" ]; then read -rsp "password of cm-monitor: " CM_WIN_PASSWORD; echo; fi
    export CM_WIN_PASSWORD
    exec docker run --rm -it --network host -e CM_WIN_PASSWORD -v "$DIR":/w:ro -v "$PWD":/out -w /out \
        python:3.12-slim sh -c 'pip install -q --root-user-action=ignore "pypsrp>=0.8,<1" && python /w/win-poc.py "$@"' sh "$@"
fi

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import pypsrp' 2>/dev/null; then
    echo "creating $VENV (pypsrp) ..."
    if ! python3 -m venv "$VENV" 2>/dev/null; then
        echo "python3 venv is not available. Either: sudo apt install python3-venv" >&2
        echo "  or run with docker:  CM_WIN_POC_DOCKER=1 $0 $*" >&2
        exit 1
    fi
    "$VENV/bin/pip" install -q "pypsrp>=0.8,<1"
fi
exec "$VENV/bin/python" "$DIR/win-poc.py" "$@"
