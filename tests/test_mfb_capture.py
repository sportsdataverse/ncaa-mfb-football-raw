"""Offline capture tests: fake fetch_fn + tmp out_dir (no network)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from ncaa_mfb_raw_scrape.mfb_capture import bundle_path, capture_contest, capture_season, is_captured

_REAL_PBP = '<div class="drives">' + "x" * 60_000  # >40 KB + the 'drives' marker
_REAL_BOX = "<table>" + "y" * 20_000


def _real_fetch(path: str) -> str:
    return _REAL_PBP if "play_by_play" in path else _REAL_BOX


def test_capture_writes_bundle_then_skips(tmp_path: Path) -> None:
    assert capture_contest(_real_fetch, "111", tmp_path, 2026) == "captured"
    assert is_captured("111", tmp_path, 2026)
    # bundle content
    with gzip.open(bundle_path("111", tmp_path, 2026), "rt", encoding="utf-8") as fh:
        b = json.load(fh)
    assert (
        b["contest_id"] == "111"
        and b["play_by_play"] == _REAL_PBP
        and b["box_score"] == _REAL_BOX
    )
    assert "captured_at" in b
    # idempotent
    assert capture_contest(_real_fetch, "111", tmp_path, 2026) == "skipped"


def test_stub_pbp_is_failed_not_written(tmp_path: Path) -> None:
    assert capture_contest(lambda _p: "tiny stub", "222", tmp_path, 2026) == "failed"
    assert not is_captured("222", tmp_path, 2026)


def test_box_is_best_effort(tmp_path: Path) -> None:
    def fetch(path: str) -> str:
        if "box_score" in path:
            raise RuntimeError("box fetch failed")
        return _REAL_PBP

    assert (
        capture_contest(fetch, "333", tmp_path, 2026) == "captured"
    )  # pbp landed; box optional
    with gzip.open(bundle_path("333", tmp_path, 2026), "rt", encoding="utf-8") as fh:
        assert json.load(fh)["box_score"] is None


def test_all_game_tabs_are_bundled(tmp_path: Path) -> None:
    # each tab fetch returns a distinct marker so we can prove all landed.
    def fetch(path: str) -> str:
        if "play_by_play" in path:
            return _REAL_PBP
        tab = path.rsplit("/", 1)[-1]  # box_score / team_stats / ...
        return f"<table>TAB:{tab}"

    assert capture_contest(fetch, "444", tmp_path, 2026) == "captured"
    with gzip.open(bundle_path("444", tmp_path, 2026), "rt", encoding="utf-8") as fh:
        b = json.load(fh)
    for tab in ("box_score", "team_stats", "individual_stats", "drives", "officials"):
        assert b[tab] == f"<table>TAB:{tab}", tab


def test_season_stats_and_chunking(tmp_path: Path) -> None:
    ids = ["1", "2", "3", "4"]
    stats = capture_season(ids, _real_fetch, tmp_path, 2026, max_contests=2)
    assert stats["captured"] == 2  # stopped after the chunk
    # resume: the 2 captured are skipped, the rest complete
    stats2 = capture_season(ids, _real_fetch, tmp_path, 2026)
    assert stats2["skipped"] == 2 and stats2["captured"] == 2


def test_failure_breaker_trips(tmp_path: Path) -> None:
    def always_fail(_path: str) -> str:
        raise RuntimeError("banned")

    with pytest.raises(RuntimeError, match="breaker tripped"):
        capture_season(
            [str(i) for i in range(100)],
            always_fail,
            tmp_path,
            2026,
            max_consecutive_failures=5,
        )
