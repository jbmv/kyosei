"""KYO-497: carried items (from KYO-483).

A consumer project re-typed about 46 standing decisions, open questions and reminders
into every closing note, with hand-kept day counts that drifted. Each is now a record of the default
``reminder`` kind (named ``carry`` until KYO-521); ``policy.carried`` marks the kinds `kyo
brief` lists, oldest first, with an
age computed from the record's tool-stamped ``created_at``; settling one takes a reason and
clears its open gates in the same transaction, which is how it meets KYO-490's
settled-requires-no-open-gates contract rather than bypassing it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from kyosei.contracts import (
    CONTRACT_CARRIED_SETTLE_REQUIRES_REASON,
    CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES,
)
from kyosei.kinds import RegistryError, load_kind_registry

from .conftest import kinds_yml, new_record


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _carry(kyo, title: str) -> str:
    result = kyo("new", "reminder", "--title", title, "--json")
    assert result.code == 0, result
    return result.json["record"]["id"]


def _backdate(db, record_id: str, ago: timedelta) -> None:
    conn = db()
    conn.execute(
        "UPDATE records SET created_at = ? WHERE id = ?",
        (_stamp(datetime.now(UTC) - ago), record_id),
    )
    conn.close()


def _gate(kyo, record_id: str, gate_type: str = "awaiting:human-review") -> str:
    result = kyo("gate", record_id, gate_type, "--do", "the maintainer decides", "--json")
    assert result.code == 0, result
    return result.json["gate_id"]


def _section(text: str, heading_prefix: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading_prefix))
    out = []
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        out.append(line)
    return out


def _carried_lines(text: str) -> list[str]:
    return _section(text, "reminders (")


# --- c1: the default kind ---------------------------------------------------------


def test_new_carry_files_a_record_with_no_criteria_and_next_never_lists_it(kyo) -> None:
    """c1: in a freshly initialised project, with no registry edit."""
    record = _carry(kyo, "SIGTERM: nothing handles it; decide deliberately")
    shown = kyo("show", record, "--json").json
    assert shown["kind"] == "reminder"
    assert shown["criteria"] == []
    # Not ready, and in no other `kyo next` list either: it is never dispatched.
    nxt = kyo("next", "--json")
    assert nxt.code == 0, nxt
    assert record not in nxt.out
    assert record not in kyo("next").out


def test_the_default_reminder_kind_is_a_non_dispatchable_uncounted_carried_work_kind(
    project,
) -> None:
    registry = load_kind_registry(project / "kyosei")
    reminder = registry["reminder"]
    assert reminder.archetype == "work"
    assert reminder.carried is True
    assert reminder.requires_criteria is False
    assert reminder.counted is False
    assert reminder.policy["dispatchable"] is False
    assert reminder.policy["swept"] is False
    assert reminder.accepts_from == ("intake",)
    # KYO-521 added `decision`.
    assert set(reminder.produces) == {"task", "bug", "investigation", "proposal", "decision"}
    for target in reminder.produces:
        assert registry[target].accepts_kind("reminder"), target
    # Every other default kind is not carried.
    assert [name for name in registry.names if registry[name].carried] == ["reminder"]


# --- c2: the registry key ---------------------------------------------------------


def _set_carried(project, kind: str, value) -> None:
    path = kinds_yml(project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        raw["kinds"][kind]["policy"].pop("carried", None)
    else:
        raw["kinds"][kind]["policy"]["carried"] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_a_registry_carrying_carried_true_loads(project) -> None:
    _set_carried(project, "task", True)
    assert load_kind_registry(project / "kyosei")["task"].carried is True


def test_a_non_boolean_carried_is_refused_naming_the_key(project, kyo) -> None:
    _set_carried(project, "reminder", "yes")
    with pytest.raises(RegistryError) as raised:
        load_kind_registry(project / "kyosei")
    assert "policy.carried" in str(raised.value)
    result = kyo("brief")
    assert result.code != 0
    assert "policy.carried" in result.err


def test_a_registry_without_the_key_behaves_as_before(project, kyo) -> None:
    """No `carried` anywhere: it loads, nothing is carried, and the brief says why its
    carried section is empty rather than printing an empty list it could not fill."""
    _set_carried(project, "reminder", None)
    registry = load_kind_registry(project / "kyosei")
    assert not any(registry[name].carried for name in registry.names)
    record = _carry(kyo, "no longer a carried kind")
    text = kyo("brief").out
    assert "no kind in kyosei/kinds.yml carries policy.carried" in text
    assert record not in "\n".join(
        line for line in text.splitlines() if line.startswith("reminders")
    )
    assert kyo("brief", "--json").json["carried"] == []
    # And a settle needs no reason, exactly as for any other uncriteria'd work kind.
    assert kyo("move", record, "done").code == 0


# --- c3: the brief section, its order and its ages ---------------------------------


@pytest.mark.parametrize("compact", [False, True])
def test_brief_lists_unsettled_carried_records_oldest_first_with_age(
    kyo, db, compact
) -> None:
    newest = _carry(kyo, "Report withdrawal: one click, no confirm")
    oldest = _carry(kyo, "idle_in_transaction backstop: undecided")
    middle = _carry(kyo, "408 retries, 429 does not")
    settled = _carry(kyo, "Settled already")
    dropped = _carry(kyo, "Dropped already")
    _backdate(db, oldest, timedelta(days=22, hours=23))
    _backdate(db, middle, timedelta(days=16, hours=1))
    _backdate(db, settled, timedelta(days=30))
    _backdate(db, dropped, timedelta(days=31))
    assert kyo("move", settled, "done", "--why", "codified").code == 0
    assert kyo("wont-do", dropped, "--why", "not doing").code == 0

    text = kyo(*(["brief", "--compact"] if compact else ["brief"])).out
    assert re.search(r"^reminders \(3; ", text, re.M), text
    lines = _carried_lines(text)
    assert [re.match(r"\s+(\S+)", line).group(1) for line in lines] == [
        oldest,
        middle,
        newest,
    ]
    # Whole 24-hour periods, rounded down.
    assert re.match(rf"\s+{oldest}  22d  idle_in_transaction", lines[0]), lines[0]
    assert re.match(rf"\s+{middle}  16d  408 retries", lines[1]), lines[1]
    assert re.match(rf"\s+{newest}  0d  Report withdrawal", lines[2]), lines[2]


def test_the_carried_section_sits_directly_after_gated_work(kyo) -> None:
    _carry(kyo, "an item")
    lines = kyo("brief").out.splitlines()
    gated = next(i for i, line in enumerate(lines) if line.startswith("gated work ("))
    carried = next(i for i, line in enumerate(lines) if line.startswith("reminders ("))
    between = [line for line in lines[gated + 1 : carried] if line.strip()]
    assert all(line.startswith("  ") for line in between), between


def test_compact_brief_drops_an_empty_carried_section_to_the_empty_line(kyo) -> None:
    text = kyo("brief", "--compact").out
    assert not re.search(r"^reminders \(", text, re.M)
    assert "reminders" in text  # named on the one line of empty count-sections


# --- c4: a carried record with an open gate ----------------------------------------


@pytest.mark.parametrize("compact", [False, True])
def test_a_gated_carried_record_shows_its_gate_and_stays_in_gated_work(
    kyo, db, compact
) -> None:
    record = _carry(kyo, "SIGTERM: decide deliberately")
    gate_id = _gate(kyo, record)
    conn = db()
    conn.execute(
        "UPDATE gates SET created_at = ? WHERE id = ?",
        (_stamp(datetime.now(UTC) - timedelta(days=3, hours=2)), gate_id),
    )
    conn.close()
    text = kyo(*(["brief", "--compact"] if compact else ["brief"])).out
    (line,) = _carried_lines(text)
    assert f"[waiting: {gate_id} awaiting:human-review, open 3d]" in line, line
    gated = _section(text, "gated work (")
    assert any(line.lstrip().startswith(record) for line in gated), gated


# --- c5: the JSON -----------------------------------------------------------------


def test_brief_json_carries_the_carried_list(kyo, db) -> None:
    record = _carry(kyo, "an item")
    _backdate(db, record, timedelta(days=5, hours=3))
    gate_id = _gate(kyo, record, "awaiting:event")
    created_at = kyo("show", record, "--json").json["created_at"]
    data = kyo("brief", "--json").json
    (item,) = data["carried"]
    assert item["id"] == record
    assert item["kind"] == "reminder"
    assert item["title"] == "an item"
    assert item["status"] == "open"
    assert item["since"] == created_at
    assert item["age_days"] == 5
    (gate,) = item["open_gates"]
    assert gate["gate_id"] == gate_id
    assert gate["gate_type"] == "awaiting:event"
    assert gate["age_days"] == 0
    assert data["carried_kinds"] == ["reminder"]


def test_show_prints_how_long_a_carried_record_has_been_carried(kyo, db) -> None:
    record = _carry(kyo, "an item")
    _backdate(db, record, timedelta(days=4, hours=1))
    assert re.search(r"^  carried: since \S+Z \(filed\), 4d$", kyo("show", record).out, re.M)
    assert kyo("show", record, "--json").json["carried"]["age_days"] == 4
    other = new_record(kyo, kind="bug", title="not carried", criteria=["it works"])
    assert "carried:" not in kyo("show", other).out
    assert "carried" not in kyo("show", other, "--json").json


# --- c6: --why is required to complete a carried record ----------------------------


def test_done_without_why_is_refused_naming_why_and_why_clears_it(kyo) -> None:
    record = _carry(kyo, "an item")
    refused = kyo("move", record, "done")
    assert refused.code == 2, refused
    assert CONTRACT_CARRIED_SETTLE_REQUIRES_REASON in refused.err
    assert "--why" in refused.err
    assert kyo("show", record, "--json").json["status"] == "open"
    # The remedy the refusal names, and nothing else, clears it.
    done = kyo("move", record, "done", "--why", "codified in retry.py")
    assert done.code == 0, done
    shown = kyo("show", record, "--json").json
    assert shown["status"] == "done"
    # The reason is stored, not only echoed: it is the record of how the item settled.
    assert any("codified in retry.py" in note["text"] for note in shown["notes"]), shown


def test_a_refused_settle_clears_no_gate(kyo) -> None:
    record = _carry(kyo, "an item")
    gate_id = _gate(kyo, record)
    assert kyo("move", record, "done").code == 2
    (gate,) = kyo("show", record, "--json").json["gates"]
    assert gate["id"] == gate_id and gate["cleared_at"] is None


# --- c7: settling clears the open gates -------------------------------------------


@pytest.mark.parametrize("verb", ["move", "wont-do"])
def test_settling_a_carried_record_clears_every_open_gate(kyo, verb) -> None:
    record = _carry(kyo, "SIGTERM: decide deliberately")
    first = _gate(kyo, record)
    second = _gate(kyo, record, "awaiting:event")
    reason = "decided on the call"
    args = (
        ["move", record, "done", "--why", reason]
        if verb == "move"
        else ["wont-do", record, "--why", reason]
    )
    result = kyo(*args)
    assert result.code == 0, result
    # Said, by gate id.
    assert "cleared 2 open gate(s)" in result.out
    assert first in result.out and second in result.out
    gates = kyo("show", record, "--json").json["gates"]
    assert all(g["cleared_at"] for g in gates), gates
    command = f"kyo move {record} done" if verb == "move" else f"kyo wont-do {record}"
    for gate in gates:
        assert command in gate["cleared_why"], gate
        assert reason in gate["cleared_why"], gate
    # The next brief lists the record in neither section.
    text = kyo("brief").out
    assert record not in "\n".join(_section(text, "gated work ("))
    assert record not in "\n".join(_carried_lines(text))


def test_settle_json_names_the_cleared_gates(kyo) -> None:
    record = _carry(kyo, "an item")
    gate_id = _gate(kyo, record)
    data = kyo("move", record, "done", "--why", "settled", "--json").json
    assert data["cleared_gates"] == [{"gate_id": gate_id, "gate_type": "awaiting:human-review"}]


def test_a_non_carried_record_is_still_refused_settling_past_an_open_gate(kyo) -> None:
    """KYO-490 is untouched for every other kind: no clearing, the refusal stands."""
    record = new_record(kyo, kind="plan", title="a plan with no criteria")
    gate_id = _gate(kyo, record)
    refused = kyo("move", record, "done", "--why", "finished")
    assert refused.code == 2, refused
    assert CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES in refused.err
    (gate,) = kyo("show", record, "--json").json["gates"]
    assert gate["id"] == gate_id and gate["cleared_at"] is None


# --- c8: filing a follow-up -------------------------------------------------------


def test_spawn_from_a_carried_record_files_a_task_produced_by_it(kyo, write_file) -> None:
    record = _carry(kyo, "Outliers export: unclassified rows excluded")
    result = kyo(
        "spawn", record, "--kind", "task", "--title", "Exclude unclassified rows",
        "--criteria-file", write_file("c.txt", "the export excludes unclassified rows\n"),
        "--touches-file", write_file("t.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    task = result.json["record"]
    assert task["kind"] == "task"
    assert result.json["link"]["type"] == "produced-by"
    incoming = [
        link["other"]
        for link in kyo("show", record, "--json").json["links"]
        if link["type"] == "produced-by"
    ]
    assert incoming == [task["id"]]
