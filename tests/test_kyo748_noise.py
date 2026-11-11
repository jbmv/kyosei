"""KYO-748: three kinds of standing noise in the brief, at filing and at each prompt.

c4 (the build-change notice, once per build) is tested beside its siblings in
``test_kyo398_build_change.py``.
"""

from __future__ import annotations

from pathlib import Path

from kyosei.contracts import CRITERION_LENGTH_WARN

from .conftest import new_record, verify_criterion

DUP_A = "the export must round-trip through kyo rebuild without any drift"
DUP_B = "the export must round trip through kyo rebuild without any drift"


def _dup_pair(kyo) -> tuple[str, str]:
    a = new_record(kyo, kind="chore", title="First", criteria=[DUP_A])
    b = new_record(kyo, kind="chore", title="Second", criteria=[DUP_B])
    return a, b


def _flags(kyo, record: str) -> list[dict]:
    return kyo("show", record, "--json").json["flags"]


def _dup_flags(kyo, record: str) -> list[dict]:
    return [f for f in _flags(kyo, record) if f["flag_type"] == "near-duplicate-criteria"]


# ------------------------------------------------------------ c1 and c5: the pair's flag


def test_a_near_duplicate_flag_closes_when_either_record_settles(kyo) -> None:
    a, b = _dup_pair(kyo)
    assert kyo("sweep").code == 0
    (flag,) = _dup_flags(kyo, a)
    assert flag["resolved_at"] is None

    # The flag sits on `a`; it is `b` settling that must close it.
    assert kyo("wont-do", b, "--why", "superseded").code == 0
    (closed,) = _dup_flags(kyo, a)
    assert closed["resolved_at"] is not None
    assert b in closed["resolved_why"]
    assert closed["resolved_why"].startswith("settled automatically")


def test_a_near_duplicate_flag_closes_when_its_own_record_settles(kyo) -> None:
    a, _b = _dup_pair(kyo)
    assert kyo("sweep").code == 0
    verify_criterion(kyo, f"{a}.c1", "met")
    moved = kyo("move", a, "done", "--json")
    assert moved.code == 0, moved
    assert moved.json["flags_closed"] == 1
    assert _dup_flags(kyo, a)[0]["resolved_at"] is not None


def test_a_sweep_raises_no_near_duplicate_flag_against_a_settled_record(kyo) -> None:
    a, b = _dup_pair(kyo)
    assert kyo("wont-do", b, "--why", "superseded").code == 0
    assert kyo("sweep").code == 0
    assert _dup_flags(kyo, a) == []
    assert _dup_flags(kyo, b) == []


def test_settled_repair_closes_a_near_duplicate_flag_whose_other_record_settled(
    kyo, db
) -> None:
    a, b = _dup_pair(kyo)
    assert kyo("sweep").code == 0
    # Debt as the old build left it: the other record settled, the flag left standing.
    with db() as conn:
        conn.execute("UPDATE records SET status = 'wont-do' WHERE id = ?", (b,))
    dry = kyo("flag", "resolve", "--settled", "--why", "repair", "--json")
    assert dry.json["flags_closed"] == 1
    assert dry.json["by_type"] == {"near-duplicate-criteria": 1}
    assert _dup_flags(kyo, a)[0]["resolved_at"] is None
    applied = kyo("flag", "resolve", "--settled", "--apply", "--why", "repair", "--json")
    assert applied.json["flags_closed"] == 1
    assert _dup_flags(kyo, a)[0]["resolved_at"] is not None


# ------------------------------------------------------------------------------- c2


def test_the_brief_counts_flags_on_settled_records_and_names_the_listing(kyo, db) -> None:
    a, b = _dup_pair(kyo)
    assert kyo("sweep").code == 0
    brief = kyo("brief", "--json").json
    assert [f["flag_type"] for f in brief["unresolved_flags"]] == ["near-duplicate-criteria"]
    assert brief["unresolved_flags_on_settled"] == 0

    with db() as conn:  # a flag left on a settled record, as old stores hold them
        conn.execute("UPDATE records SET status = 'wont-do' WHERE id = ?", (b,))
    brief = kyo("brief", "--json").json
    assert brief["unresolved_flags"] == []
    assert brief["unresolved_flags_on_settled"] == 1
    text = kyo("brief", "--compact").out
    assert "1 more on settled records, not listed: `kyo flag list`" in text
    assert "near-duplicate-criteria: 1" not in text


# ------------------------------------------------------------------------------- c3

LONG = "a" * (CRITERION_LENGTH_WARN + 10) + "."


def test_several_criterion_warnings_print_as_one_counting_line(kyo, write_file) -> None:
    result = kyo(
        "new", "task", "--title", "short", "--criteria-file",
        write_file("c.txt", f"{LONG}\nfine.\n{LONG}x\na; b; c\n"),
    )
    assert result.code == 0, result
    warnings = [line for line in result.out.splitlines() if "warning:" in line]
    assert len(warnings) == 1
    assert "3 criteria" in warnings[0] and "--check" in warnings[0]


def test_one_criterion_warning_prints_as_before(kyo, write_file) -> None:
    result = kyo(
        "new", "task", "--title", "short", "--criteria-file",
        write_file("c.txt", f"{LONG}\nfine.\n"),
    )
    warnings = [line for line in result.out.splitlines() if "warning:" in line]
    assert len(warnings) == 1
    assert "criterion is" in warnings[0] and "--check" not in warnings[0]


def test_check_lists_each_criterion_warning_the_filing_counted(kyo, write_file) -> None:
    from_file = write_file(
        "rec.md",
        f"---\ntitle: a short title\n---\n\n## Criteria\n\n- {LONG}\n- fine.\n- {LONG}x\n",
    )
    result = kyo("new", "task", "--from", from_file, "--check")
    assert result.code == 0, result
    listed = [line for line in result.out.splitlines() if "warning: criterion" in line]
    assert len(listed) == 2
    assert "criterion 1" in listed[0] and "criterion 3" in listed[1]
    assert Path(from_file).exists()
