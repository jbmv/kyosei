"""KYO-44: a knowledge record can be disposed of, and never shows a status it
cannot leave.

Two defects, both in the knowledge archetype's lifecycle, found while retiring six
probe records: the three work-archetype ones retired and the three knowledge ones
were refused by the ``knowledge-kind-has-no-status-flow`` contract.

1. `kyo wont-do` is a status transition and knowledge kinds have no status flow, so
   a knowledge record filed in error had **no disposal path at all** — it could not
   be retired and the charter forbids deleting or archiving it. The workflow's own
   standing instruction ("if a record's premise died entirely, use `kyo wont-do
   <id> --why` — never leave a dead-premise record open 'to be safe'") was
   unfollowable for half the registry, and the error named the contract rather than
   anything to do instead. Every error naming a remedy is supposed to name one that
   works; this one named none.

2. Such a record displayed ``status: open`` permanently — not a state on the way to
   another one but a label it can never leave, rendered identically to a work record
   genuinely open and awaiting dispatch. That is the "one unchecked box means three
   different things" failure the criterion-states design exists to prevent,
   reappearing one level up in the status field.

The remedy half of this file follows ``tests/test_kyo5_error_remedies.py``: hit the
error, run the *exact* command the error names — parsed out of the error text, not
retyped from the source — and assert the situation it described is gone.
"""

from __future__ import annotations

import re

from kyosei.contracts import (
    CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW,
    CONTRACT_RETIRED_REQUIRES_REASON,
    CONTRACT_TERMINAL_IS_TERMINAL,
    CONTRACT_VOID_IS_KNOWLEDGE_ONLY,
    KNOWLEDGE_STANDINGS,
    STANDING_CURRENT,
    STANDING_VOID,
    default_ladder,
)
from kyosei.errors import EXIT_CONTRACT

from .conftest import new_record

#: The remedy a refusal names, as a runnable command line. Extracted from the error
#: text so the test runs what a reader would run, not what this file remembers.
REMEDY_RE = re.compile(r"`kyo ([a-z-]+ [A-Z]+-\d+) --why <reason>`")

#: The name the default ladder gives the `retired` role. Read off the ladder rather
#: than typed, because KYO-101 made the name a project's to choose and this suite is
#: about what the retirement STATUS does, not about the word `wont-do`.
RETIRED_STATUS = default_ladder().retired


def _named_remedy(err: str) -> list[str]:
    match = REMEDY_RE.search(err)
    assert match, f"no runnable remedy found in:\n{err}"
    return match.group(1).split()


def test_a_standing_is_never_also_a_status(kyo) -> None:
    """The two vocabularies must stay disjoint, or `kyo list --status` cannot tell
    which one a caller meant and the whole distinction collapses back into one word
    meaning two things (law 3).

    KYO-101: asked of the *default ladder* now that status names are per-project. A
    project that declares a status called `current` breaks this on its own head, and
    that is its business — what must hold is that Kyosei does not ship the collision.
    """
    assert not set(KNOWLEDGE_STANDINGS) & set(default_ladder().names)


# --------------------------------------------------------------------- KYO-44.c1


def test_wont_do_on_a_knowledge_record_names_a_remedy(kyo) -> None:
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    result = kyo("wont-do", record, "--why", "measured the wrong thing")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW in result.err
    assert f"kyo void {record} --why <reason>" in result.err


def test_running_the_named_remedy_disposes_of_the_record(kyo) -> None:
    """The remedy check (KYO-5): run the *exact* command the error names, nothing else,
    and assert the state the error complained about is gone afterwards."""
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    refused = kyo("wont-do", record, "--why", "measured the wrong thing")
    assert refused.code == EXIT_CONTRACT

    remedy = _named_remedy(refused.err)
    assert remedy == ["void", record]
    result = kyo(*remedy, "--why", "measured the wrong thing")
    assert result.code == 0, result

    document = kyo("show", record, "--json").json
    assert document["status"] == RETIRED_STATUS
    assert document["standing"] == STANDING_VOID
    assert document["wont_do_reason"] == "measured the wrong thing"


def test_the_remedy_is_not_circular_once_it_has_been_run(kyo) -> None:
    """KYO-5's own failure mode: a remedy that names the command which has just
    run. A voided record's refusal says it is already void, not "run kyo void"."""
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    assert kyo("void", record, "--why", "measured the wrong thing").code == 0

    again = kyo("wont-do", record, "--why", "measured the wrong thing")
    assert again.code == EXIT_CONTRACT
    assert "is already void" in again.err
    assert f"kyo void {record} --why <reason>" not in again.err


def test_void_is_terminal(kyo) -> None:
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    assert kyo("void", record, "--why", "measured the wrong thing").code == 0
    again = kyo("void", record, "--why", "again")
    assert again.code == EXIT_CONTRACT
    assert CONTRACT_TERMINAL_IS_TERMINAL in again.err


def test_void_requires_a_reason(kyo) -> None:
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    result = kyo("void", record)
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_RETIRED_REQUIRES_REASON in result.err


def test_void_refuses_a_work_record_and_names_the_verb_that_takes_it(kyo) -> None:
    """The symmetric half — and that redirect must work too, or this is the same
    dead end pointing the other way."""
    record = new_record(kyo, kind="bug", title="a bug")
    refused = kyo("void", record, "--why", "not a real bug")
    assert refused.code == EXIT_CONTRACT
    assert CONTRACT_VOID_IS_KNOWLEDGE_ONLY in refused.err

    remedy = _named_remedy(refused.err)
    assert remedy == ["wont-do", record]
    assert kyo(*remedy, "--why", "not a real bug").code == 0
    assert kyo("show", record, "--json").json["status"] == RETIRED_STATUS


def test_move_still_refuses_a_knowledge_record_and_says_what_to_do(kyo) -> None:
    """`kyo void` is a disposal, not a flow step: the ladder stays work-only, which
    is what "no status flow" was protecting. But the refusal is no longer a dead
    end — it names both amendment and retirement."""
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    result = kyo("move", record, "working")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW in result.err
    assert "amendment" in result.err
    assert f"kyo void {record}" in result.err
    assert kyo("show", record, "--json").json["status"] == "open"


def test_amending_a_knowledge_record_is_still_available(kyo, write_file) -> None:
    """The other lifecycle the refusal names. A record whose content is wrong but
    whose premise survives is edited, not voided — so the message offering both is
    offering two things that both work."""
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    result = kyo(
        "edit", record, "--description-file", write_file("d.md", "re-measured: 40ms was p50")
    )
    assert result.code == 0, result
    assert kyo("show", record, "--json").json["description"] == "re-measured: 40ms was p50"


# --------------------------------------------------------------------- KYO-44.c2


def test_show_never_renders_a_knowledge_record_as_bare_open(kyo) -> None:
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    text = kyo("show", record).out
    assert f"{record}  [evidence]  {STANDING_CURRENT}" in text
    assert f"{record}  [evidence]  open" not in text


def test_show_json_carries_the_standing_beside_the_stored_status(kyo) -> None:
    """Beside, never instead: a JSON consumer must be able to see both the stored
    fact and how it reads."""
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    document = kyo("show", record, "--json").json
    assert document["status"] == "open"
    assert document["standing"] == STANDING_CURRENT


def test_list_never_renders_a_knowledge_record_as_bare_open(kyo) -> None:
    knowledge = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    work = new_record(kyo, kind="bug", title="a bug")
    lines = {
        line.split()[0]: line for line in kyo("list").out.splitlines() if line.startswith("TST-")
    }
    assert STANDING_CURRENT in lines[knowledge]
    assert " open " not in lines[knowledge]
    assert " open " in lines[work]


def test_list_shows_void_not_wont_do_for_a_disposed_knowledge_record(kyo) -> None:
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    assert kyo("void", record, "--why", "measured the wrong thing").code == 0
    line = next(line for line in kyo("list").out.splitlines() if line.startswith(record + " "))
    assert STANDING_VOID in line
    assert RETIRED_STATUS not in line


def test_list_filters_by_standing(kyo) -> None:
    """A listing that shows a word no filter accepts is a dead end. `current` and
    `void` are askable, and they select only knowledge records."""
    knowledge = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    new_record(kyo, kind="bug", title="a bug")
    assert [r["id"] for r in kyo("list", "--status", "current", "--json").json["records"]] == [
        knowledge
    ]
    assert kyo("list", "--status", "void", "--json").json["records"] == []

    assert kyo("void", knowledge, "--why", "measured the wrong thing").code == 0
    assert [r["id"] for r in kyo("list", "--status", "void", "--json").json["records"]] == [
        knowledge
    ]
    assert kyo("list", "--status", "current", "--json").json["records"] == []


def test_list_still_refuses_a_word_that_is_neither(kyo) -> None:
    result = kyo("list", "--status", "pending")
    assert result.code == 64
    assert STANDING_CURRENT in result.err  # the message names the standings too


def test_the_brief_counts_a_knowledge_record_by_its_standing(kyo) -> None:
    new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    entry = next(
        e for e in kyo("brief", "--json").json["counts_by_kind"] if e["kind"] == "evidence"
    )
    assert entry["by_status"] == {STANDING_CURRENT: 1}


def test_a_voided_knowledge_record_reads_void_rather_than_the_retired_word(kyo) -> None:
    """KYO-215 rewrote this from the static viewer's record page to `kyo show`.

    The assertion is the record's own, not the surface's: a voided knowledge record
    reads with the knowledge archetype's `void`, never with the work ladder's retired
    word, and the reason it was voided is shown beside it.
    """
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    assert kyo("void", record, "--why", "measured the wrong thing").code == 0
    shown = kyo("show", record).out
    assert STANDING_VOID in shown
    assert RETIRED_STATUS not in shown
    assert "measured the wrong thing" in shown
    assert kyo("show", record, "--json").json["standing"] == STANDING_VOID


def test_a_work_record_is_unchanged_by_any_of_this(kyo) -> None:
    """The standing is the knowledge archetype's vocabulary and nothing else's —
    a work record keeps every word it had."""
    record = new_record(kyo, kind="bug", title="a bug", criteria=["it works"])
    assert kyo("show", record, "--json").json["standing"] == "open"
    assert f"{record}  [bug]  open" in kyo("show", record).out
    assert kyo("move", record, "working").code == 0
    assert f"{record}  [bug]  working" in kyo("show", record).out
    assert kyo("wont-do", record, "--why", "not worth it").code == 0
    assert f"{record}  [bug]  {RETIRED_STATUS}" in kyo("show", record).out


def test_a_plan_s_member_distribution_separates_the_archetypes(kyo) -> None:
    """A plan may hold knowledge members, and its distribution counted a
    permanently-`open` one alongside a genuinely-open, dispatchable task."""
    plan = new_record(kyo, kind="plan", title="a plan")
    for kind, title in (("bug", "a bug"), ("evidence", "a measurement")):
        assert kyo("new", kind, "--title", title, "--plan", plan).code == 0
    text = kyo("show", plan).out
    assert f"{STANDING_CURRENT}:1, open:1" in text
    assert "open:2" not in text
