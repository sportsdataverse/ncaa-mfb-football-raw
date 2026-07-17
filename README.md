# ncaa-mfb-hoops-raw

Raw scraper + captured play-by-play for **NCAA Men's Football (MFB)** from
`stats.ncaa.org`, in the SportsDataverse Python stack. Mirrors
`ncaa-mbb-hoops-raw` (discover → capture → parse) and reuses the working
Akamai bm-verify transport shipped in sportsdataverse-py #271
(`NcaaFetcher.with_browser` — patchright + `--headless=new` + real Chrome UA +
US residential proxy).

**Status:** design locked, Phase 1 in progress. See [docs/DESIGN.md](docs/DESIGN.md)
for the full design, phased plan, and decision log.

## Layout (planned)

```
python/
  mfb_discover.py   # team list (sport_code=MFB) -> team pages -> contest_ids
  mfb_capture.py    # NcaaFetcher.with_browser -> raw /contests/{id}/play_by_play
  mfb_parse.py      # div.drives -> scoring/non_scoring plays -> structured frame
tests/fixtures/     # real captured games (parser ground truth)
mfb/                # committed raw pbp bundles (the -data ingest reads these)
docs/DESIGN.md
```

## Fixture

`tests/fixtures/mfb_pbp_5362535.html` — a real FBS `play_by_play` page captured
live 2026-07-16 (team 589002, 2025 season), ~99 KB, drive-based
(`div.drives` → `div.scoring_play` / `div.non_scoring_play`). Ground truth for
the parser.

## Runtime

Requires a **real-GPU host** + a **US residential** proxy pool (datacenter gets an
edge 403). Capture must **pace rotation** — a rapid browser-relaunch storm across
proxies crashes the patchright driver (EPIPE); hold an IP, rotate on real failure.
