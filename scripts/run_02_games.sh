#!/usr/bin/env bash
# User-run launcher (stage 02): capture the 6-tab bundle for a season's
# not-yet-captured contests -> mfb/raw/{ay}/{contest_id}.json.gz.
# ONLINE. CHUNK it (--max-contests) and fan out with disjoint --shard i/N as
# separate PROCESSES; a ban hard-stops the run (rc=1) -- cool down, re-run,
# it resumes (captured contests are skipped).
#   NCAA_VENDOR=decodo_patchright ./scripts/run_02_games.sh --academic-year 2026 --division 11 --max-contests 200
#   NCAA_VENDOR=decodo_patchright ./scripts/run_02_games.sh --academic-year 2026 --division 11 --shard 0/8 &
#   watch:  tail -f logs/games_<ts>.log
set -uo pipefail
source "$(dirname "$0")/_env.sh"
run_stage games python/ncaa_mfb_02_games_scrape.py --out "${ROOT}" "$@"
