"""NCAA <-> ESPN game crosswalk for MFB (stage 06).

Writes ``mfb/xwalk/espn_game_id/{ay}.json`` -- one row per NCAA contest:
``{contest_id, espn_game_id, match_method}`` -- plus the derived team map
``mfb/xwalk/espn_team_id.json`` (``{ncaa_team_id: espn_team_id}``, cumulative).

Mirrors the hoops twins' engine (sdv-py ``scrape.ncaa.espn_game_xwalk``) with
one football-specific twist: hoops schedules already carry ESPN team ids (the
bundled crosswalks), football has NO NCAA<->ESPN team crosswalk. So this
BOOTSTRAPS one:

1. score tiers -- match games on ``(date, home/away score)`` exactly, then
   date +/-1 (late kickoffs / UTC vs ET), then the unordered score pair
   (neutral-site orientation flips). Every tier drops keys resolving to more
   than one game on either side, so ambiguity lands on NULL, never a guess.
2. team map -- from the score-matched games, vote ``ncaa_team_id ->
   espn_team_id`` per side; a mapping needs >= 2 co-occurrences and >= 90%
   agreement to be believed.
3. id tiers -- the twins' four tiers (exact / date_window / unordered_pair /
   single_team) on the still-unmatched contests, using the voted team map.

A contest that survives everything keeps a NULL ``espn_game_id``; an ESPN game
id claimed by two contests is voided on both (same rule as the twins).

ESPN side: ``sportsdataverse.cfb.load_cfb_schedule(seasons=[ay - 1])`` -- the
one network call per season; the written json is then a pure offline read for
stage 03's enrichment. Seasons are STARTING-year there (2025 = fall-2025);
this repo's tree stays academic-year keyed (ay = season + 1).

Usage::

    ./.venv-or-sdv-py-python python/ncaa_mfb_06_xwalk_build.py --academic-year 2026
    python python/ncaa_mfb_06_xwalk_build.py --all          # 2014..2026
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Tier labels, in the order they are attempted.
MATCH_METHODS = (
    "score_exact",
    "score_window",
    "score_pair",
    "exact",
    "date_window",
    "unordered_pair",
    "single_team",
)


def xwalk_path(root: Path, academic_year: int) -> Path:
    return root / "mfb" / "xwalk" / "espn_game_id" / f"{academic_year}.json"


def team_map_path(root: Path) -> Path:
    return root / "mfb" / "xwalk" / "espn_team_id.json"


def _utf8_id(column: str) -> pl.Expr:
    """Int-bearing id column -> Utf8, via Int64 so a float never renders "123.0"."""
    return pl.col(column).cast(pl.Int64).cast(pl.Utf8)


def ncaa_schedule_side(root: Path, academic_year: int) -> pl.DataFrame:
    """One row per contest: date, oriented NCAA team ids + scores.

    Orientation comes from the schedule page's opponent prefix: ``@ X`` means
    the row's own team was AWAY. Both of a contest's two rows describe the same
    game; prefer the copy with both scores present.
    """
    schema = {
        "contest_id": pl.Utf8,
        "game_date": pl.Date,
        "ncaa_home_id": pl.Utf8,
        "ncaa_away_id": pl.Utf8,
        "home_points": pl.Int64,
        "away_points": pl.Int64,
    }
    path = root / "mfb" / "schedules" / "parquet" / f"{academic_year}.parquet"
    if not path.is_file():
        return pl.DataFrame(schema=schema)
    raw = pl.read_parquet(path)
    is_away = pl.col("opponent").str.strip_chars().str.starts_with("@")
    frame = raw.select(
        pl.col("contest_id").cast(pl.Utf8),
        pl.col("date").str.to_date("%m/%d/%Y", strict=False).alias("game_date"),
        pl.when(is_away)
        .then(pl.col("opponent_id"))
        .otherwise(pl.col("team_id"))
        .cast(pl.Utf8)
        .alias("ncaa_home_id"),
        pl.when(is_away)
        .then(pl.col("team_id"))
        .otherwise(pl.col("opponent_id"))
        .cast(pl.Utf8)
        .alias("ncaa_away_id"),
        pl.when(is_away)
        .then(pl.col("opponent_score"))
        .otherwise(pl.col("team_score"))
        .cast(pl.Int64)
        .alias("home_points"),
        pl.when(is_away)
        .then(pl.col("team_score"))
        .otherwise(pl.col("opponent_score"))
        .cast(pl.Int64)
        .alias("away_points"),
    )
    frame = (
        frame.filter(pl.col("contest_id").is_not_null())
        .sort(pl.col("home_points").is_null() | pl.col("away_points").is_null())
        .unique(subset=["contest_id"], keep="first", maintain_order=True)
        .sort("contest_id")
    )
    # Early-era schedule pages (<= ay 2014) carry no result column at all; the
    # built linescore dataset holds the official finals AND explicit home/away,
    # so fall back to it wherever the schedule row lacks scores.
    ls_path = root / "mfb" / "datasets" / str(academic_year) / "linescore.parquet"
    if ls_path.is_file() and frame.get_column("home_points").null_count():
        ls = (
            pl.read_parquet(ls_path)
            .select("contest_id", "home_away", "final")
            .drop_nulls()
            .unique(subset=["contest_id", "home_away"], keep="first")
            .pivot(on="home_away", index="contest_id", values="final")
        )
        if {"home", "away"} <= set(ls.columns):
            frame = frame.join(
                ls.select(
                    pl.col("contest_id").cast(pl.Utf8),
                    pl.col("home").cast(pl.Int64).alias("_ls_home"),
                    pl.col("away").cast(pl.Int64).alias("_ls_away"),
                ),
                on="contest_id",
                how="left",
            ).select(
                "contest_id",
                "game_date",
                "ncaa_home_id",
                "ncaa_away_id",
                pl.coalesce("home_points", "_ls_home").alias("home_points"),
                pl.coalesce("away_points", "_ls_away").alias("away_points"),
            )
    return frame


def espn_schedule_side(academic_year: int) -> pl.DataFrame:
    """ESPN/cfbfastR season schedule (the one network call). season = ay - 1."""
    from sportsdataverse.cfb import load_cfb_schedule

    df = load_cfb_schedule(seasons=[academic_year - 1])
    if df.height == 0:
        return df
    return (
        df.select(
            _utf8_id("game_id").alias("espn_game_id"),
            pl.col("start_date")
            .cast(pl.Utf8)
            .str.slice(0, 10)
            .str.to_date("%Y-%m-%d", strict=False)
            .alias("game_date"),
            _utf8_id("home_id").alias("espn_home_id"),
            _utf8_id("away_id").alias("espn_away_id"),
            pl.col("home_points").cast(pl.Int64),
            pl.col("away_points").cast(pl.Int64),
        )
        .drop_nulls(["espn_game_id", "game_date"])
        .unique(subset=["espn_game_id"], keep="first")
    )


def _unambiguous(frame: pl.DataFrame, keys: List[str]) -> pl.DataFrame:
    """``keys -> espn_game_id``, keeping only keys resolving to exactly one game."""
    return (
        frame.group_by(keys)
        .agg(
            pl.col("espn_game_id").n_unique().alias("_candidates"),
            pl.col("espn_game_id").first().alias("espn_game_id"),
        )
        .filter(pl.col("_candidates") == 1)
        .drop("_candidates")
    )


def _apply_tier(
    pending: pl.DataFrame, lookup: pl.DataFrame, keys: List[str], method: str
):
    # NCAA-side keys must also be unambiguous: two contests sharing a key would
    # both grab the same ESPN game -- the collision voider would then kill a
    # pair the next tier could have split correctly.
    counted = pending.with_columns(pl.len().over(keys).alias("_n"))
    joinable = counted.filter(
        (pl.col("_n") == 1) & pl.all_horizontal(pl.col(k).is_not_null() for k in keys)
    ).drop("_n")
    rest = counted.filter(
        (pl.col("_n") > 1) | ~pl.all_horizontal(pl.col(k).is_not_null() for k in keys)
    ).drop("_n")
    joined = joinable.join(lookup, on=keys, how="left")
    matched = joined.filter(pl.col("espn_game_id").is_not_null()).with_columns(
        pl.lit(method, dtype=pl.Utf8).alias("match_method")
    )
    still = pl.concat(
        [joined.filter(pl.col("espn_game_id").is_null()).drop("espn_game_id"), rest]
    )
    return matched, still


def _vote_team_map(matched: pl.DataFrame, espn: pl.DataFrame) -> "dict[str, str]":
    """ncaa_team_id -> espn_team_id by per-side majority vote over score-matched games."""
    joined = matched.join(
        espn.select("espn_game_id", "espn_home_id", "espn_away_id"), on="espn_game_id"
    )
    pairs = pl.concat(
        [
            joined.select(
                pl.col("ncaa_home_id").alias("ncaa"),
                pl.col("espn_home_id").alias("espn"),
            ),
            joined.select(
                pl.col("ncaa_away_id").alias("ncaa"),
                pl.col("espn_away_id").alias("espn"),
            ),
        ]
    ).drop_nulls()
    votes = (
        pairs.group_by("ncaa", "espn")
        .agg(pl.len().alias("n"))
        .with_columns(
            pl.col("n").sum().over("ncaa").alias("total"),
            pl.col("n").rank("dense", descending=True).over("ncaa").alias("rank"),
        )
        .filter(
            (pl.col("rank") == 1)
            & (pl.col("n") >= 2)
            & (pl.col("n") / pl.col("total") >= 0.9)
        )
    )
    return dict(
        zip(votes.get_column("ncaa").to_list(), votes.get_column("espn").to_list())
    )


def build_season_xwalk(
    root: Path, academic_year: int, espn: Optional[pl.DataFrame] = None
):
    """Returns (xwalk_frame, team_map). xwalk: contest_id / espn_game_id / match_method."""
    out_schema = {
        "contest_id": pl.Utf8,
        "espn_game_id": pl.Utf8,
        "match_method": pl.Utf8,
    }
    ncaa = ncaa_schedule_side(root, academic_year)
    if ncaa.height == 0:
        return pl.DataFrame(schema=out_schema), {}
    espn = espn if espn is not None else espn_schedule_side(academic_year)
    if espn.height == 0:
        return (
            ncaa.select(
                "contest_id",
                pl.lit(None, dtype=pl.Utf8).alias("espn_game_id"),
                pl.lit(None, dtype=pl.Utf8).alias("match_method"),
            ),
            {},
        )

    windowed = pl.concat(
        [espn.with_columns(pl.col("game_date") + pl.duration(days=d)) for d in (-1, 1)]
    )
    score_lo = pl.min_horizontal("home_points", "away_points").alias("score_lo")
    score_hi = pl.max_horizontal("home_points", "away_points").alias("score_hi")
    _SCORE = ["game_date", "home_points", "away_points"]
    _SPAIR = ["game_date", "score_lo", "score_hi"]

    pending = ncaa
    matched: List[pl.DataFrame] = []

    def run_tier(method: str, keys: List[str], lookup: pl.DataFrame, extra=()):
        nonlocal pending
        if pending.height == 0:
            return
        prepared = pending.with_columns(*extra) if extra else pending
        hit, pending_new = _apply_tier(
            prepared, _unambiguous(lookup, keys), keys, method
        )
        if hit.height:
            matched.append(hit.select("contest_id", "espn_game_id", "match_method"))
        pending = pending_new.select(ncaa.columns)

    # --- pass 1: score tiers (no team ids needed) ---------------------------
    run_tier("score_exact", _SCORE, espn)
    run_tier("score_window", _SCORE, windowed)
    run_tier(
        "score_pair",
        _SPAIR,
        espn.with_columns(score_lo, score_hi),
        (score_lo, score_hi),
    )

    # --- pass 2: vote the team map from what pass 1 matched -----------------
    score_matched = (
        pl.concat(matched).join(ncaa, on="contest_id") if matched else pl.DataFrame()
    )
    team_map = _vote_team_map(score_matched, espn) if score_matched.height else {}

    # --- pass 3: the twins' id tiers, using the voted map --------------------
    if team_map and pending.height:
        mapper = pl.DataFrame(
            {"ncaa": list(team_map.keys()), "espn": list(team_map.values())}
        )
        pending = pending.join(
            mapper.rename({"ncaa": "ncaa_home_id", "espn": "home_espn_team_id"}),
            on="ncaa_home_id",
            how="left",
        ).join(
            mapper.rename({"ncaa": "ncaa_away_id", "espn": "away_espn_team_id"}),
            on="ncaa_away_id",
            how="left",
        )
        base_cols = [*ncaa.columns, "home_espn_team_id", "away_espn_team_id"]
        espn_ids = espn.rename(
            {"espn_home_id": "home_espn_team_id", "espn_away_id": "away_espn_team_id"}
        )
        windowed_ids = pl.concat(
            [
                espn_ids.with_columns(pl.col("game_date") + pl.duration(days=d))
                for d in (-1, 1)
            ]
        )
        team_lo = pl.min_horizontal("home_espn_team_id", "away_espn_team_id").alias(
            "team_lo"
        )
        team_hi = pl.max_horizontal("home_espn_team_id", "away_espn_team_id").alias(
            "team_hi"
        )
        espn_long = pl.concat(
            [
                espn_ids.select(
                    "espn_game_id", "game_date", pl.col(side).alias("espn_team_id")
                )
                for side in ("home_espn_team_id", "away_espn_team_id")
            ]
        )
        _EXACT = ["game_date", "home_espn_team_id", "away_espn_team_id"]
        _PAIR = ["game_date", "team_lo", "team_hi"]
        _SINGLE = ["game_date", "espn_team_id"]

        def run_id_tier(method, keys, lookup, extra=()):
            nonlocal pending
            if pending.height == 0:
                return
            prepared = pending.with_columns(*extra) if extra else pending
            hit, pending_new = _apply_tier(
                prepared, _unambiguous(lookup, keys), keys, method
            )
            if hit.height:
                matched.append(hit.select("contest_id", "espn_game_id", "match_method"))
            pending = pending_new.select(base_cols)

        run_id_tier("exact", _EXACT, espn_ids)
        run_id_tier("date_window", _EXACT, windowed_ids)
        run_id_tier(
            "unordered_pair",
            _PAIR,
            espn_ids.with_columns(team_lo, team_hi),
            (team_lo, team_hi),
        )
        run_id_tier(
            "single_team",
            _SINGLE,
            espn_long,
            (
                pl.coalesce("home_espn_team_id", "away_espn_team_id").alias(
                    "espn_team_id"
                ),
            ),
        )

    unmatched = pending.select(
        "contest_id",
        pl.lit(None, dtype=pl.Utf8).alias("espn_game_id"),
        pl.lit(None, dtype=pl.Utf8).alias("match_method"),
    )
    result = pl.concat([*matched, unmatched]) if matched else unmatched

    # One ESPN game belongs to one contest; a collision voids both claimants.
    contested = (
        result.drop_nulls("espn_game_id")
        .group_by("espn_game_id")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
        .get_column("espn_game_id")
        .to_list()
    )
    if contested:
        clash = pl.col("espn_game_id").is_in(contested)
        result = result.with_columns(
            pl.when(clash)
            .then(None)
            .otherwise(pl.col("espn_game_id"))
            .alias("espn_game_id"),
            pl.when(clash)
            .then(None)
            .otherwise(pl.col("match_method"))
            .alias("match_method"),
        )
    return result.sort("contest_id"), team_map


def write_season_xwalk(root: Path, academic_year: int, frame: pl.DataFrame) -> Path:
    path = xwalk_path(root, academic_year)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(frame.to_dicts()), encoding="utf-8")
    tmp.replace(path)
    return path


def merge_team_map(root: Path, team_map: "dict[str, str]") -> Path:
    """Merge this season's voted map into the cumulative file (existing wins on conflict)."""
    path = team_map_path(root)
    existing: "dict[str, str]" = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
    merged = {**team_map, **existing}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(sorted(merged.items())), indent=0, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_espn_game_index(root: Path, academic_year: int) -> "dict[str, str]":
    """``{contest_id: espn_game_id}`` for one season -- pure offline read."""
    path = xwalk_path(Path(root), academic_year)
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {r["contest_id"]: r["espn_game_id"] for r in rows if r.get("espn_game_id")}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--academic-year", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="build 2014..2026")
    ap.add_argument("--root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)
    root = Path(args.root)
    years = range(2014, 2027) if args.all else [args.academic_year]
    if years == [None]:
        ap.error("--academic-year or --all required")
    for ay in years:
        frame, team_map = build_season_xwalk(root, ay)
        write_season_xwalk(root, ay, frame)
        merge_team_map(root, team_map)
        n = frame.height
        hit = frame.get_column("espn_game_id").is_not_null().sum() if n else 0
        by = (
            frame.drop_nulls("match_method")
            .group_by("match_method")
            .agg(pl.len())
            .to_dicts()
            if n
            else []
        )
        print(f"ay{ay}: {hit}/{n} matched  {by}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
