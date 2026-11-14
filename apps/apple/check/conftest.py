"""Fixtures of the Mac app's check harness. See ``harness.py`` for what a run is kept from."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402

#: Where a run writes ``result.json``, the review sheet and the screenshots.
OUT = Path(os.environ.get("KYOSEI_CHECK_OUT") or harness.APPLE / ".build" / "check-run")


class Results:
    def __init__(self) -> None:
        self.scenarios: list[harness.Scenario] = []
        self.measurements: dict[str, object] = {}
        self.server_version = ""


@pytest.fixture(scope="session")
def results() -> Iterator[Results]:
    """The run's result, written when the session ends, whatever passed."""
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    collected = Results()
    yield collected
    harness.write_result(
        OUT,
        collected.scenarios,
        server_version=collected.server_version,
        measurements=collected.measurements,
    )
    harness.unregister()


@pytest.fixture
def run() -> Iterator[harness.Run]:
    made = harness.Run()
    try:
        yield made
    finally:
        made.stop()


@pytest.fixture
def check_build() -> Path:
    """The check build, which ``make app-check`` builds before the harness runs."""
    return harness.require_check_build()
