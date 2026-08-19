#!/usr/bin/env bash
# User-run launcher: discover + capture NCAA MFB rosters/schedules/pbp bundles
# from stats.ncaa.org. CHUNK it (small --max-contests) for the first live runs.
#   NCAA_VENDOR=decodo_patchright ./scripts/run_mfb_capture.sh --academic-year 2026 --rosters --max-contests 20
#   watch:  tail -f logs/mfb_capture_*.log
#
# Transport: NCAA_VENDOR (canary_vendors.toml at repo root -- the canary-proven
# Decodo US sticky residential + patchright transport) is preferred. Without it,
# falls back to building MFB_PROXY_POOL from .Renviron Decodo creds.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-mfb-football-raw repo root
ROOT="$(pwd)"

# sdv-py sibling checkout: droplet layout first, then the Windows dev box.
SDV_PY="${SDV_PY:-}"
if [ -z "${SDV_PY}" ]; then
  for c in /mnt/sdv_repos/sdv-py "C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"; do
    [ -d "$c" ] && SDV_PY="$c" && break
  done
fi
# .venv layout is OS-dependent: Linux/droplet = .venv/bin, Windows = .venv/Scripts
if [ -x "${SDV_PY}/.venv/bin/python" ]; then PY="${PY:-${SDV_PY}/.venv/bin/python}"
else PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"; fi

if [ -n "${NCAA_VENDOR:-}" ]; then
  echo "transport: NCAA_VENDOR=${NCAA_VENDOR} (canary_vendors.toml)"
else
  # Fallback: US residential sticky pool (Decodo). Creds from .Renviron (call time only).
  RENV="${HOME}/.Renviron"; [ -f "$RENV" ] || RENV="${HOME}/Documents/.Renviron"
  getcred() { grep -E "^$1=" "$RENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d '\r'; }
  DECODO_USER="$(getcred DECODO_USER_NAME)"; DECODO_PASS="$(getcred DECODO_PASSWORD)"
  if [ -n "${DECODO_USER}" ] && [ -n "${DECODO_PASS}" ]; then
    pool=""
    for p in $(seq 10001 10010); do
      pool="${pool}${pool:+,}http://${DECODO_USER}:${DECODO_PASS}@us.decodo.com:${p}"
    done
    export MFB_PROXY_POOL="${pool}"
    echo "proxy pool: 10 US residential sticky sessions (creds hidden)"
  else
    echo "WARNING: no NCAA_VENDOR and no Decodo creds in ${RENV}; MFB_PROXY_POOL empty" >&2
  fi
fi

export PYTHONPATH="${SDV_PY}:${ROOT}/python"
export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
mkdir -p logs
LOG="logs/mfb_capture_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"
"${PY}" python/mfb_run.py --out "${ROOT}" "$@" 2>&1 | tee -a "${LOG}"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "${LOG}"
exit "${rc}"
