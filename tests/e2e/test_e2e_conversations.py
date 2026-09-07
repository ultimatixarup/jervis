"""Every scenario in `scenarios/` runs as its own test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from runner import Scenario, load_scenarios, run_scenario

SCENARIOS = load_scenarios()


def test_there_are_scenarios() -> None:
    assert SCENARIOS, "no YAML scenarios found"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario(scenario: Scenario, tmp_path: Path) -> None:
    if scenario.macos_only and sys.platform != "darwin":
        pytest.skip("scenario needs live macOS services")
    await run_scenario(scenario, tmp_path)
