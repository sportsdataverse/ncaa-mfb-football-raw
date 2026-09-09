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
#
# Deliberately single-stream: mfb_run.py parallelises via --shard i/N, not a
# worker count, and a daily incremental has few new contests. stats.ncaa.org is
# a hostile host -- run_backfill_all.sh shards only because it is replaying whole
# seasons. If a daily run ever needs it, add --shard here, not a --workers flag
# (there is no such flag; passing one exits 2 on every run).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
source "scripts/_env.sh"

MAX_CONTESTS="${MFB_MAX_CONTESTS:-400}"

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
  echo "[$(date -u '+%F %T')Z] daily mfb start: ay=${AY} (fall ${FALL}) max=${MAX_CONTESTS}"

  rc_total=0
  # Divisions 11 (FBS) and 12 (FCS). Every stage is file-exists resumable, so a
  # re-run after a partial night costs nothing and re-captures nothing.
  for div in 11 12; do
    run_stage "daily_mfb_capture_d${div}" python/ncaa_mfb_raw_scrape/mfb_run.py \
      --out "${ROOT}" --academic-year "${AY}" --division "${div}" \
      --max-contests "${MAX_CONTESTS}"
    rc=$?
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
git add -- mfb/ logs/ 2>/dev/null
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
