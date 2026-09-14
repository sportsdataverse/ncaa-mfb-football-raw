#!/usr/bin/env bash
# Daily in-season driver for NCAA MFB, run from the DROPLET CRONTAB.
#
# Scheduling for this pipeline is cron, NOT Prefect. sdv-orch's registry entry
# still carries `crons` (they model the season window and drive the nightly
# season-coverage report) and `schedule_active=False` keeps its deployment
# PAUSED. Do not flip that flag: a live Prefect deployment plus this cron would
# be two producers of the same data, which is exactly how nfl-raw and odds-data
# ended up with divergent histories and daily push rejections.
#
# Composes the existing numbered stages -- it does NOT reimplement them. A
# backfill is the same stages with different env (see RUNBOOK.md).
#
#   run:    NCAA_VENDOR=decodo_patchright ./scripts/daily_mfb_scraper.sh
#   watch:  tail -f logs/daily_mfb_$(date -u +%Y%m%d).log
#
# Tunables (env only -- never edit pace into the script; you should be able to
# re-tune without a commit):
#   MFB_MAX_CONTESTS  per-division capture cap per run   (default 400)
#   MFB_ACADEMIC_YEAR override the resolved ay           (default: current)
#   MFB_REFRESH_MAX_STALE_FRAC  fail if more than this share of team pages
#                     could not be re-fetched           (default 0.5)
#   MFB_WORKERS       parallel shard processes           (default 1)
#
# Single-stream by default: stats.ncaa.org is a hostile host and a normal night
# has few new contests. MFB_WORKERS=N shards instead -- N separate processes via
# mfb_run.py --shard i/N, each on a disjoint slice of canary_vendors.toml's 50
# sticky ports (keep >=2 ports/worker => N <= 25). Serially a refresh is ~16 s
# a page: 266 team pages plus 6 tabs per new contest, so catching up a missed
# week is hours (2026-09-14: ~1,500 pages, ~6.6 h at N=1).
#
# Sharded, each division runs TWO waves, never one: wave A refreshes disjoint
# slices of team pages (--skip-games), wave B captures disjoint slices of the
# contests re-discovered from those now-fresh pages. A refreshing shard only
# sees its own teams' contests, so capturing inside wave A would drop games
# (mfb_run.py refuses that combination).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
source "scripts/_env.sh"

MAX_CONTESTS="${MFB_MAX_CONTESTS:-400}"
WORKERS="${MFB_WORKERS:-1}"
if ! [[ "$WORKERS" =~ ^[0-9]+$ ]] || [ "$WORKERS" -lt 1 ] || [ "$WORKERS" -gt 25 ]; then
  echo "ERROR: MFB_WORKERS must be 1-25 (got '${WORKERS}')" >&2
  exit 2
fi

# run_wave <label> <args...> : WORKERS shard processes of mfb_run.py, waited on.
# Returns 1 if any shard failed. Each shard tees its own log via run_stage.
run_wave() {
  local label="$1"; shift
  local pids=() i rc=0
  for i in $(seq 0 $((WORKERS - 1))); do
    run_stage "${label}_s${i}of${WORKERS}" python/ncaa_mfb_raw_scrape/mfb_run.py \
      "$@" --shard "${i}/${WORKERS}" > /dev/null &
    pids+=("$!")
  done
  for i in "${!pids[@]}"; do
    wait "${pids[$i]}" || { echo "WARN ${label} shard ${i} rc=$?"; rc=1; }
  done
  return "$rc"
}

# The academic year comes from the shared helper, not a hardcoded constant --
# `range(2014, 2027)` silently excluding ay2027 is precisely the bug this repo
# just fixed. ay = season + 1; the year rolls in August.
AY="${MFB_ACADEMIC_YEAR:-$("${PY}" -c 'import sys; sys.path.insert(0,"python"); from ncaa_mfb_raw_scrape import current_academic_year as c; print(c())')}"
if ! [[ "$AY" =~ ^[0-9]{4}$ ]]; then
  echo "ERROR: could not resolve academic year (got '${AY}')" >&2
  exit 2
fi
FALL=$((AY - 1))

LOG="logs/daily_mfb_$(date -u +%Y%m%d).log"
mkdir -p logs
{
  echo "[$(date -u '+%F %T')Z] daily mfb start: ay=${AY} (fall ${FALL}) max=${MAX_CONTESTS} workers=${WORKERS}"

  rc_total=0
  # Divisions 11 (FBS) and 12 (FCS). Game bundles are file-exists resumable, so
  # a re-run after a partial night re-captures nothing.
  #
  # Discovery is NOT: --refresh-discovery re-fetches the team list and every
  # team page (~270 pages/day across both divisions). A team page only links a
  # contest once the game is played, so reading the saved copies froze fall 2026
  # at its 2026-09-09 snapshot -- every run after "captured 0", exit 0, and
  # week 2 never landed. Refresh fails the stage if most pages fall back to
  # their saved copy (MFB_REFRESH_MAX_STALE_FRAC, default 0.5).
  for div in 11 12; do
    common=(--out "${ROOT}" --academic-year "${AY}" --division "${div}")
    if [ "$WORKERS" -eq 1 ]; then
      run_stage "daily_mfb_capture_d${div}" python/ncaa_mfb_raw_scrape/mfb_run.py \
        "${common[@]}" --max-contests "${MAX_CONTESTS}" --refresh-discovery
      rc=$?
    else
      echo "[$(date -u '+%F %T')Z] div=${div} wave A: refresh team pages x${WORKERS}"
      run_wave "daily_mfb_refresh_d${div}" "${common[@]}" --refresh-discovery --skip-games
      rc=$?
      # A failed refresh wave means stale pages: capture from them anyway (the
      # games they do list are real), but the run still reports the failure.
      echo "[$(date -u '+%F %T')Z] div=${div} wave B: capture x${WORKERS}"
      per_shard=$(( (MAX_CONTESTS + WORKERS - 1) / WORKERS ))
      run_wave "daily_mfb_capture_d${div}" "${common[@]}" --max-contests "${per_shard}" || rc=1
      grep -h "discovered .* contests\|capture:" logs/daily_mfb_capture_d${div}_s*of${WORKERS}_$(date +%Y%m%d)_*.log 2>/dev/null | sort | uniq -c
    fi
    [ "$rc" -ne 0 ] && { echo "WARN capture div=${div} rc=${rc}"; rc_total=1; }
  done

  run_stage "daily_mfb_parse" python/ncaa_mfb_03_games_parse.py --academic-year "${AY}"
  rc=$?
  [ "$rc" -ne 0 ] && { echo "WARN parse rc=${rc}"; rc_total=1; }

  echo "[$(date -u '+%F %T')Z] daily mfb stages done (rc_total=${rc_total})"
  exit "$rc_total"
} 2>&1 | tee -a "$LOG"
STAGE_RC="${PIPESTATUS[0]}"

# Commit + push. The season-commit message format is load-bearing downstream, so
# it is kept verbatim.
# Both trees. Everything scraped lands under mfb/ (raw, json, schedules, teams,
# rosters, datasets, xwalk); logs/ carries the run record. `logs/` used to be
# gitignored here while the MBB/WBB twins tracked theirs, so git rejected this
# whole pathspec with exit 1 -- suppressed by a 2>/dev/null -- and no log was
# ever committed by a line written to commit logs. The ignore rule is gone
# (D22 parity), so both paths stage for real now.
git add -- mfb/ logs/
if ! git diff --cached --quiet; then
  git commit -q -m "MFB Raw Update (Start: ${FALL} End: ${FALL})"

  # Retry with a rebase rather than swallowing the failure. run_backfill_all.sh
  # still does `git push ... || true`, which reports success while the data sits
  # on the box: odds-data lost 11 hours of snapshots to exactly that line, and
  # nfl-raw diverged for days. Fail loudly instead.
  pushed=0
  for attempt in 1 2 3; do
    if git push -q origin main; then pushed=1; break; fi
    echo "push rejected (attempt ${attempt}); syncing with origin" | tee -a "$LOG"
    git fetch --quiet origin main || true
    if ! git rebase --merge origin/main >/dev/null 2>&1; then
      git rebase --abort >/dev/null 2>&1 || true
      echo "ERROR: cannot rebase onto origin/main; commit left local" | tee -a "$LOG" >&2
      break
    fi
  done
  if [ "$pushed" -ne 1 ]; then
    echo "ERROR: push failed after 3 attempts" | tee -a "$LOG" >&2
    STAGE_RC=1
  fi
else
  echo "[$(date -u '+%F %T')Z] nothing new to commit" | tee -a "$LOG"
fi

echo "[$(date -u '+%F %T')Z] daily mfb done EXIT=${STAGE_RC}" | tee -a "$LOG"
exit "$STAGE_RC"
