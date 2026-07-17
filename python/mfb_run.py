"""Live end-to-end runner: discover an MFB season -> capture raw pbp bundles.

Holds ONE browser session (no per-call relaunch -> avoids the patchright EPIPE
relaunch storm). Proxy pool comes from ``MFB_PROXY_POOL`` (comma-separated US
residential sticky sessions). Chunk with ``--max-contests`` until IP rotation at
scale is hardened (see docs/DESIGN.md open risks).

    MFB_PROXY_POOL="http://user:pass@us.decodo.com:10001,...:10002" \\
        python mfb_run.py --academic-year 2025 --division 11 --out .. --max-contests 20
"""

from __future__ import annotations

import argparse
import os
import sys

from mfb_capture import capture_season
from mfb_discover import browser_fetch_fn, discover_season


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--academic-year", type=int, default=2025, help="2025 = fall-2024 season"
    )
    ap.add_argument("--division", type=int, default=11, help="11 = FBS, 12 = FCS")
    ap.add_argument("--out", default="..", help="repo root for the mfb/ raw tree")
    ap.add_argument(
        "--max-contests",
        type=int,
        default=None,
        help="stop after N new captures (chunk)",
    )
    args = ap.parse_args(argv)

    pool = [p for p in os.environ.get("MFB_PROXY_POOL", "").split(",") if p.strip()]
    if not pool:
        print(
            "WARNING: MFB_PROXY_POOL empty -- capture needs a US residential proxy pool",
            file=sys.stderr,
        )
    fetch = browser_fetch_fn(proxy_pool=pool or None)

    ids = discover_season(args.academic_year, args.division, fetch_fn=fetch)
    print(
        f"discovered {len(ids)} MFB contests (ay={args.academic_year} div={args.division})",
        flush=True,
    )

    stats = capture_season(ids, fetch, args.out, max_contests=args.max_contests)
    print(f"capture: {stats}", flush=True)
    # non-zero only if nothing captured AND something failed (a real ban), so a
    # fully-resumed run (all skipped) still exits 0.
    return 1 if stats["captured"] == 0 and stats["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
