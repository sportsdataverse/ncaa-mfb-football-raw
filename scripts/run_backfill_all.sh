#!/usr/bin/env bash
# Orchestrator: full historical backfill, one season at a time (newest->oldest).
#   NCAA_VENDOR=decodo_patchright ./scripts/run_backfill_all.sh 2022 2009
# Per season: discovery+rosters (both divisions, one browser session each) ->
# 8-shard game capture (div 11 then 12 per shard) -> datasets build + QA line ->
# git commits per stage. Every stage is file-exists resumable, so re-running
# after an interruption fast-forwards. Stops cleanly when a season's team list
# is empty ("no MFB teams") -- the natural floor of stats.ncaa.org coverage.
# Watch: tail -f logs/bf_<ay>_*.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
START="${1:?start academic year (e.g. 2022)}"
END="${2:?end academic year (e.g. 2009)}"
export NCAA_VENDOR="${NCAA_VENDOR:-decodo_patchright}"
SDV_PY="${SDV_PY:-/mnt/sdv_repos/sdv-py}"
PY="${SDV_PY}/.venv/bin/python"
export PYTHONPATH="${SDV_PY}:${ROOT}/python" PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
# Chromium writes its temp profiles to $TMPDIR; keep them on the block storage,
# not the small root disk (2026-08-21: leaked profiles filled /).
export TMPDIR=/mnt/sdv_repos/tmp
mkdir -p logs "$TMPDIR"
SHARDS="${SHARDS:-24}"

for ay in $(seq "$START" -1 "$END"); do
  fall=$((ay - 1))
  echo "=== SEASON ay${ay} (fall ${fall}) $(date -u +%FT%TZ) ==="
  # breaker-tripped/killed browsers leak Chromium profiles under $TMPDIR; sweep
  # between seasons (and legacy /tmp leftovers). Root guard stays: syslog etc.
  # still lives on / (2026-08-21: / hit 100% and every fetch failed).
  rm -rf "$TMPDIR"/.org.chromium.* /tmp/.org.chromium.* 2>/dev/null || true
  free_kb=$(df --output=avail / | tail -1 | tr -d ' ')
  if [ "${free_kb:-0}" -lt 5242880 ]; then
    echo "ROOT DISK LOW (<5G free) -- stopping before ay${ay}; free space and rerun"
    exit 1
  fi
  # 1) discovery + rosters per division (single session; resumable)
  for div in 11 12; do
    "$PY" python/mfb_run.py --out "$ROOT" --academic-year "$ay" --division "$div" \
      --rosters --skip-games > "logs/bf_${ay}_01_div${div}.log" 2>&1
    rc=$?
    if [ $rc -ne 0 ] && grep -q "no MFB teams" "logs/bf_${ay}_01_div${div}.log"; then
      if [ "$div" = 11 ]; then
        echo "ay${ay}: no FBS teams -- stopping backfill at fall ${fall}"
        echo "BACKFILL FLOOR REACHED at ay${ay}"
        exit 0
      fi
      echo "ay${ay}: no div-12 teams (continuing with FBS only)"
    elif [ $rc -ne 0 ]; then
      echo "ay${ay} div${div}: discovery/rosters rc=${rc} (continuing)"
    fi
  done
  git add "mfb/schedules/html/${ay}" "mfb/rosters/html/${ay}" mfb/teams/html 2>/dev/null || true
  git commit -q -m "feat(data): ay${ay} (fall ${fall}) discovery -- team pages + rosters" \
    && git push -q origin main || true

  # 2) games: $SHARDS shard workers, each div 11 then div 12
  for i in $(seq 0 $((SHARDS - 1))); do
    ( "$PY" python/mfb_run.py --out "$ROOT" --academic-year "$ay" --division 11 --shard "$i/$SHARDS" \
        >  "logs/bf_${ay}_02_shard${i}.log" 2>&1
      "$PY" python/mfb_run.py --out "$ROOT" --academic-year "$ay" --division 12 --shard "$i/$SHARDS" \
        >> "logs/bf_${ay}_02_shard${i}.log" 2>&1 ) &
    sleep 3
  done
  wait
  grep -h 'capture:' logs/bf_${ay}_02_shard*.log || true
  git add mfb/json 2>/dev/null || true
  git commit -q -m "feat(data): ay${ay} (fall ${fall}) season game bundles (FBS+FCS)" \
    && git push -q origin main || true

  # 3) datasets build + QA summary (offline)
  "$PY" python/mfb_datasets.py --academic-year "$ay" > "logs/bf_${ay}_05.log" 2>&1 || true
  "$PY" - "$ay" <<'PYEOF' 2>&1 | tee -a "logs/bf_${ay}_05.log"
import sys
import polars as pl
ay = sys.argv[1]
try:
    qa = pl.read_parquet(f"mfb/datasets/{ay}/qa_pbp_vs_linescore.parquet")
    n = qa.height
    ok = (qa["final_score_match"] == True).sum()  # noqa: E712
    unv = qa["final_score_match"].null_count()
    print(f"ay{ay} QA: {ok}/{n} exact, {unv} unverifiable, {n - ok - unv} flagged")
except Exception as exc:  # noqa: BLE001
    print(f"ay{ay} QA: unavailable ({exc})")
PYEOF
  git add "mfb/datasets/${ay}" mfb/schedules/parquet mfb/rosters/parquet mfb/teams/parquet 2>/dev/null || true
  git commit -q -m "feat(data): built ay${ay} (fall ${fall}) season datasets (QA line in logs/bf_${ay}_05.log)" \
    && git push -q origin main || true
  echo "=== ay${ay} complete $(date -u +%FT%TZ) ==="
done
echo "BACKFILL COMPLETE ${START}->${END}"
