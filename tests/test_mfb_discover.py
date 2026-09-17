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


def _site(pages: "dict[str, str]"):
    """Fake stats.ncaa.org whose pages can change between calls; records forces."""
    calls: "list[tuple[str, bool]]" = []

    def fetch(path: str, force: bool = False) -> str:
        calls.append((path, force))
        return pages.get(path, "")

    return fetch, calls


def _pad(html: str) -> str:
    return html + "<!--" + "x" * 10_000 + "-->"  # clear the real-page size floor


def test_saved_pages_freeze_discovery_without_refresh(tmp_path: Path) -> None:
    pages = {
        "team/inst_team_list?academic_year=2027&conf_id=-1&division=11&sport_code=MFB": _pad(
            '<a href="/teams/1">a</a>'
        ),
        "teams/1": _pad('<a href="/contests/100/box_score">wk1</a>'),
    }
    fetch, _ = _site(pages)
    assert discover_season(2027, fetch_fn=fetch, save_dir=tmp_path) == ["100"]

    # week 2 is played: the live team page now links a second contest
    pages["teams/1"] = _pad(
        '<a href="/contests/100/box_score">wk1</a><a href="/contests/200/box_score">wk2</a>'
    )
    assert discover_season(2027, fetch_fn=fetch, save_dir=tmp_path) == ["100"]  # frozen
    got = discover_season(2027, fetch_fn=fetch, save_dir=tmp_path, refresh=True)
    assert got == ["100", "200"]
    # and the refreshed page replaced the saved copy
    saved = (tmp_path / "mfb" / "schedules" / "html" / "2027" / "1.html").read_text()
    assert "/contests/200/" in saved


def test_refresh_bypasses_fetcher_cache(tmp_path: Path) -> None:
    fetch, calls = _site(
        {
            "team/inst_team_list?academic_year=2027&conf_id=-1&division=11&sport_code=MFB": _pad(
                '<a href="/teams/1">a</a>'
            ),
            "teams/1": _pad('<a href="/contests/100/box_score">x</a>'),
        }
    )
    discover_season(2027, fetch_fn=fetch, save_dir=tmp_path, refresh=True)
    assert calls and all(force for _, force in calls)


def test_refresh_stub_keeps_saved_page(tmp_path: Path) -> None:
    tl = "team/inst_team_list?academic_year=2027&conf_id=-1&division=11&sport_code=MFB"
    pages = {tl: _pad('<a href="/teams/1">a</a><a href="/teams/2">b</a>'),
             "teams/1": _pad('<a href="/contests/100/box_score">x</a>'),
             "teams/2": _pad('<a href="/contests/300/box_score">z</a>')}
    fetch, _ = _site(pages)
    discover_season(2027, fetch_fn=fetch, save_dir=tmp_path)

    pages["teams/1"] = _pad('<a href="/contests/100/box_score">x</a><a href="/contests/200/box_score">y</a>')
    pages["teams/2"] = "bm-verify stub"  # blocked on refresh: keep the saved page
    got = discover_season(2027, fetch_fn=fetch, save_dir=tmp_path, refresh=True)
    assert got == ["100", "200", "300"]


def test_refresh_mostly_blocked_fails_loudly(tmp_path: Path) -> None:
    tl = "team/inst_team_list?academic_year=2027&conf_id=-1&division=11&sport_code=MFB"
    pages = {tl: _pad('<a href="/teams/1">a</a><a href="/teams/2">b</a>'),
             "teams/1": _pad('<a href="/contests/100/box_score">x</a>'),
             "teams/2": _pad('<a href="/contests/300/box_score">z</a>')}
    fetch, _ = _site(pages)
    discover_season(2027, fetch_fn=fetch, save_dir=tmp_path)
    pages["teams/1"] = pages["teams/2"] = "blocked"
    with pytest.raises(RuntimeError, match="discovery is stale"):
        discover_season(2027, fetch_fn=fetch, save_dir=tmp_path, refresh=True)


def test_team_shards_refresh_disjoint_slices_covering_every_team(tmp_path: Path) -> None:
    tl = "team/inst_team_list?academic_year=2027&conf_id=-1&division=11&sport_code=MFB"
    pages = {tl: _pad("".join(f'<a href="/teams/{t}">t</a>' for t in range(1, 8)))}
    pages.update({f"teams/{t}": _pad(f'<a href="/contests/{t}00/box_score">g</a>') for t in range(1, 8)})
    fetch, calls = _site(pages)
    got: "set[str]" = set()
    for i in range(3):
        got |= set(discover_season(2027, fetch_fn=fetch, save_dir=tmp_path, refresh=True, team_shard=(i, 3)))
    team_fetches = [p for p, _ in calls if p.startswith("teams/")]
    assert sorted(team_fetches) == sorted(set(team_fetches))  # no page fetched twice
    assert len(team_fetches) == 7
    assert sum(1 for p, _ in calls if p == tl) == 1  # only shard 0 refreshes the list
    assert got == {f"{t}00" for t in range(1, 8)}


def test_sharded_refresh_without_skip_games_is_refused() -> None:
    from ncaa_mfb_raw_scrape import mfb_run

    with pytest.raises(SystemExit) as exc:
        mfb_run.main(["--refresh-discovery", "--shard", "1/4"])
    assert exc.value.code == 2


def test_sharded_rosters_walk_disjoint_team_slices(monkeypatch, tmp_path: Path) -> None:
    from ncaa_mfb_raw_scrape import mfb_run

    teams = [str(t) for t in range(1, 8)]
    walked: "list[list[str]]" = []
    monkeypatch.setenv("NCAA_VENDOR", "stub")
    monkeypatch.setattr(mfb_run, "vendor_fetch_fn", lambda *a, **k: None)
    monkeypatch.setattr(mfb_run, "discover_teams", lambda *a, **k: teams)
    monkeypatch.setattr(mfb_run, "discover_season", lambda *a, **k: [])
    monkeypatch.setattr(
        mfb_run, "capture_rosters", lambda ids, *a, **k: walked.append(list(ids)) or {}
    )
    for i in range(3):
        argv = ["--out", str(tmp_path), "--refresh-discovery", "--skip-games", "--rosters"]
        assert mfb_run.main([*argv, "--shard", f"{i}/3"]) == 0

    flat = [t for ids in walked for t in ids]
    assert sorted(flat) == sorted(teams)  # every team once, no shard repeats another's
