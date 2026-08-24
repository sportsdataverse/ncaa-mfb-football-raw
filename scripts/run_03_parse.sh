#!/usr/bin/env bash
# User-run launcher (stage 03): raw bundles -> parsed + enriched per-game JSON
# (mfb/raw/{ay}/*.json.gz -> mfb/json/{contest_id}.json.gz). FULLY OFFLINE.
# Run stage 06 (xwalk) first so espn_game_id enrichment has its index.
#   ./scripts/run_03_parse.sh --academic-year 2026 [--workers 8] [--force]
#   ./scripts/run_03_parse.sh --all
set -uo pipefail
OFFLINE=1 source "$(dirname "$0")/_env.sh"
run_stage parse python/ncaa_mfb_03_games_parse.py --root "${ROOT}" "$@"
