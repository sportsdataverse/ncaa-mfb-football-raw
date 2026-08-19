#!/usr/bin/env bash
# User-run launcher (stage 04): sweep teams/{id}/roster pages ->
# mfb/rosters/html/{ay}/. ONLINE (~1 page per team). Resumable: teams whose
# roster html exists are skipped. Run after stage 01 (re-reads its team pages).
#   NCAA_VENDOR=decodo_patchright ./scripts/run_04_rosters.sh --academic-year 2026 --division 11
#   watch:  tail -f logs/rosters_<ts>.log
set -uo pipefail
source "$(dirname "$0")/_env.sh"
run_stage rosters python/ncaa_mfb_04_rosters_scrape.py --out "${ROOT}" "$@"
