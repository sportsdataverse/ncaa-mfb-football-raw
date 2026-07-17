"""Parse the committed MFB fixture -> assert the structural frame. Offline."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from mfb_parse import PBP_SCHEMA, parse_mfb_pbp

FIX = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mfb_pbp_5362535.html"
)


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


# --- Phase 3: play_text decomposition -------------------------------------


def test_every_play_is_classified() -> None:
    df = _df()
    assert df.filter(pl.col("play_type").is_null()).height == 0
    assert df.filter(pl.col("play_type") == "unknown").height == 0
    # the expected football play types all appear
    kinds = set(df.get_column("play_type").unique().to_list())
    assert {"rush", "pass", "punt", "kickoff", "field_goal"} <= kinds


def test_rush_fields_extracted() -> None:
    df = _df().filter(pl.col("play_type") == "rush")
    assert df.get_column("rusher").is_not_null().all()  # every rush has a rusher
    assert (
        df.get_column("yards_gained").is_not_null().all()
    )  # incl. 0-yard runs (plain form)
    # signed correctly: a known loss and a known gain
    assert (
        df.filter(pl.col("rusher") == "Corbett,Jermaine")
        .get_column("yards_gained")
        .min()
        < 0
    )


def test_pass_fields_extracted() -> None:
    df = _df().filter(pl.col("play_type") == "pass")
    assert df.get_column("passer").is_not_null().all()
    comp = df.filter(pl.col("pass_complete") == True)  # noqa: E712
    assert comp.get_column("receiver").is_not_null().all()
    assert (
        comp.get_column("yards_gained").is_not_null().all()
    )  # completed-pass yards (plain form)
    # incompletions are 0 yards
    inc = df.filter(pl.col("pass_complete") == False)  # noqa: E712
    assert (inc.get_column("yards_gained") == 0).all()


def test_special_teams_players() -> None:
    df = _df()
    assert (
        df.filter(pl.col("play_type") == "punt")
        .get_column("punter")
        .is_not_null()
        .all()
    )
    assert (
        df.filter(pl.col("play_type") == "kickoff")
        .get_column("kicker")
        .is_not_null()
        .all()
    )


def test_markers_flagged_not_plays() -> None:
    df = _df()
    # drive-start / timeout / period / coin-toss rows classified as markers, not real plays
    markers = {"drive_start", "timeout", "period_marker", "coin_toss"}
    assert df.filter(pl.col("play_type").is_in(list(markers))).height > 0


# --- comprehensive field extraction ---------------------------------------


def test_yard_line_split_full_coverage() -> None:
    df = _df()
    yl = df.filter(pl.col("yard_line").is_not_null())
    assert yl.get_column("yard_line_side").is_not_null().all()
    assert yl.get_column("yard_line_number").is_not_null().all()


def test_directions_extracted() -> None:
    df = _df()
    assert (
        df.filter(pl.col("play_type") == "rush")
        .get_column("run_direction")
        .is_not_null()
        .all()
    )
    assert (
        df.filter(pl.col("play_type") == "pass")
        .get_column("pass_direction")
        .is_not_null()
        .all()
    )


def test_flags_present_and_true_somewhere() -> None:
    df = _df()
    for flag in (
        "is_first_down",
        "is_touchdown",
        "is_turnover",
        "out_of_bounds",
        "no_play",
        "fair_catch",
        "penalty_flag",
    ):
        assert df.filter(pl.col(flag) == True).height > 0, flag  # noqa: E712


def test_assisted_tackle_split() -> None:
    df = _df()
    # "(Santiago,David; Zdroik,Payton)" -> tackler_1 + tackler_2 (suffix-safe names)
    row = df.filter(
        (pl.col("tackler_1") == "Santiago,David")
        & (pl.col("tackler_2") == "Zdroik,Payton")
    )
    assert row.height >= 1


def test_penalty_fully_parsed() -> None:
    df = _df().filter(pl.col("penalty_type").is_not_null())
    assert df.height > 0
    assert df.get_column("penalty_team").is_not_null().all()
    assert df.get_column("penalty_yards").is_not_null().all()
    # a known penalty with a suffix player name is captured
    assert "Wilborn Jr.,James" in df.get_column("penalty_player").drop_nulls().to_list()


def test_special_teams_yardage() -> None:
    df = _df()
    assert (
        df.filter(pl.col("play_type") == "kickoff")
        .get_column("kick_yards")
        .is_not_null()
        .all()
    )
    assert (
        df.filter(pl.col("play_type") == "punt")
        .get_column("punt_yards")
        .is_not_null()
        .all()
    )
    fg = df.filter(pl.col("play_type") == "field_goal")
    assert fg.get_column("fg_distance").is_not_null().all()
    assert fg.get_column("fg_made").is_not_null().all()


def test_touchdown_runs_flagged() -> None:
    df = _df()
    td = df.filter(pl.col("is_touchdown") == True)  # noqa: E712
    assert td.height >= 1
    # TD plays end at the goal line
    assert td.get_column("end_yard_line").str.contains("00").any()
