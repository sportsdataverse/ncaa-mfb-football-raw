"""Parse the committed MFB fixture -> assert the structural frame. Offline."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from mfb_parse import PBP_SCHEMA, parse_mfb_pbp

FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mfb_pbp_5362535.html"


def _df() -> pl.DataFrame:
    return parse_mfb_pbp(FIX.read_text(encoding="utf-8"), contest_id="5362535")


def test_returns_documented_schema() -> None:
    df = _df()
    assert df.columns == list(PBP_SCHEMA.keys())
    assert df.height > 0


def test_empty_input_is_zero_row_with_schema() -> None:
    df = parse_mfb_pbp("")
    assert df.height == 0
    assert df.columns == list(PBP_SCHEMA.keys())


def test_down_distance_yardline_extracted() -> None:
    df = _df()
    dd = df.filter(pl.col("down").is_not_null())
    assert dd.height > 0
    # every parsed down is 1-4; every parsed yard_line looks like TEAM+number
    assert dd.get_column("down").is_between(1, 4).all()
    assert dd.get_column("yard_line").str.contains(r"^[A-Z]{1,4}\d+$").all()


def test_scoring_drive_flagged() -> None:
    df = _df()
    # the Air Force TD drive (fixture: "Air Force TD ... 0 - 7") must be flagged scored
    af = df.filter(pl.col("offense") == "Air Force")
    assert af.height > 0
    assert af.get_column("drive_scored").any()
    assert "TD" in af.get_column("drive_result").to_list()


def test_play_text_preserved() -> None:
    df = _df()
    # a known play from drive 1 keeps its raw text for Phase-3 decomposition
    hit = df.filter(pl.col("play_text").str.contains("Anthony,Malakai rush left"))
    assert hit.height >= 1
    assert hit.get_column("down").to_list()[0] == 1


def test_contest_id_stamped() -> None:
    df = _df()
    assert (df.get_column("contest_id") == "5362535").all()
