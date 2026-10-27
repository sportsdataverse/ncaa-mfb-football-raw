"""Stage 06: score-less early-era schedules take finals from the parsed bundles."""

from __future__ import annotations

import gzip
import json

import ncaa_mfb_06_xwalk_build as xw
import polars as pl


def test_schedule_side_fills_scores_from_bundles(tmp_path) -> None:
    sched = tmp_path / "mfb" / "schedules" / "parquet"
    sched.mkdir(parents=True)
    pl.DataFrame(
        {
            "team_id": ["10", "20"],
            "date": ["08/29/2013", "08/29/2013"],
            "opponent_id": ["20", "10"],
            "opponent": ["@ B", "A"],  # team 10 is AWAY
            "team_score": [None, None],
            "opponent_score": [None, None],
            "contest_id": ["1", "1"],
        },
        schema_overrides={"team_score": pl.Int64, "opponent_score": pl.Int64},
    ).write_parquet(sched / "2014.parquet")
    bundles = tmp_path / "mfb" / "json"
    bundles.mkdir(parents=True)
    teams = [
        {"ncaa_team_id": "10", "home_away": "away", "final": 7},
        {"ncaa_team_id": "20", "home_away": "home", "final": 38},
    ]
    (bundles / "1.json.gz").write_bytes(gzip.compress(json.dumps({"teams": teams}).encode()))

    row = xw.ncaa_schedule_side(tmp_path, 2014).row(0, named=True)
    assert (row["ncaa_home_id"], row["home_points"]) == ("20", 38)
    assert (row["ncaa_away_id"], row["away_points"]) == ("10", 7)
