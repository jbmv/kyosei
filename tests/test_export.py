"""The deterministic export — the property that keeps changes reviewable in git."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kyosei import cli
from kyosei.export import record_document, serialise
from kyosei.schema import SCHEMA_VERSION
from kyosei.store import open_store

from .conftest import event_lines, events_text, export_of, new_record


def _export_family_trees() -> list:
    """The parsed source of every module ``export.py`` was split into (KYO-429) -- the
    scope a "stated once in code" check has to read, or a second copy of a rule in a
    sibling module would pass unseen."""
    import ast
    import inspect

    from kyosei import events_ledger, export, ledger_repair, ledger_scrub, ledgers

    return [
        ast.parse(inspect.getsource(module))
        for module in (export, events_ledger, ledgers, ledger_scrub, ledger_repair)
    ]


def test_an_export_appears_for_every_record(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    assert export_of(project, record).is_file()


def test_the_export_is_regenerated_after_every_mutation(kyo, project: Path, write_file) -> None:
    record = new_record(kyo)
    path = export_of(project, record)
    mutations = [
        ("edit", record, "--title", "A better title"),
        ("edit", record, "--description-file", write_file("d.md", "some prose")),
        ("edit", record, "--add-criteria-file", write_file("c.txt", "a criterion\n")),
        ("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about"),
        ("edit", record, "--note-file", write_file("n.md", "a note")),
        ("gate", record, "awaiting:human-review"),
        (
            "claim", record, "--level", "asserted", "--method", "code read",
            "--file", write_file("claim.md", "the widget explodes when the cache is cold"),
        ),
        ("attach", record, "--file", write_file("evidence.txt", "console excerpt")),
        ("move", record, "working"),
        # KYO-490: the gate above is still open, and settling over it takes --force.
        ("wont-do", record, "--why", "changed my mind", "--force"),
    ]
    previous = path.read_text(encoding="utf-8")
    for command in mutations:
        assert kyo(*command).code == 0, command
        current = path.read_text(encoding="utf-8")
        assert current != previous, f"{command} left the export stale"
        previous = current


def test_regeneration_is_byte_for_byte_deterministic(kyo, project: Path) -> None:
    """Same database, same bytes — otherwise every command churns the git diff."""
    record = new_record(kyo, criteria=["one", "two"])
    kyo("gate", record, "awaiting:event", "--do", "CI goes green")
    on_disk = export_of(project, record).read_text(encoding="utf-8")

    store = open_store(project)
    try:
        first = serialise(record_document(store, record))
        second = serialise(record_document(store, record))
    finally:
        store.close()
    assert first == second == on_disk


def test_keys_are_sorted_so_diffs_stay_readable(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["one"])
    text = export_of(project, record).read_text(encoding="utf-8")
    document = json.loads(text)
    assert list(document) == sorted(document)
    assert text.endswith("\n")
    assert "\n  " in text  # indented, not a single line


def test_one_file_per_record(kyo, project: Path) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    files = sorted(
        p.name
        for p in (project / "kyosei" / "export").glob("*.json")
        if not p.name.startswith("_")
    )
    assert files == [f"{first}.json", f"{second}.json"]


def test_a_link_re_exports_both_endpoints(kyo, project: Path) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    before = export_of(project, second).read_text(encoding="utf-8")
    kyo("link", first, "blocks", second)
    after = export_of(project, second).read_text(encoding="utf-8")
    assert before != after
    assert "incoming" in after


def test_nested_entries_carry_their_own_seq_and_link_carries_created_at(kyo, project: Path) -> None:
    """Rebuild needs these directly (never re-derived from allocation order)."""
    a = new_record(kyo, criteria=["one", "two"])
    b = new_record(kyo, title="two")
    kyo("link", a, "blocks", b)
    kyo("gate", a, "awaiting:human-review")
    document = json.loads(export_of(project, a).read_text(encoding="utf-8"))
    assert [c["seq"] for c in document["criteria"]] == [1, 2]
    assert document["gates"][0]["seq"] == 1
    outgoing = next(link_ for link_ in document["links"] if link_["direction"] == "outgoing")
    assert outgoing["seq"] == 1
    assert outgoing["created_at"] is not None


def test_the_export_carries_the_full_record(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, criteria=["one"])
    kyo("edit", record, "--note-file", write_file("n.md", "a note"))
    kyo("gate", record, "holding-for", "--do", "the measurement finishes")
    document = json.loads(export_of(project, record).read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "id",
        "kind",
        "title",
        "status",
        "plan",
        # KYO-100: always present, `null` when nothing declared one. Its absence is
        # what a pre-schema_version-13 document has, and `kyo rebuild` reads that
        # absence as the version signal (schema.EXPORT_FIELD_DEFAULTS[13]).
        "scope",
        # KYO-498: always present, `null` when no first-recorded shift note was named;
        # absent in a pre-26 document (schema.EXPORT_FIELD_DEFAULTS[26]).
        "since_note",
        "description",
        "wont_do_reason",
        "created_at",
        "updated_at",
        "criteria",
        "notes",
        "touches",
        # KYO-137: the docs governance pair. `doc_refs` is what this record declares
        # it affects; `docs_obligations` is what its dispatches raised and whether a
        # controller has answered. Both always present, empty lists when there are
        # none, which is what a pre-schema_version-16 document has
        # (schema.EXPORT_FIELD_DEFAULTS[16]).
        "doc_refs",
        "docs_obligations",
        "gates",
        "links",
        "claims",
        "evidence_artifacts",
        "dispatches",
        "reports",
        "flags",
    }


# ------------------------------------------------------------------ KYO-194
#
# Where a write command's `--json` payload puts the id it minted. The consumer report
# these tests come from: `kyo new bug ... --json | python3 -c
# 'print(json.load(sys.stdin).get("id"))'` printed `None` three times in one day, and
# the id was recovered from `kyo list` — a human surface, read for a machine fact, three
# chances to transpose a character. The script was not wrong about the convention; there
# was no convention.


def test_a_minted_record_id_is_at_the_payload_root_on_every_command_that_mints_one(
    kyo, project: Path, write_file
) -> None:
    """KYO-194.c1, the half about record ids. One key, `record_id`, on every write
    command whose payload carries a record — and it is the *same* id the nested
    document carries, checked rather than assumed, because a top-level key holding the
    wrong id is worse than no key at all."""
    record = new_record(kyo, criteria=["a"])

    created = kyo(
        "new", "bug", "--title", "Filed straight", "--criteria-file", "-", "--json",
        stdin="something is true\n",
    )  # fmt: skip
    assert created.code == 0, created
    assert created.json["record_id"] == created.json["record"]["id"]

    edited = kyo("edit", record, "--title", "A better title", "--json")
    assert edited.code == 0, edited
    assert edited.json["record_id"] == edited.json["record"]["id"] == record

    spawned = kyo(
        "spawn", record, "--kind", "task", "--title", "A spawned thing",
        "--criteria-file", write_file("sc.txt", "another thing is true\n"),
        "--touches-file", write_file("st.txt", "kyosei/config.yml\n"), "--json",
    )  # fmt: skip
    assert spawned.code == 0, spawned
    assert spawned.json["record_id"] == spawned.json["record"]["id"]


def test_a_promotion_puts_the_minted_record_id_at_the_payload_root(
    kyo, project: Path, write_file
) -> None:
    """`kyo promote` is the third of c1's three, and it needs an idea file rather than
    a plain record, so it gets its own test rather than a branch in the one above."""
    idea = project / "ideas" / "a-thought.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# A thought\n\nWorth doing something about.\n", encoding="utf-8")
    promoted = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("pc.txt", "something is true\n"),
        "--touches-file", write_file("pt.txt", "kyosei/config.yml\n"), "--json",
    )  # fmt: skip
    assert promoted.code == 0, promoted
    assert promoted.json["record_id"] == promoted.json["record"]["id"]


def test_a_minted_child_id_is_at_the_payload_root_under_a_key_of_the_same_shape(
    kyo, project: Path, write_file
) -> None:
    """KYO-194.c1, the half about child ids. `claim_id` and `evidence_id` were already
    there and are the shape the rest were made to match; `link_id` was the odd one out,
    reachable only at `link.id`, so a script that worked for `kyo claim` returned `None`
    for `kyo link` too."""
    one = new_record(kyo, criteria=["a"])
    two = new_record(kyo, title="Another record", criteria=["b"])

    claimed = kyo(
        "claim", one, "--level", "asserted", "--method", "code read",
        "--file", write_file("claim.md", "the widget explodes when the cache is cold"),
        "--json",
    )  # fmt: skip
    assert claimed.code == 0, claimed
    assert claimed.json["claim_id"].startswith(f"{one}.")

    attached = kyo(
        "attach", one, "--file", write_file("evidence.txt", "console excerpt"), "--json",
    )  # fmt: skip
    assert attached.code == 0, attached
    assert attached.json["evidence_id"].startswith(f"{one}.")

    linked = kyo("link", one, "blocks", two, "--json")
    assert linked.code == 0, linked
    assert linked.json["link_id"] == linked.json["link"]["id"], (
        "the link id was reachable only at `link.id`; a script written against "
        "`kyo claim --json` found nothing at the root and reported None"
    )


def test_every_command_payload_carries_its_minted_id_at_the_top_level() -> None:
    """KYO-194.c2. The enumeration is `cli.command_payload_keys`, which reads this
    package's own sources with `ast` rather than a list kept by hand — so a write
    command added tomorrow is in this test the moment it exists, which a list is
    exactly what cannot promise. The rule is `cli.PAYLOAD_ID_CONVENTION`: a payload
    that nests an object names that object's id beside it at the root.

    What this cannot see is stated on `command_payload_keys` and worth repeating:
    payloads built by spreading `**data` (`kyo sweep`, `kyo brief`, `kyo base run`)
    contribute only their literal keys, and a command with several payload variants is
    reported as their intersection — so a variant that carries neither the object nor
    its id is invisible here, which is correct (it has no object to name) rather than
    merely tolerated.
    """
    payloads = cli.command_payload_keys()
    assert len(payloads) > 30, (
        f"the payload scan found only {len(payloads)} command(s); it reads `Output(...)` "
        "dict literals out of src/kyosei/*.py, so a near-empty result means the scan "
        "broke, not that the commands went away"
    )
    offenders = [
        (command, nested, id_key)
        for command, keys in sorted(payloads.items())
        for nested, id_key in cli.PAYLOAD_ID_CONVENTION.items()
        if nested in keys and id_key not in keys
    ]
    assert not offenders, (
        "these payloads nest an object and do not name its id at the top level: "
        + "; ".join(
            f"`kyo {command} --json` has {nested!r} but no {id_key!r}"
            for command, nested, id_key in offenders
        )
        + ". A script reading the root gets None, not an error (KYO-194)"
    )


def test_kyo_reference_parses_each_source_file_once(monkeypatch) -> None:
    """KYO-578. `reference_markdown` asks for the payload keys once per command; before
    the scan was cached it re-parsed every `src/kyosei/*.py` each time (6240 parses,
    about 14 s of `kyo reference`'s 15). Counted through `ast.parse` from a cold cache,
    a whole reference render parses each source file at most once."""
    import ast
    import pathlib

    sources = len(list(pathlib.Path(cli.__file__).resolve().parent.glob("*.py")))
    real_parse = ast.parse
    parses: list[int] = []

    def counting_parse(*args, **kwargs):
        parses.append(1)
        return real_parse(*args, **kwargs)

    cli._command_payload_keys_once.cache_clear()
    monkeypatch.setattr(ast, "parse", counting_parse)
    try:
        cli.reference_markdown()
    finally:
        cli._command_payload_keys_once.cache_clear()
    assert 0 < len(parses) <= sources, (
        f"one `kyo reference` render parsed {len(parses)} source file(s) for "
        f"{sources} file(s) on disk; the payload-key scan must be computed once per "
        "process (`cli._command_payload_keys_once`), not once per command"
    )


def test_the_payload_id_check_is_a_check_and_not_a_tautology() -> None:
    """The canary the check above needs: run the same rule over the payload shapes as
    they were before KYO-194 and it must call every one of them out. Without this,
    a `PAYLOAD_ID_CONVENTION` that had quietly emptied would pass in silence."""
    before_kyo194 = {
        "new": ("ok", "command", "record", "duplicate_warnings", "scope_suggestion"),
        "claim": ("ok", "command", "claim_id", "record"),
        "link": ("ok", "command", "link", "records"),
    }
    offenders = {
        command
        for command, keys in before_kyo194.items()
        for nested, id_key in cli.PAYLOAD_ID_CONVENTION.items()
        if nested in keys and id_key not in keys
    }
    assert offenders == {"new", "claim", "link"}, (
        "the pre-KYO-194 shapes are the exact population this rule exists to reject; "
        f"it rejected {sorted(offenders)}"
    )


def test_the_generated_reference_states_each_write_command_s_json_id_keys() -> None:
    """KYO-194.c3. `kyo reference` is generated from the parser and knew nothing about
    payloads, so the only way to learn where the minted id lives was to guess, get
    `None`, and go read it off `kyo list`. It now states the keys, from
    `cli.command_payload_id_keys` — the same function the test above reads, so the
    reference cannot name a key the payload lacks nor omit one it has."""
    text = cli.reference_markdown()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## `kyo "):
            current = line[len("## `kyo ") :].rstrip("`")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    id_keys = cli.command_payload_id_keys()
    assert id_keys, "the payload scan found nothing, so this test would assert nothing"
    checked = 0
    for command, keys in sorted(id_keys.items()):
        if not keys:
            continue
        top_level = command.split(" ", 1)[0]
        assert top_level in sections, (
            f"the payload names a command {command!r} the reference has no section for"
        )
        body = "\n".join(sections[top_level])
        for key in keys:
            assert f"`{key}`" in body, (
                f"`kyo {command} --json` carries {key!r} and the reference section for "
                f"`kyo {top_level}` never names it"
            )
        checked += 1
    assert checked >= 15, f"only {checked} command section(s) were checked"


def test_the_reference_names_the_key_the_consumer_s_script_could_not_find() -> None:
    """The specific regression: `kyo new --json` and `record_id`, in the section a
    script author would actually open. A generic loop can pass while the one command in
    the report is the one that slipped."""
    text = cli.reference_markdown()
    section = text.split("## `kyo new`", 1)[1].split("\n## ", 1)[0]
    assert "`record_id`" in section, section


# --------------------------------------------------------------- KYO-268: the events ledger
#
# Two defects in the export's SHAPE, measured on this project's own store:
# an event snapshot was unbounded because it embedded every evidence artifact's body
# (55.0 MB of a 224.1 MB ledger, across 3,736 snapshots), and the ledger was one file
# that every commit re-stored whole — so `git maintenance`'s repack delta-searched dozens
# of 224 MB versions against each other at 22 GB resident and 27.6 GB of swap, and the
# machine stopped responding with nothing naming the cause.


def _snapshot_bytes(event: dict, side: str) -> int:
    document = event.get(side)
    return 0 if document is None else len(json.dumps(document, sort_keys=True).encode("utf-8"))


def test_a_snapshot_of_a_record_with_a_1mb_artifact_is_under_10kb(
    kyo, project: Path, write_file
) -> None:
    """KYO-268.c1, measured rather than argued.

    The 1 MB artifact is the fixture the criterion asks for; the assertion is on the
    snapshot, not on the artifact, because the artifact is *supposed* to be 1 MB — it is
    stored verbatim, once, on the record's own export document (law 5). What must not
    happen is the ledger keeping a second copy of it per mutation.
    """
    body = "x" * (1024 * 1024)
    record = new_record(kyo, kind="bug", title="Big evidence", criteria=["a"])
    assert kyo("attach", record, "--file", write_file("big.txt", body)).code == 0
    assert kyo("edit", record, "--title", "Still big").code == 0
    assert kyo("move", record, "working").code == 0

    events = event_lines(project)
    with_evidence = [
        event
        for event in events
        if isinstance(event.get("after"), dict) and event["after"].get("evidence_artifacts")
    ]
    assert with_evidence, "no event snapshot mentions the artifact at all"
    largest = max(
        max(_snapshot_bytes(event, "before"), _snapshot_bytes(event, "after"))
        for event in with_evidence
    )
    assert largest < 10 * 1024, (
        f"the largest snapshot of a record carrying a 1 MB artifact is {largest} bytes; "
        "an evidence body has got back into the ledger (KYO-268.c1)"
    )
    # And the body really is still stored, once, where it belongs.
    assert body in export_of(project, record).read_text(encoding="utf-8")
    assert body not in events_text(project)


def test_a_snapshot_names_each_artifact_by_id_and_sha_only(
    kyo, project: Path, write_file
) -> None:
    """KYO-268.c1's shape, asserted as an exact key set for the reason
    ``test_events_jsonl_gets_one_line_per_event`` asserts one: a key arriving back in a
    snapshot unannounced is the drift this test exists to catch, and the key that would
    arrive back is ``text``."""
    record = new_record(kyo, kind="bug", title="Evidence", criteria=["a"])
    result = kyo("attach", record, "--file", write_file("e.txt", "console excerpt"), "--json")
    assert result.code == 0, result
    evidence_id = result.json["evidence_id"]

    artifacts = [
        artifact
        for event in event_lines(project)
        for side in (event.get("before"), event.get("after"))
        if isinstance(side, dict)
        for artifact in side.get("evidence_artifacts") or []
    ]
    assert artifacts
    assert all(set(artifact) == {"id", "sha256"} for artifact in artifacts), artifacts
    document = json.loads(export_of(project, record).read_text(encoding="utf-8"))
    stored = next(a for a in document["evidence_artifacts"] if a["id"] == evidence_id)
    matching = [a for a in artifacts if a["id"] == evidence_id]
    assert matching and all(a["sha256"] == stored["sha256"] for a in matching)
    # The sha is the point: the snapshot is a checkable reference into the document,
    # not merely a note that something was attached.
    assert stored["sha256"] == hashlib.sha256(b"console excerpt").hexdigest()


def test_the_stripper_leaves_a_payload_that_is_not_a_record_document_alone() -> None:
    """``record_event`` is also called with ``{"redacted": ...}`` and with a stop
    audit's ``{"stop_audit": ...}``, neither of which has an evidence list. The stripper
    must pass those through rather than reshaping them — and must never mutate its
    argument, because ``_commit_and_export`` hands the same document to the event and to
    the command's own ``--json`` payload."""
    from kyosei.events_ledger import event_snapshot

    assert event_snapshot(None) is None
    assert event_snapshot({"redacted": "TST-1.e1"}) == {"redacted": "TST-1.e1"}

    original = {"id": "TST-1", "evidence_artifacts": [{"id": "TST-1.e1", "text": "body",
                                                       "sha256": "abc", "author": "me"}]}
    stripped = event_snapshot(original)
    assert stripped["evidence_artifacts"] == [{"id": "TST-1.e1", "sha256": "abc"}]
    assert original["evidence_artifacts"][0]["text"] == "body", "the argument was mutated"


# ------------------------------------------------------------------------ KYO-268.c2


def _shard_dir(project: Path) -> Path:
    return project / "kyosei" / "export" / "_events"


def _shard_state(project: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(_shard_dir(project).iterdir())}


def test_events_are_written_to_shards_not_to_one_growing_file(kyo, project: Path) -> None:
    new_record(kyo, title="one")
    new_record(kyo, title="two")
    assert not (project / "kyosei" / "export" / "_events.jsonl").exists()
    shards = sorted(_shard_dir(project).iterdir())
    assert [p.name for p in shards] == ["000001.jsonl"]
    assert len(event_lines(project)) == 2


def test_a_shard_closes_at_the_limit_and_then_one_file_changes_per_mutation(
    kyo, project: Path, monkeypatch
) -> None:
    """KYO-268.c2's load-bearing assertion, and the whole reason for the layout.

    Under one file, every mutation rewrote a file whose size was the entire history —
    so every commit handed git a new blob that size. Under shards, a mutation appends to
    the newest shard and the closed ones are blobs git already has. The limit is patched
    down rather than reached honestly: the real one is 4 MiB
    (``events_ledger.EVENTS_SHARD_MAX_BYTES``) and the property under test is the rule, not the
    number.
    """
    from kyosei import events_ledger as events_ledger_module

    monkeypatch.setattr(events_ledger_module, "EVENTS_SHARD_MAX_BYTES", 2048)
    for i in range(12):
        assert kyo("new", "bug", "--title", f"record {i}", "--json").code == 0
    closed = sorted(p.name for p in _shard_dir(project).iterdir())
    assert len(closed) > 1, f"the limit never closed a shard: {closed}"

    before = _shard_state(project)
    for i in range(3):
        assert kyo("new", "bug", "--title", f"more {i}", "--json").code == 0
        after = _shard_state(project)
        changed = {name for name in after if before.get(name) != after[name]}
        assert len(changed) == 1, (
            f"mutation {i} rewrote {sorted(changed)}; exactly one shard may change"
        )
        # And the one that changed only ever grew — append-only, charter Layer 2.
        name = changed.pop()
        if name in before:
            assert after[name].startswith(before[name])
        before = after


def test_the_shard_rule_is_stated_once_in_code() -> None:
    """Law 3: one constant decides where a shard closes, and one function reads it.

    Asserted because the alternative — a size test written inline at the append site and
    again in the migration — is how the two would come to disagree, leaving a migrated
    export packed to a different rule than the one appends follow.
    """
    import ast

    readers = {
        node.name
        for tree in _export_family_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Name) and inner.id == "EVENTS_SHARD_MAX_BYTES"
            for inner in ast.walk(node)
        )
    }
    assert readers == {"shard_to_append_to", "migrate_events_to_shards"}, readers


# ------------------------------------------------------------------------ KYO-268.c3


def _make_monolithic(project: Path) -> Path:
    """Put this project's export back into the pre-KYO-268 layout, verbatim."""
    from kyosei.events_ledger import events_files_in

    export_dir = project / "kyosei" / "export"
    text = "".join(p.read_text(encoding="utf-8") for p in events_files_in(export_dir))
    for shard in sorted(_shard_dir(project).iterdir()):
        shard.unlink()
    _shard_dir(project).rmdir()
    monolithic = export_dir / "_events.jsonl"
    monolithic.write_text(text, encoding="utf-8")
    return monolithic


def test_doctor_accepts_the_old_layout_and_names_the_migration(kyo, project: Path) -> None:
    record = new_record(kyo, kind="bug", title="Old layout", criteria=["a"])
    for i in range(3):
        assert kyo("edit", record, "--title", f"renamed {i}").code == 0
    monolithic = _make_monolithic(project)

    result = kyo("doctor")
    assert result.code == 0, (
        "the old layout is accepted during the transition, so this is a notice and the "
        f"command exits 0:\n{result.err}"
    )
    reported = result.out + result.err
    assert "_events.jsonl" in reported
    assert "kyo export --migrate-events" in reported
    assert monolithic.is_file()


def test_the_named_remedy_clears_the_check(kyo, project: Path) -> None:
    """The standing rule for any message naming a remedy: a test proves the remedy
    clears the thing it is named for."""
    record = new_record(kyo, kind="bug", title="Old layout", criteria=["a"])
    for i in range(3):
        assert kyo("edit", record, "--title", f"renamed {i}").code == 0
    before = event_lines(project)
    monolithic = _make_monolithic(project)

    result = kyo("export", "--migrate-events", "--json")
    assert result.code == 0, result
    assert result.json["migrated"] is True
    assert result.json["events"] == len(before)

    assert not monolithic.exists()
    assert sorted(p.name for p in _shard_dir(project).iterdir()) == result.json["shards"]
    # Lossless, and "lossless" means the bytes: same lines, same order, same stamps.
    assert event_lines(project) == before

    after = kyo("doctor")
    assert after.code == 0, after.err
    assert "kyo export --migrate-events" not in (after.out + after.err)


def test_migrating_an_already_sharded_export_says_so_and_changes_nothing(
    kyo, project: Path
) -> None:
    new_record(kyo, kind="bug", title="Already sharded", criteria=["a"])
    before = _shard_state(project)

    result = kyo("export", "--migrate-events", "--json")
    assert result.code == 0, result
    assert result.json["migrated"] is False
    assert _shard_state(project) == before


def test_the_migration_also_packs_shards_written_beside_an_unmigrated_file(
    kyo, project: Path
) -> None:
    """A build at schema_version 20 appends to shards whether or not the old file has
    been migrated, so an export can legitimately hold both — with every monolithic line
    OLDER than every shard line. The migration has to re-lay the shards, not append to
    them, or history comes out in the wrong order."""
    record = new_record(kyo, kind="bug", title="Both layouts", criteria=["a"])
    assert kyo("edit", record, "--title", "first").code == 0
    _make_monolithic(project)
    assert kyo("edit", record, "--title", "second").code == 0
    assert (project / "kyosei" / "export" / "_events.jsonl").is_file()
    assert _shard_dir(project).is_dir()
    before = event_lines(project)

    assert kyo("export", "--migrate-events").code == 0
    assert not (project / "kyosei" / "export" / "_events.jsonl").exists()
    assert event_lines(project) == before


def test_the_migration_never_holds_more_than_one_line_in_memory() -> None:
    """KYO-268.c3, and the reason it is a property test rather than a benchmark.

    This command's whole reason for existing is a 226 MB ledger on a machine whose swap
    was at 27.6 GB of 28.6 GB. Reading that file into a list first would ask for several
    hundred megabytes of resident memory to fix an out-of-memory machine — and it would
    do it on exactly the machine least able to spare it, because a store only reaches
    this size on the layout that made it grow.

    Asserted against the AST rather than by measuring, for the same reason
    ``test_the_shard_rule_is_stated_once_in_code`` is: a measurement of this passes on a
    small fixture whatever the code does. What is checked is that the function's own body
    never binds the whole of a file — no ``.read_text()``, ``.readlines()``,
    ``.extend(...)`` or ``list(...)`` over a handle — and does iterate a file object.
    """
    import ast

    function = next(
        node
        for tree in _export_family_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "migrate_events_to_shards"
    )
    called = {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    whole_file = {"read_text", "read_bytes", "readlines", "extend"}
    assert not (called & whole_file), sorted(called & whole_file)
    assert any(
        isinstance(node, ast.For) and isinstance(node.target, ast.Name)
        for node in ast.walk(function)
    )


def test_an_interrupted_migration_leaves_no_staging_directory_behind(
    kyo, project: Path
) -> None:
    """The swap leaves staging and superseded directories on disk for the length of two
    renames. A previous run that died mid-way must not stop the next one, and neither
    directory may survive a successful run — a stray ``_events.superseded/`` in a
    git-canonical export is a second copy of the ledger nothing reads."""
    record = new_record(kyo, kind="bug", title="Interrupted", criteria=["a"])
    assert kyo("edit", record, "--title", "again").code == 0
    export_dir = project / "kyosei" / "export"
    _make_monolithic(project)
    # Exactly what a killed run leaves behind.
    (export_dir / "_events.migrating").mkdir()
    (export_dir / "_events.migrating" / "000001.jsonl").write_text("{}\n", encoding="utf-8")
    (export_dir / "_events.superseded").mkdir()

    before = event_lines(project)
    assert kyo("export", "--migrate-events").code == 0
    assert event_lines(project) == before
    assert not (export_dir / "_events.migrating").exists()
    assert not (export_dir / "_events.superseded").exists()



# ------------------------------------------------------------------------ KYO-274.c1
#
# KYO-268.c1 took evidence bodies out of an event snapshot and stopped there, because
# widening its own criterion was not its worker's to do. What it left behind, measured on
# this store's own ledger and filed as this record: 68.3 MB of frozen dispatch briefs and
# 49.1 MB of ingested worker reports in a 224.1 MB ledger — a record document embeds all
# three bodies by the same mechanism, so every mutation of a dispatched record copied its
# brief and its report into the ledger twice.


def _dispatch_with_a_big_brief_and_report(
    kyo, project: Path, write_file, brief_body: str, report_body: str
) -> dict:
    """A record with one dispatch whose brief carries ``brief_body`` and whose ingested
    report carries ``report_body``.

    The brief is made big through a COVERED record's description rather than the
    dispatched record's own, and that shape is the whole point. A frozen brief renders
    every covered record's description (``brief._also_covers_block``), so the body lands
    in the brief while the dispatched record's own document stays small — which is what
    lets the measurement below be about the brief and nothing else. A big description on
    the record itself would put the same bytes in its own snapshot by a route this record
    does not touch: ``records.description`` is a scalar a snapshot must keep, because
    ``store.description_dated_at`` dates the last edit by comparing it across the two
    sides. Written the other way first, and the measured snapshot was 1,049,919 bytes of
    description with the brief already gone.

    The report is made big the way a real report is: prose in a criterion verdict's
    ``notes``, which `kyo report` ingests verbatim.
    """
    from .conftest import valid_report

    record = new_record(kyo, kind="bug", title="Big brief", criteria=["a"])
    covered = new_record(kyo, kind="bug", title="Covered", criteria=["b"])
    assert kyo("edit", covered, "--description-file", write_file("d.md", brief_body)).code == 0
    dispatched = kyo("--full", "dispatch", record, "--also-covers", covered, "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    assert brief_body in dispatched.json["record"]["dispatches"][0]["brief"]

    report = valid_report(
        dispatch_id=dispatch_id,
        record_id=record,
        worked_from_sha=dispatched.json["worked_from_sha"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "met",
                "method": "reasoned-about",
                "notes": report_body,
            }
        ],
    )
    report_path = project / "a-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = kyo("report", dispatch_id, "--file", str(report_path), "--json")
    assert result.code == 0, result
    report_path.unlink()
    return {"record": record, "covered": covered, "dispatch_id": dispatch_id}


def _stored(project: Path, record: str) -> dict:
    return json.loads(export_of(project, record).read_text(encoding="utf-8"))


def test_a_snapshot_of_a_record_with_a_1mb_brief_and_report_is_under_10kb(
    kyo, git_project: Path, write_file
) -> None:
    """KYO-274.c1's measurement, and deliberately the same one KYO-268.c1 made: the
    fixture is a record with ONE dispatch whose brief and report are each 1 MB, and the
    bound is the 10 KB that record set. Both bodies are *supposed* to be 1 MB — each is
    stored verbatim, once, where it belongs (law 5). What must not happen is a snapshot
    carrying a second copy of either, per mutation, forever.
    """
    brief_body = "b" * (1024 * 1024)
    report_body = "r" * (1024 * 1024)
    fixture = _dispatch_with_a_big_brief_and_report(
        kyo, git_project, write_file, brief_body, report_body
    )
    record = fixture["record"]
    assert kyo("edit", record, "--title", "Still big").code == 0
    assert kyo("move", record, "working").code == 0

    stored = _stored(git_project, record)
    brief = stored["dispatches"][0]["brief"]
    assert len(brief) > 1024 * 1024, "precondition: the frozen brief is over 1 MB"

    with_dispatch = [
        event
        for event in event_lines(git_project)
        if event.get("record_id") == record
        and isinstance(event.get("after"), dict)
        and event["after"].get("dispatches")
    ]
    assert with_dispatch, "no event snapshot mentions the dispatch at all"
    largest = max(
        max(_snapshot_bytes(event, "before"), _snapshot_bytes(event, "after"))
        for event in with_dispatch
    )
    assert largest < 10 * 1024, (
        f"the largest snapshot of a record carrying a 1 MB brief and a 1 MB report is "
        f"{largest} bytes; a body has got back into the ledger (KYO-274.c1)"
    )
    # And both bodies really are still stored, once, where they belong.
    assert brief_body in brief
    assert report_body in json.dumps(stored["reports"][0]["body"])
    ledger = events_text(git_project)
    assert brief not in ledger
    assert report_body not in ledger


def test_a_snapshot_names_a_brief_and_a_report_by_digest_not_body(
    kyo, git_project: Path, write_file
) -> None:
    """KYO-274.c1's shape, asserted as the presence of one key and the absence of another
    for the reason ``test_a_snapshot_names_each_artifact_by_id_and_sha_only`` asserts an
    exact key set: the key that would arrive back unannounced is ``brief``.

    The digest is the point, exactly as ``sha256`` is for evidence — a snapshot that
    named only the dispatch id would say a brief existed, not which bytes it was. It is
    recomputed here from the body on the record's own export document rather than read
    back out of the snapshot, because a digest nobody can re-derive is decoration.
    """
    from kyosei.events_ledger import body_digest

    fixture = _dispatch_with_a_big_brief_and_report(
        kyo, git_project, write_file, "brief prose " * 20, "report prose " * 20
    )
    stored = _stored(git_project, fixture["record"])
    stored_dispatch = stored["dispatches"][0]
    stored_report = stored["reports"][0]

    snapshots = [
        side
        for event in event_lines(git_project)
        for side in (event.get("before"), event.get("after"))
        if isinstance(side, dict) and side.get("id") == fixture["record"]
    ]
    dispatches = [row for side in snapshots for row in side.get("dispatches") or []]
    reports = [row for side in snapshots for row in side.get("reports") or []]
    assert dispatches and reports, "the fixture produced no snapshot of either"

    for row in dispatches:
        assert "brief" not in row, row
        assert row["brief_sha256"] == body_digest(stored_dispatch["brief"])
        assert row["id"] == stored_dispatch["id"]
    for row in reports:
        assert "body" not in row, row
        assert row["body_sha256"] == body_digest(stored_report["body"])
        assert row["id"] == stored_report["id"]

    # The mutable fields stay, which is the one way this differs from the evidence
    # treatment: a dispatch row moves `running` -> `reported` and the record's own
    # document shows only where it ended up, so the ledger is the only place that
    # transition is readable at all (law 6). Reduced to {id, sha} it would be gone.
    assert {row["state"] for row in dispatches} == {"running", "reported"}, dispatches


def test_the_stripper_names_every_embedded_body_in_one_place() -> None:
    """KYO-274.c1: "through the same stripper KYO-268.c1 introduced ... so the three
    cannot diverge". What makes that true is that ``event_snapshot`` is the only reader
    of either table — a fourth body added to a record document is a line in
    ``EVENT_SNAPSHOT_BODIES`` or it is nothing at all, and a second stripper somewhere
    else fails this check the day it is written."""
    import ast

    from kyosei import events_ledger as events_ledger_module

    readers = {
        node.name
        for tree in _export_family_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Name)
            and inner.id in {"EVENT_SNAPSHOT_BODIES", "EVENT_SNAPSHOT_EVIDENCE_KEYS"}
            for inner in ast.walk(node)
        )
    }
    assert readers == {"event_snapshot"}, sorted(readers)
    assert [collection for collection, _, _ in events_ledger_module.EVENT_SNAPSHOT_BODIES] == [
        "dispatches",
        "reports",
    ]


def test_the_stripper_still_leaves_a_payload_that_is_not_a_record_document_alone() -> None:
    """The KYO-268 passthrough property, re-asserted for the widened stripper: a payload
    with no ``dispatches``/``reports`` list is not reshaped, and the argument is never
    mutated — ``_commit_and_export`` hands the same document to the event and to the
    command's own ``--json`` payload."""
    from kyosei.events_ledger import body_digest, event_snapshot

    assert event_snapshot(None) is None
    assert event_snapshot({"stop_audit": {"outcome": "clean"}}) == {
        "stop_audit": {"outcome": "clean"}
    }

    original = {
        "id": "TST-1",
        "dispatches": [{"id": "TST-1.d1", "state": "running", "brief": "a frozen brief"}],
        "reports": [{"id": "TST-1.r1", "body": {"commits": []}}],
    }
    stripped = event_snapshot(original)
    assert stripped["dispatches"] == [
        {"id": "TST-1.d1", "state": "running", "brief_sha256": body_digest("a frozen brief")}
    ]
    assert stripped["reports"] == [
        {"id": "TST-1.r1", "body_sha256": body_digest({"commits": []})}
    ]
    assert original["dispatches"][0]["brief"] == "a frozen brief", "the argument was mutated"
    assert original["reports"][0]["body"] == {"commits": []}, "the argument was mutated"


def test_the_digest_is_recomputable_from_the_document_a_reader_holds() -> None:
    """Neither ``dispatches`` nor ``reports`` has ever had a sha column, so unlike
    evidence's the digest is computed rather than read — which makes "what exactly was
    hashed" a thing this suite has to pin. A brief is hashed as its own UTF-8 bytes; an
    ingested report is hashed over the canonical serialisation :func:`serialise` writes,
    so the value does not depend on which json dump produced the dict."""
    from kyosei.events_ledger import body_digest
    from kyosei.export import _digest

    assert body_digest(None) is None
    assert body_digest("a frozen brief") == _digest("a frozen brief")
    body = {"b": [1, 2], "a": "é"}
    assert body_digest(body) == _digest('{"a": "é", "b": [1, 2]}')
    assert body_digest(body) == body_digest({"a": "é", "b": [1, 2]})


# ------------------------------------------- KYO-274.c1: the readers, on both formats
#
# Every line written before this change carries the bodies and always will — the ledger
# is append-only and nothing here rewrites it, which the record says in as many words.
# So each reader has to work on both formats, and each test below runs one against a
# ledger holding a line of each.


def _put_the_bodies_back(project: Path, brief: str, report_body: dict) -> int:
    """Rewrite every event shard so each snapshot carries the bodies again, stamped at
    the last schema_version that wrote them that way. This is what a pre-KYO-274 line IS,
    and building one by hand is the only way to have one: this build cannot write one.
    """
    from kyosei.events_ledger import event_line, event_shard_paths_in

    restored = 0
    for path in event_shard_paths_in(project / "kyosei" / "export"):
        lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            event = json.loads(raw)
            touched = False
            for side in ("before", "after"):
                document = event.get(side)
                if not isinstance(document, dict):
                    continue
                for row in document.get("dispatches") or []:
                    row.pop("brief_sha256", None)
                    row["brief"] = brief
                    touched = True
                for row in document.get("reports") or []:
                    row.pop("body_sha256", None)
                    row["body"] = report_body
                    touched = True
            if touched:
                event["schema_version"] = 20  # the last version that wrote a body
                restored += 1
            lines.append(event_line(event))
        path.write_text("".join(lines), encoding="utf-8")
    return restored


def test_rebuild_still_loads_a_ledger_whose_old_lines_carry_the_bodies(
    kyo, git_project: Path, write_file
) -> None:
    """The reader that matters most, because it is the one every store runs after a bump.
    ``rebuild`` loads a snapshot as opaque JSON, which is exactly why an older line
    survives a format change — but "exactly why" is an argument, and this is the check.
    The ledger is left holding both formats at once, which is the state every upgraded
    store is in rather than an exotic one."""
    marker = "a description distinctive enough to find in the brief"
    fixture = _dispatch_with_a_big_brief_and_report(
        kyo, git_project, write_file, marker, "some report prose"
    )
    stored = _stored(git_project, fixture["record"])
    restored = _put_the_bodies_back(
        git_project, stored["dispatches"][0]["brief"], stored["reports"][0]["body"]
    )
    assert restored, "the fixture produced no snapshot to age"
    assert kyo("edit", fixture["record"], "--title", "After the bump").code == 0
    stamps = {event.get("schema_version") for event in event_lines(git_project)}
    assert stamps == {20, SCHEMA_VERSION}, stamps

    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("doctor").code == 0
    # The old lines came back verbatim, bodies and all: rebuild reads them, it does not
    # reformat them (law 4 — history is not rewritten by reading it).
    aged = [e for e in event_lines(git_project) if e.get("schema_version") == 20]
    assert aged and all(
        "brief" in row
        for event in aged
        for side in (event.get("before"), event.get("after"))
        if isinstance(side, dict)
        for row in side.get("dispatches") or []
    )
    assert kyo("show", fixture["record"], "--json").code == 0


def test_the_embedded_copy_scrub_still_clears_a_brief_from_an_old_line(
    kyo, git_project: Path, write_file
) -> None:
    """``kyo redact``'s second pass exists because a frozen brief renders claim
    statements into itself and the brief was then copied into every snapshot. KYO-274
    stops the copying; it does not un-copy what is already in the ledger, so the pass has
    to keep finding the old ones. It matches TEXT in the file, which is what makes it
    indifferent to which format a line is — the property pinned here, on a ledger holding
    a line of each.
    """
    secret = "the claim statement that must not survive a redaction"
    record = new_record(kyo, kind="task", title="Cited", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("cl.txt", secret), "--json",
    ).json["claim_id"]  # fmt: skip
    assert kyo("dispatch", record, "--json").code == 0
    brief = _stored(git_project, record)["dispatches"][0]["brief"]
    assert secret in brief, "precondition: the brief quotes the claim"
    # Precondition, and KYO-274.c1's ledger half in one line: this build's own snapshots
    # have nothing of the brief in them for the pass to find.
    assert brief not in events_text(git_project)

    assert _put_the_bodies_back(git_project, brief, {}), "no snapshot to age"
    assert secret in events_text(git_project), "the aged line should hold the brief"

    result = kyo("redact", claim_id, "--why", "carried an id", "--json")
    assert result.code == 0, result
    assert result.json["global_scrub_ran"] is True
    assert secret not in events_text(git_project), "the old line kept the redacted text"
    assert secret not in export_of(git_project, record).read_text(encoding="utf-8")


def test_the_id_based_ledger_scrub_is_untouched_by_either_format(
    kyo, git_project: Path, write_file
) -> None:
    """The other half of `kyo redact`'s ledger work — the pass that rewrites an evidence
    artifact's own entry by id — never looks at ``dispatches``/``reports``, so it has to
    behave identically whichever format a line is. Asserted rather than assumed, because
    "it does not look there" is the kind of claim that quietly stops being true."""
    secret = "a console excerpt that names somebody"
    record = new_record(kyo, kind="bug", title="Attached", criteria=["a"])
    evidence_id = kyo(
        "attach", record, "--file", write_file("e.txt", secret), "--json"
    ).json["evidence_id"]
    assert kyo("dispatch", record, "--json").code == 0
    brief = _stored(git_project, record)["dispatches"][0]["brief"]
    assert _put_the_bodies_back(git_project, brief, {}), "no snapshot to age"

    assert kyo("redact", evidence_id, "--why", "named a person").code == 0
    assert secret not in events_text(git_project)
    seen = 0
    for event in event_lines(git_project):
        for side in (event.get("before"), event.get("after")):
            if not isinstance(side, dict):
                continue
            for artifact in side.get("evidence_artifacts") or []:
                if artifact.get("id") == evidence_id:
                    seen += 1
                    assert artifact.get("text") != secret
    assert seen, "no snapshot named the artifact, so nothing was exercised"


def test_kyo_show_reads_the_bodies_from_the_document_not_from_a_snapshot(
    kyo, git_project: Path, write_file
) -> None:
    """The brief and the report stay readable, in full, at the surfaces a person uses —
    which they do because neither surface ever read a snapshot: `kyo show` builds the
    record document from the database (``cmd_views.cmd_show`` -> ``record_document``) and
    `kyo show <dispatch-id>` reads the ``dispatches`` row. Pinned anyway, because
    "stripped from the ledger" must never become "gone"."""
    fixture = _dispatch_with_a_big_brief_and_report(
        kyo, git_project, write_file, "the covered description", "the reported prose"
    )

    shown = kyo("show", fixture["record"], "--json")
    assert shown.code == 0, shown
    assert "the covered description" in shown.json["dispatches"][0]["brief"]
    assert "the reported prose" in json.dumps(shown.json["reports"][0]["body"])
    dispatch_shown = kyo("show", fixture["dispatch_id"], "--json")
    assert dispatch_shown.code == 0, dispatch_shown
    assert "the covered description" in json.dumps(dispatch_shown.json)
