#!/usr/bin/env bash
# Runs one main.py --trade cycle, meant to be invoked by cron or a
# systemd timer once per candle close (hourly, for TIMEFRAME=1h).
#
# Why this wrapper exists instead of pointing cron straight at
# `python main.py --trade`: cron runs jobs with a near-empty
# environment — no activated venv, often a different $PATH, and never
# your interactive shell's cwd. Every one of those breaks a bare
# `python main.py --trade` in a way that looks like nothing happened
# (silent import errors mailed to a cron log nobody reads). This script
# pins the working directory to the repo root (wherever it's actually
# cloned, not hardcoded) and activates .venv itself, so the crontab
# line only has to know the path to this file.
#
# Exit code is main.py's own: 0 on a clean cycle (including a cycle
# that decided to just "hold"), non-zero if the cycle raised — cron's
# own failure handling (a MAILTO, or whatever monitors exit codes on
# your systemd timer) is the intended way to notice a bad run, on top
# of main.py's own logs/trading.log.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Resolve the interpreter to run by name rather than assuming a plain
# `python` exists -- true with no venv at all (recent macOS/Linux often
# don't ship a bare `python`), and also true for a venv that lost its
# own `python` symlink (e.g. moved to a new path after creation) but
# still has its versioned binary. Once .venv/bin is on $PATH (if it was
# activated above), `command -v` finds the venv's own interpreter
# first; otherwise it falls through to whatever is on the system.
# python3.9 specifically first -- see the README's note on the
# `X | None` syntax regression that only shows up on 3.9.
PYTHON_BIN=""
for candidate in python3.9 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "run_trade_cycle.sh: no python interpreter found (checked python3.9, python3, python)" >&2
    exit 1
fi

set +e
"$PYTHON_BIN" main.py --trade
STATUS=$?
set -e

# Keep the dashboard's "real trade history / open position" view current
# without a manual `cp` after every run: trade_journal.py already wrote
# the authoritative copy to data/trade_journal.json above (best-effort,
# never fails the cycle), so mirror it into the dashboard's public data
# dir here too. Best-effort on purpose, same as trade_journal.py itself
# -- a copy failure (e.g. dashboard/ not present on this box) must never
# fail the cron job or mask main.py's own exit code.
if [ -f "data/trade_journal.json" ] && [ -d "dashboard/public/data" ]; then
    cp "data/trade_journal.json" "dashboard/public/data/trade_journal.json" || true
fi

exit "$STATUS"
