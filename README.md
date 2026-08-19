# ncaa-mfb-football-raw

Raw scraper + captured game data for **NCAA Men's Football (MFB)** from
`stats.ncaa.org`, in the SportsDataverse Python stack. Mirrors
`ncaa-mbb-hoops-raw` (discover → capture → parse → datasets) and rides the
canary-proven `decodo_patchright` transport (patchright + `--headless=new` +
Decodo US sticky residential; see `canary_vendors.toml.example`).

**Status:** live capture running for the 2025 season (`academic_year=2026`).
Discovery, schedules, rosters, and 6-tab game bundles are wired end-to-end;
parsing graduates to sdv-py (`cfb_ncaa_pbp` / `cfb_ncaa_box`), with the
cfbfastR column-parity mapping prototyped here (`mfb_cfbfastr.py`, 102
cfbfastR-named columns incl. parity-tested running scores). See
[docs/DESIGN.md](docs/DESIGN.md) for the phased plan and decision log.

## Layout

```
python/
  mfb_discover.py   # team list (sport_code=MFB) -> team pages -> contest_ids
                    # + persisted teams/schedules HTML + roster sweep
  mfb_capture.py    # 6-tab gzip JSON bundle per contest -> mfb/json/
  mfb_run.py        # live runner: discovery + rosters + game bundles
  mfb_parse.py      # (superseded by sdv-py cfb_ncaa_pbp; kept for history)
  mfb_cfbfastr.py   # NCAA structural pbp -> cfbfastR-named columns (prototype)
  mfb_datasets.py   # offline: HTML + bundles -> tidy season parquet
tests/fixtures/     # real captured pages (parser ground truth)
mfb/
  teams/{html,parquet}/         # team lists per (ay, division)
  schedules/{html,parquet}/     # team pages + schedule master
  rosters/html/{ay}/            # team roster pages
  json/                         # gzip game bundles (pbp + 5 detail tabs)
  datasets/{ay}/                # built parquet (pbp, pbp_cfbfastr, boxes, ...)
docs/DESIGN.md
```

## Running

```sh
NCAA_VENDOR=decodo_patchright ./scripts/run_mfb_capture.sh \
    --academic-year 2026 --division 11 --rosters --max-contests 100
# watch: tail -f logs/mfb_capture_*.log
```

`academic_year` is the ENDING year (2026 = fall-2025 season). Division 11 =
FBS, 12 = FCS. All stages are file-exists resumable; a consecutive-failure
breaker hard-stops ban storms. Offline dataset build (no network):

```sh
PYTHONPATH=/mnt/sdv_repos/sdv-py:python \
  /mnt/sdv_repos/sdv-py/.venv/bin/python python/mfb_datasets.py --academic-year 2026
```

## Game-detail tabs

Each bundle captures `play_by_play` (validity gate) plus `box_score`,
`team_stats`, `individual_stats`, `drives`, `officials`. There is **no
participation tab** on stats.ncaa.org football pages — `individual_stats`
(anyone credited with a stat) is the closest surface.

## Runtime

Requires a US residential transport — datacenter IPs get an instant Akamai
edge 403. Capture holds ONE browser session (a rapid relaunch storm across
proxies crashes the patchright driver with EPIPE); sticky session ids are
re-minted per run by the sdv-py vendor seam.
