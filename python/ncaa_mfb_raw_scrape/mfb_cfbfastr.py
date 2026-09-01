"""Compat shim -- the NCAA->cfbfastR mapper GRADUATED to sdv-py (PR #384).

The implementation now lives in ``sportsdataverse.cfb.cfb_ncaa_cfbfastr``
(``to_cfbfastr`` + frozen ``CFBFASTR_SCHEMA``), ported behavior-preserving with
full value-parity verified on this repo's fixtures. This module keeps the names
``mfb_datasets.py`` and ``test_mfb_cfbfastr.py`` import so the raw pipeline is
untouched; new code should import from ``sportsdataverse.cfb`` directly.

``parse_ot_drives`` compat: the old local parser returned only the OT rows;
the graduated ``to_cfbfastr(ot_drives=...)`` accepts the FULL
``parse_cfb_ncaa_drives`` frame and selects ``period > 4`` itself, so the shim
just returns the full parse (n_plays/yards included since sdv-py PR #384).
"""

from __future__ import annotations

import polars as pl
from sportsdataverse.cfb import (
    CFBFASTR_SCHEMA,
    parse_cfb_ncaa_drives,
    to_cfbfastr,
)
from sportsdataverse.cfb import parse_cfb_ncaa_drive_titles as parse_drive_titles
from sportsdataverse.cfb import parse_cfb_ncaa_scoring_summary as parse_scoring_summary
from sportsdataverse.cfb.cfb_ncaa_cfbfastr import _first_last, _norm_team

__all__ = [
    "CFBFASTR_SCHEMA",
    "_first_last",
    "_norm_team",
    "parse_drive_titles",
    "parse_ot_drives",
    "parse_scoring_summary",
    "to_cfbfastr",
]


def parse_ot_drives(drives_html: str) -> pl.DataFrame:
    """Full drives frame for ``to_cfbfastr(ot_drives=...)`` (it filters period > 4)."""
    return parse_cfb_ncaa_drives(drives_html)
