"""Offline dataset build: persisted HTML + game bundles -> tidy parquet.

Fully offline (reads ``mfb/**`` written by the capture stages; no network).

Outputs (``{root}/mfb/...``, ``{ay}`` = academic year, ending-year convention):

* ``teams/parquet/{ay}_div{d}.parquet``      -- team id/name per division
* ``schedules/parquet/{ay}.parquet``          -- schedule master (one row per
  team-game: date, opponent, result, contest_id, attendance)
* ``datasets/{ay}/pbp.parquet``               -- structural NCAA pbp (49 cols)
* ``datasets/{ay}/pbp_cfbfastr.parquet``      -- cfbfastR-named play frame
* ``datasets/{ay}/team_stats.parquet``        -- per-quarter team box
* ``datasets/{ay}/player_stats_{cat}.parquet``-- individual box, one per category
* ``datasets/{ay}/drives.parquet``            -- drive chart
* ``datasets/{ay}/officials.parquet``         -- officiating crews
* ``datasets/{ay}/linescore.parquet``         -- linescore + game info

Parsers come from sdv-py (``cfb_ncaa_pbp`` / ``cfb_ncaa_box``); the
cfbfastR-name mapping is the local prototype (:mod:`mfb_cfbfastr`).
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Optional

import polars as pl

_RESULT_RE = re.compile(r"^([WLT])\s+(\d+)-(\d+)")
_TEAM_HREF_RE = re.compile(r"/teams/(\d+)")
_CONTEST_HREF_RE = re.compile(r"/contests/(\d+)/")


def parse_team_list(html: str) -> pl.DataFrame:
    """Team list page -> one row per team (``team_id``, ``team_name``)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    rows, seen = [], set()
    for a in soup.select('a[href*="/teams/"]'):
        m = _TEAM_HREF_RE.search(a.get("href") or "")
        name = a.get_text(" ", strip=True)
        if m and name and m.group(1) not in seen:
            seen.add(m.group(1))
            rows.append({"team_id": m.group(1), "team_name": name})
    return pl.DataFrame(rows, schema={"team_id": pl.Utf8, "team_name": pl.Utf8})


def parse_team_schedule(html: str, team_id: "Optional[str]" = None) -> pl.DataFrame:
    """Team page schedule table -> one row per game.

    Columns: ``team_id``, ``team_name`` (from the page's card header, record
    stripped), ``date`` (MM/DD/YYYY as printed), ``opponent_id``/``opponent``,
    ``result`` (raw, e.g. ``"W 42-35 (2OT)"``), ``outcome`` (W/L/T),
    ``team_score``/``opponent_score``, ``contest_id``, ``attendance``.
    """
    from bs4 import BeautifulSoup

    schema = {
        "team_id": pl.Utf8,
        "team_name": pl.Utf8,
        "date": pl.Utf8,
        "opponent_id": pl.Utf8,
        "opponent": pl.Utf8,
        "result": pl.Utf8,
        "outcome": pl.Utf8,
        "team_score": pl.Int64,
        "opponent_score": pl.Int64,
        "contest_id": pl.Utf8,
        "attendance": pl.Int64,
    }
    soup = BeautifulSoup(html or "", "html.parser")
    header = soup.select_one("div.card-header")
    team_name = None
    if header:
        team_name = (
            re.sub(r"\s*\([\d\-]+\)\s*$", "", header.get_text(" ", strip=True)) or None
        )
    table = soup.find("table")
    rows: "list[dict]" = []
    if table is not None:
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if len(cells) < 3 or not cells[0].get_text(strip=True):
                continue
            opp_a = cells[1].find("a")
            res_a = cells[2].find("a")
            opp_m = _TEAM_HREF_RE.search(opp_a.get("href") or "") if opp_a else None
            con_m = _CONTEST_HREF_RE.search(res_a.get("href") or "") if res_a else None
            result = cells[2].get_text(" ", strip=True) or None
            rm = _RESULT_RE.match(result or "")
            att = (cells[3].get_text(strip=True) if len(cells) > 3 else "").replace(
                ",", ""
            )
            rows.append(
                {
                    "team_id": team_id,
                    "team_name": team_name,
                    "date": cells[0].get_text(" ", strip=True),
                    "opponent_id": opp_m.group(1) if opp_m else None,
                    "opponent": cells[1].get_text(" ", strip=True) or None,
                    "result": result,
                    "outcome": rm.group(1) if rm else None,
                    "team_score": int(rm.group(2)) if rm else None,
                    "opponent_score": int(rm.group(3)) if rm else None,
                    "contest_id": con_m.group(1) if con_m else None,
                    "attendance": int(att) if att.isdigit() else None,
                }
            )
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


def build_schedule_master(root: "str | Path", academic_year: int) -> pl.DataFrame:
    """All persisted team pages for a season -> one schedule-master parquet."""
    root = Path(root)
    frames = []
    for p in sorted(
        (root / "mfb" / "schedules" / "html" / str(academic_year)).glob("*.html")
    ):
        df = parse_team_schedule(p.read_text(encoding="utf-8"), team_id=p.stem)
        if df.height:
            frames.append(df)
    master = pl.concat(frames) if frames else parse_team_schedule("")
    master = master.with_columns(pl.lit(academic_year).alias("academic_year"))
    out = root / "mfb" / "schedules" / "parquet" / f"{academic_year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    master.write_parquet(out)
    return master


def build_teams(root: "str | Path", academic_year: int) -> pl.DataFrame:
    """Persisted team-list pages -> teams parquet (one file per division found)."""
    root = Path(root)
    frames = []
    for p in sorted(
        (root / "mfb" / "teams" / "html").glob(f"{academic_year}_div*.html")
    ):
        division = int(p.stem.split("div")[-1])
        df = parse_team_list(p.read_text(encoding="utf-8")).with_columns(
            pl.lit(academic_year).alias("academic_year"),
            pl.lit(division).alias("division"),
        )
        if df.height:
            frames.append(df)
            out = (
                root
                / "mfb"
                / "teams"
                / "parquet"
                / f"{academic_year}_div{division}.parquet"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            df.write_parquet(out)
    return (
        pl.concat(frames)
        if frames
        else parse_team_list("").with_columns(
            pl.lit(academic_year).alias("academic_year"), pl.lit(0).alias("division")
        )
    )


def build_game_datasets(
    root: "str | Path", academic_year: int, *, season: "Optional[int]" = None
) -> "dict[str, int]":
    """Every captured bundle -> per-dataset season parquet under ``mfb/datasets/{ay}/``.

    ``season`` (fall year, e.g. 2025 for ay 2026) is written into the cfbfastR
    frame; defaults to ``academic_year - 1``.
    """
    import sys

    sdv_py = "/mnt/sdv_repos/sdv-py"
    if sdv_py not in sys.path:
        sys.path.insert(0, sdv_py)
    from mfb_cfbfastr import to_cfbfastr
    from sportsdataverse.cfb.cfb_ncaa_box import (
        parse_cfb_ncaa_drives,
        parse_cfb_ncaa_linescore,
        parse_cfb_ncaa_officials,
        parse_cfb_ncaa_player_stats,
        parse_cfb_ncaa_team_stats,
    )
    from sportsdataverse.cfb.cfb_ncaa_pbp import parse_cfb_ncaa_pbp

    root = Path(root)
    season = season if season is not None else academic_year - 1
    # season scoping: only bundles whose contest_id is in this season's schedule master
    sched_path = root / "mfb" / "schedules" / "parquet" / f"{academic_year}.parquet"
    season_ids = None
    if sched_path.exists():
        season_ids = set(
            pl.read_parquet(sched_path).get_column("contest_id").drop_nulls().to_list()
        )
    acc: "dict[str, list[pl.DataFrame]]" = {}
    player_acc: "dict[str, list[pl.DataFrame]]" = {}
    n = 0
    for p in sorted((root / "mfb" / "json").glob("*.json.gz")):
        cid = p.stem.split(".")[0]
        if season_ids is not None and cid not in season_ids:
            continue
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            bundle = json.load(fh)
        n += 1
        pbp = parse_cfb_ncaa_pbp(bundle.get("play_by_play") or "", contest_id=cid)
        drives = parse_cfb_ncaa_drives(bundle.get("drives") or "", contest_id=cid)
        linescore = parse_cfb_ncaa_linescore(
            bundle.get("box_score") or "", contest_id=cid
        )
        acc.setdefault("pbp", []).append(pbp)
        acc.setdefault("pbp_cfbfastr", []).append(
            to_cfbfastr(pbp, season=season, drives=drives, linescore=linescore)
        )
        acc.setdefault("drives", []).append(drives)
        acc.setdefault("linescore", []).append(linescore)
        acc.setdefault("team_stats", []).append(
            parse_cfb_ncaa_team_stats(bundle.get("team_stats") or "", contest_id=cid)
        )
        acc.setdefault("officials", []).append(
            parse_cfb_ncaa_officials(bundle.get("officials") or "", contest_id=cid)
        )
        for cat, frame in parse_cfb_ncaa_player_stats(
            bundle.get("individual_stats") or "", contest_id=cid
        ).items():
            player_acc.setdefault(cat, []).append(frame)

    out_dir = root / "mfb" / "datasets" / str(academic_year)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, frames in acc.items():
        frames = [f for f in frames if f.height]
        if not frames:
            continue
        df = pl.concat(frames, how="diagonal_relaxed")
        df.write_parquet(out_dir / f"{name}.parquet")
        written[name] = df.height
    for cat, frames in player_acc.items():
        frames = [f for f in frames if f.height]
        if not frames:
            continue
        slug = re.sub(r"\W+", "_", cat.lower()).strip("_")
        df = pl.concat(frames, how="diagonal_relaxed")
        df.write_parquet(out_dir / f"player_stats_{slug}.parquet")
        written[f"player_stats_{slug}"] = df.height
    written["games"] = n
    return written


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--academic-year", type=int, default=2026)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args()
    teams = build_teams(args.root, args.academic_year)
    print(f"teams: {teams.height}")
    master = build_schedule_master(args.root, args.academic_year)
    print(
        f"schedule master: {master.height} team-games, "
        f"{master.get_column('contest_id').drop_nulls().n_unique()} unique contests"
    )
    print(f"game datasets: {build_game_datasets(args.root, args.academic_year)}")
