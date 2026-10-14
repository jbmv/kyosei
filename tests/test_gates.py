"""Gates: a dimension orthogonal to status, cleared by their own id."""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import cmd_next
from kyosei import registry as registry_module
from kyosei.contracts import GATE_TYPES, STATUS_ROLES, load_status_ladder
from kyosei.scaffold import defaults_dir

from .conftest import export_of, new_record, valid_report


def test_every_gate_type_is_accepted(kyo) -> None:
    record = new_record(kyo)
    for gate_type in GATE_TYPES:
        result = kyo("gate", record, gate_type, "--do", "the exact condition")
        assert result.code == 0, gate_type
    assert len(kyo("show", record, "--json").json["gates"]) == len(GATE_TYPES)


def test_human_run_requires_the_exact_thing_to_run(kyo) -> None:
    """The instruction lives ON the record, not in chat."""
    record = new_record(kyo)
    result = kyo("gate", record, "awaiting:human-run")
    assert result.code == 64
    assert "requires --do" in result.err


def test_human_review_needs_no_payload(kyo) -> None:
    """"read this and judge it" carries no command; it is a different ask."""
    record = new_record(kyo)
    assert kyo("gate", record, "awaiting:human-review").code == 0


def test_human_run_and_human_review_are_never_flattened(kyo) -> None:
    record = new_record(kyo)
    kyo("gate", record, "awaiting:human-run", "--do", "migrate staging, then reload the dashboard")
    kyo("gate", record, "awaiting:human-review")
    types = {g["type"] for g in kyo("show", record, "--json").json["gates"]}
    assert types == {"awaiting:human-run", "awaiting:human-review"}


def test_gates_do_not_change_status(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("move", record, "working").code == 0
    assert kyo("gate", record, "awaiting:event", "--do", "the nightly deploy lands").code == 0
    assert kyo("show", record, "--json").json["status"] == "working"


def test_a_record_may_carry_several_gates_at_once(kyo) -> None:
    record = new_record(kyo)
    kyo("gate", record, "awaiting:event", "--do", "CI goes green")
    kyo("gate", record, "holding-for", "--do", "the latency measurement finishes")
    open_gates = [g for g in kyo("show", record, "--json").json["gates"] if not g["cleared_at"]]
    assert len(open_gates) == 2


def test_ungate_takes_the_gates_own_id(kyo) -> None:
    record = new_record(kyo)
    gate_id = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    assert gate_id.startswith(f"{record}.g")
    result = kyo("ungate", gate_id, "--why", "reviewed on the call")
    assert result.code == 0
    gate = kyo("show", record, "--json").json["gates"][0]
    assert gate["cleared_at"] is not None
    assert gate["cleared_why"] == "reviewed on the call"


def test_clearing_a_gate_twice_fails_loudly(kyo) -> None:
    record = new_record(kyo)
    gate_id = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    assert kyo("ungate", gate_id).code == 0
    result = kyo("ungate", gate_id)
    assert result.code == 1
    assert "already cleared" in result.err


def test_ungate_refuses_an_unknown_gate(kyo) -> None:
    record = new_record(kyo)
    result = kyo("ungate", f"{record}.g7")
    assert result.code == 1
    assert "no gate" in result.err


def test_unknown_gate_type_is_refused_with_the_known_list(kyo) -> None:
    record = new_record(kyo)
    result = kyo("gate", record, "awaiting:coffee", "--do", "x")
    assert result.code == 64
    assert "awaiting:human-run" in result.err


def test_a_cleared_gate_is_kept_not_deleted(kyo) -> None:
    """Law 4: supersede, never delete — the history of the hold survives."""
    record = new_record(kyo)
    gate_id = kyo("gate", record, "holding-for", "--do", "the p95 lands", "--json").json["gate_id"]
    kyo("ungate", gate_id, "--why", "measurement done")
    gates = kyo("show", record, "--json").json["gates"]
    assert [g["id"] for g in gates] == [gate_id]
    assert gates[0]["do"] == "the p95 lands"


# ------------------------------------------------------------------- KYO-88
#
# A consumer project's orchestrator reported this as the most important structural gap:
# a record's gate (APP-58.g2) asked a question that the same record's own verified claim
# had answered three days earlier. Nothing connected them. The record sat blocked on a
# question it had already resolved, and the overlap was found only because someone read
# both. Claims and gates can be about the same thing with nothing noticing; surfacing a
# record's claims next to its open gates in `kyo show` would have caught it.
#
# A gate that will never clear itself does not decay, raises no flag, and appears in
# no staleness class. It just removes work from the board for as long as nobody
# rereads the record end to end — which is what `kyo brief` exists to stop a
# controller from having to do.


def _gate_block(out: str, gate_id: str) -> list[str]:
    """One gate's line and everything indented under it, before the next gate."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(gate_id))
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip() or not line.startswith("    "):
            break
        block.append(line)
    return block


def _claim(kyo, record: str, statement: str, *, level: str = "verified") -> str:
    result = kyo(
        "claim", record, "--level", level, "--method", "read the store", "--file", "-",
        "--json", stdin=statement,
    )
    assert result.code == 0, result
    return result.json["claim_id"]


def test_an_open_gate_is_rendered_with_the_records_claims(kyo) -> None:
    """KYO-88.c1/c2: the claims, with their verification levels, are at the gate —
    not a section away, not held in the reader's head."""
    record = new_record(kyo)
    gate_id = kyo(
        "gate", record, "awaiting:human-review", "--do", "does the signal survive offline?",
        "--json",
    ).json["gate_id"]
    claim_id = _claim(kyo, record, "the offline signal is a cache artifact, measured twice")

    block = "\n".join(_gate_block(kyo("show", record).out, gate_id))
    assert claim_id in block
    assert "verified" in block
    assert "the offline signal is a cache artifact, measured twice" in block


def test_the_gate_and_a_verified_claim_land_in_one_contiguous_region(kyo) -> None:
    """KYO-88.c4, asserted as adjacency rather than as membership: the failure was
    that the two were both present and could not be read together."""
    record = new_record(kyo)
    gate_id = kyo("gate", record, "awaiting:event", "--do", "the p95 lands", "--json").json[
        "gate_id"
    ]
    claim_id = _claim(kyo, record, "the p95 landed at 180ms on 2026-08-24")

    lines = kyo("show", record).out.splitlines()
    gate_line = next(i for i, line in enumerate(lines) if gate_id in line)
    claim_line = next(i for i, line in enumerate(lines) if claim_id in line and i > gate_line)
    assert claim_line - gate_line <= 2, "\n".join(lines[gate_line : claim_line + 1])


def test_every_claim_is_shown_and_none_is_dropped_to_keep_the_block_short(kyo) -> None:
    """No cap, deliberately. The claim a cap hides is exactly the claim this
    rendering exists to surface, and a truncated list reads as complete."""
    record = new_record(kyo)
    gate_id = kyo("gate", record, "holding-for", "--do", "the review", "--json").json["gate_id"]
    claim_ids = [_claim(kyo, record, f"finding number {n}", level="asserted") for n in range(8)]

    block = "\n".join(_gate_block(kyo("show", record).out, gate_id))
    assert all(claim_id in block for claim_id in claim_ids)
    assert "claims on this record (8)" in block


def test_a_multi_line_claim_is_indexed_by_its_first_line_and_says_so(kyo) -> None:
    """One line per claim keeps the block an index, not a second copy of the claims
    section — and the reader is told where the rest of the sentence is."""
    record = new_record(kyo)
    gate_id = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    _claim(kyo, record, "the first line of the answer\nand two\nmore lines of it")

    block = "\n".join(_gate_block(kyo("show", record).out, gate_id))
    assert "the first line of the answer" in block
    assert "more lines of it" not in block
    assert "+2 more line(s) under `claims` below" in block
    # ...and the whole statement is still rendered, a few lines down, unchanged.
    assert "more lines of it" in kyo("show", record).out


def test_a_record_with_no_claims_says_so_at_the_gate(kyo) -> None:
    """"Compared and found nothing" and "there was nothing to compare" are different
    facts, and an empty block would render them identically."""
    record = new_record(kyo)
    gate_id = kyo("gate", record, "awaiting:event", "--do", "CI goes green", "--json").json[
        "gate_id"
    ]
    block = "\n".join(_gate_block(kyo("show", record).out, gate_id))
    assert "claims on this record (0)" in block
    assert "nothing recorded to compare this gate against" in block


def test_a_cleared_gate_carries_no_claims(kyo) -> None:
    """A cleared gate is not a question any more; the record's answers beside it
    would be noise in the one rendering KYO-88 exists to keep readable."""
    record = new_record(kyo)
    gate_id = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    claim_id = _claim(kyo, record, "an answer to something else entirely")
    assert kyo("ungate", gate_id, "--why", "reviewed").code == 0

    block = "\n".join(_gate_block(kyo("show", record).out, gate_id))
    assert claim_id not in block
    assert claim_id in kyo("show", record).out


def test_nothing_is_cleared_closed_or_reordered_by_a_claim(kyo) -> None:
    """KYO-88.c3. Deciding that a claim answers a gate is a judgment about meaning,
    and getting it wrong silently unblocks work — worse than work merely stuck. The
    change is to what is rendered, never to what is decided: `kyo ungate` stays a
    controller act, the record stays gated, and the gate order is untouched."""
    record = new_record(kyo, criteria=["a"])
    first = kyo("gate", record, "awaiting:event", "--do", "the deploy lands", "--json").json[
        "gate_id"
    ]
    second = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    _claim(kyo, record, "the deploy landed on 2026-08-24 and the review is done")

    doc = kyo("show", record, "--json").json
    assert [g["id"] for g in doc["gates"]] == [first, second]
    assert all(g["cleared_at"] is None for g in doc["gates"])
    # Still held out of the ready list — a claim is not a clearance.
    assert record not in [item["id"] for item in kyo("next", "--json").json["ready"]]
    assert [g["record_id"] for g in kyo("brief", "--json").json["gated"]] == [record, record]


# ------------------------------------------------------------------------- KYO-102
#
# "Gates stay orthogonal to status, and gain a tag registry."
#
# The problem in one sentence: a gate's payload is free text, so "everything waiting
# on a human to review a UI element" is not a query. Gate *types*
# were already a closed four-member registry; only the payload was unstructured. So the
# change is one more registry dimension on the shared shape (src/kyosei/registry.py),
# with the free text kept beside it — and every test below is written against the thing
# the tags were built for, which is the query, not the column.


TWO_TAGS = """\
gate-tags:
  ui-review:
    summary: A person has to look at a rendered interface and judge it.
  deploy:
    summary: Waiting on a release to reach an environment where this can be seen.
"""


def gate_tags_yml(project: Path) -> Path:
    return project / "kyosei" / "gate-tags.yml"


def write_gate_tags(project: Path, text: str) -> Path:
    path = gate_tags_yml(project)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------------ KYO-102.c1


def test_a_gate_carries_tags_from_the_registry(kyo, project: Path) -> None:
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "gate", record, "awaiting:human-review", "--tag", "ui-review", "--json"
    )
    assert result.code == 0, result
    gates = kyo("show", record, "--json").json["gates"]
    assert [g["tags"] for g in gates] == [["ui-review"]]


def test_a_gate_may_carry_several_tags_in_the_order_given(kyo, project: Path) -> None:
    """Order is the caller's, not the sort's: the first tag is the one they reached for."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    kyo(
        "gate", record, "awaiting:event", "--do", "the release ships",
        "--tag", "deploy", "--tag", "ui-review",
    )
    assert kyo("show", record, "--json").json["gates"][0]["tags"] == ["deploy", "ui-review"]


def test_an_unknown_tag_is_refused_naming_the_registry_and_what_it_holds(
    kyo, project: Path
) -> None:
    """KYO-102.c1 in full: refused at WRITE time, loudly, naming the file and the tags
    it does hold. Nothing is stored — a gate is not created with the good tags and the
    bad one dropped, because a partly-applied classification is worse than none."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    result = kyo("gate", record, "awaiting:human-review", "--tag", "ui-reveiw")
    assert result.code != 0
    assert "gate-tag 'ui-reveiw' is not declared" in result.err
    assert str(gate_tags_yml(project)) in result.err
    assert "declared gate-tags: deploy, ui-review" in result.err
    assert kyo("show", record, "--json").json["gates"] == []


def test_the_remedy_the_refusal_names_clears_the_refusal(kyo, project: Path) -> None:
    """The project's standing rule: an error message naming a remedy ships with a test
    proving the remedy works. The remedy here is "add a `<name>:` entry with `summary:`
    to <file>", which is what this does, verbatim."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    assert kyo("gate", record, "awaiting:human-review", "--tag", "security").code != 0
    write_gate_tags(
        project,
        TWO_TAGS + "  security:\n    summary: Waiting on a security review.\n",
    )
    result = kyo("gate", record, "awaiting:human-review", "--tag", "security")
    assert result.code == 0, result
    assert kyo("show", record, "--json").json["gates"][0]["tags"] == ["security"]


def test_a_project_with_no_registry_file_is_told_to_create_it(kyo, project: Path) -> None:
    """The absent-file case is its own sentence, because the two states need different
    remedies: one says add a name to a file, the other says the file is not there yet."""
    gate_tags_yml(project).unlink()
    record = new_record(kyo, criteria=["a"])
    result = kyo("gate", record, "awaiting:human-review", "--tag", "ui-review")
    assert result.code != 0
    assert "does not exist" in result.err
    assert "declares no gate-tags at all" in result.err


def test_a_repeated_tag_is_stored_once_and_the_echo_says_so(kyo, project: Path) -> None:
    """Law 5: the echo is of what was STORED. A tag typed twice is a typo under either
    reading, and collapsing it is only honest if the caller can see it collapsed."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "gate", record, "awaiting:human-review", "--tag", "deploy", "--tag", "deploy"
    )
    assert result.code == 0, result
    assert "deploy, deploy" not in result.out
    assert kyo("show", record, "--json").json["gates"][0]["tags"] == ["deploy"]


def test_a_malformed_registry_is_refused_with_every_problem_at_once(
    kyo, project: Path
) -> None:
    """The shared loader's law-6 style, inherited rather than re-implemented."""
    write_gate_tags(
        project,
        "gate-tags:\n  UI_Review:\n    summary: Wrong shape of name.\n"
        "  deploy:\n    detail: not a field this dimension has\n",
    )
    result = kyo("list")
    assert result.code != 0
    assert "name must be a lowercase slug" in result.err
    assert "summary must be a non-empty string" in result.err
    assert "unknown key(s) detail" in result.err


# ------------------------------------------------------------------------ KYO-102.c2


def test_a_list_surface_selects_gated_records_by_tag(kyo, project: Path) -> None:
    """The worked example, run: "everything waiting on a human to review a UI
    element". The free-text payload could never answer it; this is the query that can."""
    write_gate_tags(project, TWO_TAGS)
    reviewed = new_record(kyo, title="the settings pane", criteria=["a"])
    deploying = new_record(kyo, title="the nightly job", criteria=["a"])
    untagged = new_record(kyo, title="something else", criteria=["a"])
    kyo("gate", reviewed, "awaiting:human-review", "--tag", "ui-review")
    kyo("gate", deploying, "awaiting:event", "--do", "the release ships", "--tag", "deploy")
    kyo("gate", untagged, "awaiting:human-review")

    listed = kyo("list", "--gate-tag", "ui-review", "--json").json
    assert [item["id"] for item in listed["records"]] == [reviewed]
    assert listed["records"][0]["gate_tags"] == ["ui-review"]
    # And the tag is SHOWN, not only searched for — a field you can filter on and never
    # see is one you have to already know the value of.
    assert "ui-review" in kyo("list", "--gate-tag", "ui-review").out


def test_the_filter_reads_open_gates_only(kyo, project: Path) -> None:
    """A cleared gate is something the record WAS waiting on. Answering "what is waiting
    on a UI review" with it would answer a question about the present from the past."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    gate_id = kyo(
        "gate", record, "awaiting:human-review", "--tag", "ui-review", "--json"
    ).json["gate_id"]
    assert [r["id"] for r in kyo("list", "--gate-tag", "ui-review", "--json").json["records"]] == [
        record
    ]
    kyo("ungate", gate_id, "--why", "reviewed on the call")
    assert kyo("list", "--gate-tag", "ui-review", "--json").json["records"] == []


def test_an_unknown_filter_value_is_refused_not_answered_with_nothing(
    kyo, project: Path
) -> None:
    """Law 6, and the same sentence the write path gets: an empty listing reads as
    "nothing is waiting on that", which is a different and possibly false fact."""
    write_gate_tags(project, TWO_TAGS)
    result = kyo("list", "--gate-tag", "ui-reveiw")
    assert result.code != 0
    assert "gate-tag 'ui-reveiw' is not declared" in result.err
    assert "declared gate-tags: deploy, ui-review" in result.err


def test_tags_survive_a_rebuild_from_export_unchanged(kyo, project: Path) -> None:
    """KYO-102.c2's second half: the store rebuilt from the git-canonical export carries
    the tags, so the query is answerable on a fresh checkout and not only in the database
    that happened to write them."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    kyo(
        "gate", record, "awaiting:event", "--do", "the release ships",
        "--tag", "deploy", "--tag", "ui-review",
    )
    doc = json.loads(export_of(project, record).read_text(encoding="utf-8"))
    assert doc["gates"][0]["tags"] == ["deploy", "ui-review"]

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["gates"][0]["tags"] == ["deploy", "ui-review"]
    assert [r["id"] for r in kyo("list", "--gate-tag", "deploy", "--json").json["records"]] == [
        record
    ]
    # And the export re-derives byte-for-byte from the rebuilt store.
    assert kyo("doctor").code == 0


def test_the_brief_shows_what_each_open_gate_is_waiting_on(kyo, project: Path) -> None:
    """The brief is the surface a controller opens a session on, so the classification
    has to reach it — including under --compact, which is what the SessionStart hook
    runs."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "ui-review")
    assert kyo("brief", "--json").json["gated"][0]["tags"] == ["ui-review"]
    assert "ui-review" in kyo("brief", "--compact").out


def test_doctor_fails_a_gate_whose_tag_has_left_the_registry(kyo, project: Path) -> None:
    """The half a write-time check cannot see. The registry is an ordinary editable
    file, so a tag can be dropped the day after the gates carrying it were written — at
    which point the gate has silently left the one query it was tagged for, because
    `kyo list --gate-tag` refuses that word outright."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "ui-review")
    write_gate_tags(
        project,
        "gate-tags:\n  deploy:\n    summary: Waiting on a release.\n",
    )
    result = kyo("doctor")
    assert result.code != 0
    assert "gate-tag 'ui-review' is not declared" in result.out + result.err
    check = kyo("doctor", "--json").json["checks"]["gate_tags"]
    assert check["ok"] is False
    assert len(check["failures"]) == 1
    # It names the gate, not just the tag: a store with forty gates needs to know which.
    assert f"carried by gate {record}.g1" in check["failures"][0]


# ------------------------------------------------------------------------ KYO-102.c3


def test_the_free_text_payload_is_kept_beside_the_tags(kyo, project: Path) -> None:
    """KYO-102: free text becomes an optional detail rather than the only content —
    kept, not removed. The tag says what KIND of waiting this is; the payload says
    WHICH one, and no tag can carry that."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    kyo(
        "gate", record, "awaiting:event", "--do", "the 2026-09 release reaches staging",
        "--tag", "deploy",
    )
    gate = kyo("show", record, "--json").json["gates"][0]
    assert gate["tags"] == ["deploy"]
    assert gate["do"] == "the 2026-09 release reaches staging"
    shown = kyo("show", record).out
    assert "tags: deploy" in shown
    assert "do: the 2026-09 release reaches staging" in shown


def test_a_tag_does_not_excuse_a_gate_from_its_payload(kyo, project: Path) -> None:
    """The one thing "demoted to optional detail" must not be read as. `--do` is
    required for the three gate types nobody could clear without it, and a tag is not a
    substitute: "waiting on a deploy" does not say which deploy, so a tag-only gate of
    those types would be a gate nobody can clear."""
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    result = kyo("gate", record, "awaiting:event", "--tag", "deploy")
    assert result.code == 64
    assert "requires --do" in result.err


def test_the_four_gate_types_are_unchanged(kyo, project: Path) -> None:
    write_gate_tags(project, TWO_TAGS)
    assert GATE_TYPES == (
        "awaiting:event",
        "awaiting:human-run",
        "awaiting:human-review",
        "holding-for",
    )
    record = new_record(kyo, criteria=["a"])
    for gate_type in GATE_TYPES:
        assert kyo("gate", record, gate_type, "--do", "x", "--tag", "deploy").code == 0


def test_no_status_named_gated_exists_anywhere(kyo, project: Path) -> None:
    """KYO-102's central refusal, checked against the statuses this project actually
    declares (KYO-101 made them a registry, so a hardcoded name list would be checking
    the wrong thing) and against every archetype role a status may carry.

    A record must be able to be `working` AND blocked — KYO-30 was in exactly that
    state for a whole session — and a `gated` status makes that unsayable.
    """
    ladder = load_status_ladder(project / "kyosei")
    assert "gated" not in ladder.names
    assert "gated" not in STATUS_ROLES
    assert kyo("list", "--status", "gated").code != 0

    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    assert kyo("move", record, "working").code == 0
    kyo("gate", record, "awaiting:human-review", "--tag", "ui-review")
    listed = kyo("list", "--gate-tag", "ui-review", "--json").json["records"]
    # Working AND gated, in one row, in the surface that selects by tag.
    assert [(r["id"], r["status"], r["gate_tags"]) for r in listed] == [
        (record, "working", ["ui-review"])
    ]


def test_tagging_a_gate_changes_no_status(kyo, project: Path) -> None:
    write_gate_tags(project, TWO_TAGS)
    record = new_record(kyo, criteria=["a"])
    kyo("move", record, "working")
    result = kyo("gate", record, "awaiting:human-review", "--tag", "ui-review")
    assert "status is unchanged" in result.out
    assert kyo("show", record, "--json").json["status"] == "working"


# ------------------------------------------------------- the shape, and the packaged file


def test_the_dimension_is_a_declaration_not_a_loader() -> None:
    """The four dimensions share ONE shape, and KYO-100 chose it.
    This is that inheritance, stated for this dimension: `gate-tags.yml`, top-level key
    `gate-tags`, entries with a slug name and a summary — and no loader of its own."""
    assert registry_module.GATE_TAG.filename == "gate-tags.yml"
    assert registry_module.GATE_TAG.plural == "gate-tags"
    assert registry_module.GATE_TAG.required_entry_keys == ("summary",)
    # Nothing dimension-specific: a gate-tag entry validates against nothing in
    # `contracts`, which is why the Dimension is declared in `registry` beside SCOPE
    # rather than in `contracts` beside STATUS.
    assert registry_module.GATE_TAG.optional_fields == {}
    assert registry_module.GATE_TAG.required_fields == {}
    assert registry_module.GATE_TAG in vars(registry_module).values()


def test_the_packaged_default_is_what_a_new_project_gets(project: Path) -> None:
    """One copy of this file in the codebase (law 3), copied verbatim by `kyo init`."""
    shipped = (defaults_dir() / registry_module.GATE_TAG.filename).read_text(encoding="utf-8")
    assert gate_tags_yml(project).read_text(encoding="utf-8") == shipped


def test_the_packaged_default_declares_no_tags(kyo, project: Path) -> None:
    """Shipped empty, on scopes.yml's terms rather than statuses.yml's: what a project
    waits on is a claim about THAT project, and a seeded set would be names to delete
    before the first true one is written. A project that declares none still works —
    every gate simply carries no tags, exactly as every gate did before this release."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("gate", record, "awaiting:human-review").code == 0
    assert kyo("show", record, "--json").json["gates"][0]["tags"] == []
    assert kyo("doctor").code == 0


# ------------------------------------------------------------------------- KYO-267
#
# The other end of a gate: the thing that should have produced one and did not.
# `kyo verify --verdict unmet-unobserved` records a wait on a person; the desktop's
# Awaiting list, `kyo brief`'s gated work and `kyo list --gate-tag` are all derived
# from gates and from nothing else, so a wait with no gate is a wait nobody can find.
# KYO-264.c3 and KYO-265.c3 were both left in that state.
#
# What `kyo verify` now does is SAY SO and hand over the exact line. What it must never
# do is write the gate: a gate carries an instruction addressed to a person, and
# composing it out of a criterion's reason would be the tool putting words in somebody's
# mouth. Both halves are asserted below.


def _waiting_on_a_person(
    kyo, *, reason: str = "the maintainer has not watched the running app yet"
) -> tuple[str, str]:
    """A record with one criterion verified `unmet-unobserved`, and that verify's run."""
    record = new_record(kyo, criteria=["the panel refreshes", "the badge clears"])
    result = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        reason,
    )
    assert result.code == 0, result
    return record, result.out


def test_verify_unobserved_prints_the_gate_line_that_makes_the_wait_findable(kyo) -> None:
    """KYO-267.c1. The exact command, not a description of one."""
    record, out = _waiting_on_a_person(kyo)
    expected = (
        f'kyo gate {record} awaiting:human-run --do '
        f'"{cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER}"'
    )
    assert expected in out, out


def test_the_suggested_do_is_a_placeholder_not_the_reason_quoted_back(kyo) -> None:
    """KYO-349.c1. The old suggestion quoted the criterion id and its stored --why
    straight into --do, which answers WHY a person has to look, not WHAT to look at
    or run -- so pasting it produced a gate whose payload was the reason, read as if
    it were an instruction. --do is a placeholder naming what belongs there instead;
    the id and reason are still in this output, beside the line, never inside it."""
    record, out = _waiting_on_a_person(kyo, reason="watch the badge after a cold start")
    suggestion = next(line for line in out.splitlines() if "kyo gate" in line)
    assert f"{record}.c1" not in suggestion
    assert "watch the badge after a cold start" not in suggestion
    assert cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER in suggestion
    # Beside the line, not gone: the reason is still right there in this same output.
    assert f"{record}.c1" in out
    assert "watch the badge after a cold start" in out


def test_verify_writes_no_gate_of_its_own(kyo) -> None:
    """KYO-267.c1, the half that matters most: it suggests, and a gate stays the
    controller's act. Nothing is written to `gates` by the verify above."""
    record, _ = _waiting_on_a_person(kyo)
    assert kyo("show", record, "--json").json["gates"] == []


# ------------------------------------------------------------------------ KYO-349
#
# The suggested `kyo gate ... --do "..."` line is meant to be pasted -- and the old
# `--do` was the criterion's stored --why, quoted back as if it were an instruction.
# A controller who pasted it unedited stored the REASON a person has to look as the
# gate's payload, never the thing to actually look at or run: exactly the defect a
# gate carrying "the exact command" (the project's own standing rule) exists against.
# c1 replaces --do with a named placeholder; c2 makes pasting it unedited fail loudly
# rather than storing filler as an instruction.


def test_gate_refuses_the_suggested_placeholder_pasted_unedited(kyo) -> None:
    """KYO-349.c2: the placeholder was never a payload, so `kyo gate` refuses it by
    value, naming what a real one has to be."""
    record = new_record(kyo)
    result = kyo(
        "gate", record, "awaiting:human-run",
        "--do", cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER,
    )
    assert result.code != 0
    assert "placeholder" in result.err
    assert "write" in result.err
    assert kyo("show", record, "--json").json["gates"] == []


def test_gate_accepts_the_placeholder_text_once_it_is_actually_edited(kyo) -> None:
    """The refusal is on the exact value, not on any gate whose payload happens to
    mention what a placeholder is -- a real `--do` that quotes or discusses the
    suggestion (rather than being it, unedited) is an ordinary payload."""
    record = new_record(kyo)
    result = kyo(
        "gate", record, "awaiting:human-run",
        "--do", "watch the app after a cold start, then check the badge clears",
    )
    assert result.code == 0, result
    assert kyo("show", record, "--json").json["gates"][0]["do"] == (
        "watch the app after a cold start, then check the badge clears"
    )


def test_the_pasted_suggestion_end_to_end_is_refused_not_stored(kyo) -> None:
    """The loop KYO-349 was filed over, run start to finish: `kyo verify` suggests a
    line, the controller pastes its `--do` value unedited into `kyo gate`, and the
    whole thing is refused rather than landing the reason on the record as if it
    were a command."""
    record, out = _waiting_on_a_person(kyo)
    suggestion = next(line for line in out.splitlines() if "kyo gate" in line)
    assert cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER in suggestion

    result = kyo(
        "gate", record, "awaiting:human-run",
        "--do", cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER,
    )
    assert result.code != 0
    assert kyo("show", record, "--json").json["gates"] == []


def test_no_suggestion_when_an_awaiting_gate_is_already_open(kyo) -> None:
    """The wait is already findable; repeating the remedy would teach the controller
    to ignore the line in the case where it is real."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    assert kyo("gate", record, "awaiting:human-run", "--do", "watch the app").code == 0
    result = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "the maintainer has not looked",
    )
    assert result.code == 0, result
    assert "kyo gate" not in result.out, result.out


def test_a_cleared_awaiting_gate_stops_making_the_wait_findable(kyo) -> None:
    """A gate that was cleared is not a gate. The suggestion comes back."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    gate_id = kyo(
        "gate", record, "awaiting:human-run", "--do", "watch the app", "--json"
    ).json["gate_id"]
    assert kyo("ungate", gate_id, "--why", "looked, and it was wrong again").code == 0
    result = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "still not watched on the second build",
    )
    assert f"kyo gate {record} awaiting:human-run" in result.out, result.out


def test_a_holding_for_gate_does_not_make_the_wait_findable(kyo) -> None:
    """Deliberate: the Awaiting list reads `awaiting:*` and nothing else, so a
    `holding-for` gate leaves the ask exactly as invisible as no gate at all."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    assert kyo("gate", record, "holding-for", "--do", "the latency run finishes").code == 0
    result = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "the maintainer has not looked",
    )
    assert f"kyo gate {record} awaiting:human-run" in result.out, result.out


def test_no_suggestion_while_a_dispatch_is_still_running(kyo, git_project) -> None:
    """KYO-267.c1's third condition. While a worker is out there, `unmet-unobserved`
    is the honest state of a criterion that worker is about to observe, and a gate
    would be premature."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    assert kyo("dispatch", record, "--json").code == 0
    result = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "the worker has not reported yet",
    )
    assert result.code == 0, result
    assert "kyo gate" not in result.out, result.out


def test_a_met_verdict_never_suggests_a_gate(kyo) -> None:
    """Nothing is waiting on anybody, so there is nothing to make findable."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed"
    )
    assert "kyo gate" not in result.out, result.out


def test_the_json_caller_gets_the_same_finding(kyo) -> None:
    """A `--json` consumer sees the verdict and would otherwise see none of its
    consequence."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    data = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "the maintainer has not looked",
        "--json",
    ).json
    assert data["ungated_unobserved"]["id"] == record
    assert data["ungated_unobserved"]["remedy"].startswith(
        f"kyo gate {record} awaiting:human-run --do "
    )


def test_the_class_reads_states_and_gate_types_that_really_exist() -> None:
    """Law 2, and the rename this forecloses: the predicate keys on one criterion
    state and a derived set of gate types, and either could be renamed in
    `contracts` while this module kept comparing against a string nothing holds —
    which would silently empty the class rather than fail anything."""
    from kyosei.cmd_next import AWAITING_GATE_TYPES, UNOBSERVED_CRITERION_STATE
    from kyosei.contracts import CRITERION_STATES, GATE_TYPES_REQUIRING_DO

    assert UNOBSERVED_CRITERION_STATE in CRITERION_STATES
    assert AWAITING_GATE_TYPES and set(GATE_TYPES) >= AWAITING_GATE_TYPES
    # The remedy names `awaiting:human-run` because its `--do` payload is required:
    # the instruction to the person cannot be lost by forgetting a flag.
    assert "awaiting:human-run" in GATE_TYPES_REQUIRING_DO


# ------------------------------------------------------------------ KYO-272
#
# KYO-267.c1 put the prompt above in `_verify_one` only — one criterion, typed by a
# controller. But KYO-264.c3 and KYO-265.c3, the case KYO-267 was filed on, both
# arrived as `unmet-unobserved` verdicts inside a worker's REPORT, applied in bulk by
# `kyo verify --from-report <dispatch-id>` (KYO-198) through `_verify_from_report` —
# a different code path that landed the same wait with no follow-up line. This
# section proves the two paths agree: both call `cmd_next.ungated_unobserved`, the
# one predicate, and both print the line `ungated_unobserved_remedy` builds, the one
# function — never a description of the command, the command itself.


def _dispatch(kyo, record: str) -> dict:
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    return result.json


def _write_report(tmp_path: Path, name: str, payload: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _reported(kyo, tmp_path: Path, record: str, verdicts: list[dict], name: str = "r.json") -> dict:
    """Dispatch `record`, ingest a report carrying `verdicts`, return the ingest's json."""
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record,
        worked_from_sha=d["worked_from_sha"], criteria_verdicts=verdicts,
    )  # fmt: skip
    ingested = kyo(
        "report", d["dispatch_id"], "--file", _write_report(tmp_path, name, report), "--json"
    )
    assert ingested.code == 0, ingested
    return ingested.json


def test_verify_from_report_prints_the_gate_line_for_an_unmet_unobserved_verdict(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-272.c1. The exact line `_verify_one` prints, for a verdict that arrived
    through a worker's report rather than a controller's typed `kyo verify`."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{
            "criterion_id": f"{record}.c1", "verdict": "unmet-unobserved",
            "method": "reasoned-about",
            "reason": "the maintainer has not watched the running app yet",
        }],
    )  # fmt: skip

    applied = kyo("verify", "--from-report", ingested["dispatch_id"])
    assert applied.code == 0, applied
    expected = (
        f'kyo gate {record} awaiting:human-run --do '
        f'"{cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER}"'
    )
    assert expected in applied.out, applied.out


def test_verify_from_report_json_caller_gets_the_same_finding(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """A `--json` consumer sees the verdict just applied and would otherwise see none
    of its consequence — the same guarantee KYO-267 made for `kyo verify`."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{
            "criterion_id": f"{record}.c1", "verdict": "unmet-unobserved",
            "method": "reasoned-about", "reason": "the maintainer has not looked",
        }],
    )  # fmt: skip
    applied = kyo("verify", "--from-report", ingested["dispatch_id"], "--json")
    assert applied.code == 0, applied
    prompts = applied.json["ungated_unobserved"]
    assert len(prompts) == 1
    assert prompts[0]["id"] == record
    assert prompts[0]["remedy"].startswith(f"kyo gate {record} awaiting:human-run --do ")


def test_verify_from_report_writes_no_gate_of_its_own(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The half that matters most: it suggests, and a gate stays the controller's
    act. Nothing is written to `gates` by the verify above."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{
            "criterion_id": f"{record}.c1", "verdict": "unmet-unobserved",
            "method": "reasoned-about", "reason": "not watched yet",
        }],
    )  # fmt: skip
    assert kyo("verify", "--from-report", ingested["dispatch_id"]).code == 0
    assert kyo("show", record, "--json").json["gates"] == []


def test_verify_from_report_prints_nothing_when_an_awaiting_gate_is_already_open(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The wait is already findable before the report is even applied; repeating the
    remedy would teach the controller to ignore the line in the case where it is
    real."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    assert kyo("gate", record, "awaiting:human-run", "--do", "watch the app").code == 0
    ingested = _reported(
        kyo, tmp_path, record,
        [{
            "criterion_id": f"{record}.c1", "verdict": "unmet-unobserved",
            "method": "reasoned-about", "reason": "not watched yet",
        }],
    )  # fmt: skip
    applied = kyo("verify", "--from-report", ingested["dispatch_id"])
    assert applied.code == 0, applied
    assert "kyo gate" not in applied.out, applied.out


def test_verify_from_report_prints_nothing_while_another_dispatch_still_runs_against_the_record(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-267.c1's third condition, met in the shape this path actually reaches it
    in: at the moment a report is ingested its OWN dispatch has already moved to
    `reported`, not `running` (KYO-90) — so the case worth proving is a SECOND
    dispatch still running against the same record when the first one's verdicts are
    applied, the KYO-264/265 shape this dispatch was filed on."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{
            "criterion_id": f"{record}.c1", "verdict": "unmet-unobserved",
            "method": "reasoned-about", "reason": "not watched yet",
        }],
    )  # fmt: skip
    assert _dispatch(kyo, record)  # a second, still-running dispatch on the same record

    applied = kyo("verify", "--from-report", ingested["dispatch_id"])
    assert applied.code == 0, applied
    assert "kyo gate" not in applied.out, applied.out


def test_verify_from_report_a_met_verdict_never_suggests_a_gate(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """Nothing is waiting on anybody, so there is nothing to make findable."""
    record = new_record(kyo, criteria=["the panel refreshes"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}],
    )  # fmt: skip
    applied = kyo("verify", "--from-report", ingested["dispatch_id"])
    assert applied.code == 0, applied
    assert "kyo gate" not in applied.out, applied.out
