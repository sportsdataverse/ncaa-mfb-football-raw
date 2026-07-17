"""Parse stats.ncaa.org MFB play-by-play HTML -> tidy polars frame.

Phase 2 (structural): one row per play with drive context + down/distance/
yard_line (already isolated in the markup) + raw ``play_text``. Phase 3 will
decompose ``play_text`` into play_type / players / yards_gained (cfbfastR-style).

Markup (fixture-verified, contest 5362535): ``div.drives`` holds, per drive, an
``h5.(non_)scoring_play`` title, a header-body ``div`` (team + score), then a
``div`` whose bordered child ``div``s are the plays -- each ``<span>`` bold
down/dist/yardline + ``<span>`` play text. ``scoring_play`` class = drive scored.
"""

from __future__ import annotations

import re

import polars as pl
from bs4 import BeautifulSoup

# h5 drive title: "{team} {result} {clock},{yardline}, {n} plays, {yards} yards, {top} {a} - {h}"
_DRIVE_RE = re.compile(
    r"^(?P<team>.+?)\s+(?P<result>[A-Za-z/]+)\s+(?P<start_clock>\d+:\d+),(?P<start_yard_line>[A-Z]{1,4}\d+),\s+"
    r"(?P<n_plays>\d+)\s+plays?,\s+(?P<yards>-?\d+)\s+yards?,\s+(?P<top>\d+:\d+)\s+"
    r"(?P<score_away>\d+)\s*-\s*(?P<score_home>\d+)\s*$"
)
# play prefix: "1st & 10 at MC9" (distance may be "Goal")
_DD_RE = re.compile(
    r"^(?P<down>1st|2nd|3rd|4th)\s+&\s+(?P<distance>\d+|Goal)\s+at\s+(?P<yard_line>[A-Z]{1,4}\d+)", re.I
)
_DOWN = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}

PBP_SCHEMA: "dict[str, pl.DataType]" = {
    "contest_id": pl.Utf8,
    "drive_number": pl.Int64,
    "play_number": pl.Int64,
    "offense": pl.Utf8,
    "drive_result": pl.Utf8,
    "drive_scored": pl.Boolean,
    "down": pl.Int64,
    "distance": pl.Int64,
    "yard_line": pl.Utf8,
    "play_text": pl.Utf8,
}


def _spaces(text: str) -> str:
    return " ".join(text.split())


def parse_mfb_pbp(html: str, contest_id: "str | int | None" = None, *, return_as_pandas: bool = False):
    """Parse an MFB ``play_by_play`` page into one row per play.

    Empty/unparseable input returns a zero-row frame with the documented schema
    (callers can chain without null-checks).
    """
    soup = BeautifulSoup(html or "", "html.parser")
    rows: "list[dict]" = []
    drive_number = 0
    cid = str(contest_id) if contest_id is not None else None

    for container in soup.select("div.drives"):
        drive: "dict" = {}
        for child in container.find_all(["h5", "div"], recursive=False):
            classes = child.get("class") or []
            is_drive_el = "scoring_play" in classes or "non_scoring_play" in classes
            if not is_drive_el:
                continue
            if child.name == "h5":
                drive_number += 1
                m = _DRIVE_RE.match(_spaces(child.get_text(" ", strip=True)))
                drive = {
                    "drive_number": drive_number,
                    "offense": m.group("team") if m else None,
                    "drive_result": m.group("result") if m else None,
                    "drive_scored": "scoring_play" in classes,
                }
            elif child.select_one(".headerRight") is None:
                # play-list div (the header-body div has .headerRight; skip it)
                play_number = 0
                for play in child.find_all("div", recursive=False):
                    spans = play.find_all("span")
                    if len(spans) < 2:
                        continue
                    ddm = _DD_RE.match(_spaces(spans[0].get_text(" ", strip=True)))
                    play_number += 1
                    dist = ddm.group("distance") if ddm else None
                    rows.append(
                        {
                            "contest_id": cid,
                            "drive_number": drive.get("drive_number"),
                            "play_number": play_number,
                            "offense": drive.get("offense"),
                            "drive_result": drive.get("drive_result"),
                            "drive_scored": drive.get("drive_scored"),
                            "down": _DOWN.get(ddm.group("down").lower()) if ddm else None,
                            "distance": int(dist) if dist and dist.isdigit() else None,
                            "yard_line": ddm.group("yard_line") if ddm else None,
                            "play_text": _spaces(spans[1].get_text(" ", strip=True)),
                        }
                    )

    df = pl.DataFrame(rows, schema_overrides=PBP_SCHEMA) if rows else pl.DataFrame(schema=PBP_SCHEMA)
    return df.to_pandas() if return_as_pandas else df
