#!/usr/bin/env bash
# User-run launcher (stage 07): NCAA player_id <-> ESPN athlete_id crosswalk.
# OFFLINE except one load_cfb_game_rosters release read per season. Needs stage 06's
# mfb/xwalk/espn_team_id.json (the NCAA <-> ESPN team map).
#   ./scripts/run_07_player_xwalk.sh --seasons 2026          # academic year
#   ./scripts/run_07_player_xwalk.sh --seasons 2026 --dry-run
set -uo pipefail
OFFLINE=1 source "$(dirname "$0")/_env.sh"
run_stage player_xwalk python/ncaa_mfb_07_player_xwalk_build.py --root "${ROOT}" "$@"
