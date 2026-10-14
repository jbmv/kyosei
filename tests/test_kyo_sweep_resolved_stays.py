"""KYO-529: a resolved description-predates-claim flag stays resolved.

A consumer report: twice on one record a ``description-predates-claim``
flag resolved with ``kyo flag resolve`` came back on the next ``kyo sweep``, and only
editing the description stopped it. The cause: the sweep's "already flagged?" question
consulted UNRESOLVED flags only, and this class is re-derived whole from the store on
every run, so the moment a human resolved it the next sweep saw no standing flag and
filed the identical finding again. A resolution is a judgement of one finding -- this
description against this newest claim -- and it stands until either half moves.
"""

from __future__ import annotations

from pathlib import Path

FLAG_TYPE = "description-predates-claim"


def _record(kyo, project: Path, write_file) -> str:
    (project / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    idea = project / "ideas" / "d.md"
    idea.write_text("# d\n\nDo the thing.\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("d-criteria.txt", "it works\n"),
        "--touches-file", write_file("d-touches.txt", "alpha.py\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _claim(kyo, record: str, write_file, text: str) -> str:
    result = kyo(
        "claim", record, "--level", "asserted", "--method", "code read",
        "--file", write_file("c.md", text), "--json",
    )
    assert result.code == 0, result
    return kyo("show", record, "--json").json["claims"][-1]["id"]


def _raised(sweep) -> list[dict]:
    assert sweep.code == 0, sweep
    return [f for f in sweep.json["flags"] if f["flag_type"] == FLAG_TYPE]


def _open(kyo, record: str) -> list[dict]:
    return [
        f
        for f in kyo("show", record, "--json").json["flags"]
        if f["flag_type"] == FLAG_TYPE and f["resolved_at"] is None
    ]


def _raise_and_resolve(kyo, project: Path, write_file) -> tuple[str, str]:
    record = _record(kyo, project, write_file)
    claim_id = _claim(kyo, record, write_file, "the first finding")
    raised = _raised(kyo("sweep", "--json"))
    assert len(raised) == 1 and claim_id in raised[0]["detail"], raised
    resolved = kyo("flag", "resolve", raised[0]["flag_id"], "--why", "read it; still right")
    assert resolved.code == 0, resolved
    assert _open(kyo, record) == []
    return record, raised[0]["flag_id"]


def test_a_resolved_flag_is_not_raised_again_while_nothing_changes(
    kyo, project: Path, write_file
) -> None:
    """c1/c2. Fails before the fix: the second sweep re-files the identical finding."""
    record, _flag_id = _raise_and_resolve(kyo, project, write_file)

    for _ in range(2):
        assert _raised(kyo("sweep", "--json")) == []
        assert _open(kyo, record) == []


def test_a_newer_claim_raises_it_again(kyo, project: Path, write_file) -> None:
    """c2. Fails if a resolution suppressed the class for the record rather than the
    one finding it judged."""
    record, flag_id = _raise_and_resolve(kyo, project, write_file)
    assert _raised(kyo("sweep", "--json")) == []

    newer = _claim(kyo, record, write_file, "a newer finding")
    raised = _raised(kyo("sweep", "--json"))
    assert len(raised) == 1, raised
    assert newer in raised[0]["detail"]
    assert raised[0]["flag_id"] != flag_id
    assert [f["id"] for f in _open(kyo, record)] == [raised[0]["flag_id"]]


def test_a_description_edit_then_a_newer_claim_raises_it_again(
    kyo, project: Path, write_file
) -> None:
    """c2, the other half of the condition: an edited description clears the finding,
    and a claim filed after that edit raises a fresh one."""
    record, _flag_id = _raise_and_resolve(kyo, project, write_file)
    edited = kyo("edit", record, "--description-file", write_file("desc.md", "rewritten"))
    assert edited.code == 0, edited
    assert _raised(kyo("sweep", "--json")) == []

    newer = _claim(kyo, record, write_file, "found after the rewrite")
    raised = _raised(kyo("sweep", "--json"))
    assert len(raised) == 1 and newer in raised[0]["detail"], raised
