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

exec python main.py --trade
