"""Stage 02 -- games: capture the 6-tab bundle for every not-yet-captured contest.

Thin shim over :func:`mfb_run.main` (no forced flags): discovery re-reads the
persisted team pages from stage 01 (zero HTTP when they exist), then
``capture_season`` writes ``mfb/raw/{ay}/{contest_id}.json.gz``. Same flags as
``mfb_run.py``; chunk with ``--max-contests N`` and fan out with ``--shard i/N``
(one PROCESS per shard). Resumable: captured contests are skipped; a ban
hard-stops the run with rc=1.
"""

from __future__ import annotations

import sys

import mfb_run


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return mfb_run.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
