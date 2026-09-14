"""MFB season discovery: team list (sport_code=MFB) -> team pages -> contest_ids.

Pure parse functions + an injectable ``fetch_fn`` so discovery is fully offline-
testable; the live default drives ``NcaaFetcher.with_browser`` (real-GPU host +
US residential proxy pool -- see docs/DESIGN.md; datacenter IPs get an edge 403).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, List, Optional

FetchFn = Callable[..., str]  # (path, force=False) -> html

#: A real stats.ncaa.org page (team list / team page / roster) is >=40 KB of
#: framework HTML; a bm-verify stub or edge block is ~2 KB.
_MIN_PAGE_BYTES = 10_000

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
    return lambda path, force=True: fetcher.fetch_html(path, force=True)


def vendor_fetch_fn(
    root: "str | Path", *, shard_i: int = 0, shard_n: int = 1
) -> FetchFn:
    """Build a fetch from the ``NCAA_VENDOR`` canary-vendor transport
    (``canary_vendors.toml`` in ``root``, e.g. ``decodo_patchright`` -- the
    2026-07-16 canary PASS the mbb/wbb sweeps ride). Reuses the engine seam so
    sticky-session re-minting + shard offsetting come for free."""
    from sportsdataverse.scrape.ncaa.capture import _vendor_fetcher

    fetcher = _vendor_fetcher(
        os.environ["NCAA_VENDOR"], root, shard_i=shard_i, shard_n=shard_n
    )
    return lambda path, force=False: fetcher.fetch_html(path, force=force)


def _read_or_fetch(
    path: "Optional[Path]", fetch_fn: FetchFn, url_path: str, *, refresh: bool = False
) -> "tuple[str, bool]":
    """Fetch ``url_path``, persisting real pages to ``path`` (resume = read disk).

    Returns ``(html, fresh)``. ``fresh`` is False when the page came from disk.

    ``path=None`` (tests / no save_dir) never touches disk. A too-small body
    (bm-verify stub) is returned but NOT persisted, so a retry re-fetches.

    ``refresh=True`` bypasses BOTH caches -- the persisted page here and the
    fetcher's own ``.ncaa_fetch_cache`` (``force=True``). Discovery for an
    in-progress season must refresh: a team page only links a contest once the
    game is played, so a page saved in week 1 lists week-1 games forever. That
    froze fall 2026 at its 2026-09-09 snapshot -- every later run "captured 0",
    exit 0. A refresh that comes back as a stub or raises falls back to the
    saved copy (``fresh=False``) rather than shrinking the discovered set;
    the caller decides whether too many fallbacks is a failure.
    """
    cached = path.read_text(encoding="utf-8") if path is not None and path.exists() else None
    if cached is not None and not refresh:
        return cached, False
    try:
        html = (fetch_fn(url_path, force=True) if refresh else fetch_fn(url_path)) or ""
    except Exception:  # noqa: BLE001 - a transport failure on refresh keeps the saved page
        if cached is None:
            raise
        return cached, False
    if len(html) >= _MIN_PAGE_BYTES:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        return html, True
    return (cached, False) if cached is not None else (html, False)


def discover_teams(
    academic_year: int,
    division: int = 11,
    *,
    fetch_fn: "Optional[FetchFn]" = None,
    save_dir: "str | Path | None" = None,
    refresh: bool = False,
) -> List[str]:
    """Team ids for a season/division, persisting the team-list HTML when
    ``save_dir`` (repo root) is given -> ``mfb/teams/html/{ay}_div{d}.html``.
    ``refresh`` re-fetches instead of reading the saved list."""
    fetch = fetch_fn or browser_fetch_fn()
    tl_path = (
        Path(save_dir)
        / "mfb"
        / "teams"
        / "html"
        / f"{academic_year}_div{division}.html"
        if save_dir
        else None
    )
    html, _ = _read_or_fetch(
        tl_path, fetch, team_list_path(academic_year, division), refresh=refresh
    )
    teams = parse_team_ids(html)
    if not teams:
        raise ValueError(
            f"no MFB teams for academic_year={academic_year} division={division}"
        )
    return teams


def capture_rosters(
    team_ids: "List[str]",
    fetch_fn: FetchFn,
    out_dir: "str | Path",
    academic_year: int,
    *,
    max_consecutive_failures: int = 10,
    log_every: int = 25,
) -> "dict[str, int]":
    """Persist ``teams/{id}/roster`` pages -> ``mfb/rosters/html/{ay}/{id}.html``.

    Idempotent (file-exists resume); a consecutive-failure breaker hard-stops a
    ban storm, mirroring :func:`mfb_capture.capture_season`.
    """
    stats = {"captured": 0, "skipped": 0, "failed": 0}
    consecutive = 0
    for i, team_id in enumerate(team_ids, 1):
        path = (
            Path(out_dir)
            / "mfb"
            / "rosters"
            / "html"
            / str(academic_year)
            / f"{team_id}.html"
        )
        if path.exists():
            stats["skipped"] += 1
        else:
            try:
                html = fetch_fn(f"teams/{team_id}/roster") or ""
            except Exception:  # noqa: BLE001 - transport failure = failed roster, breaker counts it
                html = ""
            if len(html) >= _MIN_PAGE_BYTES:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(html, encoding="utf-8")
                stats["captured"] += 1
                consecutive = 0
            else:
                stats["failed"] += 1
                consecutive += 1
                if consecutive >= max_consecutive_failures:
                    raise RuntimeError(
                        f"{consecutive} consecutive roster failures -- breaker tripped; {stats}"
                    )
        if log_every and i % log_every == 0:
            print(f"rosters {i}/{len(team_ids)}: {stats}", flush=True)
    return stats


def discover_season(
    academic_year: int,
    division: int = 11,
    *,
    fetch_fn: Optional[FetchFn] = None,
    save_dir: "str | Path | None" = None,
    log_every: int = 25,
    refresh: bool = False,
) -> List[str]:
    """Discover every MFB ``contest_id`` in a season (team list -> team pages -> dedup).

    Args:
        academic_year: ENDING year -- ``2026`` = the fall-2025 season.
        division: 11 = FBS (default), 12 = FCS.
        fetch_fn: ``(path) -> html``. Defaults to a live browser session.
        save_dir: repo root; when given, persists the team list to
            ``mfb/teams/html/`` and each team page (the schedule source) to
            ``mfb/schedules/html/{ay}/{team_id}.html``, and re-runs read the
            persisted pages instead of re-fetching (resume).
        log_every: progress print cadence over the team-page sweep (0 = quiet).
        refresh: re-fetch the team list and every team page, bypassing both
            the persisted pages and the fetcher cache. REQUIRED for a season
            still being played (see :func:`_read_or_fetch`); leave False for a
            completed season, where the saved pages are final.

    Returns:
        Sorted, de-duplicated list of ``contest_id`` strings.

    Raises:
        ValueError: the team list resolved zero teams (bad year/division or a
            fetch failure) -- raised loudly instead of returning a hollow list.
        RuntimeError: ``refresh`` was requested but more than
            ``MFB_REFRESH_MAX_STALE_FRAC`` (env, default 0.5) of team pages
            fell back to their saved copy -- a blocked transport must fail the
            run, not quietly re-serve last week's schedule.
    """
    fetch = fetch_fn or browser_fetch_fn()
    teams = discover_teams(
        academic_year, division, fetch_fn=fetch, save_dir=save_dir, refresh=refresh
    )
    contests: "set[str]" = set()
    stale = 0
    for i, team_id in enumerate(teams, 1):
        page_path = (
            Path(save_dir)
            / "mfb"
            / "schedules"
            / "html"
            / str(academic_year)
            / f"{team_id}.html"
            if save_dir
            else None
        )
        html, fresh = _read_or_fetch(page_path, fetch, f"teams/{team_id}", refresh=refresh)
        stale += refresh and not fresh
        contests.update(parse_contest_ids(html))
        if log_every and i % log_every == 0:
            print(f"team pages {i}/{len(teams)}: {len(contests)} contests", flush=True)
    if refresh:
        max_frac = float(os.environ.get("MFB_REFRESH_MAX_STALE_FRAC", "0.5"))
        print(
            f"refresh: {len(teams) - stale}/{len(teams)} team pages re-fetched, "
            f"{stale} fell back to the saved copy",
            flush=True,
        )
        if teams and stale / len(teams) > max_frac:
            raise RuntimeError(
                f"refresh: {stale}/{len(teams)} team pages could not be re-fetched "
                f"(> {max_frac:.0%}) -- discovery is stale, failing the run"
            )
    return sorted(contests)
