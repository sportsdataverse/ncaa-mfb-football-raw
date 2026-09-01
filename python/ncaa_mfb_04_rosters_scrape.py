"""Stage 04 -- rosters: sweep ``teams/{id}/roster`` pages -> ``mfb/rosters/html/{ay}/``.

Thin shim over :func:`mfb_run.main` with ``--rosters --skip-games`` forced.
Discovery re-reads stage 01's persisted team pages (zero HTTP when present), so
this costs ~one page per team. Same flags as ``mfb_run.py``; resumable (teams
whose roster html exists are skipped).
"""

from __future__ import annotations

import sys

import ncaa_mfb_raw_scrape.mfb_run as mfb_run

FORCED = ("--rosters", "--skip-games")


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return mfb_run.main([*argv, *FORCED])


if __name__ == "__main__":
    raise SystemExit(main())
