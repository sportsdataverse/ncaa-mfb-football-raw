"""Raw bundle -> parsed + enriched per-game JSON (stage 03).

Reads the captured 6-tab HTML bundles ``mfb/raw/{ay}/{contest_id}.json.gz``,
parses every tab via the graduated sdv-py ``cfb_ncaa_*`` parsers, and writes
``mfb/json/{contest_id}.json.gz`` -- the processed tree the mbb/wbb twins keep
under ``{lg}/json/``. FULLY OFFLINE.

Payload (one game)::

    {
      "contest_id": "6386512",
      "academic_year": 2026, "season": 2025,          # season = STARTING year
      "espn_game_id": "401752676" | null,             # from stage 06's xwalk
      "teams": [ {team, home_away, ncaa_team_id, espn_team_id, final} x2 ],
      "pbp": [...], "drives": [...], "linescore": [...],
      "scoring_summary": [...], "team_stats": [...], "officials": [...],
      "player_stats": {category: [...]}
    }

Enrichment is a pure offline read of ``mfb/xwalk/`` (stage 06) -- run that
first. Gzipped (unlike the plain-json twins) deliberately: the hoops trees
weigh 134G plain and their .git carries it forever.

Usage::

    ./scripts/run_03_parse.sh --academic-year 2026 [--workers 8] [--force]
    ./scripts/run_03_parse.sh --all
"""

from __future__ import annotations

import argparse
import gzip
import json
from multiprocessing import get_context
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def parsed_path(root: Path, contest_id: str) -> Path:
    return root / "mfb" / "json" / f"{contest_id}.json.gz"


def _teams_block(
    linescore_rows: "list[dict]",
    ncaa_ids: "dict[str, dict]",
    team_map: "dict[str, str]",
    espn_game_id: "str | None",
) -> "list[dict]":
    """One identity row per side, mirroring the twins' ``teams`` block."""
    out = []
    seen = set()
    for r in linescore_rows:
        key = (r.get("team"), r.get("home_away"))
        if not r.get("team") or key in seen:
            continue
        seen.add(key)
        ncaa_id = ncaa_ids.get(r["home_away"], {}).get("ncaa_team_id")
        out.append(
            {
                "team": r["team"],
                "home_away": r["home_away"],
                "ncaa_team_id": ncaa_id,
                "espn_team_id": team_map.get(ncaa_id) if ncaa_id else None,
                "final": r.get("final"),
                "espn_game_id": espn_game_id,
            }
        )
    return out


def parse_contest(
    args: "tuple[str, str, int, dict, dict, dict, bool]",
) -> "tuple[str, str]":
    """Worker: one bundle -> one parsed json. Returns (contest_id, status)."""
    root_s, cid, ay, game_index, team_map, ncaa_ids, force = args
    root = Path(root_s)
    out = parsed_path(root, cid)
    if out.exists() and not force:
        return cid, "skipped"
    raw = root / "mfb" / "raw" / str(ay) / f"{cid}.json.gz"
    if not raw.exists():
        return cid, "missing_raw"
    from sportsdataverse.cfb import (
        parse_cfb_ncaa_drives,
        parse_cfb_ncaa_linescore,
        parse_cfb_ncaa_officials,
        parse_cfb_ncaa_player_stats,
        parse_cfb_ncaa_scoring_summary,
        parse_cfb_ncaa_team_stats,
    )
    from sportsdataverse.cfb.cfb_ncaa_pbp import parse_cfb_ncaa_pbp

    try:
        with gzip.open(raw, "rt", encoding="utf-8") as fh:
            bundle = json.load(fh)
        pbp_html = bundle.get("play_by_play") or ""
        box_html = bundle.get("box_score") or ""
        linescore = parse_cfb_ncaa_linescore(box_html, contest_id=cid).to_dicts()
        payload = {
            "contest_id": cid,
            "academic_year": ay,
            "season": ay - 1,
            "espn_game_id": game_index.get(cid),
            "teams": _teams_block(
                linescore, ncaa_ids.get(cid, {}), team_map, game_index.get(cid)
            ),
            "pbp": parse_cfb_ncaa_pbp(pbp_html, contest_id=cid).to_dicts(),
            "drives": parse_cfb_ncaa_drives(
                bundle.get("drives") or "", contest_id=cid
            ).to_dicts(),
            "linescore": linescore,
            "scoring_summary": parse_cfb_ncaa_scoring_summary(
                box_html, contest_id=cid
            ).to_dicts(),
            "team_stats": parse_cfb_ncaa_team_stats(
                bundle.get("team_stats") or "", contest_id=cid
            ).to_dicts(),
            "officials": parse_cfb_ncaa_officials(
                bundle.get("officials") or "", contest_id=cid
            ).to_dicts(),
            "player_stats": {
                cat: frame.to_dicts()
                for cat, frame in parse_cfb_ncaa_player_stats(
                    bundle.get("individual_stats") or "", contest_id=cid
                ).items()
            },
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(payload, fh, default=str)
        tmp.replace(out)
        return cid, "parsed"
    except Exception as exc:  # noqa: BLE001 -- one bad bundle must not kill the sweep
        return cid, f"error: {type(exc).__name__}: {exc}"


def _season_inputs(root: Path, ay: int) -> "tuple[list[str], dict, dict, dict]":
    import polars as pl

    from ncaa_mfb_06_xwalk_build import load_espn_game_index, team_map_path

    cids = sorted(
        p.name.split(".")[0] for p in (root / "mfb" / "raw" / str(ay)).glob("*.json.gz")
    )
    game_index = load_espn_game_index(root, ay)
    tm_path = team_map_path(root)
    team_map = (
        json.loads(tm_path.read_text(encoding="utf-8")) if tm_path.is_file() else {}
    )
    # contest -> {"home": {...}, "away": {...}} NCAA ids, from the schedule's "@" orientation
    ncaa_ids: "dict[str, dict]" = {}
    sched = root / "mfb" / "schedules" / "parquet" / f"{ay}.parquet"
    if sched.is_file():
        raw = pl.read_parquet(sched)
        is_away = pl.col("opponent").str.strip_chars().str.starts_with("@")
        oriented = raw.select(
            pl.col("contest_id").cast(pl.Utf8),
            pl.when(is_away)
            .then(pl.col("opponent_id"))
            .otherwise(pl.col("team_id"))
            .cast(pl.Utf8)
            .alias("home_id"),
            pl.when(is_away)
            .then(pl.col("team_id"))
            .otherwise(pl.col("opponent_id"))
            .cast(pl.Utf8)
            .alias("away_id"),
        ).unique(subset=["contest_id"], keep="first")
        for r in oriented.to_dicts():
            ncaa_ids[r["contest_id"]] = {
                "home": {"ncaa_team_id": r["home_id"]},
                "away": {"ncaa_team_id": r["away_id"]},
            }
    return cids, game_index, team_map, ncaa_ids


def run_season(root: Path, ay: int, workers: int, force: bool) -> "dict[str, int]":
    cids, game_index, team_map, ncaa_ids = _season_inputs(root, ay)
    jobs = [(str(root), cid, ay, game_index, team_map, ncaa_ids, force) for cid in cids]
    stats: "dict[str, int]" = {}
    # spawn, not fork: the parent has polars (Rayon threads) loaded by the time
    # the pool starts, and forked children inherit its lock state and deadlock
    # at 0% CPU. Spawned children import everything fresh.
    with get_context("spawn").Pool(workers) as pool:
        for cid, status in pool.imap_unordered(parse_contest, jobs, chunksize=16):
            key = status.split(":")[0]
            stats[key] = stats.get(key, 0) + 1
            if status.startswith("error"):
                print(f"  {cid} {status}", flush=True)
    print(f"ay{ay}: {stats}", flush=True)
    return stats


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--academic-year", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="parse 2014..2026")
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--force", action="store_true", help="re-parse even when output exists"
    )
    args = ap.parse_args(argv)
    if not args.all and args.academic_year is None:
        ap.error("--academic-year or --all required")
    years = range(2014, 2027) if args.all else [args.academic_year]
    for ay in years:
        run_season(Path(args.root), ay, args.workers, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
