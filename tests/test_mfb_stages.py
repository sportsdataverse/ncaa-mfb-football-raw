"""Stage-shim tests: each numbered ``ncaa_mfb_NN_*`` entrypoint delegates to the
working ``mfb_*`` module with the right forced flags, and each
``scripts/run_NN_*.sh`` invokes its OWN shim (mirrors the mbb/wbb numbering gate).
No network, no filesystem writes."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# (shim module, delegate module, forced argv tail)
STAGES = (
    ("ncaa_mfb_01_schedules_scrape", "ncaa_mfb_raw_scrape.mfb_run", ("--skip-games",)),
    ("ncaa_mfb_02_games_scrape", "ncaa_mfb_raw_scrape.mfb_run", ()),
    ("ncaa_mfb_04_rosters_scrape", "ncaa_mfb_raw_scrape.mfb_run", ("--rosters", "--skip-games")),
    ("ncaa_mfb_05_datasets_build", "ncaa_mfb_raw_scrape.mfb_datasets", ()),
)


@pytest.mark.parametrize("shim,delegate,forced", STAGES)
def test_shim_delegates_with_forced_flags(monkeypatch, shim, delegate, forced) -> None:
    mod = importlib.import_module(shim)
    seen: list[list[str]] = []
    monkeypatch.setattr(
        importlib.import_module(delegate), "main", lambda argv: seen.append(argv) or 7
    )
    assert mod.main(["--academic-year", "2026"]) == 7
    assert seen == [["--academic-year", "2026", *forced]]


@pytest.mark.parametrize("shim,delegate,forced", STAGES)
def test_launcher_invokes_its_own_shim(shim, delegate, forced) -> None:
    num = shim.split("_")[2]
    (script,) = sorted(REPO.glob(f"scripts/run_{num}_*.sh"))
    text = script.read_text(encoding="utf-8")
    assert f"python/{shim}.py" in text
    assert 'source "$(dirname "$0")/_env.sh"' in text
    # 05 is the only offline stage: it must say so (and need no transport).
    assert ("OFFLINE=1 source" in text) == (num == "05")


def test_datasets_main_parses_argv() -> None:
    """mfb_datasets.main is the delegate for 05 -- argparse smoke, no build."""
    import ncaa_mfb_raw_scrape.mfb_datasets as mfb_datasets

    with pytest.raises(SystemExit) as ei:
        mfb_datasets.main(["--help"])
    assert ei.value.code == 0


def test_runbook_lists_every_stage() -> None:
    text = (REPO / "RUNBOOK.md").read_text(encoding="utf-8")
    for shim, _, _ in STAGES:
        assert shim in text, shim
    for script in REPO.glob("scripts/run_*.sh"):
        assert re.search(rf"\b{re.escape(script.name)}\b", text), script.name
