#!/usr/bin/env bash
# User-run launcher (stage 01): discover a season's teams + team pages ->
# mfb/teams/html/, mfb/schedules/html/{ay}/ (the schedule source) -> contest_ids.
# ONLINE (stats.ncaa.org team pages; ~1 page per team). Resumable: persisted
# pages are re-read, not re-fetched.
#   NCAA_VENDOR=decodo_patchright ./scripts/run_01_schedules.sh --academic-year 2026 --division 11
#   NCAA_VENDOR=decodo_patchright ./scripts/run_01_schedules.sh --academic-year 2026 --division 12
#   watch:  tail -f logs/schedules_<ts>.log
# --academic-year is the ENDING year (2026 = fall-2025); --division 11=FBS 12=FCS.
set -uo pipefail
source "$(dirname "$0")/_env.sh"
run_stage schedules python/ncaa_mfb_01_schedules_scrape.py --out "${ROOT}" "$@"
