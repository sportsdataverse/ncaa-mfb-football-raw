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
  ncaa_mfb_01_schedules_scrape.py  # numbered stage shims (see RUNBOOK.md):
  ncaa_mfb_02_games_scrape.py      #   thin argparse wrappers over mfb_run /
  ncaa_mfb_04_rosters_scrape.py    #   mfb_datasets; 03 (parse) is a hole --
  ncaa_mfb_05_datasets_build.py    #   parsing lives in sdv-py, runs in 05
scripts/
  _env.sh           # sourced by every launcher: venv, transport, logging
  run_01_schedules.sh  run_02_games.sh  run_04_rosters.sh  run_05_datasets.sh  run_06_xwalk.sh
  run_mfb_capture.sh   # combined one-session runner (01 + 04 + 02)
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

Numbered stages (full table, resumability, backfill: [RUNBOOK.md](RUNBOOK.md)):

```sh
export NCAA_VENDOR=decodo_patchright                                  # canary_vendors.toml creds
./scripts/run_01_schedules.sh --academic-year 2026 --division 11     # discovery (team pages)
./scripts/run_04_rosters.sh   --academic-year 2026 --division 11     # roster pages
./scripts/run_02_games.sh     --academic-year 2026 --division 11 --max-contests 200   # game bundles
./scripts/run_05_datasets.sh  --academic-year 2026                   # OFFLINE: tidy parquet
./scripts/run_06_xwalk.sh     --academic-year 2026                   # OFFLINE: NCAA<->ESPN game crosswalk -> mfb/xwalk/
# watch: tail -f logs/<schedules|rosters|games|datasets>_<ts>.log
```

`academic_year` is the ENDING year (2026 = fall-2025 season). Division 11 =
FBS, 12 = FCS (run the online stages once per division). All stages are
file-exists resumable; a consecutive-failure breaker hard-stops ban storms.
`./scripts/run_mfb_capture.sh` is the combined one-session runner (01 + 04 +
02) for chunked one-shot runs. Backfill = the same stages with a different
`--academic-year`.

## Known source gaps (2025 season)

* **No pbp published** for 2 of 1,687 contests — Furman @ Campbell
  (`6419926`, 09/13) and Bethune-Cookman @ Grambling (`6400590`, 11/08):
  stats.ncaa.org serves a ~21 KB stub. Finals live in the schedule master.
* **OT drives are omitted from pbp pages** — reconstructed one-row-per-drive
  from the drives tab + scoring-summary checkpoints, flagged
  `ot_synthesized=True` in `pbp_cfbfastr`.
* Some FCS pages label drive h5 titles with the DEFENSE and/or misalign drive
  numbers across tabs; both are auto-corrected (drive-start-marker vote,
  score-based OT alignment). QA: `datasets/{ay}/qa_pbp_vs_linescore.parquet`
  — 1,685/1,685 exact finals for 2025.

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
