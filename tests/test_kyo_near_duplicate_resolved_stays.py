"""KYO-532: a resolved near-duplicate-criteria flag stays resolved until either text moves."""

from __future__ import annotations

from pathlib import Path

FLAG_TYPE = "near-duplicate-criteria"
TEXT = "the sweep honours a resolved flag until the finding moves on this record"


def _promote(kyo, project: Path, write_file, name: str, text: str) -> str:
    (project / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDo {name}.\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file(f"{name}-c.txt", text + "\n"),
        "--touches-file", write_file(f"{name}-t.txt", f"{name}.py\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _raised(sweep) -> list[dict]:
    assert sweep.code == 0, sweep
    return [f for f in sweep.json["flags"] if f["flag_type"] == FLAG_TYPE]


def _pair_resolved(kyo, project: Path, write_file) -> tuple[str, str]:
    a = _promote(kyo, project, write_file, "aa", TEXT)
    b = _promote(kyo, project, write_file, "bb", TEXT)
    raised = _raised(kyo("sweep", "--json"))
    assert len(raised) == 1, raised
    done = kyo("flag", "resolve", raised[0]["flag_id"], "--why", "read both; not duplicates")
    assert done.code == 0, done
    return a, b


def test_resolved_pair_is_not_raised_again(kyo, project: Path, write_file) -> None:
    """Fails before the fix: the next sweep re-files the identical pair."""
    _pair_resolved(kyo, project, write_file)
    for _ in range(2):
        assert _raised(kyo("sweep", "--json")) == []


def test_restating_either_criterion_raises_it_again(kyo, project: Path, write_file) -> None:
    a, b = _pair_resolved(kyo, project, write_file)
    assert _raised(kyo("sweep", "--json")) == []
    cid = kyo("show", b, "--json").json["criteria"][0]["id"]
    restateed = kyo(
        "restate", cid, "--text-file", write_file("new.txt", TEXT + " again\n"), "--json"
    )
    assert restateed.code == 0, restateed
    raised = _raised(kyo("sweep", "--json"))
    assert len(raised) == 1, raised
