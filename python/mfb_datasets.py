"""Offline dataset build: persisted HTML + game bundles -> tidy parquet.

Fully offline (reads ``mfb/**`` written by the capture stages; no network).

Outputs (``{root}/mfb/...``, ``{ay}`` = academic year, ending-year convention):

* ``teams/parquet/{ay}_div{d}.parquet``      -- team id/name per division
* ``schedules/parquet/{ay}.parquet``          -- schedule master (one row per
  team-game: date, opponent, result, contest_id, attendance, espn_game_id)
* ``rosters/parquet/{ay}.parquet``            -- per-team season rosters

REFERENCE frames only. Game-grain season datasets (pbp, drives, box, ...)
moved to ``ncaa-mfb-football-data``, which reshapes this repo's parsed
``mfb/json/{contest_id}.json.gz`` payloads (stage 03) -- the -raw/-data
split the MBB/WBB twins use.
"""

from __future__ import annotations

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


_ROSTER_COLMAP = {
    "GP": "games_played",
    "GS": "games_started",
    "#": "jersey",
    "StatCrew #": "statcrew_jersey",
    "Name": "player_name",
    "Class": "player_class",
    "Position": "position",
    "Height": "height",
    "Weight": "weight",
    "Hometown": "hometown",
    "High School": "high_school",
}
_PLAYER_HREF_RE = re.compile(r"/players/(\d+)")


def parse_team_roster(html: str, team_id: "Optional[str]" = None) -> pl.DataFrame:
    """Team roster page -> one row per player.

    Header-keyed (teams vary in which columns they publish); adds ``player_id``
    from the ``/players/{id}`` link and ``team_name`` from the card header.
    """
    from bs4 import BeautifulSoup

    schema = {
        "team_id": pl.Utf8,
        "team_name": pl.Utf8,
        "player_id": pl.Utf8,
        "player_name": pl.Utf8,
        "jersey": pl.Utf8,
        "statcrew_jersey": pl.Utf8,
        "player_class": pl.Utf8,
        "position": pl.Utf8,
        "height": pl.Utf8,
        "weight": pl.Int64,
        "hometown": pl.Utf8,
        "high_school": pl.Utf8,
        "games_played": pl.Int64,
        "games_started": pl.Int64,
    }
    soup = BeautifulSoup(html or "", "html.parser")
    header = soup.select_one("div.card-header")
    team_name = None
    if header:
        team_name = (
            re.sub(r"\s*\([\d\-]+\).*$", "", header.get_text(" ", strip=True)) or None
        )
    table = soup.find("table", id=re.compile(r"^rosters_form_players_.*_data_table$"))
    rows: "list[dict]" = []
    if table is not None:
        trs = table.find_all("tr")
        if trs:
            head = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
            for tr in trs[1:]:
                cells = tr.find_all(["th", "td"])
                if len(cells) != len(head):
                    continue
                rec: "dict" = {"team_id": team_id, "team_name": team_name}
                for h, c in zip(head, cells):
                    key = _ROSTER_COLMAP.get(h)
                    if key:
                        rec[key] = c.get_text(" ", strip=True) or None
                a = tr.find("a", href=_PLAYER_HREF_RE)
                rec["player_id"] = (
                    _PLAYER_HREF_RE.search(a["href"]).group(1) if a else None
                )
                for k in ("weight", "games_played", "games_started"):
                    v = rec.get(k)
                    rec[k] = int(v) if isinstance(v, str) and v.isdigit() else None
                if rec.get("player_name"):
                    rows.append(rec)
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


def build_rosters(root: "str | Path", academic_year: int) -> pl.DataFrame:
    """All persisted roster pages for a season -> one rosters parquet."""
    root = Path(root)
    frames = []
    for p in sorted(
        (root / "mfb" / "rosters" / "html" / str(academic_year)).glob("*.html")
    ):
        df = parse_team_roster(p.read_text(encoding="utf-8"), team_id=p.stem)
        if df.height:
            frames.append(df)
    rosters = pl.concat(frames) if frames else parse_team_roster("")
    rosters = rosters.with_columns(pl.lit(academic_year).alias("academic_year"))
    out = root / "mfb" / "rosters" / "parquet" / f"{academic_year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    rosters.write_parquet(out)
    return rosters


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
    # espn_game_id from stage 06's crosswalk (null when unmatched / not yet built)
    from ncaa_mfb_06_xwalk_build import load_espn_game_index

    espn_idx = load_espn_game_index(root, academic_year)
    if espn_idx and "contest_id" in master.columns:
        master = master.with_columns(
            pl.col("contest_id")
            .cast(pl.Utf8)
            .replace_strict(espn_idx, default=None, return_dtype=pl.Utf8)
            .alias("espn_game_id")
        )
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


def main(argv: "Optional[list[str]]" = None) -> int:
    """CLI: build the REFERENCE frames (teams, rosters, schedule master) for one season.

    Game-grain season datasets moved to ncaa-mfb-football-data (the reshape
    stage), which builds them from this repo's parsed ``mfb/json/`` payloads.
    """
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--academic-year", type=int, default=2026)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args(argv)
    teams = build_teams(args.root, args.academic_year)
    print(f"teams: {teams.height}")
    rosters = build_rosters(args.root, args.academic_year)
    print(
        f"rosters: {rosters.height} players, {rosters.get_column('team_id').n_unique()} teams"
    )
    master = build_schedule_master(args.root, args.academic_year)
    print(
        f"schedule master: {master.height} team-games, "
        f"{master.get_column('contest_id').drop_nulls().n_unique()} unique contests"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
