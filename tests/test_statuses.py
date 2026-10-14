"""KYO-101: statuses are a per-project registry and contracts key off roles.

Every test that renames something renames it to a word this codebase does not contain,
because the whole claim is that no name is special. If any of these start passing for a
reason other than the role lookup — a fallback to the default ladder, a leftover literal
— the rename is what stops being load-bearing, so the renames are deliberate and odd:
`shipped`, `shelved`, `filed`, `in-review`.

The shared registry shape is exercised by ``tests/test_scopes.py``; what is here is this
dimension's own rules (roles, cardinalities, ladder order) and the contracts that bind
to them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import registry as registry_module
from kyosei.contracts import (
    CONTRACT_ACTIVE_REQUIRES_CRITERIA,
    CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION,
    CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA,
    CONTRACT_RETIRED_REQUIRES_REASON,
    CONTRACT_TERMINAL_IS_TERMINAL,
    ROLE_ACTIVE,
    ROLE_COMPLETE,
    ROLE_INITIAL,
    ROLE_NEEDS_VERIFICATION,
    ROLE_RETIRED,
    ROLE_VERIFIED,
    STANDING_VOID,
    STATUS,
    STATUS_ROLES,
    StatusRegistryError,
    default_ladder,
    load_status_ladder,
)
from kyosei.errors import EXIT_CONTRACT, EXIT_USAGE
from kyosei.scaffold import defaults_dir
from kyosei.schema import PRE_STATUS_REGISTRY_SCHEMA_VERSION, SCHEMA_VERSION

from .conftest import new_record, verify_criterion

#: The default ladder with `done` renamed and nothing else changed. Everything about
#: the completion terminal's obligations must survive the rename untouched.
RENAMED_COMPLETION = """\
statuses:
  open:
    summary: Filed, nothing started.
    role: initial
  working:
    summary: Underway.
    role: active
  built:
    summary: Written, unchecked.
    role: needs-verification
  verified:
    summary: A verdict is recorded.
    role: verified
  shipped:
    summary: Out the door, with the criteria to say so.
    role: complete
  wont-do:
    summary: Retired without shipping.
    role: retired
"""

#: The retirement terminal renamed, and nothing else.
RENAMED_RETIREMENT = """\
statuses:
  open:
    summary: Filed, nothing started.
    role: initial
  working:
    summary: Underway.
    role: active
  built:
    summary: Written, unchecked.
    role: needs-verification
  verified:
    summary: A verdict is recorded.
    role: verified
  done:
    summary: Finished.
    role: complete
  shelved:
    summary: Put down for good, with a reason.
    role: retired
"""


def statuses_yml(project: Path) -> Path:
    return project / "kyosei" / "statuses.yml"


def write_statuses(project: Path, text: str) -> Path:
    path = statuses_yml(project)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------- KYO-101.c1: role, not name


def test_a_renamed_completion_terminal_still_requires_settled_criteria(
    kyo, project: Path
) -> None:
    """KYO-101.c1. The contract that refused `kyo move X done` refuses
    `kyo move X shipped` — under the new name, naming the new name — because it was
    never about the word `done`."""
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["ships a fix", "logs stay clean"])

    result = kyo("move", record, "shipped")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA in result.err
    assert "ships a fix" in result.err and "logs stay clean" in result.err
    # And the old name is now simply not a status here — the rename is real.
    assert kyo("move", record, "done").code == EXIT_USAGE


def test_a_renamed_completion_terminal_still_requires_a_met_criterion(
    kyo, project: Path
) -> None:
    """KYO-101.c1, the second half. Settling every criterion by withdrawing it is not
    completion: 'all criteria met' is vacuously true at zero met criteria, and the
    rename must not be a way past that either."""
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["a thing that turned out not to matter"])
    assert kyo(
        "edit",
        record,
        "--criterion",
        f"{record}.c1",
        "--state",
        "void-withdrawn",
        "--why",
        "dropped on purpose",
    ).code == 0

    result = kyo("move", record, "shipped")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION in result.err
    # The refusal speaks the project's own vocabulary, not this codebase's.
    assert "shipped" in result.err
    assert "done" not in result.err


def test_a_qualified_move_into_the_renamed_terminal_is_allowed(kyo, project: Path) -> None:
    """The other side of the same contract: renaming must not make completion
    impossible either. A test that only proves refusals would pass on a build that
    refuses everything."""
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["ships a fix"])
    verify_criterion(kyo, f"{record}.c1", "met")
    result = kyo("move", record, "shipped", "--json")
    assert result.code == 0, result
    assert result.json["record"]["status"] == "shipped"


def test_a_renamed_active_status_still_requires_criteria(kyo, project: Path) -> None:
    """`working-requires-criteria` is `active-requires-criteria`, and it binds to
    whatever this project calls the rung where work is underway."""
    write_statuses(
        project,
        RENAMED_COMPLETION.replace(
            "  working:\n    summary: Underway.\n    role: active\n",
            "  in-flight:\n    summary: Underway.\n    role: active\n",
        ),
    )
    record = new_record(kyo)  # no criteria
    result = kyo("move", record, "in-flight")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_ACTIVE_REQUIRES_CRITERIA in result.err


# --------------------------------------- KYO-101.c2: the retirement terminal is distinct


def test_a_renamed_retirement_terminal_still_requires_a_reason(kyo, project: Path) -> None:
    """KYO-101.c2. Renaming `wont-do` must not be a way to retire a record without
    saying why — which is exactly what a name-keyed contract would have allowed."""
    write_statuses(project, RENAMED_RETIREMENT)
    record = new_record(kyo, criteria=["a"])

    result = kyo("move", record, "shelved")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_RETIRED_REQUIRES_REASON in result.err
    assert "shelved" in result.err


def test_a_renamed_retirement_terminal_is_still_terminal(kyo, project: Path) -> None:
    write_statuses(project, RENAMED_RETIREMENT)
    record = new_record(kyo, criteria=["a"])
    assert kyo("move", record, "shelved", "--why", "the premise died").code == 0

    result = kyo("move", record, "open")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_TERMINAL_IS_TERMINAL in result.err


def test_the_completion_terminal_is_not_terminal_under_any_name(kyo, project: Path) -> None:
    """KYO-101.c2's other edge, and the reason `complete` and `retired` are two roles.

    The charter is explicit that transitions may move backward and that `wont-do`
    alone "retires the ID permanently". Folding both terminals onto one role would
    have made a completed record unreopenable — a behaviour change nobody decided.
    """
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["ships a fix"])
    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "shipped").code == 0
    assert kyo("move", record, "working").code == 0


def test_kyo_wont_do_moves_to_whatever_the_project_calls_retirement(
    kyo, project: Path
) -> None:
    """`kyo wont-do` takes no status argument, so it has to be able to NAME the
    retirement status. That is why exactly one status may carry the role."""
    write_statuses(project, RENAMED_RETIREMENT)
    record = new_record(kyo, criteria=["a"])
    result = kyo("wont-do", record, "--why", "superseded", "--json")
    assert result.code == 0, result
    assert result.json["record"]["status"] == "shelved"
    assert result.json["record"]["wont_do_reason"] == "superseded"
    assert kyo("doctor").code == 0


def test_a_voided_knowledge_record_still_reads_void_after_a_rename(
    kyo, project: Path
) -> None:
    """KYO-101.c2, "renders as retired". A knowledge record's standing is derived from
    whether its status is terminal, so a rename that broke that lookup would make a
    retired decision read `current` — a live-looking record that is not."""
    write_statuses(project, RENAMED_RETIREMENT)
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    result = kyo("void", record, "--why", "measured wrong", "--json")
    assert result.code == 0, result
    assert result.json["standing"] == STANDING_VOID
    assert result.json["record"]["status"] == "shelved"
    shown = kyo("show", record, "--json").json
    assert shown["standing"] == STANDING_VOID
    assert STANDING_VOID in kyo("show", record).out


# ------------------------------------------- KYO-101.c3: the default reproduces v1


def test_the_default_ladder_is_v1s_ladder_exactly() -> None:
    """KYO-101.c3. Order included: the file's order IS the ladder, so a default that
    listed the same six names in another order would still be a behaviour change."""
    ladder = default_ladder()
    assert ladder.names == ("open", "working", "built", "verified", "done", "wont-do")
    assert ladder.initial == "open"
    assert ladder.complete == "done"
    assert ladder.retired == "wont-do"
    assert ladder.flow == "open → working → built → verified → done"
    assert ladder.active_names() == ("open", "working", "built", "verified")


def test_the_default_ladder_maps_every_v1_status_to_the_role_it_always_had() -> None:
    ladder = default_ladder()
    assert [ladder.role(name) for name in ladder.names] == [
        ROLE_INITIAL,
        ROLE_ACTIVE,
        ROLE_NEEDS_VERIFICATION,
        ROLE_VERIFIED,
        ROLE_COMPLETE,
        ROLE_RETIRED,
    ]
    assert set(STATUS_ROLES) == {ladder.role(name) for name in ladder.names}


def test_a_project_with_no_statuses_file_gets_the_default_ladder(
    kyo, project: Path
) -> None:
    """KYO-101.c3. Every store written before this release is in exactly this state,
    and it must keep every name and every behaviour it had."""
    statuses_yml(project).unlink()
    assert load_status_ladder(project / "kyosei").names == default_ladder().names

    record = new_record(kyo, criteria=["a"])
    assert kyo("move", record, "working").code == 0
    assert kyo("move", record, "built").code == 0
    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "done", "--json").json["record"]["status"] == "done"
    assert kyo("doctor").code == 0


def test_kyo_init_writes_the_same_ladder_it_falls_back_to(project: Path) -> None:
    """Law 3: the packaged default and the copied-into-the-project file are one file,
    so they cannot be two different ladders."""
    shipped = (defaults_dir() / STATUS.filename).read_text(encoding="utf-8")
    assert statuses_yml(project).read_text(encoding="utf-8") == shipped


def test_a_store_rebuilt_from_a_pre_registry_export_shows_no_change(
    kyo, project: Path
) -> None:
    """KYO-101.c3, the rebuild half. `records.status` changed MEANING at
    schema_version 14 and no export field changed at all, so a document written at 13
    rebuilds byte-for-byte into a store that behaves identically — no default to fill
    in, and above all no rename."""
    # KYO-102 bumped to 15, so this is no longer `PRE_STATUS_REGISTRY_SCHEMA_VERSION + 1`
    # and cannot be again: the guard was written when 14 was the head, and its real
    # subject is that a version BELOW the status-registry bump is what this test loads.
    # Written as the inequality it always meant, so the next bump does not fail a test
    # about statuses for a reason that has nothing to do with statuses.
    assert SCHEMA_VERSION > PRE_STATUS_REGISTRY_SCHEMA_VERSION
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("move", record, "built").code == 0

    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert doc["status"] == "built"
    doc["schema_version"] = PRE_STATUS_REGISTRY_SCHEMA_VERSION
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0

    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["status"] == "built"
    assert rebuilt["title"] == "widget"
    # And it is still the same rung of the same ladder, by role, not by luck.
    assert load_status_ladder(project / "kyosei").role("built") == ROLE_NEEDS_VERIFICATION


# ------------------------------------------------- the registry's own rules (roles)


def test_a_status_with_no_role_is_refused(kyo, project: Path) -> None:
    """A status the contract machinery cannot bind to must not be loadable. The
    shared shape enforces it (Dimension.required_fields), so the message is the
    registry's own."""
    write_statuses(project, "statuses:\n  open:\n    summary: Filed.\n")
    result = kyo("list")
    assert result.code != 0
    assert "role is required" in result.err


def test_an_unknown_role_names_the_six(kyo, project: Path) -> None:
    write_statuses(
        project,
        RENAMED_COMPLETION.replace("role: complete", "role: finished-ish"),
    )
    result = kyo("list")
    assert result.code != 0
    for role in STATUS_ROLES:
        assert role in result.err


#: One entry per role, so a test can drop exactly one and leave a well-formed file.
_ENTRIES = {
    ROLE_INITIAL: "  open:\n    summary: Filed, nothing started.\n    role: initial\n",
    ROLE_ACTIVE: "  working:\n    summary: Underway.\n    role: active\n",
    ROLE_COMPLETE: "  shipped:\n    summary: Out the door.\n    role: complete\n",
    ROLE_RETIRED: "  wont-do:\n    summary: Retired.\n    role: retired\n",
}


@pytest.mark.parametrize("missing", sorted({ROLE_INITIAL, ROLE_COMPLETE, ROLE_RETIRED}))
def test_a_ladder_missing_a_singleton_role_is_refused(
    kyo, project: Path, missing: str
) -> None:
    """Each of these three is a status the tool has to be able to NAME. A ladder
    without one is not a ladder with a gap; it is a ladder no command can use, so it
    is refused at load rather than discovered at the first `kyo new`."""
    body = "".join(text for role, text in _ENTRIES.items() if role != missing)
    write_statuses(project, "statuses:\n" + body)
    result = kyo("list")
    assert result.code != 0
    assert f"no status declares `role: {missing}`" in result.err


def test_two_statuses_claiming_one_singleton_role_are_refused(
    kyo, project: Path
) -> None:
    write_statuses(
        project,
        RENAMED_COMPLETION
        + "  also-shipped:\n    summary: A second completion.\n    role: complete\n",
    )
    result = kyo("list")
    assert result.code != 0
    assert "2 statuses declare `role: complete`" in result.err
    assert "shipped" in result.err and "also-shipped" in result.err


def test_the_repeatable_roles_may_appear_any_number_of_times(kyo, project: Path) -> None:
    """`active`, `needs-verification` and `verified` are set-membership tests, so a
    project with two review rungs is a project, not a violation."""
    write_statuses(
        project,
        RENAMED_COMPLETION
        + "  in-review:\n    summary: A second unverified rung.\n"
        "    role: needs-verification\n",
    )
    ladder = load_status_ladder(project / "kyosei")
    assert ladder.with_role(ROLE_NEEDS_VERIFICATION) == ("built", "in-review")
    record = new_record(kyo, criteria=["a"])
    assert kyo("move", record, "in-review").code == 0


def test_the_ladder_is_the_files_order_not_alphabetical(project: Path) -> None:
    """Declaration order carries meaning for this dimension, so there is no
    `position:` field to keep in step with it (law 3)."""
    write_statuses(
        project,
        "statuses:\n"
        "  zeta:\n    summary: First.\n    role: initial\n"
        "  alpha:\n    summary: Second.\n    role: active\n"
        "  mid:\n    summary: Third.\n    role: complete\n"
        "  omega:\n    summary: Fourth.\n    role: retired\n",
    )
    ladder = load_status_ladder(project / "kyosei")
    assert ladder.names == ("zeta", "alpha", "mid", "omega")
    assert ladder.registry.names == ["alpha", "mid", "omega", "zeta"]


def test_a_role_is_not_a_per_project_invention(project: Path) -> None:
    """The names are the project's; the roles are not. A project that could add a
    seventh role could add a contract nothing enforces."""
    write_statuses(project, RENAMED_COMPLETION.replace("role: active", "role: blocked"))
    with pytest.raises(registry_module.RegistryError) as caught:
        load_status_ladder(project / "kyosei")
    assert "must be one of" in str(caught.value)


def test_a_broken_ladder_raises_its_own_error_class(project: Path) -> None:
    """A ladder whose *shape* is fine and whose *arrangement* is not gets its own
    class, because the remedy differs: nothing here is malformed, and what is wrong is
    which roles are declared."""
    write_statuses(project, "statuses:\n  open:\n    summary: Filed.\n    role: initial\n")
    with pytest.raises(StatusRegistryError):
        load_status_ladder(project / "kyosei")


# ----------------------------------------------------------- the surfaces follow


def test_a_new_record_is_filed_in_the_renamed_initial_status(kyo, project: Path) -> None:
    write_statuses(
        project, RENAMED_COMPLETION.replace("  open:", "  filed:").replace(
            "    summary: Filed, nothing started.\n    role: initial",
            "    summary: Filed, nothing started.\n    role: initial",
        )
    )
    record = new_record(kyo, criteria=["a"])
    assert kyo("show", record, "--json").json["status"] == "filed"


def test_kyo_next_reports_the_renamed_needs_verification_rung(kyo, project: Path) -> None:
    """The awaiting-verification queue is `needs-verification`'s consumer, and it is
    why that role and `verified` are two roles rather than one: a record somebody has
    already verified must not reappear in the queue of things waiting to be verified.
    """
    write_statuses(
        project,
        RENAMED_COMPLETION.replace(
            "  built:\n    summary: Written, unchecked.\n    role: needs-verification\n",
            "  in-review:\n    summary: Written, unchecked.\n    role: needs-verification\n",
        ),
    )
    waiting = new_record(kyo, title="waiting", criteria=["a"])
    checked = new_record(kyo, title="checked", criteria=["b"])
    assert kyo("move", waiting, "in-review").code == 0
    # KYO-546: entering verified needs a recorded verdict.
    ran = kyo("verify", f"{checked}.c1", "--verdict", "met", "--method", "reasoned-about")
    assert ran.code == 0, ran
    assert kyo("move", checked, "verified").code == 0

    listed = kyo("brief", "--json").json["awaiting_verification"]
    assert [row["id"] for row in listed] == [waiting]
    assert listed[0]["status"] == "in-review"


def test_a_renamed_completion_status_reads_as_itself_wherever_it_is_shown(
    kyo, project: Path
) -> None:
    """A view whose vocabulary was frozen at import time would have no place for
    `shipped`, and every record in it would vanish without a word.

    KYO-215 rewrote this from the static viewer's board (`<th>shipped</th>`) to
    `kyo show` and `kyo list`, which is where the same question is now asked. The
    subject was never the markup: it is that the project's own word for completion
    reaches the surface, and that the retired word `done` does not come back with it.
    """
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["ships a fix"])
    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "shipped").code == 0

    shown = kyo("show", record).out
    assert "shipped" in shown
    assert "done" not in shown

    listed = kyo("list", "--status", "shipped", "--json").json["records"]
    assert [row["id"] for row in listed] == [record]
    assert listed[0]["status"] == "shipped"


def test_list_filters_and_refuses_by_the_projects_own_vocabulary(kyo, project: Path) -> None:
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["ships a fix"])
    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "shipped").code == 0

    assert [r["id"] for r in kyo("list", "--status", "shipped", "--json").json["records"]] == [
        record
    ]
    refused = kyo("list", "--status", "done")
    assert refused.code == EXIT_USAGE
    assert "shipped" in refused.err


def test_doctor_fails_a_record_whose_status_has_left_the_registry(
    kyo, project: Path
) -> None:
    """The half a write-time check cannot see. `kyo move` refuses an undeclared status
    — but the registry is an ordinary editable file, and a status can be renamed the
    day after the records sitting in it were written."""
    write_statuses(project, RENAMED_COMPLETION)
    record = new_record(kyo, criteria=["ships a fix"])
    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "shipped").code == 0
    assert kyo("doctor").code == 0

    write_statuses(project, RENAMED_RETIREMENT)  # `shipped` is gone; `done` is back
    failures = kyo("doctor", "--json").json["checks"]["statuses"]["failures"]
    assert failures, failures
    assert record in failures[0] and "shipped" in failures[0]
    # Nothing was migrated on our behalf: the record still says what it said.
    assert kyo("show", record, "--json").json["status"] == "shipped"


def test_doctor_reports_a_broken_ladder_rather_than_tripping_over_it(
    kyo, project: Path
) -> None:
    write_statuses(project, "statuses:\n  open:\n    summary: Filed.\n    role: initial\n")
    result = kyo("doctor", "--json")
    assert result.code != 0
    assert result.json["checks"]["statuses"]["failures"], result.out
