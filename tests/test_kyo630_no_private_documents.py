"""KYO-630: planning documents live in the store repository, never in this one.

Proposals, measurements and other planning documents were written under `docs/proposals/`
while the store was committed beside the code. The code repository is published, so they
now live in the store's own repository, and nothing may be tracked under `docs/proposals/`
here again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORIES = ("docs/proposals/",)


def tracked_under(repo: Path, directories: tuple[str, ...]) -> list[str]:
    """The paths git tracks in ``repo`` under any of ``directories``."""
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--", *directories],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _is_git_checkout(path: Path) -> bool:
    probe = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def test_nothing_is_tracked_under_a_private_directory() -> None:
    if not _is_git_checkout(REPO):
        pytest.skip("not a git checkout, so there is no tracked tree to inspect")
    tracked = tracked_under(REPO, PRIVATE_DIRECTORIES)
    assert tracked == [], (
        "planning documents belong in the store repository, not here; untrack and move: "
        + ", ".join(tracked)
    )


def test_the_check_sees_a_tracked_proposal(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    proposal = tmp_path / "docs" / "proposals" / "a-plan.md"
    proposal.parent.mkdir(parents=True)
    proposal.write_text("# A plan\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs"], check=True)
    assert tracked_under(tmp_path, PRIVATE_DIRECTORIES) == ["docs/proposals/a-plan.md"]
