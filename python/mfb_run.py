"""Live end-to-end runner: discover an MFB season -> capture rosters + raw bundles.

Holds ONE transport session (no per-call relaunch -> avoids the patchright EPIPE
relaunch storm). Transport preference order:

1. ``NCAA_VENDOR`` (e.g. ``decodo_patchright``) -- the canary-proven transport
   from ``canary_vendors.toml`` at the repo root (Decodo US sticky residential;
   sticky session ids re-minted per run). This is what the mbb/wbb sweeps ride.
2. ``MFB_PROXY_POOL`` (comma-separated US residential proxy URLs) driving a
   plain ``NcaaFetcher.with_browser`` session.

    NCAA_VENDOR=decodo_patchright \\
        python mfb_run.py --academic-year 2026 --division 11 --out .. --max-contests 20

Discovery persists the team list (``mfb/teams/html/``) and every team page
(``mfb/schedules/html/{ay}/`` -- the schedule source) as a side effect;
``--rosters`` additionally sweeps ``teams/{id}/roster`` pages. All stages are
file-exists resumable.
"""

from __future__ import annotations

import argparse
import os
import sys

from mfb_capture import capture_season
from mfb_discover import (
    browser_fetch_fn,
    capture_rosters,
    discover_season,
    discover_teams,
    vendor_fetch_fn,
)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--academic-year",
        type=int,
        default=2026,
        help="ENDING year: 2026 = fall-2025 season",
    )
    ap.add_argument("--division", type=int, default=11, help="11 = FBS, 12 = FCS")
    ap.add_argument("--out", default="..", help="repo root for the mfb/ raw tree")
    ap.add_argument(
        "--max-contests",
        type=int,
        default=None,
        help="stop after N new captures (chunk)",
    )
    ap.add_argument(
        "--rosters", action="store_true", help="also sweep teams/{id}/roster pages"
    )
    ap.add_argument(
        "--skip-games",
        action="store_true",
        help="discovery (+ rosters) only; no game bundles",
    )
    args = ap.parse_args(argv)

    if os.environ.get("NCAA_VENDOR"):
        print(
            f"transport: NCAA_VENDOR={os.environ['NCAA_VENDOR']} (canary_vendors.toml)",
            flush=True,
        )
        fetch = vendor_fetch_fn(args.out)
    else:
        pool = [p for p in os.environ.get("MFB_PROXY_POOL", "").split(",") if p.strip()]
        if not pool:
            print(
                "WARNING: no NCAA_VENDOR and MFB_PROXY_POOL empty -- capture needs a "
                "US residential transport",
                file=sys.stderr,
            )
        fetch = browser_fetch_fn(proxy_pool=pool or None)

    teams = discover_teams(
        args.academic_year, args.division, fetch_fn=fetch, save_dir=args.out
    )
    print(
        f"discovered {len(teams)} MFB teams (ay={args.academic_year} div={args.division})",
        flush=True,
    )
    ids = discover_season(
        args.academic_year, args.division, fetch_fn=fetch, save_dir=args.out
    )
    print(f"discovered {len(ids)} MFB contests", flush=True)

    if args.rosters:
        rstats = capture_rosters(teams, fetch, args.out, args.academic_year)
        print(f"rosters: {rstats}", flush=True)

    if args.skip_games:
        return 0
    stats = capture_season(ids, fetch, args.out, max_contests=args.max_contests)
    print(f"capture: {stats}", flush=True)
    # non-zero only if nothing captured AND something failed (a real ban), so a
    # fully-resumed run (all skipped) still exits 0.
    return 1 if stats["captured"] == 0 and stats["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
