#!/usr/bin/env bash
# User-run launcher: the COMBINED runner (stages 01 + 04 + 02 in one browser
# session): discover + rosters + capture game bundles. Prefer the numbered
# per-stage launchers (run_01_schedules.sh, run_02_games.sh, run_04_rosters.sh,
# run_05_datasets.sh -- see RUNBOOK.md); this stays for one-shot chunked runs.
#   NCAA_VENDOR=decodo_patchright ./scripts/run_mfb_capture.sh --academic-year 2026 --rosters --max-contests 20
#   watch:  tail -f logs/mfb_capture_<ts>.log
#
# Transport: NCAA_VENDOR (canary_vendors.toml at repo root -- the canary-proven
# Decodo US sticky residential + patchright transport) is preferred. Without it,
# falls back to building MFB_PROXY_POOL from .Renviron Decodo creds (_env.sh).
set -uo pipefail
source "$(dirname "$0")/_env.sh"
run_stage mfb_capture python/mfb_run.py --out "${ROOT}" "$@"
