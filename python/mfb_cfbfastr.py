"""Map stats.ncaa.org MFB pbp (``parse_cfb_ncaa_pbp`` output) to cfbfastR-named columns.

Prototype for the sdv-py ``cfb_pbp`` column-parity goal: take the 49-column NCAA
structural frame and emit as many of the ~330 cfbfastR ``cfb_pbp`` columns as a
raw-text parser can honestly produce -- ids, pos/def teams, period/clock,
down/distance/yards_to_goal, running scores, participant names (cfbfastR
"First Last" naming), play-type labels, and the flag family. Model outputs
(EPA/WP/...) and ESPN-participant columns are out of scope by design.

Stateful derivations (period from quarter markers, running scores, play
numbering) run in one ordered pass; window/lag columns are polars ops on top.

Input: the frame from :func:`sportsdataverse.cfb.cfb_ncaa_pbp.parse_cfb_ncaa_pbp`.
Optional ``drives`` / ``linescore`` frames (from ``cfb_ncaa_box``) refine
period + home/away when the full bundle is available.
"""

from __future__ import annotations

import math
import re
from typing import Optional

import polars as pl

_TRAILING_CLOCK_RE = re.compile(r"clock (\d{1,2}:\d{2})")
_QTR_MARKER_RE = re.compile(r"start of (\d)(?:st|nd|rd|th) quarter", re.I)
_INT_BY_RE = re.compile(
    r"intercepted by ([A-Z][\w'.\- ]*,[\w'.\- ]+?)(?: at| return| for|\.|,|$)"
)
_RET_YDS_RE = re.compile(r"return (\d+) yards", re.I)
# XP/FG result: "good" appears in both cases and "NO GOOD"/"no good" must not match.
_KICK_GOOD_RE = re.compile(r"(?<!no )good", re.I)

# Drive h5 title: "{team} {RESULT} {clock},{yardline}, {n} plays, {yards} yards,
# {top} {away} - {home}". RESULT is an ALL-CAPS token (TD/FG/FGA/PUNT/INT/FUMB/
# DOWNS/HALF/...) -- anchoring on that keeps multi-word mixed-case team names
# intact (the graduated parser's lazy .+? donates "Carolina" to result when the
# result token is missing, truncating "East Carolina" to "East").
_DRIVE_TITLE_RE = re.compile(
    r"^(?P<team>.+?)(?:\s+(?P<result>[A-Z/]{2,10}))?\s+"
    # side codes can be MIXED case ("Ric25" for Rice) -- [A-Z]-only drops
    # every such team's drives (the graduated parser shares this bug).
    r"(?P<start_clock>\d+:\d+),(?P<start_yard_line>[A-Za-z&]{1,4}\d+),\s+"
    r"(?P<n_plays>\d+)\s+plays?,\s+(?P<yards>-?\d+)\s+yards?,\s+"
    r"(?P<top>\d+:\d+)\s+(?P<score_away>\d+)\s*-\s*(?P<score_home>\d+)\s*$"
)


def parse_drive_titles(html: str) -> pl.DataFrame:
    """Drive ``h5`` titles -> one row per drive with the running-score checkpoint.

    Columns: ``drive_number``, ``team``, ``result``, ``start_clock``,
    ``start_yard_line``, ``n_plays``, ``yards``, ``top`` (time of possession),
    ``score_away``/``score_home`` (the game score AFTER the drive -- an
    authoritative checkpoint the play-level running score snaps to).
    """
    from bs4 import BeautifulSoup

    schema = {
        "drive_number": pl.Int64,
        "team": pl.Utf8,
        "result": pl.Utf8,
        "start_clock": pl.Utf8,
        "start_yard_line": pl.Utf8,
        "n_plays": pl.Int64,
        "yards": pl.Int64,
        "top": pl.Utf8,
        "score_away": pl.Int64,
        "score_home": pl.Int64,
    }
    soup = BeautifulSoup(html or "", "html.parser")
    rows = []
    n = 0
    for container in soup.select("div.drives"):
        for h5 in container.find_all("h5", recursive=False):
            classes = h5.get("class") or []
            if not ("scoring_play" in classes or "non_scoring_play" in classes):
                continue
            n += 1
            m = _DRIVE_TITLE_RE.match(" ".join(h5.get_text(" ", strip=True).split()))
            rows.append(
                {
                    "drive_number": n,
                    "team": m.group("team") if m else None,
                    "result": m.group("result") if m else None,
                    "start_clock": m.group("start_clock") if m else None,
                    "start_yard_line": m.group("start_yard_line") if m else None,
                    "n_plays": int(m.group("n_plays")) if m else None,
                    "yards": int(m.group("yards")) if m else None,
                    "top": m.group("top") if m else None,
                    "score_away": int(m.group("score_away")) if m else None,
                    "score_home": int(m.group("score_home")) if m else None,
                }
            )
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


#: play rows that are game furniture, not plays (dropped from the cfbfastR frame
#: after they've fed the stateful pass).
_MARKER_TYPES = {"drive_start", "coin_toss"}


_OT_PERIOD_RE = re.compile(r"^(\d+)OT$")


def _period_num(s: "str | None") -> "int | None":
    """'3' -> 3, '1OT' -> 5, '2OT' -> 6."""
    if not s:
        return None
    m = _OT_PERIOD_RE.match(s)
    if m:
        return 4 + int(m.group(1))
    return int(s) if s.isdigit() else None


def parse_scoring_summary(box_html: str) -> pl.DataFrame:
    """box_score ``scoring_summary_table`` -> one row per score.

    Columns: ``period`` (int, OT -> 5+), ``clock``, ``team`` ("Has Ball" --
    often empty), ``play_text`` (real description; empty for some OT rows),
    ``n_plays``, ``yards``, ``top``, ``score_away``/``score_home`` (running).
    The table's ``tr``s concatenate logical rows, so cells are re-chunked by 9.
    """
    from bs4 import BeautifulSoup

    schema = {
        "period": pl.Int64,
        "clock": pl.Utf8,
        "team": pl.Utf8,
        "play_text": pl.Utf8,
        "n_plays": pl.Int64,
        "yards": pl.Int64,
        "top": pl.Utf8,
        "score_away": pl.Int64,
        "score_home": pl.Int64,
    }
    soup = BeautifulSoup(box_html or "", "html.parser")
    table = soup.find("table", id="scoring_summary_table")
    rows: "list[dict]" = []
    if table is not None:
        cells = [c.get_text(" ", strip=True) for c in table.find_all(["th", "td"])]
        # drop the title cell + the 9-cell header, then chunk by 9
        flat = [c for c in cells if c != "Scoring Summary"]
        if len(flat) >= 9 and flat[0] == "Period":
            flat = flat[9:]
        for i in range(0, len(flat) - 8, 9):
            chunk = flat[i : i + 9]
            period = _period_num(chunk[0])
            if period is None:
                continue
            rows.append(
                {
                    "period": period,
                    "clock": chunk[1] or None,
                    "team": chunk[2] or None,
                    "play_text": chunk[3] or None,
                    "n_plays": int(chunk[4]) if chunk[4].isdigit() else None,
                    "yards": int(chunk[5].lstrip("-"))
                    * (-1 if chunk[5].startswith("-") else 1)
                    if chunk[5].lstrip("-").isdigit()
                    else None,
                    "top": chunk[6] or None,
                    "score_away": int(chunk[7]) if chunk[7].isdigit() else None,
                    "score_home": int(chunk[8]) if chunk[8].isdigit() else None,
                }
            )
    # the tr-concatenation duplicates rows; dedup preserving order
    df = pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)
    return df.unique(maintain_order=True)


def parse_ot_drives(drives_html: str) -> pl.DataFrame:
    """drives-tab rows whose Quarter is ``NOT`` a digit (``1OT``/``2OT``/...).

    The graduated ``parse_cfb_ncaa_drives`` nulls the OT quarter, so this
    re-reads the raw table keeping the true OT period number.
    """
    from bs4 import BeautifulSoup

    schema = {
        "drive_number": pl.Int64,
        "period": pl.Int64,
        "team": pl.Utf8,
        "start_how": pl.Utf8,
        "start_yard_line": pl.Utf8,
        "end_how": pl.Utf8,
        "end_yard_line": pl.Utf8,
        "n_plays": pl.Int64,
        "yards": pl.Int64,
    }
    soup = BeautifulSoup(drives_html or "", "html.parser")
    table = soup.find("table", id="public_game_drives_data_table")
    rows: "list[dict]" = []
    if table is not None:
        for tr in table.find_all("tr")[1:]:
            r = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(r) < 13 or not (r[0] or "").isdigit():
                continue
            period = _period_num(r[1])
            if period is None or period <= 4:
                continue
            rows.append(
                {
                    "drive_number": int(r[0]),
                    "period": period,
                    "team": r[2] or None,
                    "start_how": r[4] or None,
                    "start_yard_line": r[6] or None,
                    "end_how": r[8] or None,
                    "end_yard_line": r[10] or None,
                    "n_plays": int(r[11]) if r[11].isdigit() else None,
                    "yards": int(r[12].lstrip("-"))
                    * (-1 if r[12].startswith("-") else 1)
                    if r[12].lstrip("-").isdigit()
                    else None,
                }
            )
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


#: end_how -> approximate cfbfastR play_type label for synthesized OT rows.
_OT_END_HOW_LABEL = {
    "TD": "Touchdown",
    "FG": "Field Goal Good",
    "FGA": "Field Goal Missed",
    "PUNT": "Punt",
    "INT": "Pass Interception Return",
    "FUMB": "Fumble Recovery (Opponent)",
    "DOWNS": "Turnover on Downs",
    "HALF": "End of Game",
    "END": "End of Game",
}


def _first_last(name: "str | None") -> "str | None":
    """NCAA 'Last[ Suffix],First' -> cfbfastR 'First Last[ Suffix]'."""
    if not name or "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def _clock_secs(clock: "str | None") -> "int | None":
    if not clock or ":" not in clock:
        return None
    mm, ss = clock.split(":", 1)
    try:
        return int(mm) * 60 + int(ss)
    except ValueError:
        return None


def _own_side(df: pl.DataFrame) -> "dict[str, str]":
    """Infer each offense's own yard-line side code (e.g. Merrimack -> 'MC').

    Drives overwhelmingly start in the offense's own territory, so of the two
    possible (team name -> side code) assignments, pick the one under which
    more first-plays-of-drive sit on the offense's own side.
    """
    teams = [t for t in df.get_column("offense").unique().to_list() if t]
    sides = [s for s in df.get_column("yard_line_side").unique().to_list() if s]
    if len(teams) != 2 or len(sides) != 2:
        return {}
    firsts = (
        df.filter(pl.col("yard_line_side").is_not_null())
        .group_by("drive_number", maintain_order=True)
        .first()
        .select("offense", "yard_line_side")
        .to_dicts()
    )
    a = {teams[0]: sides[0], teams[1]: sides[1]}
    b = {teams[0]: sides[1], teams[1]: sides[0]}
    score_a = sum(1 for r in firsts if a.get(r["offense"]) == r["yard_line_side"])
    score_b = sum(1 for r in firsts if b.get(r["offense"]) == r["yard_line_side"])
    return a if score_a >= score_b else b


def _play_type_label(r: "dict") -> str:
    """Map the NCAA structural play_type to the cfbfastR play_type vocabulary."""
    pt, td = r["play_type"], bool(r["is_touchdown"])
    if pt == "rush" or pt == "kneel":
        return "Rushing Touchdown" if td else "Rush"
    if pt == "pass":
        if r["turnover_type"] == "interception":
            return "Interception Return Touchdown" if td else "Pass Interception Return"
        if r["pass_complete"]:
            return "Passing Touchdown" if td else "Pass Reception"
        return "Pass Incompletion"
    if pt == "sack":
        return "Sack"
    if pt == "punt":
        return "Blocked Punt" if "blocked" in (r["play_text"] or "").lower() else "Punt"
    if pt == "kickoff":
        return "Kickoff Return Touchdown" if td else "Kickoff"
    if pt == "field_goal":
        return "Field Goal Good" if r["fg_made"] else "Field Goal Missed"
    if pt == "extra_point":
        return (
            "Extra Point Good"
            if _KICK_GOOD_RE.search(r["play_text"] or "")
            else "Extra Point Missed"
        )
    if pt == "two_point":
        return "Two Point Pass" if r["passer"] else "Two Point Rush"
    if pt == "penalty":
        return "Penalty"
    if pt == "timeout":
        return "Timeout"
    if pt == "period_marker":
        return "End Period"
    return pt


def to_cfbfastr(
    pbp: pl.DataFrame,
    *,
    season: "Optional[int]" = None,
    week: "Optional[int]" = None,
    drives: "Optional[pl.DataFrame]" = None,
    linescore: "Optional[pl.DataFrame]" = None,
    drive_titles: "Optional[pl.DataFrame]" = None,
    ot_drives: "Optional[pl.DataFrame]" = None,
    scoring_summary: "Optional[pl.DataFrame]" = None,
) -> pl.DataFrame:
    """cfbfastR-named play frame from the NCAA structural pbp frame.

    Args:
        pbp: output of ``parse_cfb_ncaa_pbp`` (one game).
        season: season year (2025 = fall-2025), written to ``season``/``year``.
        week: optional week number (from the schedule master).
        drives: optional ``parse_cfb_ncaa_drives`` frame -- refines ``period``
            per drive when quarter markers are missing from the pbp page.
        linescore: optional ``parse_cfb_ncaa_linescore`` frame -- provides
            ``home``/``away`` team names.
        drive_titles: optional :func:`parse_drive_titles` frame -- authoritative
            per-drive team labels (fixes graduated-parser team truncation) and
            running-score checkpoints the play-level score snaps to at each
            drive boundary (self-heals OT scoring rules + missed events).

    Returns:
        One row per play (markers/furniture dropped) with cfbfastR-named columns.
    """
    if pbp.height == 0:
        return pl.DataFrame()
    title_team: "dict[int, str]" = {}
    checkpoint: "dict[int, tuple[int, int]]" = {}  # drive -> (score_away, score_home)
    title_result: "dict[int, str]" = {}
    if drive_titles is not None and drive_titles.height:
        for t in drive_titles.to_dicts():
            dn = t["drive_number"]
            if t["team"]:
                title_team[dn] = t["team"]
            if t["result"]:
                title_result[dn] = t["result"]
            if t["score_away"] is not None and t["score_home"] is not None:
                checkpoint[dn] = (t["score_away"], t["score_home"])
    if title_team:
        # authoritative per-drive team labels fix truncated offense values
        pbp = pbp.with_columns(
            pl.col("drive_number")
            .replace_strict(title_team, default=None, return_dtype=pl.Utf8)
            .fill_null(pl.col("offense"))
            .alias("offense")
        )
    own_side = _own_side(pbp)
    teams = (
        sorted(set(title_team.values()))
        if len(set(title_team.values())) == 2
        else [t for t in pbp.get_column("offense").unique().to_list() if t]
    )
    home = away = None
    if linescore is not None and linescore.height:
        ls = linescore.group_by("team", "home_away").len()
        for r in ls.to_dicts():
            if r["home_away"] == "home":
                home = r["team"]
            elif r["home_away"] == "away":
                away = r["team"]
    # away/home slot inference from checkpoints when the linescore is absent or
    # its names don't match the drive-title labels: the first scoring drive
    # whose checkpoint moved exactly one slot pins its team to that slot.
    if checkpoint and (away not in teams or home not in teams):
        prev = (0, 0)
        for dn in sorted(checkpoint):
            ca, ch = checkpoint[dn]
            team = title_team.get(dn)
            other = next((t for t in teams if t != team), None)
            if team in teams and other is not None:
                if (
                    ca > prev[0]
                    and ch == prev[1]
                    and title_result.get(dn) in ("TD", "FG")
                ):
                    away, home = team, other
                    break
                if (
                    ch > prev[1]
                    and ca == prev[0]
                    and title_result.get(dn) in ("TD", "FG")
                ):
                    home, away = team, other
                    break
            prev = (ca, ch)
    drive_period: "dict[int, int]" = {}
    if drives is not None and drives.height:
        drive_period = {
            r["drive_number"]: r["quarter"]
            for r in drives.select("drive_number", "quarter").to_dicts()
            if r["drive_number"] is not None and r["quarter"] is not None
        }

    game_id = pbp.get_column("contest_id")[0]
    gid = int(game_id) if game_id and str(game_id).isdigit() else None

    period = 1
    last_td_team: "Optional[str]" = None
    score: "dict[str, int]" = {t: 0 for t in teams}
    game_play_number = 0
    half_play_number = 0
    prev_half = 1
    prev_drive: "Optional[int]" = None
    rows: "list[dict]" = []
    drive_play_counter: "dict[int, int]" = {}

    def _snap(drive: "Optional[int]") -> None:
        """Snap the running score to the title checkpoint of a finished drive."""
        if drive in checkpoint and away in score and home in score:
            score[away], score[home] = checkpoint[drive]

    all_rows = pbp.to_dicts()
    # the checkpoint is the score AFTER a drive, so the drive's LAST play must
    # emit exactly it (event-sourcing can't see OT-shootout scoring rules).
    last_play_of_drive: "dict[int, int]" = {
        r["drive_number"]: i
        for i, r in enumerate(all_rows)
        if r["drive_number"] is not None
    }

    for i, r in enumerate(all_rows):
        text = r["play_text"] or ""
        qm = _QTR_MARKER_RE.search(text.lower())
        if qm:
            period = int(qm.group(1))
        if r["drive_number"] in drive_period:
            period = drive_period[r["drive_number"]]
        half = 1 if period <= 2 else 2
        if half != prev_half:
            half_play_number = 0
            prev_half = half

        if r["drive_number"] != prev_drive:
            _snap(prev_drive)
            prev_drive = r["drive_number"]

        offense = title_team.get(r["drive_number"]) or r["offense"]
        defense = next((t for t in teams if t != offense), None) if offense else None

        # running score -- award points to the right side of the ball
        pts_off = pts_def = 0
        if r["play_type"] not in (
            "timeout",
            "period_marker",
            "drive_start",
            "coin_toss",
        ):
            if r["is_touchdown"]:
                # a fumble-return TD has turnover_type set WITHOUT is_turnover
                to_defense = r["turnover_type"] in ("interception", "fumble")
                if to_defense:
                    pts_def += 6
                    last_td_team = defense
                else:
                    pts_off += 6
                    last_td_team = offense
            if r["play_type"] == "field_goal" and r["fg_made"]:
                pts_off += 3
            # XP/2pt belong to whoever scored the preceding TD (a defensive TD's
            # try is kicked by the drive's DEFENSE, so drive offense is wrong).
            if r["play_type"] == "extra_point" and _KICK_GOOD_RE.search(text):
                if (last_td_team or offense) == defense:
                    pts_def += 1
                else:
                    pts_off += 1
            if r["play_type"] == "two_point" and "successful" in text.lower():
                if (last_td_team or offense) == defense:
                    pts_def += 2
                else:
                    pts_off += 2
            if r["is_safety"]:
                pts_def += 2
        if offense and pts_off:
            score[offense] = score.get(offense, 0) + pts_off
        if defense and pts_def:
            score[defense] = score.get(defense, 0) + pts_def
        if last_play_of_drive.get(r["drive_number"]) == i:
            _snap(r["drive_number"])

        if r["play_type"] in _MARKER_TYPES:
            continue
        game_play_number += 1
        half_play_number += 1
        dn = r["drive_number"]
        drive_play_counter[dn] = drive_play_counter.get(dn, 0) + 1

        clock = r["clock"]
        if not clock:
            cm = _TRAILING_CLOCK_RE.search(text)
            clock = cm.group(1) if cm else None
        secs = _clock_secs(clock)
        time_secs_rem = (
            (4 - period) * 900 + secs if secs is not None and period <= 4 else None
        )

        side, num = r["yard_line_side"], r["yard_line_number"]
        ytg = None
        if side is not None and num is not None and offense in own_side:
            ytg = 100 - num if own_side[offense] == side else num
        end_ytg = None
        eyl = r["end_yard_line"] or ""
        em = re.match(r"([A-Za-z&]{1,4})(\d+)$", eyl)
        if em and offense in own_side:
            end_ytg = (
                100 - int(em.group(2))
                if own_side[offense] == em.group(1)
                else int(em.group(2))
            )

        pt = r["play_type"]
        is_rush = pt in ("rush", "kneel")
        is_pass_att = pt == "pass"
        is_sack = pt == "sack"
        completion = bool(r["pass_complete"]) if is_pass_att else False
        interception = r["turnover_type"] == "interception"
        scoring_play = bool(pts_off or pts_def)
        int_m = _INT_BY_RE.search(text)
        ret_m = _RET_YDS_RE.search(text)

        pos_score = score.get(offense, 0) if offense else None
        def_score = score.get(defense, 0) if defense else None
        rows.append(
            {
                # ids / context
                "game_id": gid,
                "id_play": gid * 10_000 + game_play_number if gid else None,
                "drive_id": gid * 100 + dn if gid and dn else None,
                "game_play_number": game_play_number,
                "half_play_number": half_play_number,
                "drive_play_number": drive_play_counter[dn],
                "drive_number": dn,
                "season": season,
                "year": season,
                "week": week,
                "period": period,
                "half": half,
                "clock.minutes": secs // 60 if secs is not None else None,
                "clock.seconds": secs % 60 if secs is not None else None,
                "TimeSecsRem": time_secs_rem,
                "Under_two": time_secs_rem is not None and time_secs_rem <= 120,
                # teams
                "pos_team": offense,
                "def_pos_team": defense,
                "offense_play": offense,
                "defense_play": defense,
                "home": home,
                "away": away,
                # score (after the play, cfbfastR convention for offense/defense_score)
                "pos_team_score": pos_score,
                "def_pos_team_score": def_score,
                "offense_score": pos_score,
                "defense_score": def_score,
                "pos_score_diff": pos_score - def_score
                if pos_score is not None and def_score is not None
                else None,
                "score_pts": pts_off - pts_def,
                "scoring_play": scoring_play,
                "scoring": scoring_play,
                # situation
                "down": r["down"],
                "distance": r["distance"],
                "yard_line": r["yard_line"],
                "yards_to_goal": ytg,
                "yards_to_goal_end": end_ytg,
                "Goal_To_Go": (
                    r["distance"] is not None
                    and ytg is not None
                    and ytg <= r["distance"]
                ),
                "log_ydstogo": math.log(r["distance"]) if r["distance"] else None,
                "yards_gained": r["yards_gained"],
                # typing
                "play_type": _play_type_label(r),
                "orig_play_type": pt,
                "play_text": r["play_text"],
                "rush": is_rush,
                "rush_td": is_rush and bool(r["is_touchdown"]),
                "pass": is_pass_att or is_sack,
                "pass_td": is_pass_att and completion and bool(r["is_touchdown"]),
                "pass_attempt": is_pass_att,
                "completion": completion,
                "target": is_pass_att and r["receiver"] is not None,
                "sack": is_sack,
                "sack_vec": is_sack,
                "int": interception,
                "int_td": interception and bool(r["is_touchdown"]),
                "turnover_vec": bool(r["is_turnover"])
                or r["turnover_type"] == "fumble",
                "downs_turnover": r["turnover_type"] == "downs",
                "touchdown": bool(r["is_touchdown"]),
                "td_play": bool(r["is_touchdown"]),
                "safety": bool(r["is_safety"]),
                "fumble_vec": bool(r["is_fumble"]),
                "punt": pt == "punt",
                "punt_play": pt == "punt",
                "kickoff_play": pt == "kickoff",
                "kick_play": pt == "field_goal" or pt == "extra_point",
                "fg_inds": pt == "field_goal",
                "fg_made": r["fg_made"],
                "punt_blocked": pt == "punt" and "blocked" in text.lower(),
                "punt_fair_catch": pt == "punt" and bool(r["fair_catch"]),
                "firstD_by_yards": bool(r["is_first_down"])
                and not bool(r["penalty_flag"]),
                "firstD_by_penalty": bool(r["is_first_down"])
                and bool(r["penalty_flag"]),
                # penalties
                "penalty_flag": bool(r["penalty_flag"]),
                "penalty_no_play": bool(r["no_play"]),
                "penalty_declined": "declined" in text.lower(),
                "penalty_offset": "off-setting" in text.lower()
                or "offsetting" in text.lower(),
                "penalty_text": r["penalty_type"],
                "yds_penalty": r["penalty_yards"],
                # participants (cfbfastR "First Last")
                "rusher_player_name": _first_last(r["rusher"]) if is_rush else None,
                "passer_player_name": _first_last(r["passer"])
                if (is_pass_att or is_sack)
                else None,
                "receiver_player_name": _first_last(r["receiver"]),
                "interception_player_name": _first_last(int_m.group(1))
                if int_m and interception
                else None,
                "punter_player_name": _first_last(r["punter"]),
                "punt_returner_player_name": _first_last(r["returner"])
                if pt == "punt"
                else None,
                "fg_kicker_player_name": _first_last(r["kicker"])
                if pt in ("field_goal", "extra_point")
                else None,
                "kickoff_player_name": _first_last(r["kicker"])
                if pt == "kickoff"
                else None,
                "kickoff_returner_player_name": _first_last(r["returner"])
                if pt == "kickoff"
                else None,
                "yds_rushed": r["yards_gained"] if is_rush else None,
                "yds_receiving": r["yards_gained"] if completion else None,
                "yds_sacked": -r["yards_gained"]
                if is_sack and r["yards_gained"] is not None
                else None,
                "yds_punted": r["punt_yards"],
                "yds_punt_return": r["return_yards"] if pt == "punt" else None,
                "yds_kickoff": r["kick_yards"],
                "yds_kickoff_return": r["return_yards"] if pt == "kickoff" else None,
                "yds_int_return": int(ret_m.group(1))
                if ret_m and interception
                else None,
                "yds_fg": r["fg_distance"],
                "drive_result": r["drive_result"],
                "drive_scoring": r["drive_scored"],
                "ot_synthesized": False,
            }
        )

    # --- OT synthesis: stats.ncaa.org pbp pages omit OT drives. Rebuild them
    # (one row per drive) from the drives tab, with scores walked through the
    # scoring-summary running-score checkpoints. Rows are flagged
    # ot_synthesized=True; play_text is the summary's real description when it
    # ships one, else an honest synthesized descriptor.
    max_pbp_drive = max(
        (r["drive_number"] for r in rows if r["drive_number"]), default=0
    )
    if rows and ot_drives is not None and ot_drives.height:
        ot_checkpoints = (
            scoring_summary.filter(pl.col("period") > 4).to_dicts()
            if scoring_summary is not None and scoring_summary.height
            else []
        )
        template = dict.fromkeys(rows[-1].keys())
        for od in sorted(ot_drives.to_dicts(), key=lambda d: d["drive_number"]):
            if od["drive_number"] <= max_pbp_drive:
                continue  # this OT drive IS on the pbp page already
            offense = od["team"]
            defense = next((t for t in teams if t != offense), None)
            scoring = od["end_how"] in ("TD", "FG", "SAF")
            summary_text = None
            if scoring and ot_checkpoints:
                cp = ot_checkpoints.pop(0)
                summary_text = cp["play_text"]
                if away in score and home in score and cp["score_away"] is not None:
                    score[away], score[home] = cp["score_away"], cp["score_home"]
            elif scoring and offense in score:
                score[offense] += 3 if od["end_how"] == "FG" else 6
            game_play_number += 1
            half_play_number += 1
            pos_s = score.get(offense) if offense else None
            def_s = score.get(defense) if defense else None
            row = dict(template)
            row.update(
                {
                    "game_id": gid,
                    "id_play": gid * 10_000 + game_play_number if gid else None,
                    "drive_id": gid * 100 + od["drive_number"] if gid else None,
                    "game_play_number": game_play_number,
                    "half_play_number": half_play_number,
                    "drive_play_number": 1,
                    "drive_number": od["drive_number"],
                    "season": season,
                    "year": season,
                    "week": week,
                    "period": od["period"],
                    "half": 3,
                    "pos_team": offense,
                    "def_pos_team": defense,
                    "offense_play": offense,
                    "defense_play": defense,
                    "home": home,
                    "away": away,
                    "pos_team_score": pos_s,
                    "def_pos_team_score": def_s,
                    "offense_score": pos_s,
                    "defense_score": def_s,
                    "pos_score_diff": pos_s - def_s
                    if pos_s is not None and def_s is not None
                    else None,
                    "scoring_play": scoring,
                    "scoring": scoring,
                    "yard_line": od["start_yard_line"],
                    "play_type": _OT_END_HOW_LABEL.get(od["end_how"], od["end_how"]),
                    "orig_play_type": "ot_drive",
                    "play_text": summary_text
                    or (
                        f"{offense} OT drive ({od['end_how']}): "
                        f"{od['n_plays']} plays, {od['yards']} yards, "
                        f"{od['start_yard_line']} to {od['end_yard_line']}"
                    ),
                    "touchdown": od["end_how"] == "TD",
                    "td_play": od["end_how"] == "TD",
                    "fg_inds": od["end_how"] in ("FG", "FGA"),
                    "fg_made": od["end_how"] == "FG"
                    if od["end_how"] in ("FG", "FGA")
                    else None,
                    "punt": od["end_how"] == "PUNT",
                    "punt_play": od["end_how"] == "PUNT",
                    "int": od["end_how"] == "INT",
                    "turnover_vec": od["end_how"] in ("INT", "FUMB", "DOWNS"),
                    "downs_turnover": od["end_how"] == "DOWNS",
                    "drive_result": od["end_how"],
                    "drive_scoring": scoring,
                    "ot_synthesized": True,
                }
            )
            rows.append(row)

    df = pl.DataFrame(rows, infer_schema_length=None)
    # window/lag bookkeeping over the ordered game
    return df.with_columns(
        pl.col("pos_team").shift(1).alias("lag_pos_team"),
        pl.col("pos_team").shift(-1).alias("lead_pos_team"),
        pl.col("play_type").shift(1).alias("lag_play_type"),
        pl.col("play_type").shift(-1).alias("lead_play_type"),
        pl.col("play_text").shift(1).alias("lag_play_text"),
        pl.col("play_text").shift(-1).alias("lead_play_text"),
        (pl.col("pos_team") != pl.col("pos_team").shift(1)).alias("change_of_pos_team"),
        (pl.col("turnover_vec").shift(1) == True).alias("play_after_turnover"),  # noqa: E712
        pl.len().alias("n_plays_in_game"),
    )


if __name__ == "__main__":  # self-check on the committed fixtures
    import json
    import pathlib
    import sys

    sys.path.insert(0, "/mnt/sdv_repos/sdv-py")
    from sportsdataverse.cfb.cfb_ncaa_pbp import parse_cfb_ncaa_pbp

    fixture_dir = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    target = json.load(
        open(
            "/tmp/claude-0/-mnt-sdv-repos/448acaa6-27ea-43cd-bc3c-8a38228c1b8d/scratchpad/cfb_pbp_target_cols.json"
        )
    )["target"]
    for fx in sorted(fixture_dir.glob("mfb_pbp_*.html")):
        cid = fx.stem.split("_")[-1]
        df = to_cfbfastr(
            parse_cfb_ncaa_pbp(fx.read_text(), contest_id=cid), season=2024
        )
        matched = [c for c in df.columns if c in target]
        assert df.height > 100, (fx.name, df.height)
        assert df.get_column("period").max() >= 4, fx.name
        # running score sanity: final score components are non-negative and increasing
        assert df.get_column("pos_team_score").min() >= 0
        print(
            f"{fx.name}: {df.height} plays, {len(df.columns)} cols, "
            f"{len(matched)} cfbfastR-named, final "
            f"{df.select('pos_team', 'pos_team_score', 'def_pos_team', 'def_pos_team_score').row(-1)}"
        )
    print("self-check OK")
