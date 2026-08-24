#!/usr/bin/env bash
# User-run launcher (stage 06): NCAA <-> ESPN game crosswalk.
# OFFLINE except one load_cfb_schedule release read per season.
#   ./scripts/run_06_xwalk.sh --academic-year 2026
#   ./scripts/run_06_xwalk.sh --all
set -uo pipefail
OFFLINE=1 source "$(dirname "$0")/_env.sh"
run_stage xwalk python/ncaa_mfb_06_xwalk_build.py --root "${ROOT}" "$@"
