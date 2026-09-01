"""Offline discovery tests: pure parsers on the committed team-list fixture +
discover_season with an injected fetch_fn (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ncaa_mfb_raw_scrape.mfb_discover import (
    discover_season,
    parse_contest_ids,
    parse_team_ids,
    team_list_path,
)

TEAM_LIST = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "mfb_team_list_2025_fbs.html"
)


def test_team_list_path() -> None:
    p = team_list_path(2025, 11)
    assert "sport_code=MFB" in p and "academic_year=2025" in p and "division=11" in p


def test_parse_team_ids_on_fbs_fixture() -> None:
    ids = parse_team_ids(TEAM_LIST.read_text(encoding="utf-8"))
    assert len(ids) == 134  # FBS teams, 2025 season
    assert all(i.isdigit() for i in ids)
    assert len(set(ids)) == len(ids)  # deduped


def test_parse_contest_ids_dedups_across_page_tabs() -> None:
    html = (
        '<a href="/contests/123/box_score">x</a>'
        '<a href="/contests/123/play_by_play">y</a>'  # same game, different tab
        '<a href="/contests/456/box_score">z</a>'
    )
    assert parse_contest_ids(html) == ["123", "456"]


def test_parse_empty() -> None:
    assert parse_team_ids("") == []
    assert parse_contest_ids("") == []


def test_discover_season_walks_and_dedups() -> None:
    def fake(path: str) -> str:
        if "inst_team_list" in path:
            return '<a href="/teams/1">a</a><a href="/teams/2">b</a>'
        if path == "teams/1":
            return '<a href="/contests/100/box_score">x</a><a href="/contests/200/box_score">y</a>'
        if path == "teams/2":
            return '<a href="/contests/200/box_score">y</a><a href="/contests/300/box_score">z</a>'
        return ""

    assert discover_season(2025, fetch_fn=fake) == [
        "100",
        "200",
        "300",
    ]  # sorted + deduped across teams


def test_discover_no_teams_raises_loudly() -> None:
    with pytest.raises(ValueError, match="no MFB teams"):
        discover_season(2025, fetch_fn=lambda _p: "")
