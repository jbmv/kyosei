"""KYO-944.c8: the Mac app's check harness runs on request, and never in ``make test`` or CI.

The harness (``apps/apple/check``) starts the Mac app and puts its window on the screen of
whoever is logged in, so it must not be collected by the suite or run on a CI machine. It is
kept out by where it is: ``pyproject.toml`` collects ``tests/`` alone. These tests fail if
that stops being so, or if ``make app-check`` stops running the harness and saying where its
result file is.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "apps" / "apple" / "check"


def _recipe(target: str) -> str:
    """What ``make`` would run for ``target``, printed and not run."""
    done = subprocess.run(
        ["make", "-n", target], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_suite_collects_tests_and_not_the_harness() -> None:
    options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert options["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert list(HARNESS.glob("test_*.py")), "the harness has no test file to keep out"
    assert ROOT / "tests" not in HARNESS.parents


def test_make_test_does_not_name_the_harness() -> None:
    recipe = _recipe("test")
    assert "pytest" in recipe
    assert "apps/apple" not in recipe and "app-check" not in recipe


def test_ci_does_not_run_the_harness() -> None:
    workflows = list((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflows
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        assert "app-check" not in text, workflow
        assert "apps/apple/check" not in text, workflow
        assert "package.sh" not in text, workflow


def test_make_app_check_runs_the_harness_and_prints_the_result_files_path() -> None:
    recipe = _recipe("app-check")
    build = recipe.index("apps/apple/package.sh check")
    run = recipe.index("pytest apps/apple/check")
    said = recipe.index("result.json")
    assert build < run < said
    # The path is printed whether the harness passed or not, and its status is make's.
    assert "s=$?" in recipe and "exit $s" in recipe
