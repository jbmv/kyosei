"""KYO-494: ``kyo confirm`` — "the maintainer looked and approves", as one act.

The design is KYO-481's. What this file checks is the property each design decision rests
on rather than the shortcut itself: that targets resolve in one fixed order (a named
criterion, the gate's own ids, the sole unobserved criterion, no target) and nowhere
else, that every refusal writes nothing and prints a
line that clears it when run, that the words land verbatim in evidence and never in a
gate's clearing reason, that only the answered human gate clears, that the walk stops at
any gate still open, and that a second run applies nothing twice.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

from kyosei.cmd_confirm import (
    CONTRACT_CONFIRM_AMBIGUOUS_CRITERIA,
    CONTRACT_CONFIRM_AMBIGUOUS_GATE,
    CONTRACT_CONFIRM_CRITERION_NOT_CONFIRMABLE,
    CONTRACT_CONFIRM_DISPATCH_RUNNING,
    CONTRACT_CONFIRM_GATE_NAMES_A_MISSING_CRITERION,
    CONTRACT_CONFIRM_GATE_NOT_CONFIRMABLE,
    CONTRACT_CONFIRM_NOTHING_TO_CONFIRM,
    CONTRACT_CONFIRM_RECORD_RETIRED,
)
from kyosei.contracts import CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES, load_status_ladder
from kyosei.errors import EXIT_CONTRACT

from .conftest import new_record

WORDS = "The maintainer: \"looks right\""


# ------------------------------------------------------------------------ helpers


def _quote_file(tmp_path: Path, text: str = WORDS, name: str = "confirm.txt") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _unobserved(kyo, criterion_id: str) -> None:
    done = kyo(
        "verify", criterion_id, "--verdict", "unmet-unobserved", "--method", "reasoned-about",
        "--why", "needs a person to look",
    )  # fmt: skip
    assert done.code == 0, done


def _gate(kyo, record: str, gate_type: str, do: str | None = None) -> str:
    args = ["gate", record, gate_type, "--json"]
    if do is not None:
        args[3:3] = ["--do", do]
    made = kyo(*args)
    assert made.code == 0, made
    return made.json["gate_id"]


def _show(kyo, record: str) -> dict:
    return kyo("show", record, "--json").json


def _states(kyo, record: str) -> dict[str, str]:
    return {c["id"]: c["state"] for c in _show(kyo, record)["criteria"]}


def _open_gates(kyo, record: str) -> list[str]:
    return [g["id"] for g in _show(kyo, record)["gates"] if not g.get("cleared_at")]


def _export(project: Path) -> dict[str, bytes]:
    root = project / "kyosei" / "export"
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def _ready_lines(text: str) -> list[list[str]]:
    """Every `kyo confirm ...` line a refusal printed, as argv for the runner."""
    out = []
    for line in text.splitlines():
        at = line.find("kyo confirm ")
        if at != -1:
            out.append(shlex.split(line[at:].rstrip("`"))[1:])
    return out


def _refused(kyo, project: Path, contract: str, *args: str):
    before = _export(project)
    result = kyo(*args)
    assert result.code == EXIT_CONTRACT, result
    text = result.out + result.err
    assert contract in text, text
    assert _export(project) == before, "a refusal writes nothing"
    return text


def _complete_status(project: Path) -> str:
    return load_status_ladder(project / "kyosei" / "statuses.yml").complete


# ------------------------------------------------------------------------------- c1


def test_confirm_needs_exactly_one_quote_source(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("confirm", record).code != 0
    both = kyo("confirm", record, "--quote", "ok", "--quote-file", _quote_file(tmp_path))
    assert both.code != 0 and "not both" in both.err


def test_a_quote_with_shell_residue_is_refused_and_names_its_file_twin(
    kyo, project: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    before = _export(project)
    result = kyo("confirm", record, "--quote", "it works with  and the banner")
    assert result.code != 0
    assert "--quote-file" in result.err
    assert _export(project) == before


def test_an_empty_quote_is_refused(kyo, project: Path, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    before = _export(project)
    empty = kyo("confirm", record, "--quote-file", _quote_file(tmp_path, "\n\n"))
    assert empty.code != 0 and "empty" in empty.err
    assert kyo("confirm", record, "--quote", "").code != 0
    assert _export(project) == before


# ------------------------------------------------------------------------------- c2


def test_rule_1_criterion_flag_overrides_the_gate_payload(kyo, tmp_path: Path) -> None:
    """An unmet-unbuilt criterion is allowed when named, and named means only it."""
    record = new_record(kyo, criteria=["a", "b"])
    _unobserved(kyo, f"{record}.c2")
    _gate(kyo, record, "awaiting:human-run", f"look at {record}.c2")
    done = kyo(
        "confirm", record, "--criterion", f"{record}.c1", "--no-close",
        "--quote-file", _quote_file(tmp_path), "--json",
    )  # fmt: skip
    assert done.code == 0, done
    assert done.json["rule"] == "criterion-flag"
    assert [t["criterion_id"] for t in done.json["targets"]] == [f"{record}.c1"]
    assert done.json["targets"][0]["prior_state"] == "unmet-unbuilt"
    assert _states(kyo, record) == {f"{record}.c1": "met", f"{record}.c2": "unmet-unobserved"}
    assert done.json["gates_left"], "the gate still waits on c2, which was not named"


def test_rule_2_the_gates_own_full_form_ids(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    _unobserved(kyo, f"{record}.c1")
    _unobserved(kyo, f"{record}.c2")
    gate = _gate(kyo, record, "awaiting:human-run", f"open the app; {record}.c2 is the banner")
    done = kyo("confirm", record, "--no-close", "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    assert done.json["rule"] == "gate-payload"
    assert _states(kyo, record)[f"{record}.c2"] == "met"
    assert _states(kyo, record)[f"{record}.c1"] == "unmet-unobserved"
    assert [g["gate_id"] for g in done.json["gates_cleared"]] == [gate]


def test_another_records_id_in_the_payload_is_ignored(kyo, tmp_path: Path) -> None:
    other = new_record(kyo, criteria=["x"])
    _unobserved(kyo, f"{other}.c1")
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    _gate(kyo, record, "awaiting:human-run", f"compare with {other}.c1, then check {record}.c1")
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    assert done.json["ignored"] == [f"{other}.c1"]
    assert _states(kyo, other) == {f"{other}.c1": "unmet-unobserved"}, "never verified"
    assert _states(kyo, record) == {f"{record}.c1": "met"}


def test_rule_3_the_sole_unobserved_criterion(kyo, tmp_path: Path) -> None:
    """The proposal case: no gate at all."""
    record = new_record(kyo, criteria=["built", "the maintainer's decision is recorded"])
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked"
    ).code == 0  # fmt: skip
    _unobserved(kyo, f"{record}.c2")
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    assert done.json["rule"] == "sole-unobserved"
    assert [t["criterion_id"] for t in done.json["targets"]] == [f"{record}.c2"]


def test_rule_4_no_target_attaches_the_words_and_clears_the_gate(
    kyo, project: Path, tmp_path: Path
) -> None:
    """APP-58: every criterion already met, a review gate open — a pure approval."""
    record = new_record(kyo, criteria=["a"])
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked"
    ).code == 0  # fmt: skip
    gate = _gate(kyo, record, "awaiting:human-review")
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    assert done.json["rule"] == "no-target" and done.json["targets"] == []
    evidence = _show(kyo, record)["evidence_artifacts"]
    assert [e["text"] for e in evidence] == [WORDS]
    assert evidence[0]["supports_id"] == record
    assert gate not in _open_gates(kyo, record)
    assert _show(kyo, record)["status"] == _complete_status(project)


# ------------------------------------------------------------------------------- c3


def _run_ready_line(kyo, text: str, index: int = 0):
    lines = _ready_lines(text)
    assert lines, f"the refusal printed no ready `kyo confirm` line:\n{text}"
    return kyo(*lines[index], "--json")


def test_ambiguous_gate_is_refused_and_its_line_clears_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    first = _gate(kyo, record, "awaiting:human-run", "run it")
    _gate(kyo, record, "awaiting:human-review")
    quote = _quote_file(tmp_path)
    text = _refused(
        kyo, project, CONTRACT_CONFIRM_AMBIGUOUS_GATE, "confirm", record, "--quote-file", quote
    )
    assert len(_ready_lines(text)) == 2
    ran = _run_ready_line(kyo, text)
    assert ran.code == 0, ran
    assert [g["gate_id"] for g in ran.json["gates_cleared"]] == [first]


def test_ambiguous_criteria_is_refused_and_its_line_clears_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    _unobserved(kyo, f"{record}.c1")
    _unobserved(kyo, f"{record}.c2")
    text = _refused(
        kyo, project, CONTRACT_CONFIRM_AMBIGUOUS_CRITERIA,
        "confirm", record, "--quote-file", _quote_file(tmp_path),
    )  # fmt: skip
    ran = _run_ready_line(kyo, text, index=-1)
    assert ran.code == 0, ran
    assert set(_states(kyo, record).values()) == {"met"}


def test_nothing_to_confirm_is_refused_and_its_line_clears_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    text = _refused(
        kyo, project, CONTRACT_CONFIRM_NOTHING_TO_CONFIRM,
        "confirm", record, "--quote-file", _quote_file(tmp_path),
    )  # fmt: skip
    ran = _run_ready_line(kyo, text)
    assert ran.code == 0, ran
    assert _states(kyo, record) == {f"{record}.c1": "met"}


def test_a_retired_record_is_refused(kyo, project: Path, tmp_path: Path) -> None:
    """A record moved to wont-do with a gate open. The remedy is `kyo ungate` alone."""
    record = new_record(kyo, criteria=["a"])
    gate = _gate(kyo, record, "awaiting:human-review")
    retired = kyo("move", record, "wont-do", "--why", "superseded by another", "--force")
    assert retired.code == 0, retired
    text = _refused(
        kyo, project, CONTRACT_CONFIRM_RECORD_RETIRED,
        "confirm", record, "--quote-file", _quote_file(tmp_path),
    )  # fmt: skip
    assert f"kyo ungate {gate}" in text
    assert _ready_lines(text) == [], "no confirmation of retired work is offered"


def test_a_running_dispatch_is_refused_and_the_same_line_clears_it_once_closed(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-x", "--json")
    assert dispatched.code == 0, dispatched
    text = _refused(
        kyo, git_project, CONTRACT_CONFIRM_DISPATCH_RUNNING,
        "confirm", record, "--quote-file", _quote_file(tmp_path),
    )  # fmt: skip
    closed = kyo(
        "dispatch", "close", dispatched.json["dispatch_id"], "--withdrawn",
        "--why", "frozen by mistake in this test",
    )  # fmt: skip
    assert closed.code == 0, closed
    ran = _run_ready_line(kyo, text)
    assert ran.code == 0, ran
    assert _states(kyo, record) == {f"{record}.c1": "met"}


def test_a_foreign_void_or_missing_criterion_is_refused_and_its_line_clears_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    other = new_record(kyo, criteria=["x"])
    record = new_record(kyo, criteria=["a", "b"])
    assert kyo(
        "edit", record, "--criterion", f"{record}.c2", "--state", "void-withdrawn",
        "--why", "not needed after all",
    ).code == 0  # fmt: skip
    quote = _quote_file(tmp_path)
    for bad in (f"{other}.c1", f"{record}.c2", f"{record}.c9"):
        text = _refused(
            kyo, project, CONTRACT_CONFIRM_CRITERION_NOT_CONFIRMABLE,
            "confirm", record, "--criterion", f"{record}.c1", bad, "--no-close",
            "--quote-file", quote,
        )  # fmt: skip
        assert bad in text
    ran = _run_ready_line(kyo, text)
    assert ran.code == 0, ran
    assert _states(kyo, record)[f"{record}.c1"] == "met"
    assert _states(kyo, other) == {f"{other}.c1": "unmet-unbuilt"}


def test_a_non_human_or_cleared_gate_is_refused_and_its_line_clears_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    event = _gate(kyo, record, "awaiting:event", "CI goes green")
    cleared = _gate(kyo, record, "awaiting:human-review")
    assert kyo("ungate", cleared, "--why", "looked earlier").code == 0
    human = _gate(kyo, record, "awaiting:human-run", "run it")
    quote = _quote_file(tmp_path)
    for gate in (event, cleared):
        text = _refused(
            kyo, project, CONTRACT_CONFIRM_GATE_NOT_CONFIRMABLE,
            "confirm", record, "--gate", gate, "--no-close", "--quote-file", quote,
        )  # fmt: skip
    ran = _run_ready_line(kyo, text)
    assert ran.code == 0, ran
    assert [g["gate_id"] for g in ran.json["gates_cleared"]] == [human]
    assert event in _open_gates(kyo, record), "an event gate is never cleared"


def test_a_gate_naming_a_criterion_the_record_lacks_is_refused(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    gate = _gate(kyo, record, "awaiting:human-run", f"look at {record}.c7")
    text = _refused(
        kyo, project, CONTRACT_CONFIRM_GATE_NAMES_A_MISSING_CRITERION,
        "confirm", record, "--quote-file", _quote_file(tmp_path),
    )  # fmt: skip
    assert f"kyo redo {gate}" in text
    ran = _run_ready_line(kyo, text)
    assert ran.code == 0, ran
    assert gate not in _open_gates(kyo, record)


# ------------------------------------------------------------------------------- c4


def test_the_evidence_is_the_quote_byte_for_byte(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    text = "The maintainer:\n\n> it works — `kyo` and all\n"
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path, text), "--json")
    assert done.code == 0, done
    stored = text.rstrip("\n")  # read_prose drops trailing blank lines, as everywhere
    digest = hashlib.sha256(stored.encode("utf-8")).hexdigest()
    assert done.json["quote"]["sha256"] == digest
    [evidence] = _show(kyo, record)["evidence_artifacts"]
    assert evidence["text"] == stored and evidence["sha256"] == digest
    [criterion] = _show(kyo, record)["criteria"]
    last = criterion["verifications"][-1]
    assert (last["verdict"], last["method"]) == ("met", "human-observed")


def test_several_targets_share_one_sha_and_stdin_is_read_once(kyo) -> None:
    """KYO-458.g1's shape: one gate naming two criteria, the words on stdin."""
    record = new_record(kyo, criteria=["a", "b"])
    _unobserved(kyo, f"{record}.c1")
    _unobserved(kyo, f"{record}.c2")
    _gate(kyo, record, "awaiting:human-run", f"check {record}.c1 and {record}.c2")
    done = kyo("confirm", record, "--quote-file", "-", "--json", stdin=WORDS + "\n")
    assert done.code == 0, done
    evidence = _show(kyo, record)["evidence_artifacts"]
    assert len(evidence) == 2
    assert {e["text"] for e in evidence} == {WORDS}, "both steps got the words from one read"
    assert len({e["sha256"] for e in evidence}) == 1
    assert {e["supports_id"] for e in evidence} == {f"{record}.c1", f"{record}.c2"}


# ------------------------------------------------------------------------------- c5


def test_only_the_answered_human_gate_clears_with_a_pointer_not_the_words(
    kyo, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    event = _gate(kyo, record, "awaiting:event", "the deploy lands")
    holding = _gate(kyo, record, "holding-for", "the latency measurement")
    human = _gate(kyo, record, "awaiting:human-review")
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    assert [g["gate_id"] for g in done.json["gates_cleared"]] == [human]
    assert set(_open_gates(kyo, record)) == {event, holding}
    [cleared] = [g for g in _show(kyo, record)["gates"] if g["id"] == human]
    why = cleared["cleared_why"]
    assert "looks right" not in why and WORDS not in why, "the words stay in evidence only"
    assert "\n" not in why and "kyo confirm" in why and f"{record}.c1" in why
    ungate = [s for s in done.json["steps"] if s["argv"][0] == "ungate"]
    assert ungate[0]["data"]["flag"] is None, "no gate-cleared-stale-criteria flag"


# ------------------------------------------------------------------------------- c6


def test_the_record_is_walked_rung_by_rung_to_completion(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    _gate(kyo, record, "awaiting:human-run", f"look at {record}.c1")
    was = _show(kyo, record)["status"]
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    ladder = load_status_ladder(project / "kyosei" / "statuses.yml")
    rungs = [n for n in ladder.names if ladder.role(n) != "retired"]
    expected = rungs[rungs.index(was) + 1 : rungs.index(ladder.complete) + 1]
    [closed] = done.json["closed"]
    assert closed["walked"] == expected and len(expected) > 1
    assert [s["argv"] for s in done.json["steps"] if s["argv"][0] == "move"] == [
        ["move", record, rung] for rung in expected
    ]


def test_an_unsettled_criterion_leaves_the_record_where_it_was(
    kyo, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    _unobserved(kyo, f"{record}.c1")
    _gate(kyo, record, "awaiting:human-run", f"look at {record}.c1")
    was = _show(kyo, record)["status"]
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path))
    assert done.code == 0, done
    assert _show(kyo, record)["status"] == was
    assert f"{record}.c2" in done.out and "stays" in done.out


def test_any_open_gate_left_stops_the_walk_with_the_reason(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    event = _gate(kyo, record, "awaiting:event", "the deploy lands")
    was = _show(kyo, record)["status"]
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0, done
    assert _show(kyo, record)["status"] == was
    [left] = done.json["left_open"]
    assert any(CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES in line for line in left["why"])
    assert any(event in line for line in left["why"])


def test_no_close_leaves_the_status_alone(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    was = _show(kyo, record)["status"]
    done = kyo("confirm", record, "--no-close", "--quote-file", _quote_file(tmp_path), "--json")
    assert done.code == 0 and done.json["complete"] is True, done
    assert _show(kyo, record)["status"] == was
    assert not [s for s in done.json["steps"] if s["argv"][0] == "move"]


# ------------------------------------------------------------------------------- c7


def test_a_partial_result_exits_0_with_complete_false_and_an_incomplete_line(
    kyo, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    _gate(kyo, record, "awaiting:event", "the deploy lands")
    quote = _quote_file(tmp_path)
    as_json = kyo("confirm", record, "--quote-file", quote, "--json")
    assert as_json.code == 0
    assert as_json.json["complete"] is False and as_json.json["ok"] is True
    text = kyo("confirm", record, "--quote-file", quote)
    assert text.code == 0
    [line] = [line for line in text.out.splitlines() if line.startswith("INCOMPLETE: ")]
    assert record in line and "left open" in line
    assert text.out.splitlines()[1] == line, "just under the headline, as accept prints it"


def test_a_whole_result_is_complete_and_prints_no_incomplete_line(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    done = kyo("confirm", record, "--quote-file", _quote_file(tmp_path))
    assert done.code == 0 and "INCOMPLETE" not in done.out, done


# ------------------------------------------------------------------------------- c8


def test_a_second_run_with_the_same_quote_applies_nothing_twice(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    _unobserved(kyo, f"{record}.c1")
    _unobserved(kyo, f"{record}.c2")
    _gate(kyo, record, "awaiting:human-run", f"check {record}.c1 and {record}.c2")
    quote = _quote_file(tmp_path)
    first = kyo("confirm", record, "--quote-file", quote, "--json")
    assert first.code == 0 and first.json["complete"] is True, first
    before = _export(project)
    second = kyo("confirm", record, "--quote-file", quote, "--json")
    assert second.code == 0, second
    assert second.json["complete"] is True
    assert second.json["steps"] == []
    assert _export(project) == before, "no verification, evidence, clearing or move twice"


def test_a_second_run_naming_the_criteria_skips_them_as_already_applied(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    _unobserved(kyo, f"{record}.c1")
    quote = _quote_file(tmp_path)
    args = ("confirm", record, "--criterion", f"{record}.c1", "--quote-file", quote, "--json")
    assert kyo(*args).code == 0
    before = _export(project)
    second = kyo(*args)
    assert second.code == 0 and second.json["complete"] is True, second
    assert second.json["skipped"][0]["reason"].startswith("already applied")
    assert _export(project) == before
