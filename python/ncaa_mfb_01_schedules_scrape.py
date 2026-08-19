"""Stage 01 -- discovery: team list + team pages -> contest_ids (the schedule source).

Thin shim over :func:`mfb_run.main` with ``--skip-games`` forced: it sweeps
``mfb/teams/html/`` and ``mfb/schedules/html/{ay}/`` and stops before any game
bundle. Same flags as ``mfb_run.py`` (``--academic-year``, ``--division``,
``--out``, ``--shard``); transport comes from ``NCAA_VENDOR`` / ``MFB_PROXY_POOL``
exactly as the combined runner. File-exists resumable.

Stage numbers mirror ncaa-mbb-hoops-raw (01 schedules, 02 games, 04 rosters,
05 datasets). 03 (parse) is a deliberate HOLE: MFB parsing graduated to sdv-py
and runs inside stage 05.
"""

from __future__ import annotations

import sys

import mfb_run

FORCED = ("--skip-games",)


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return mfb_run.main([*argv, *FORCED])


if __name__ == "__main__":
    raise SystemExit(main())
