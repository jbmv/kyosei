"""The audit log: every mutation writes an events row with actor and before/after."""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.cli import MUTATING_COMMANDS

from .conftest import git_init, new_record


def _events(db) -> list[dict]:
    with db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY seq")]


def _git_init(project) -> None:
    """A minimal git repo with one commit, so `kyo dispatch` can stamp a base sha
    (KYO-7.c1: read from git, error cleanly when not a git repo — tested elsewhere;
    this just satisfies the happy path for the mutating-commands sweep)."""
    git_init(project)


def test_creating_a_record_logs_an_event_with_no_before(kyo, db) -> None:
    record = new_record(kyo)
    events = _events(db)
    assert len(events) == 1
    assert events[0]["record_id"] == record
    assert events[0]["command"] == "new bug"
    assert events[0]["before_json"] is None
    assert json.loads(events[0]["after_json"])["id"] == record


def test_every_mutating_command_writes_an_event(kyo, db, write_file, project) -> None:
    """If a command mutates and logs nothing, this test names it."""
    _git_init(project)
    record = new_record(kyo, criteria=["a", "b"])
    idea = project / "ideas" / "promote-me.md"
    idea.write_text("# Promote Me\n\nDo the thing.\n", encoding="utf-8")
    config = project / "kyosei" / "config.yml"
    fixture = Path(__file__).resolve().parent / "fixtures" / "recx-index.json"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"recx:\n  prior_art:\n    state: off\n    index_url: {fixture.as_uri()}\n",
        encoding="utf-8",
    )
    invocations = {
        # `evidence` rather than `chore`: since KYO-43 `kyo new` only files kinds
        # whose registry entry carries `intake`, and `evidence` is one of the two
        # that does. It is also a knowledge kind, so TST-2 is what `void` disposes
        # of below.
        "new": ("new", "evidence", "--title", "another"),
        # KYO-50: the kind-to-kind edge (`bug` produces `evidence`). Placed before
        # `wont-do` retires `record`, because `kyo spawn` refuses a retired source
        # and dict order here is execution order.
        "spawn": ("spawn", record, "--kind", "evidence", "--title", "what the repro showed"),
        "edit": ("edit", record, "--title", "renamed"),
        # KYO-58: voids a criterion and adds its replacement in one transaction, so
        # it logs the record's before/after like any other mutation.
        "restate": (
            "restate",
            f"{record}.c2",
            "--text-file",
            write_file("restated.txt", "b, stated properly\n"),
        ),
        "gate": ("gate", record, "awaiting:human-review"),
        # KYO-130. `--no-tags` rather than `--tag NAME` because the fixture project's
        # `gate-tags.yml` declares none (`gate-tags: {}` — see
        # src/kyosei/defaults/gate-tags.yml, which deliberately seeds no names), and a
        # tag it does not declare is refused at write time. Retagging to the empty set
        # a gate that already carries none still writes its event, which is the only
        # thing this sweep asserts; the tags actually moving is
        # tests/test_kyo130_gate_retag.py's business.
        "retag": ("retag", f"{record}.g1", "--no-tags"),
        # KYO-148. Like `retag`, ordered before `ungate` — but where `retag` on a cleared
        # gate merely tests something this sweep is not about, `redo` on one is REFUSED
        # (a cleared gate's payload is what somebody was told to do and did), so running
        # it after `ungate` would assert nothing and fail.
        "redo": ("redo", f"{record}.g1", "--do", "go and look at the staging banner"),
        # Ordered after `retag` and not before it: dict order is execution order here,
        # and `retag` on a cleared gate would be testing something this sweep is not.
        "ungate": ("ungate", f"{record}.g1"),
        "link": ("link", record, "blocks", "TST-2"),
        "move": ("move", record, "working"),
        "wont-do": ("wont-do", record, "--why", "done thinking"),
        # KYO-44.c1: the knowledge archetype's disposal path. TST-2 is the
        # `evidence` record filed by the `new` invocation above.
        "void": ("void", "TST-2", "--why", "measured the wrong thing"),
        "claim": (
            "claim", record, "--level", "asserted", "--method", "code read",
            "--file", write_file("claim.md", "the widget explodes when the cache is cold"),
        ),
        "verify": (
            "verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about",
        ),
        "attach": ("attach", record, "--file", write_file("evidence.txt", "console excerpt")),
        # KYO-585: offline, against the fixture index the prior-art tests use (state: off,
        # so nothing else in this sweep searches).
        "prior-art": ("prior-art", record),
        "promote": (
            "promote",
            str(idea),
            "--kind",
            "chore",
            "--criteria-file",
            write_file("promote-criteria.txt", "it works\n"),
            "--touches-file",
            write_file("promote-touches.txt", "kyosei/config.yml\n"),
        ),
        # KYO-8: sweep/brief/note each always write at least one global
        # (record_id=NULL) event, even when they touch no record's export —
        # see commands.cmd_sweep/cmd_brief/cmd_note — so they fit the generic
        # "just run it and count events" shape the rest of this dict uses.
        "sweep": ("sweep",),
        "brief": ("brief",),
        "note": ("note", "--shift", "--file", write_file("shift-note.md", "handoff thought")),
    }
    # `flag resolve` needs an existing flag id, so — like dispatch/report/split
    # below — it gets its own bespoke setup rather than a fixed tuple above.
    # KYO-137: `docs` is here because only ONE of its three actions mutates, and the one
    # that does (`kyo docs settle`) needs a docs obligation to settle — which needs a git
    # repository, a document, a branch, a dispatch and a report to exist at all. Building
    # that here would double the length of this test to re-derive a fixture that already
    # exists; the event assertion for it lives beside that fixture, in
    # `tests/test_kyo137_docs_obligations.py::test_settling_an_obligation_writes_an_event`.
    # KYO-331: `accept` is here for `docs`'s reason exactly. Every write it makes is a
    # write of a command it composes, so reaching one needs a dispatch, an ingested
    # report carrying a verdict, and a merged branch to correct the drift against — the
    # fixture that already exists in `tests/test_kyo329_accept.py`, where the assertions
    # that each of its three steps landed live beside it
    # (`test_accept_applies_verdicts_docs_verdicts_and_the_drift_correction_in_one_act`).
    # KYO-536: `close-wave` is here for `accept`'s reason: it needs a finished record, a
    # committed tree, a recorded suite pass and declared generators. Its event assertion
    # is `tests/test_kyo_close_wave.py::test_closing_a_record_writes_events`.
    non_dict_commands = {
        "dispatch", "report", "accept", "confirm", "split", "flag", "unlink", "redact", "docs",
        "close-wave",
    }
    assert set(invocations) | non_dict_commands == set(MUTATING_COMMANDS)

    for name, command in invocations.items():
        before = len(_events(db))
        assert kyo(*command).code == 0, name
        assert len(_events(db)) > before, f"{name} mutated without writing an event"

    # dispatch/report/split are inter-dependent (report needs a dispatch id;
    # split needs a still-unmet criterion), so they get their own bespoke setup
    # rather than a fixed tuple in `invocations` above — same assertion either way.
    before = len(_events(db))
    dispatched_json = kyo("dispatch", record, "--json")
    assert dispatched_json.code == 0, dispatched_json
    dispatch_id = dispatched_json.json["dispatch_id"]
    assert len(_events(db)) > before, "dispatch mutated without writing an event"

    report_path = write_file(
        "report.json",
        json.dumps(
            {
                "dispatch_id": dispatch_id,
                "record_id": record,
                "worked_from_sha": dispatched_json.json["worked_from_sha"],
                "criteria_verdicts": [],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [],
            }
        ),
    )
    before = len(_events(db))
    reported = kyo("report", dispatch_id, "--file", report_path)
    assert reported.code == 0, reported
    assert len(_events(db)) > before, "report mutated without writing an event"

    # `redact` needs an evidence artifact's own id, which only `kyo attach` reports.
    attached = kyo("attach", record, "--file", write_file("redact-me.txt", "id 12345"), "--json")
    assert attached.code == 0, attached
    before = len(_events(db))
    redacted = kyo("redact", attached.json["evidence_id"], "--why", "carries an identifier")
    assert redacted.code == 0, redacted
    assert len(_events(db)) > before, "redact mutated without writing an event"

    # `unlink` needs a live link's own id, which only the create call reports.
    linked = kyo("link", record, "duplicates", "TST-2", "--json")
    assert linked.code == 0, linked
    before = len(_events(db))
    unlinked = kyo("unlink", linked.json["link"]["id"], "--why", "filed in error")
    assert unlinked.code == 0, unlinked
    assert len(_events(db)) > before, "unlink mutated without writing an event"

    before = len(_events(db))
    # `.c3` rather than `.c2`: the `restate` invocation above voided c2 and put its
    # replacement in c3, and `kyo split` refuses a criterion that is already settled.
    split = kyo("split", record, "--to-successor", f"{record}.c3")
    assert split.code == 0, split
    assert len(_events(db)) > before, "split mutated without writing an event"

    # `flag resolve` needs an existing flag; sweeping a record whose declared
    # touch is about to vanish guarantees one deterministically (KYO-8.c2's
    # touch-missing class).
    vanishing = project / "will-vanish.txt"
    vanishing.write_text("temp", encoding="utf-8")
    idea2 = project / "ideas" / "flag-me.md"
    idea2.write_text("# Flag Me\n\nWill lose a touch.\n", encoding="utf-8")
    promoted = kyo(
        "promote",
        str(idea2),
        "--kind",
        "chore",
        "--criteria-file",
        write_file("flag-criteria.txt", "it still works\n"),
        "--touches-file",
        write_file("flag-touches.txt", "will-vanish.txt\n"),
        "--json",
    )
    assert promoted.code == 0, promoted
    vanishing.unlink()
    swept = kyo("sweep", "--json")
    assert swept.code == 0, swept
    assert swept.json["flags"], "expected the vanished touch to produce a flag"
    flag_id = swept.json["flags"][0]["flag_id"]

    before = len(_events(db))
    resolved = kyo("flag", "resolve", flag_id)
    assert resolved.code == 0, resolved
    assert len(_events(db)) > before, "flag resolve mutated without writing an event"

    # KYO-494: `confirm` writes through `verify`, and every events row is that command's
    # own. Its own record, made last: the sweep above retired `record` and its ids are
    # pinned by the steps before it.
    confirm_record = new_record(kyo, criteria=["looked at"])
    before = len(_events(db))
    confirmed = kyo(
        "confirm", confirm_record, "--criterion", f"{confirm_record}.c1", "--no-close",
        "--quote", "looked; it works",
    )  # fmt: skip
    assert confirmed.code == 0, confirmed
    assert len(_events(db)) > before, "confirm mutated without writing an event"


def test_before_and_after_snapshots_bracket_the_change(kyo, db) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("move", record, "working")
    event = _events(db)[-1]
    # KYO-418: the prior value is in `changed_json`, and no whole `before` is written.
    assert event["before_json"] is None
    assert json.loads(event["changed_json"])["status"] == "open"
    assert json.loads(event["after_json"])["status"] == "working"


def test_actor_defaults_to_orchestrator(kyo, db) -> None:
    """KYO-519 (KYO-513): the default was `controller` before the rename."""
    new_record(kyo)
    assert _events(db)[0]["actor"] == "orchestrator"


def test_actor_comes_from_the_flag(kyo, db) -> None:
    kyo("--actor", "worker-3", "new", "bug", "--title", "found it")
    assert _events(db)[0]["actor"] == "worker-3"


def test_actor_comes_from_the_environment(kyo, db, monkeypatch) -> None:
    monkeypatch.setenv("KYOSEI_ACTOR", "hook")
    new_record(kyo)
    assert _events(db)[0]["actor"] == "hook"


def test_the_flag_beats_the_environment(kyo, db, monkeypatch) -> None:
    monkeypatch.setenv("KYOSEI_ACTOR", "hook")
    kyo("--actor", "human", "new", "bug", "--title", "found it")
    assert _events(db)[0]["actor"] == "human"


def test_a_link_logs_an_event_for_both_endpoints(kyo, db) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    before = len(_events(db))
    kyo("link", first, "blocks", second)
    logged = _events(db)[before:]
    assert {event["record_id"] for event in logged} == {first, second}


def test_a_refused_mutation_logs_nothing(kyo, db) -> None:
    """A rolled-back transaction must leave no trace — half-writes are the enemy."""
    record = new_record(kyo, criteria=["a"])
    before = _events(db)
    assert kyo("move", record, "done").code == 2
    assert _events(db) == before
