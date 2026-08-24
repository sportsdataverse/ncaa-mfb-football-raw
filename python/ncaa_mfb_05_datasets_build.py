"""Stage 05 -- datasets: persisted HTML + game bundles -> tidy season parquet.

Thin shim over :func:`mfb_datasets.main`. FULLY OFFLINE (no proxy, no network):
builds ``mfb/teams/parquet/``, ``mfb/rosters/parquet/``,
``mfb/{teams,rosters,schedules}/parquet/`` reference frames from
what stages 01/02/04 already captured. Flags: ``--academic-year``, ``--root``.
Not sharded -- one output file per kind, so run it once after the sweeps.
"""

from __future__ import annotations

import sys

import mfb_datasets


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return mfb_datasets.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
