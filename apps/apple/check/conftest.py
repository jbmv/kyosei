"""Fixtures of the Mac app's check harness. See ``harness.py`` for what a run is kept from."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import frontmost  # noqa: E402
import harness  # noqa: E402

#: Where a run writes ``result.json``, the review sheet and the screenshots.
OUT = Path(os.environ.get("KYOSEI_CHECK_OUT") or harness.APPLE / ".build" / "check-run")


#: The log of the app in front (``frontmost.py``). ``make app-check`` starts the sampling
#: before it builds anything and names the log here; a run of pytest alone samples itself.
FRONT_LOG = Path(os.environ.get("KYOSEI_CHECK_FRONT_LOG") or OUT / "frontmost.jsonl")


class Results:
    def __init__(self) -> None:
        self.scenarios: list[harness.Scenario] = []
        self.measurements: dict[str, object] = {}
        self.server_version = ""
        self.front_log = FRONT_LOG


@pytest.fixture(scope="session", autouse=True)
def results() -> Iterator[Results]:
    """The run's result, written when the session ends, whatever passed. The app in front
    is sampled for as long as the session lasts (KYO-945)."""
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.iterdir():
        if old != FRONT_LOG or "KYOSEI_CHECK_FRONT_LOG" not in os.environ:
            shutil.rmtree(old) if old.is_dir() else old.unlink()
    watcher = None
    if "KYOSEI_CHECK_FRONT_LOG" not in os.environ:
        watcher = subprocess.Popen(  # noqa: S603
            [sys.executable, str(harness.HERE / "frontmost.py"), "watch", str(FRONT_LOG)]
        )
    collected = Results()
    try:
        yield collected
    finally:
        if watcher is not None:
            watcher.terminate()
            watcher.wait(timeout=10)
        harness.write_result(
            OUT,
            collected.scenarios,
            server_version=collected.server_version,
            measurements=collected.measurements,
            front_log=FRONT_LOG,
        )
        harness.unregister()


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Each test is a step of the run in the log of the app in front."""
    if FRONT_LOG.parent.exists():
        frontmost.mark(FRONT_LOG, f"pytest {item.nodeid}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The judgement of the app in front is made last, over everything the run did."""
    items.sort(key=lambda item: "test_zz_frontmost" in item.nodeid)


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
