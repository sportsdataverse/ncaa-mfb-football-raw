"""Offline tests: team-list + team-page schedule parsers on committed fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

pl = pytest.importorskip("polars")

from ncaa_mfb_raw_scrape.mfb_datasets import parse_team_list, parse_team_schedule  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def test_parse_team_list_fbs() -> None:
    df = parse_team_list(
        (FIXTURES / "mfb_team_list_2025_fbs.html").read_text(encoding="utf-8")
    )
    assert df.height == 134  # fall-2024 FBS
    assert df.get_column("team_id").n_unique() == 134
    assert "Ohio St." in df.get_column("team_name").to_list()


def test_parse_team_schedule() -> None:
    df = parse_team_schedule(
        (FIXTURES / "mfb_team_page_2026_606000.html").read_text(encoding="utf-8"),
        team_id="606000",
    )
    assert df.height == 12  # Charlotte played 12 games in fall 2025
    assert df.get_column("team_name").unique().to_list() == ["Charlotte 49ers"]
    assert df.get_column("contest_id").drop_nulls().n_unique() == 12
    first = df.row(0, named=True)
    assert first["date"] == "08/29/2025"
    assert first["opponent"] == "App State"
    assert first["outcome"] == "L"
    assert (first["team_score"], first["opponent_score"]) == (11, 34)
    assert first["attendance"] == 35718


def test_parse_empty() -> None:
    assert parse_team_list("").height == 0
    assert parse_team_schedule("").height == 0
