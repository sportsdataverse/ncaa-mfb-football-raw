# RUNBOOK — ncaa-mfb-football-raw

Numbered, idempotent pipeline stages (SDV pipeline-stage convention). Numbers
mirror `ncaa-mbb-hoops-raw` / `ncaa-wbb-hoops-raw` so a stage number means the
same thing across the NCAA raw repos: **03 (parse) is a deliberate hole** —
MFB parsing graduated to sdv-py (`cfb_ncaa_pbp` / `cfb_ncaa_box`) and runs
inside stage 05. A retired stage leaves a hole rather than renumbering.

Every stage shim in `python/` is a thin argparse wrapper that delegates to the
working modules (`mfb_run.main` / `mfb_datasets.main`); `mfb_run.py` stays the
combined one-session runner (`scripts/run_mfb_capture.sh`).

`--academic-year` is the **ENDING** year (2026 = fall-2025 season).
`--division 11` = FBS, `12` = FCS — run online stages once per division.

## Stages

| NN | stage | entrypoint | launcher | online? | resumability | typical invocation |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | discovery: team list + team pages → contest_ids | `python/ncaa_mfb_01_schedules_scrape.py` | `scripts/run_01_schedules.sh` | online (≈1 page/team) | persisted `mfb/teams/html/`, `mfb/schedules/html/{ay}/` re-read, not re-fetched | `NCAA_VENDOR=decodo_patchright ./scripts/run_01_schedules.sh --academic-year 2026 --division 11` |
| 02 | games: 6-tab bundle per contest → `mfb/raw/{ay}/{contest_id}.json.gz` | `python/ncaa_mfb_02_games_scrape.py` | `scripts/run_02_games.sh` | online (3–6 pages/game) | captured contests skipped; ban ⇒ rc=1 hard-stop, re-run resumes; chunk `--max-contests`, fan out `--shard i/N` (one process each) | `NCAA_VENDOR=decodo_patchright ./scripts/run_02_games.sh --academic-year 2026 --division 11 --max-contests 200` |
| 03 | *(hole — parse lives in sdv-py, runs in 05)* | — | — | — | — | — |
| 03 | parse: raw bundles → parsed + enriched per-game JSON `mfb/json/{contest_id}.json.gz` (espn_game_id, teams block) | `python/ncaa_mfb_03_games_parse.py` | `scripts/run_03_parse.sh` | **offline** | outputs skipped unless `--force`; run stage 06 first for enrichment | `./scripts/run_03_parse.sh --academic-year 2026 --workers 8` |
| 04 | rosters: `teams/{id}/roster` → `mfb/rosters/html/{ay}/` | `python/ncaa_mfb_04_rosters_scrape.py` | `scripts/run_04_rosters.sh` | online (≈1 page/team) | teams with roster html skipped | `NCAA_VENDOR=decodo_patchright ./scripts/run_04_rosters.sh --academic-year 2026 --division 11` |
| 05 | datasets: HTML → `mfb/{teams,rosters,schedules}/parquet/` reference frames (game datasets build in `ncaa-mfb-football-data`) | `python/ncaa_mfb_05_datasets_build.py` | `scripts/run_05_datasets.sh` | **offline** (no proxy) | pure function of the tree; re-run overwrites; NOT sharded (one file per kind) | `./scripts/run_05_datasets.sh --academic-year 2026` |
| 06 | xwalk: NCAA↔ESPN game crosswalk → `mfb/xwalk/espn_game_id/{ay}.json` (+ voted team map) | `python/ncaa_mfb_06_xwalk_build.py` | `scripts/run_06_xwalk.sh` | offline + one `load_cfb_schedule` release read per season | re-run overwrites | `./scripts/run_06_xwalk.sh --academic-year 2026` |
| — | full historical backfill orchestrator (01+04+02+05 per season, newest→oldest, commits per stage) | — | `scripts/run_backfill_all.sh` | online | every stage resumable | `NCAA_VENDOR=decodo_patchright ./scripts/run_backfill_all.sh 2026 2014` |
| — | combined runner (01 + 04 + 02 in one browser session) | `python/mfb_run.py` | `scripts/run_mfb_capture.sh` | online | as above | `NCAA_VENDOR=decodo_patchright ./scripts/run_mfb_capture.sh --academic-year 2026 --rosters --max-contests 20` |

`scripts/_env.sh` is sourced by every launcher (not run): repo root, sdv-py
sibling venv resolution (droplet `/mnt/sdv_repos/sdv-py` first, then the
Windows dev box), `PYTHONPATH`, `PYTHONUNBUFFERED`, transport (`NCAA_VENDOR`
preferred; `MFB_PROXY_POOL` from `.Renviron` Decodo creds as fallback;
`OFFLINE=1` skips it), timestamped `logs/<stage>_<ts>.log` with a printed
`tail -f` watch line, and an `EXIT=<rc>` trailer that propagates the Python
exit code.

## Run order (one season)

```sh
export NCAA_VENDOR=decodo_patchright          # creds in canary_vendors.toml (gitignored)
for d in 11 12; do
  ./scripts/run_01_schedules.sh --academic-year 2026 --division $d
  ./scripts/run_04_rosters.sh   --academic-year 2026 --division $d
  ./scripts/run_02_games.sh     --academic-year 2026 --division $d --max-contests 200   # repeat until 0 new
done
./scripts/run_05_datasets.sh --academic-year 2026                                      # offline, once
./scripts/run_06_xwalk.sh    --academic-year 2026                                      # offline: ESPN crosswalk
./scripts/run_03_parse.sh    --academic-year 2026                                      # offline: parsed+enriched json
```

Watch any stage live: `tail -f logs/<schedules|games|rosters|datasets>_<ts>.log`
(the path is printed at start). Completion: grep `EXIT=` in the log.

## Backfill

Same stages, different `--academic-year` — no separate implementation:

```sh
for ay in 2025 2024; do
  for d in 11 12; do
    ./scripts/run_01_schedules.sh --academic-year $ay --division $d
    ./scripts/run_04_rosters.sh   --academic-year $ay --division $d
    ./scripts/run_02_games.sh     --academic-year $ay --division $d --max-contests 300
  done
  ./scripts/run_05_datasets.sh --academic-year $ay
done
```

Resumable at every level: re-running any stage for a season that is already
complete fetches nothing (01/04 re-read persisted html, 02 skips captured
bundles) and exits 0. Pace rules (stats.ncaa.org is a hostile host — US
residential transport required, ban = hard stop, cool down before re-run) live
in README.md "Runtime" and `docs/DESIGN.md`.

## Tests

```sh
PYTHONPATH="/mnt/sdv_repos/sdv-py:$PWD/python" \
  /mnt/sdv_repos/sdv-py/.venv/bin/python -m pytest python/ tests/ -q
```

`python/test_mfb_stages.py` is the stage gate: each shim delegates with its
forced flags, each `run_NN_*.sh` invokes its own shim, and this runbook lists
every stage and launcher.
