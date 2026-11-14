"""KYO-945: a run of the harness does not take the place of the app in front.

The first tests are of the judgement alone, on logs written here. The last reads the log of
this run, which ``make app-check`` began before it built anything, and fails if a process of
the run was in front at any sample or a check build says it became the active app. It runs
after every other test of the harness (``conftest.pytest_collection_modifyitems``).

A run under a locked screen passes whatever it would have done to an unlocked one; see
``frontmost.py``. The result file says whether the screen was locked.
"""

from __future__ import annotations

import json
from pathlib import Path

import frontmost
import harness

GAME = {
    "asn": "ASN:0x0-0x1",
    "name": "Game",
    "bundle": "com.example.game",
    "pid": 1,
    "executable": "/Applications/Game.app/Contents/MacOS/Game",
}  # noqa: E501
MAIL = {**GAME, "asn": "ASN:0x0-0x2", "name": "Mail", "bundle": "com.example.mail"}
CHECK = {**GAME, "asn": "ASN:0x0-0x3", "name": "KyoseiCheck", "bundle": harness.CHECK_IDENTIFIER}
RUNNER = {
    **GAME,
    "asn": "ASN:0x0-0x4",
    "name": "xctest",
    "bundle": "",
    "executable": "/Applications/Xcode.app/Contents/Developer/usr/bin/xctest",
}  # noqa: E501


def _log(path: Path, *rows: object) -> Path:
    lines = []
    count = 0
    for row in rows:
        if isinstance(row, str):
            lines.append({"t": float(count), "step": row})
        else:
            count += 1
            front, head = row  # type: ignore[misc]
            lines.append(
                {
                    "t": float(count),
                    "n": count,
                    "sample": {"front": front, "head": head, "order": [], "locked": False},
                }
            )  # noqa: E501
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def test_a_run_that_leaves_the_front_alone_passes(tmp_path: Path) -> None:
    judged = frontmost.judge(
        _log(tmp_path / "log", "package", (GAME, GAME), "pytest", (GAME, GAME))
    )
    assert judged["passed"] and judged["taken"] == [] and judged["samples"] == 2
    assert judged["screen_locked"] == [False]


def test_a_check_build_in_front_fails_the_run_and_names_the_step(tmp_path: Path) -> None:
    log = _log(
        tmp_path / "log",
        "swift test",
        (GAME, GAME),
        "pytest scenario F",
        (CHECK, CHECK),
        (GAME, GAME),
    )
    judged = frontmost.judge(log)
    assert not judged["passed"]
    assert [(taken["step"], taken["place"], taken["to"]) for taken in judged["taken"]] == [
        ("pytest scenario F", "front", harness.CHECK_IDENTIFIER),
        ("pytest scenario F", "head", harness.CHECK_IDENTIFIER),
    ]
    assert "pytest scenario F" in frontmost.sentence(judged)
    assert frontmost.main(["judge", str(log)]) == 1


def test_a_test_runner_in_front_fails_the_run(tmp_path: Path) -> None:
    judged = frontmost.judge(_log(tmp_path / "log", "swift test", (GAME, GAME), (RUNNER, GAME)))
    assert not judged["passed"] and judged["taken"][0]["step"] == "swift test"


def test_a_process_of_the_run_in_front_at_the_first_sample_fails_it(tmp_path: Path) -> None:
    assert not frontmost.judge(_log(tmp_path / "log", "pytest", (CHECK, GAME)))["passed"]


def test_the_persons_own_change_of_app_is_recorded_and_does_not_fail_the_run(
    tmp_path: Path,
) -> None:
    judged = frontmost.judge(_log(tmp_path / "log", "pytest", (GAME, GAME), (MAIL, MAIL)))
    assert judged["passed"]
    assert [(change["from"], change["to"]) for change in judged["other_changes"]] == [
        ("com.example.game", "com.example.mail")
    ] * 2


def test_a_run_that_was_not_sampled_does_not_pass(tmp_path: Path) -> None:
    assert not frontmost.judge(tmp_path / "absent")["passed"]
    assert not frontmost.judge(_log(tmp_path / "log", "pytest"))["passed"]


def test_the_screens_lock_is_read() -> None:
    assert frontmost.screen_is_locked() in (True, False)


def test_this_run_did_not_take_the_front(results) -> None:
    front = harness.front_of_the_run(results.front_log)
    results.measurements["front"] = {"said": front["said"], "screen_locked": front["screen_locked"]}
    assert front["sampled"], "the app in front was not sampled through this run"
    assert front["judgement"]["passed"], front["said"]
    took = [
        build
        for build in harness.FRONT_TAKEN
        if not build["asked_to_activate"] and (build["became_active"] or build["menus_on_screen"])
    ]
    assert not took, f"a check build became active or put a menu on the screen: {took}"
