# NCAA Men's Football (MFB) play-by-play — design + plan

**Status:** design locked 2026-07-16 (brainstorming). Feasibility probed live.
**Goal:** full structured play-by-play for NCAA football from stats.ncaa.org, in
the SportsDataverse Python stack, mirroring the MBB pipeline and the working
patchright/bm-verify transport shipped in sdv-py #271.

## Understanding summary

- **Feasible (probed live 2026-07-16):** stats.ncaa.org serves ~99 KB drive-based
  MFB play-by-play at `/contests/{id}/play_by_play`, gated by Akamai bm-verify
  (cleared by `NcaaFetcher.with_browser` — patchright + `--headless=new` + real UA
  + US residential). Markup: `div.drives` → per-play `div.scoring_play` /
  `div.non_scoring_play` (+ `h5` headers). Keyword census on one game: drive×33,
  punt×24, quarter×7, kickoff×5, touchdown×4, field-goal×2.
- **Discovery:** MFB team list `team/inst_team_list?academic_year={ay}&division={div}&sport_code=MFB`
  (division 11 = FBS → 134 teams for ay=2025). Each team page `/teams/{id}`
  carries ~12 `/contests/{id}/box_score` links → contest IDs. (`team/schedule_list`
  exists but returned only 1 contest — team pages are the discovery source.)
- **The crux is the parser** — football-specific, drive-based, turning play *text*
  into structured fields (like cfbfastR/nflfastR do for ESPN).

## Assumptions

1. Same repo/module pattern as MBB: this `ncaa-mfb-hoops-raw` repo (discover →
   capture → parse, raw committed) + sdv-py `sportsdataverse/football/mfb_ncaa_*`
   (or `cfb/`-adjacent) modules. Fetch layer is **already shared** (mbb_ncaa_fetch).
2. Capture reuses `NcaaFetcher` as-is. **Caveat (from the probe):** do NOT
   rapid-relaunch the browser across proxies on cold-start misses — a 10-proxy
   relaunch storm crashed the patchright Node driver (EPIPE). Capture must pace
   rotation (solve once, hold the IP, rotate only on real ban / rotate_every).
3. FBS (division 11) first; FCS/D2/D3 later.
4. Scope of "full structured parse" = the structured **pbp frame** (down, distance,
   yard line, play type, yards gained, players, scoring, drive id/context). EPA/WP
   **models are downstream** (separate effort, mirroring cfbfastR's parse/model split).

## Decision log

| Decision | Alternatives | Why |
|---|---|---|
| Source = stats.ncaa.org | ESPN (cfbfastR already covers ESPN MFB) | User needs all-division official data; probed it has pbp |
| Football first | baseball (has baseballr reference) | User's call; de-risked via live probe |
| Mirror MBB pipeline + shared fetch | new bespoke stack | Reuse the working transport + repo shape |
| Parse = drive-based (`.scoring_play`/`.non_scoring_play`) | table scrape (only 3 tables/9 rows — wrong) | Matches the actual markup |
| Structured frame now, EPA/WP later | full model suite up front | Parse and models are separable (cfbfastR precedent) |

## Design

### Pipeline (mirrors `ncaa-mbb-hoops-raw`)
1. **Discover** (`python/mfb_discover.py`): team list (sport_code=MFB, division) →
   team pages → dedup `contest_id`s → `schedule_master.parquet`.
2. **Capture** (`python/mfb_capture.py`): `NcaaFetcher.with_browser` fetches
   `/contests/{id}/play_by_play` (+ `box_score`) → committed raw HTML/JSON bundle.
   Idempotent (file-exists resume). Paced rotation (no relaunch storm).
3. **Parse** (`python/mfb_parse.py` + sdv-py parser): `div.drives` →
   `.scoring_play`/`.non_scoring_play` → one row per play; extract structured
   fields from the play text.
4. **Publish** (`ncaa-mfb-hoops-data`): tidy parquet + gh release (later).

### Parser target columns (structured frame)
`contest_id, drive_id, drive_number, play_number, quarter, clock, offense, defense,
down, distance, yard_line (side+num), play_type, yards_gained, scoring (bool),
score_offense, score_defense, players (rusher/passer/receiver/tackler...), play_text`.

### Validation
- Parse against **real captured fixtures** (never synthetic) — one FBS game first.
- Cross-check vs cfbfastR/ESPN for the same game where both cover it (parity spot-check).
- Empty/failed pages return the documented schema (never raise per-family).

## Phased implementation plan

- **Phase 1 — discovery + capture + 1 fixture.** MFB discovery (team list → contest
  ids), capture module, and commit ONE real FBS game's raw pbp as the parser fixture.
  Reuses the transport; lowest risk. *Deliverable: a captured `contests/{id}/play_by_play`.*
- **Phase 2 — drive/play structural parse. ✓ DONE** (`python/mfb_parse.py`,
  `parse_mfb_pbp`). Parses the FULL game (205 plays from fixture 5362535) → one
  row per play with drive_number/offense/drive_result/drive_scored + down/
  distance/yard_line + raw play_text. 6 offline tests green. Note: rows include
  "drive start at" markers + the opening kickoff (Phase 3 classifies/filters).
- **Phase 3 — structured field extraction (NEXT).** Decompose `play_text` →
  play_type / players / yards_gained / end_yard_line / formation, cfbfastR-style.
  Grammar (from the fixture): names are `Last,First`; formation prefix like
  `No Huddle-Shotgun`; verbs `rush {dir} for {n} yards {gain|loss} to the {yl}
  ({tacklers})`, `pass complete {depth} {dir} to {recv} caught at {yl}, for {n}
  yards to the {yl}`, `kickoff {n} yards to {yl} {returner} return {n} yards`,
  `punt {n} yards to {yl} fair catch by {r}`, `field goal`, `PENALTY {team} ...`.
  Tacklers in parens, multiple separated by `;`. Build the regex against the
  committed fixture + add fixtures for pass/rush/punt/FG/penalty/scoring variety.
- **Phase 4 — validation + scale.** Parity spot-check vs cfbfastR; season backfill
  with paced rotation; publish to `-data`.

## Open risks

1. **Play-text grammar** is the hard part (Phase 3) — NCAA official text differs
   from ESPN; regex must be built + validated against real fixtures.
2. **IP rotation at scale** (the standing #2 follow-up) — residential is
   semi-consumable; capture needs paced rotation across a pool.
3. **Transport relaunch storm** (EPIPE) — capture must avoid rapid browser
   relaunches; prefer holding an IP and rotating on real failure only.
