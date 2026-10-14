"""KYO-521: the `carry` kind is renamed `reminder`; a decision can be filed directly with
evidence, or spawned from a reminder; the skill says what is a reminder and what is not.

A consumer project's open carry records were mostly not carry -- settled rules, defects, a
few decisions actually waiting on the operator and a few dated reminders -- and its
`decision` kind held no records, because a decision could only be spawned from a proposal
or an investigation, so conversational decisions went to carry. `reminder` names the kind
more clearly for human users, and a decision may be filed directly with evidence.

Each test below fails if its mechanism is removed: the packaged registry naming the kind
`carry` again, `decision` losing `intake` or the reminder edge, `kyo new` filing a decision
without evidence (or no longer storing the evidence it was given), and the upgrade notes'
steps no longer taking a `carry` store to a clean `kind_registry` check.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from kyosei.contracts import CONTRACT_FILING_REQUIRES_EVIDENCE
from kyosei.errors import EXIT_CONTRACT
from kyosei.kinds import load_kind_registry

from .conftest import kinds_yml, new_record

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = REPO_ROOT / "src" / "kyosei" / "defaults"
#: KYO-593: the `/kyosei` skill's two files moved out of ``defaults/`` into the plugin.
SKILL_DIR = REPO_ROOT / "src/kyosei/harness/claude-code/plugin/skills/kyosei"
PACKAGED_KINDS = DEFAULTS / "kinds.yml"

#: The operator's words in the shape the criterion names: who, where, then the words.
WORDS = (
    "The operator, orchestrator session (Claude Code, this repository):\n"
    '"yes, use SQLite for the store; no server process"\n'
)


def _packaged() -> dict:
    return yaml.safe_load(PACKAGED_KINDS.read_text(encoding="utf-8"))["kinds"]


# ------------------------------------------------------------------ c4: the rename


def test_the_packaged_registry_names_the_kind_reminder_and_never_carry() -> None:
    """c4: `reminder` with the policy and edges `carry` had (plus c7's `decision`), and
    no packaged kind defines or names `carry`. Fails if the default is named carry again."""
    kinds = _packaged()
    assert "carry" not in kinds, "the packaged default kind is named `carry` again"
    reminder = kinds["reminder"]
    assert reminder["archetype"] == "work"
    assert reminder["policy"] == {
        "requires_criteria": False,
        "dispatchable": False,
        "unattended_ok": False,
        "write_scope": "none",
        "swept": False,
        "counted": False,
        "carried": True,
    }
    assert reminder["accepts_from"] == ["intake"]
    assert set(reminder["produces"]) == {"task", "bug", "investigation", "proposal", "decision"}
    for name, entry in kinds.items():
        edges = [*(entry.get("accepts_from") or []), *(entry.get("produces") or [])]
        assert "carry" not in edges, f"{name} still names `carry` as an edge"


def test_no_packaged_default_teaches_the_old_name() -> None:
    """c4/c5: no packaged default files a record of, spawns to, or names the kind `carry`.
    The English verb is everywhere in these files, so the check is on the kind's uses:
    `kyo new carry`, `--kind carry`, `<carry-id>`, a `carry:` key, and the backticked
    kind name -- the last allowed only in the reference's account of the rename."""
    uses = re.compile(r"kyo new carry|--kind carry|<carry-id>|^\s*carry:", re.M)
    for path in sorted([*DEFAULTS.rglob("*"), *SKILL_DIR.rglob("*")]):
        if not path.is_file() or path.suffix not in {".md", ".yml", ".yaml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not uses.search(text), f"{path.relative_to(REPO_ROOT)} teaches the old name"
        if path.name != "REFERENCE.md":
            assert "`carry`" not in text, f"{path.relative_to(REPO_ROOT)} names `carry`"


def test_kyo_init_scaffolds_reminder(kyo, project: Path) -> None:
    """c4: `kyo init` writes the packaged registry, so a fresh store has `reminder`, files
    it, lists it in the brief's `reminders` section, and has no `carry`."""
    registry = load_kind_registry(project / "kyosei")
    assert "reminder" in registry.names
    assert "carry" not in registry.names
    assert [n for n in registry.names if registry[n].carried] == ["reminder"]
    refused = kyo("new", "carry", "--title", "x")
    assert refused.code != 0 and "'carry' is not a known kind" in refused.err, refused

    filed = kyo("new", "reminder", "--title", "ask about the rollout date", "--json")
    assert filed.code == 0, filed
    record_id = filed.json["record_id"]
    text = kyo("brief").out
    assert re.search(r"^reminders \(1; ", text, re.M), text
    assert record_id in text
    assert not re.search(r"^carried", text, re.M), text


# --------------------------------------------------- c7: the registry edges, traversed


def test_decision_accepts_intake_and_reminder_with_both_edges_written() -> None:
    """c7: `decision` accepts direct filing and `reminder`; `reminder` produces `decision`."""
    kinds = _packaged()
    assert {"intake", "reminder"} <= set(kinds["decision"]["accepts_from"])
    assert {"proposal", "investigation"} <= set(kinds["decision"]["accepts_from"])
    assert "decision" in kinds["reminder"]["produces"]
    assert kinds["decision"]["policy"]["requires_evidence_links"] is True


def test_a_decision_is_filed_directly_and_spawned_from_a_reminder(kyo, write_file) -> None:
    """c7: both new routes, end to end on a fresh store."""
    words = write_file("words.md", WORDS)
    direct = kyo(
        "new", "decision", "--title", "Use SQLite", "--evidence-file", words, "--json"
    )
    assert direct.code == 0, direct
    assert kyo("show", direct.json["record_id"], "--json").json["kind"] == "decision"

    reminder = kyo("new", "reminder", "--title", "Ask whether to keep the cache", "--json")
    assert reminder.code == 0, reminder
    reminder_id = reminder.json["record_id"]
    spawned = kyo(
        "spawn",
        reminder_id,
        "--kind",
        "decision",
        "--title",
        "Keep the cache",
        "--evidence-file",
        words,
        "--json",
    )
    assert spawned.code == 0, spawned
    decision_id = spawned.json["record_id"]
    shown = kyo("show", decision_id, "--json").json
    assert shown["kind"] == "decision"
    assert any(
        link["type"] == "produced-by" and link["other"] == reminder_id
        for link in shown["links"]
    ), shown["links"]


# --------------------------------------------- c8: evidence in the same act, or refused


def test_filing_a_decision_without_evidence_is_refused_naming_the_flag(kyo) -> None:
    """c8: refused under its own contract, exit 2, naming `--evidence-file`; nothing filed."""
    refused = kyo("new", "decision", "--title", "Use SQLite")
    assert refused.code == EXIT_CONTRACT, refused
    assert CONTRACT_FILING_REQUIRES_EVIDENCE in refused.err
    assert "--evidence-file" in refused.err
    assert "requires_evidence_links" in refused.err
    assert kyo("list", "--json").json["records"] == []


def test_the_named_flag_clears_the_refusal_and_stores_the_words_as_attach_does(
    kyo, write_file
) -> None:
    """c8: the remedy the refusal names clears it, and the evidence is the row `kyo
    attach` writes -- verbatim text, its sha256, on the new record -- in the same act."""
    assert kyo("new", "decision", "--title", "Use SQLite").code == EXIT_CONTRACT
    words = write_file("words.md", WORDS)
    filed = kyo("new", "decision", "--title", "Use SQLite", "--evidence-file", words, "--json")
    assert filed.code == 0, filed
    record_id = filed.json["record_id"]
    assert filed.json["evidence_id"] == f"{record_id}.e1"

    (evidence,) = kyo("show", record_id, "--json").json["evidence_artifacts"]
    stored = WORDS.rstrip("\n")
    assert evidence["text"] == stored
    assert evidence["sha256"] == hashlib.sha256(stored.encode("utf-8")).hexdigest()

    # The same row shape `kyo attach` writes, so `kyo redact` and every reader treat it alike.
    other = new_record(kyo, kind="task", title="a task", criteria=["done"])
    assert kyo("attach", other, "--file", words).code == 0
    (attached,) = kyo("show", other, "--json").json["evidence_artifacts"]
    assert set(attached) == set(evidence)
    assert attached["sha256"] == evidence["sha256"]


def test_evidence_from_stdin_and_beside_from(kyo, write_file) -> None:
    """`--evidence-file -` reads stdin; it works beside `--from`; and two flags naming
    stdin at once are refused, since one stream is read once."""
    by_stdin = kyo(
        "new", "decision", "--title", "Use SQLite", "--evidence-file", "-", "--json",
        stdin=WORDS,
    )
    assert by_stdin.code == 0, by_stdin

    record = write_file("decision.md", "---\ntitle: Keep the old cache warm\n---\n\nWhy.\n")
    words = write_file("words.md", WORDS)
    beside = kyo("new", "decision", "--from", record, "--evidence-file", words, "--json")
    assert beside.code == 0, beside
    assert beside.json["evidence_id"] is not None

    both = kyo("new", "decision", "--from", "-", "--evidence-file", "-", stdin=WORDS)
    assert both.code != 0
    assert "both name stdin" in both.err


def test_a_kind_without_the_policy_files_as_before(kyo) -> None:
    """The refusal is the policy's consumer, not a rule about `kyo new`: a task needs no
    evidence, and a reminder needs none."""
    assert kyo("new", "task", "--title", "t").code == 0
    assert kyo("new", "reminder", "--title", "r").code == 0


# ------------------------------------------- c6: the upgrade notes' steps, on a fixture


def _write_kinds(project: Path, kinds: dict) -> None:
    kinds_yml(project).write_text(yaml.safe_dump({"kinds": kinds}, sort_keys=False), "utf-8")


def _read_kinds(project: Path) -> dict:
    return yaml.safe_load(kinds_yml(project).read_text(encoding="utf-8"))["kinds"]


def _pre_kyo521(project: Path) -> None:
    """The registry a project scaffolded between KYO-497 and KYO-521 had: `carry`, its four
    produced kinds accepting it, and `decision` accepting only proposal/investigation."""
    kinds = _read_kinds(project)
    carry = kinds.pop("reminder")
    carry["produces"] = ["task", "bug", "investigation", "proposal"]
    for entry in kinds.values():
        entry["accepts_from"] = [
            "carry" if token == "reminder" else token for token in entry["accepts_from"]
        ]
    kinds["decision"]["accepts_from"] = ["proposal", "investigation"]
    kinds["carry"] = carry
    _write_kinds(project, kinds)


def _kind_registry_failures(kyo) -> list[str]:
    return kyo("doctor", "--json").json["checks"]["kind_registry"]["failures"]


def test_the_install_notes_steps_rename_a_carry_store_to_a_clean_doctor(
    kyo, project: Path, write_file
) -> None:
    """c6: the steps docs/reference/topics/install.md gives, run in its order on a store
    with `carry` records (open, gated, dated by a shift note, done, dropped), ending with
    `kyo doctor` reporting no `kind_registry` problem. Leaving out the re-kind step is
    shown to leave one, which is why the notes say "including the settled ones"."""
    notes = (REPO_ROOT / "docs" / "reference" / "topics" / "install.md").read_text("utf-8")
    for step in (
        "add the `reminder` entry, copied from the packaged",
        "`kyo list --kind carry` lists every carry record, settled ones included",
        "Run `kyo edit <id> --kind reminder` for every one of them",
        "including the settled ones",
        "Remove the `carry` entry and every edge naming it",
        "the `kind_registry` check reports no problem",
    ):
        assert step in " ".join(notes.split()), step

    _pre_kyo521(project)
    assert _kind_registry_failures(kyo) == []
    note = kyo(
        "note", "--shift", "--file", "-", "--json", stdin="the rollout date is open"
    ).json["shift_note_id"]
    ids = [
        kyo("new", "carry", "--title", title, "--json").json["record_id"]
        for title in ("rollout date", "use SQLite?", "settled rule", "dropped item")
    ]
    dated = kyo("new", "carry", "--title", "dated", "--since-note", note, "--json")
    assert dated.code == 0, dated
    ids.append(dated.json["record_id"])
    assert kyo("gate", ids[1], "awaiting:human-review", "--do", "decide").code == 0
    assert kyo("move", ids[2], "done", "--why", "written into the docs").code == 0
    assert kyo("wont-do", ids[3], "--why", "no longer relevant").code == 0

    # Step 1: add `reminder` beside `carry`, its edges, and `intake` on `decision`.
    kinds = _read_kinds(project)
    kinds["reminder"] = _packaged()["reminder"]
    for target in ("task", "bug", "investigation", "proposal", "decision"):
        kinds[target]["accepts_from"].append("reminder")
    kinds["decision"]["accepts_from"].insert(0, "intake")
    _write_kinds(project, kinds)
    assert _kind_registry_failures(kyo) == [], "both kinds present must load cleanly"

    # Without step 2, step 3 strands every carry record -- settled ones included.
    without_rekind = {
        name: {
            **entry,
            "accepts_from": [t for t in entry["accepts_from"] if t != "carry"],
        }
        for name, entry in kinds.items()
        if name != "carry"
    }
    _write_kinds(project, without_rekind)
    stranded = " ".join(_kind_registry_failures(kyo))
    assert all(record_id in stranded for record_id in ids), stranded
    _write_kinds(project, kinds)

    # Step 2: every carry record, settled ones included, re-kinded.
    listed = [r["id"] for r in kyo("list", "--kind", "carry", "--json").json["records"]]
    assert sorted(listed) == sorted(ids)
    for record_id in listed:
        result = kyo("edit", record_id, "--kind", "reminder")
        assert result.code == 0, result

    # Step 3: remove `carry` and its edges. Step 4: doctor is clean.
    _write_kinds(project, without_rekind)
    assert "carry" not in json.dumps(_read_kinds(project))
    assert _kind_registry_failures(kyo) == []

    # What the rename kept: the gate, the since-note, the open ones in the brief.
    shown = kyo("show", ids[4], "--json").json
    assert shown["kind"] == "reminder" and shown["since_note"] == note
    brief = kyo("brief").out
    assert re.search(r"^reminders \(3; ", brief, re.M), brief
    # And the decision edges the step added work.
    words = write_file("spawn-words.md", WORDS)
    spawned = kyo(
        "spawn", ids[1], "--kind", "decision", "--title", "Use SQLite", "--evidence-file", words
    )
    assert spawned.code == 0, spawned


# ------------------------------------------- c1, c2, c9: what the prose has to say


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = re.search(r"^## ", text[start + len(heading) :], re.M)
    end = start + len(heading) + following.start() if following else len(text)
    return " ".join(text[start:end].split())


def test_the_skill_says_what_is_a_reminder_and_what_is_not() -> None:
    """c1, in section 6 of the packaged skill."""
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    notes = _section(skill, "## 6. Notes")
    for phrase in (
        "`kyo new reminder` is for a decision waiting on the operator",
        "`awaiting:human-review`",
        "a reminder tied to a date or event",
        "a defect is a bug",
        # KYO-521: a settled rule is a decision filed with the operator's words.
        "a settled rule is a decision filed with",
        "buildable work is a task",
        'Stories: `${CLAUDE_SKILL_DIR}/REFERENCE.md`, "6. Notes"',
    ):
        assert phrase in notes, phrase


def test_the_reference_carries_the_consumer_incident_in_the_cited_section() -> None:
    """c2: the section the skill's section 6 cites holds the incident: what a catch-all
    reminder kind fills up with, and why the skill's old wording caused it. KYO-620 made
    the account impersonal and dropped its dated counts and quotations."""
    reference = (SKILL_DIR / "REFERENCE.md").read_text(encoding="utf-8")
    notes = " ".join(_section(reference, "## 6. Notes").split())
    for phrase in (
        "closing-note habit",
        "mostly settled rules, defects and buildable work",
        "few decisions actually waiting on the operator",
        "gives an agent no reason to file anything as its real kind",
    ):
        assert phrase in notes, phrase


def test_every_account_of_decisions_names_both_routes() -> None:
    """c9: the charter, the registry topic page, the guide's kinds page and the skill say a
    decision made in conversation is filed directly with the operator's words as evidence,
    and that the proposal route remains for one needing design work first."""
    pages = {
        "docs/design/charter.md": ("--evidence-file", "operator's words", "proposal"),
        "docs/reference/topics/registries.md": ("--evidence-file", "operator", "proposal"),
        "docs/guide/06-kinds-scopes-documents.md": ("--evidence-file", "your words", "proposal"),
        "src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md": (
            "--evidence-file",
            "operator's words",
            "proposal",
        ),
    }
    for page, phrases in pages.items():
        text = " ".join((REPO_ROOT / page).read_text(encoding="utf-8").split())
        assert "kyo new decision" in text, page
        assert "design work first" in text, page
        for phrase in phrases:
            assert phrase in text, (page, phrase)
