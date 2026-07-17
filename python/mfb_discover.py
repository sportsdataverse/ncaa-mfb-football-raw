"""MFB season discovery: team list (sport_code=MFB) -> team pages -> contest_ids.

Pure parse functions + an injectable ``fetch_fn`` so discovery is fully offline-
testable; the live default drives ``NcaaFetcher.with_browser`` (real-GPU host +
US residential proxy pool -- see docs/DESIGN.md; datacenter IPs get an edge 403).
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

FetchFn = Callable[[str], str]  # (path) -> html

_TEAM_ID_RE = re.compile(r"/teams/(\d+)")
_CONTEST_ID_RE = re.compile(r"/contests/(\d+)/")


def team_list_path(academic_year: int, division: int = 11) -> str:
    """stats.ncaa.org MFB team-list path. ``division`` 11 = FBS, 12 = FCS."""
    return (
        f"team/inst_team_list?academic_year={academic_year}"
        f"&conf_id=-1&division={division}&sport_code=MFB"
    )


def parse_team_ids(html: str) -> List[str]:
    """Distinct ``/teams/{id}`` ids from an MFB team-list page (order preserved)."""
    return list(dict.fromkeys(_TEAM_ID_RE.findall(html or "")))


def parse_contest_ids(html: str) -> List[str]:
    """Distinct ``/contests/{id}`` ids from a team page (order preserved)."""
    return list(dict.fromkeys(_CONTEST_ID_RE.findall(html or "")))


def browser_fetch_fn(proxy_pool: "Optional[List[str]]" = None) -> FetchFn:
    """Build a live ``(path) -> html`` fetch backed by one NcaaFetcher browser
    session. Pass a US-residential ``proxy_pool``; hold the session (no per-call
    relaunch) to avoid the patchright relaunch-storm crash."""
    from sportsdataverse.mbb.mbb_ncaa_fetch import NcaaFetcher

    fetcher = NcaaFetcher.with_browser(proxy_pool=proxy_pool)
    return lambda path: fetcher.fetch_html(path, force=True)


def discover_season(
    academic_year: int, division: int = 11, *, fetch_fn: Optional[FetchFn] = None
) -> List[str]:
    """Discover every MFB ``contest_id`` in a season (team list -> team pages -> dedup).

    Args:
        academic_year: e.g. ``2025`` for the 2024-25 (fall 2024) season.
        division: 11 = FBS (default), 12 = FCS.
        fetch_fn: ``(path) -> html``. Defaults to a live browser session.

    Returns:
        Sorted, de-duplicated list of ``contest_id`` strings.

    Raises:
        ValueError: the team list resolved zero teams (bad year/division or a
            fetch failure) -- raised loudly instead of returning a hollow list.
    """
    fetch = fetch_fn or browser_fetch_fn()
    teams = parse_team_ids(fetch(team_list_path(academic_year, division)))
    if not teams:
        raise ValueError(
            f"no MFB teams for academic_year={academic_year} division={division}"
        )
    contests: "set[str]" = set()
    for team_id in teams:
        contests.update(parse_contest_ids(fetch(f"teams/{team_id}")))
    return sorted(contests)
