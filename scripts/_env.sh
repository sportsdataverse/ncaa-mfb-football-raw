#!/usr/bin/env bash
# Shared env for the run_*.sh launchers -- `source` it, never run it.
#
# Sets: ROOT (repo root, cwd), SDV_PY, PY (venv python), PYTHONPATH,
# PYTHONUNBUFFERED/PYTHONIOENCODING, and -- for ONLINE stages -- the transport
# (NCAA_VENDOR preferred; MFB_PROXY_POOL fallback from .Renviron Decodo creds).
# Pass OFFLINE=1 before sourcing to skip the transport block entirely.
#
# Usage inside a launcher:
#   source "$(dirname "$0")/_env.sh"            # online stage
#   OFFLINE=1 source "$(dirname "$0")/_env.sh"  # offline stage
#   run_stage <log-prefix> python/<shim>.py "$@"
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1   # -> ncaa-mfb-football-raw repo root
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

if [ -n "${OFFLINE:-}" ]; then
  echo "offline stage: no proxy / transport needed"
elif [ -n "${NCAA_VENDOR:-}" ]; then
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

# run_stage <log-prefix> <python-entrypoint> [args...]
# Tees to logs/<prefix>_<ts>.log and propagates the PYTHON exit code -- `$?`
# after a pipe is tee's status, and a bare trailing `echo` would mask a ban
# hard-stop as success.
run_stage() {
  local prefix="$1" entry="$2"; shift 2
  mkdir -p logs
  local log="logs/${prefix}_$(date +%Y%m%d_%H%M%S).log"
  echo "log -> ${log}  (watch: tail -f ${log})"
  "${PY}" "${entry}" "$@" 2>&1 | tee -a "${log}"
  local rc=${PIPESTATUS[0]}
  echo "EXIT=${rc}" | tee -a "${log}"
  return "${rc}"
}
