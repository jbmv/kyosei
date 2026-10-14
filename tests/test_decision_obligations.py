"""KYO-554: decision obligations -- a record whose kind sets
``policy.raises_docs_obligations`` (the default ``decision``) raises a STANDING docs
obligation on each ssot section its title or description names by id, and on each of its
doc-refs; the obligation has no dispatch, is listed by `kyo brief`, `kyo show` and `kyo
doctor`, and settles by `kyo docs settle` or by voiding the record.

Each test fails if its mechanism is removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kyosei import doctor
from kyosei.kinds import KNOWLEDGE_POLICY_OPTIONAL_KEYS, load_kind_registry

from .conftest import kinds_yml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_KINDS = REPO_ROOT / "src" / "kyosei" / "defaults" / "kinds.yml"

_SET = """\
documents:
  specs:
    summary: The specs.
    globs: [docs/specs/**/*.md, docs/foundation.md]
    section_status:
      statuses: [current, superseded]
    ssot:
      id_heading: '{id}. {title}'
      id_pattern: '[A-Z]{1,4}-\\d+'
      prefixes:
        P: docs/foundation.md
        API: docs/specs/api.md
  notes:
    summary: Plain notes.
    globs: [docs/notes/*.md]
"""

_API = """\
# API spec

## API-12. Unknown fields are rejected

status: current

Prose.

## API-13. Retries are bounded

status: current

More prose.
"""

_FOUNDATION = """\
# Foundation

## P-1. The store is local

status: current

Prose.
"""

_NOTES = """\
# Notes on the pipeline

## Batching

Text.
"""

WORDS = "The maintainer:\n\"yes\"\n"


def _write(project: Path, rel: str, text: str) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def specs(project: Path) -> Path:
    _write(project, "kyosei/documents.yml", _SET)
    _write(project, "docs/specs/api.md", _API)
    _write(project, "docs/foundation.md", _FOUNDATION)
    _write(project, "docs/notes/pipeline.md", _NOTES)
    return project


def _decision(kyo, write_file, title: str, description: str | None = None) -> dict:
    args = ["new", "decision", "--title", title, "--evidence-file",
            write_file("words.md", WORDS), "--json"]
    if description is not None:
        args += ["--description-file", write_file("desc.md", description)]
    result = kyo(*args)
    assert result.code == 0, result
    return result.json


def _standing(kyo, record_id: str) -> list[dict]:
    doc = kyo("show", record_id, "--json").json
    return [o for o in doc["docs_obligations"] if o["dispatch_id"] is None]


# ------------------------------------------------------------------ c1: the policy key


def test_the_policy_key_is_optional_validated_and_set_on_the_packaged_decision(
    project: Path,
) -> None:
    """c1: an optional knowledge-policy bool, refused when not a bool, and on in the
    packaged default for `decision`."""
    assert "raises_docs_obligations" in KNOWLEDGE_POLICY_OPTIONAL_KEYS
    packaged = yaml.safe_load(PACKAGED_KINDS.read_text(encoding="utf-8"))["kinds"]
    assert packaged["decision"]["policy"]["raises_docs_obligations"] is True
    registry = load_kind_registry(project / "kyosei")
    assert registry.require("decision").raises_docs_obligations is True
    assert registry.require("evidence").raises_docs_obligations is False

    raw = yaml.safe_load(kinds_yml(project).read_text(encoding="utf-8"))
    raw["kinds"]["decision"]["policy"]["raises_docs_obligations"] = "yes"
    kinds_yml(project).write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception, match="raises_docs_obligations"):
        load_kind_registry(project / "kyosei")


# ------------------------------------------------- c2, c4: ids in the title or description


def test_a_decision_naming_ids_raises_one_standing_obligation_per_section(
    kyo, specs: Path, write_file
) -> None:
    """c2: one obligation per section an id resolves to, cause `decision`, no dispatch;
    an id named twice raises once, and a token that is not a declared id (UTF-8,
    KYO-12) raises nothing."""
    filed = _decision(
        kyo, write_file, "Unknown fields: API-12 and P-1 change",
        "API-12 again, plus UTF-8 and KYO-12 which are not ids.\n",
    )
    record = filed["record_id"]
    standing = _standing(kyo, record)
    assert [(o["doc_path"], o["section"], o["cause"]) for o in standing] == [
        ("docs/specs/api.md", "API-12. Unknown fields are rejected", "decision"),
        ("docs/foundation.md", "P-1. The store is local", "decision"),
    ]
    assert all(o["verdict"] is None for o in standing)
    assert [o["id"] for o in filed["standing_obligations"]["raised"]] == [
        o["id"] for o in standing
    ]


def test_a_kind_without_the_policy_raises_nothing(kyo, specs: Path) -> None:
    """c1/c2: the policy, not the text, decides. A bug naming API-12 raises nothing."""
    result = kyo("new", "bug", "--title", "API-12 is wrong", "--json")
    assert result.code == 0, result
    assert result.json["standing_obligations"] is None
    assert kyo("show", result.json["record_id"], "--json").json["docs_obligations"] == []


def test_turning_the_policy_off_stops_the_raise(kyo, specs: Path, write_file) -> None:
    """c1: the key is what is read -- the packaged decision with it removed raises none."""
    raw = yaml.safe_load(kinds_yml(specs).read_text(encoding="utf-8"))
    raw["kinds"]["decision"]["policy"].pop("raises_docs_obligations", None)
    kinds_yml(specs).write_text(yaml.safe_dump(raw), encoding="utf-8")
    filed = _decision(kyo, write_file, "API-12 changes")
    assert _standing(kyo, filed["record_id"]) == []


def test_an_unresolved_id_raises_nothing_and_the_output_names_it(
    kyo, specs: Path, write_file
) -> None:
    """c4: API-99 has a declared prefix and no owner -- nothing raised, the write says so."""
    result = kyo(
        "new", "decision", "--title", "API-99 and API-13",
        "--evidence-file", write_file("w.md", WORDS),
    )
    assert result.code == 0, result
    assert "API-99" in result.out and "no heading in any ssot document owns it" in result.out
    record = result.out.split()[1]
    assert [o["section"] for o in _standing(kyo, record)] == ["API-13. Retries are bounded"]


# ------------------------------------------------------------------ c3: doc-refs


def test_doc_refs_raise_on_the_named_section_or_a_bare_paths_first_heading(
    kyo, specs: Path, write_file
) -> None:
    """c3: a `path#section` doc-ref raises on that section; a bare path on the document's
    first heading, as `_doc_ref_obligations` files a dispatch's."""
    record = _decision(kyo, write_file, "Batching changes")["record_id"]
    assert _standing(kyo, record) == []
    refs = write_file("refs.txt", "docs/notes/pipeline.md#Batching\ndocs/foundation.md\n")
    edited = kyo("edit", record, "--doc-refs-file", refs, "--json")
    assert edited.code == 0, edited
    standing = _standing(kyo, record)
    assert [(o["doc_path"], o["section"], o["cause"]) for o in standing] == [
        ("docs/notes/pipeline.md", "Batching", "doc-refs"),
        ("docs/foundation.md", "Foundation", "doc-refs"),
    ]


# ------------------------------------------------------------------ c5: edits


def test_an_edit_raises_only_sections_the_record_did_not_already_hold(
    kyo, specs: Path, write_file
) -> None:
    """c5: re-editing raises only the newly named section; a settled one is not re-raised."""
    record = _decision(kyo, write_file, "API-12 changes")["record_id"]
    (first,) = _standing(kyo, record)
    settled = kyo("docs", "settle", first["id"], "--verdict", "updated", "--method", "read it")
    assert settled.code == 0, settled

    edited = kyo("edit", record, "--title", "API-12 and API-13 change", "--json")
    assert edited.code == 0, edited
    assert [o["section"] for o in edited.json["standing_obligations"]["raised"]] == [
        "API-13. Retries are bounded"
    ]
    again = kyo("edit", record, "--description-file", write_file("d.md", "API-12, API-13\n"),
                "--json")
    assert again.json["standing_obligations"]["raised"] == []
    assert len(_standing(kyo, record)) == 2


# ------------------------------------------------------------------ c2: spawn and promote


def test_spawn_and_promote_raise_too(kyo, specs: Path, write_file) -> None:
    """c2: the two other filing paths raise as `kyo new` does."""
    reminder = kyo("new", "reminder", "--title", "Ask about retries", "--json").json["record_id"]
    spawned = kyo(
        "spawn", reminder, "--kind", "decision", "--title", "API-13 changes",
        "--evidence-file", write_file("w.md", WORDS), "--json",
    )
    assert spawned.code == 0, spawned
    assert [o["section"] for o in _standing(kyo, spawned.json["record_id"])] == [
        "API-13. Retries are bounded"
    ]

    raw = yaml.safe_load(kinds_yml(specs).read_text(encoding="utf-8"))
    raw["kinds"]["decision"]["accepts_from"].append("idea")
    kinds_yml(specs).write_text(yaml.safe_dump(raw), encoding="utf-8")
    _write(specs, "ideas/store.md", "# P-1 stays\n\nThe store stays local.\n")
    promoted = kyo("promote", str(specs / "ideas" / "store.md"), "--kind", "decision", "--json")
    assert promoted.code == 0, promoted
    assert [o["section"] for o in _standing(kyo, promoted.json["record_id"])] == [
        "P-1. The store is local"
    ]


# ------------------------------------------------------------------ c6: the schema


def test_a_standing_obligation_round_trips_export_and_rebuild(
    kyo, specs: Path, write_file
) -> None:
    """c6: dispatch_id is null in the export and survives a rebuild from it."""
    record = _decision(kyo, write_file, "API-12 changes")["record_id"]
    export = json.loads(
        (specs / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    assert [o["dispatch_id"] for o in export["docs_obligations"]] == [None]
    (specs / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["docs_obligations"] == export["docs_obligations"]


# ------------------------------------------------------- c7, c8, c9: brief, show, doctor


def test_brief_show_and_doctor_list_the_open_ones(kyo, specs: Path, write_file) -> None:
    """c7: the brief's own heading, with record, document, section and age (also under
    --compact); c8: `kyo show` lists them with their state; c9: doctor's count is a
    notice and the run still exits 0."""
    record = _decision(kyo, write_file, "API-12 changes")["record_id"]
    (obligation,) = _standing(kyo, record)

    brief = kyo("brief")
    assert brief.code == 0, brief
    heading = next(line for line in brief.out.splitlines()
                   if line.startswith("standing docs obligations (1"))
    assert heading
    row = next(line for line in brief.out.splitlines() if line.strip().startswith(obligation["id"]))
    assert record in row and "docs/specs/api.md" in row and "[API-12." in row and "0d" in row
    compact = kyo("brief", "--compact")
    assert obligation["id"] in compact.out
    data = kyo("brief", "--json").json["standing_obligations"]
    assert [(o["id"], o["record_id"], o["age_days"]) for o in data] == [
        (obligation["id"], record, 0)
    ]

    shown = kyo("show", record)
    assert "standing docs obligations (1 open of 1):" in shown.out
    assert f"{obligation['id']}  [open]  docs/specs/api.md" in shown.out

    assert "open_standing_obligations" in doctor.NOTICE_CHECKS
    ran = kyo("doctor", "--json")
    assert ran.code == 0, ran
    assert "1 standing docs obligation(s) open on 1 record(s)" in ran.out


# ------------------------------------------------------------------ c10, c11: settling


def test_docs_settle_settles_one_and_it_leaves_the_brief(kyo, specs: Path, write_file) -> None:
    """c10."""
    record = _decision(kyo, write_file, "API-12 changes")["record_id"]
    (obligation,) = _standing(kyo, record)
    result = kyo("docs", "settle", obligation["id"], "--verdict", "not-affected",
                 "--method", "read the section")
    assert result.code == 0, result
    (after,) = _standing(kyo, record)
    assert (after["verdict"], after["method"]) == ("not-affected", "read the section")
    assert kyo("brief", "--json").json["standing_obligations"] == []
    assert "[not-affected]" in kyo("show", record).out
    assert "standing docs obligation" not in kyo("doctor").out


def test_void_settles_the_open_ones_not_affected_with_its_reason(
    kyo, specs: Path, write_file
) -> None:
    """c11: the void's reason and author are carried, and they leave the brief."""
    record = _decision(kyo, write_file, "API-12 and API-13 change")["record_id"]
    first, _second = _standing(kyo, record)
    assert kyo("docs", "settle", first["id"], "--verdict", "updated", "--method", "m").code == 0
    voided = kyo("void", record, "--why", "filed against the wrong spec", "--json")
    assert voided.code == 0, voided
    standing = _standing(kyo, record)
    assert standing[0]["verdict"] == "updated", "an earlier verdict is never overwritten"
    assert standing[1]["verdict"] == "not-affected"
    assert standing[1]["settled_why"] == "filed against the wrong spec"
    assert standing[1]["author"] == standing[0]["author"]
    assert voided.json["standing_settled"] == [standing[1]["id"]]
    assert kyo("brief", "--json").json["standing_obligations"] == []


# ------------------------------------------------------------------ c12: upgrade


def test_a_record_filed_before_the_policy_raises_nothing_until_it_is_written(
    kyo, specs: Path, write_file
) -> None:
    """c12: a decision filed while its kind did not raise holds nothing after the kind
    starts raising -- and a rebuild raises nothing either -- until its next edit."""
    raw = yaml.safe_load(kinds_yml(specs).read_text(encoding="utf-8"))
    raw["kinds"]["decision"]["policy"].pop("raises_docs_obligations", None)
    kinds_yml(specs).write_text(yaml.safe_dump(raw), encoding="utf-8")
    record = _decision(kyo, write_file, "API-12 changes")["record_id"]
    raw["kinds"]["decision"]["policy"]["raises_docs_obligations"] = True
    kinds_yml(specs).write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert kyo("rebuild", "--force").code == 0
    assert kyo("doctor").code == 0
    assert _standing(kyo, record) == []
    assert kyo("brief", "--json").json["standing_obligations"] == []

    assert kyo("edit", record, "--title", "API-12 changes, restated").code == 0
    assert len(_standing(kyo, record)) == 1
