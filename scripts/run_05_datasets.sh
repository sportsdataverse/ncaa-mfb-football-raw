#!/usr/bin/env bash
# User-run launcher (stage 05): persisted HTML + bundles -> tidy season parquet
# (mfb/teams|rosters|schedules/parquet/, mfb/datasets/{ay}/*.parquet).
# FULLY OFFLINE -- no proxy creds, no network, safe to run any time. Not
# sharded (one output file per kind); run once after the sweeps finish.
#   ./scripts/run_05_datasets.sh --academic-year 2026
#   watch:  tail -f logs/datasets_<ts>.log
set -uo pipefail
OFFLINE=1 source "$(dirname "$0")/_env.sh"
run_stage datasets python/ncaa_mfb_05_datasets_build.py --root "${ROOT}" "$@"
