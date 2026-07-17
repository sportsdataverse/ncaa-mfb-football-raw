#!/usr/bin/env bash
# User-run launcher: discover + capture NCAA MFB pbp bundles from stats.ncaa.org.
# Requires a real-GPU host + US residential proxies. CHUNK it (small --max-contests)
# until IP rotation at scale is hardened -- a big run can burn residential IPs.
#   ./scripts/run_mfb_capture.sh --academic-year 2025 --max-contests 20
#   watch:  tail -f logs/mfb_capture_*.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-mfb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"

# US residential sticky pool (Decodo). Creds from .Renviron (read only at call time).
RENV="${HOME}/.Renviron"; [ -f "$RENV" ] || RENV="${HOME}/Documents/.Renviron"
getcred() { grep -E "^$1=" "$RENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d '\r'; }
DECODO_USER="$(getcred DECODO_USER)"; DECODO_PASS="$(getcred DECODO_PASS)"
if [ -n "${DECODO_USER}" ] && [ -n "${DECODO_PASS}" ]; then
  pool=""
  for p in 10001 10002 10003 10004 10005 10006 10007 10008 10009 10010; do
    pool="${pool}${pool:+,}http://${DECODO_USER}:${DECODO_PASS}@us.decodo.com:${p}"
  done
  export MFB_PROXY_POOL="${pool}"
  echo "proxy pool: 10 US residential sticky sessions (creds hidden)"
else
  echo "WARNING: DECODO_USER/DECODO_PASS not in ${RENV}; MFB_PROXY_POOL empty" >&2
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
