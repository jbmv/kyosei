"""KYO-149.c3 — what ``kyo report-schema`` prints is what ``kyo report`` enforces.

There is one copy of ``report.schema.json`` in this repository, and
``tests/test_kyo149_consumer_paths.py`` keeps it shipped and reachable. But "one
copy of the file" is not the whole of law 3 here, because the file is not what
runs. :mod:`kyosei.report_schema` deliberately does not execute JSON Schema — it
hand-implements the subset the document uses, to stay stdlib-first (KYO-7's
recorded decision) — so the *shape a report is actually judged against* is written
down twice: once as a document, once as Python constants.

Before KYO-149 that duplication was nearly harmless, because the document was
unreachable: no worker could read it, so no worker could be misled by it. Making it
reachable makes it authoritative in the reader's mind, and an authoritative document
that quietly disagrees with the validator is a worse failure than the broken path it
replaced — a confident lie in place of a loud absence. A worker would write to the
printed schema, ``kyo report`` would refuse the result, and both would be citing
"the schema".

So this file derives the constants from the shipped document and compares. It reads
through :func:`kyosei.report_schema.schema_document`, which is the same call the
command prints from — not a second ``open()`` of a path — so "the document this test
checked" and "the document a worker was handed" cannot come apart either.

What this does NOT claim: that the validator implements JSON Schema. It does not,
and is not meant to. This pins the *vocabulary* — required keys, permitted keys,
every enum, the if/then, the sha pattern — which is what the two actually share and
what a drift would silently change.
"""

from __future__ import annotations

import pytest

from kyosei import report_schema as rs

SCHEMA = rs.schema_document()

#: ``(python required tuple, python allowed set, the schema object holding them)``
#: for every object shape the validator mirrors. Adding a ``$defs`` entry without a
#: row here is caught by :func:`test_every_schema_def_is_mirrored`, so this table
#: cannot fall behind the document by omission — the failure mode a hand-written
#: table normally has.
SHAPES = {
    "report": (rs.TOP_LEVEL_REQUIRED, rs.TOP_LEVEL_ALLOWED, SCHEMA),
    "criterionVerdict": (
        rs.CRITERION_VERDICT_REQUIRED,
        rs.CRITERION_VERDICT_ALLOWED,
        SCHEMA["$defs"]["criterionVerdict"],
    ),
    "premiseFalse": (
        rs.PREMISE_FALSE_REQUIRED,
        rs.PREMISE_FALSE_ALLOWED,
        SCHEMA["$defs"]["premiseFalse"],
    ),
    "disagreement": (
        rs.DISAGREEMENT_REQUIRED,
        rs.DISAGREEMENT_ALLOWED,
        SCHEMA["$defs"]["disagreement"],
    ),
    "correctedNumber": (
        rs.CORRECTED_NUMBER_REQUIRED,
        rs.CORRECTED_NUMBER_ALLOWED,
        SCHEMA["$defs"]["correctedNumber"],
    ),
    "commitRef": (rs.COMMIT_REQUIRED, rs.COMMIT_ALLOWED, SCHEMA["$defs"]["commitRef"]),
    "docsVerdict": (
        rs.DOCS_VERDICT_REQUIRED,
        rs.DOCS_VERDICT_ALLOWED,
        SCHEMA["$defs"]["docsVerdict"],
    ),
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_required_and_permitted_keys_match_the_document(name: str) -> None:
    required, allowed, node = SHAPES[name]
    assert set(required) == set(node["required"]), f"{name}: required keys differ"
    assert allowed == set(node["properties"]), f"{name}: permitted keys differ"
    assert node["additionalProperties"] is False, (
        f"{name}: the document stopped forbidding unknown keys, but the validator "
        f"still rejects them — the two now disagree about what a valid report is"
    )


def test_the_verdict_enum_matches() -> None:
    enum = SCHEMA["$defs"]["criterionVerdict"]["properties"]["verdict"]["enum"]
    assert set(enum) == set(rs.CRITERION_STATES)


def test_the_method_enum_matches() -> None:
    enum = SCHEMA["$defs"]["criterionVerdict"]["properties"]["method"]["enum"]
    assert set(enum) == set(rs.VERIFICATION_METHODS)


def test_the_docs_verdict_enum_matches() -> None:
    """KYO-199.c4. The verdict a worker may propose and the verdict `kyo docs settle`
    records are one enum (`contracts.OBLIGATION_VERDICTS`); if the printed schema and the
    validator came apart here, a worker would write a verdict the settle path refuses."""
    enum = SCHEMA["$defs"]["docsVerdict"]["properties"]["verdict"]["enum"]
    assert set(enum) == set(rs.DOCS_VERDICT_VERDICTS)


def test_the_conditional_reason_rule_matches() -> None:
    """The one ``if/then`` in the document: these verdicts require a ``reason``."""
    node = SCHEMA["$defs"]["criterionVerdict"]
    conditional = set(node["if"]["properties"]["verdict"]["enum"])
    assert conditional == set(rs.CRITERION_VERDICT_REASON_REQUIRED)
    # And the `then` demands exactly the base required keys plus `reason` — a
    # `then` that forgot one would leave the validator stricter than the document.
    assert set(node["then"]["required"]) == set(rs.CRITERION_VERDICT_REQUIRED) | {"reason"}


@pytest.mark.parametrize(
    "node",
    [
        SCHEMA["properties"]["worked_from_sha"],
        SCHEMA["$defs"]["commitRef"]["properties"]["sha"],
    ],
    ids=["worked_from_sha", "commits[].sha"],
)
def test_the_sha_pattern_matches(node: dict) -> None:
    assert node["pattern"] == rs.SHA_RE.pattern


def test_the_nullable_optional_fields_match_the_document() -> None:
    """KYO-224.c1/c2. ``rs.NULLABLE_OPTIONAL`` is the validator's copy of every
    ``["<type>", "null"]`` union in the document, and the two must not come apart.

    Both directions, deliberately. A field the document calls nullable that the
    validator still refuses is the original defect rebuilt one key over — a worker
    reads the printed schema, writes the null it permits, and is refused by something
    citing the same schema. A field the validator lets through that the document does
    not is the opposite lie, and just as invisible without this.
    """
    declared: set[tuple[str, str]] = set()
    for name, (_required, _allowed, node) in SHAPES.items():
        for key, prop in node["properties"].items():
            declared_type = prop.get("type")
            if isinstance(declared_type, list) and "null" in declared_type:
                declared.add((name, key))
    assert declared == set(rs.NULLABLE_OPTIONAL)
    # A nullable field is never a required one: "unset" and "required" are the two
    # things this must not be able to say at once.
    for name, key in declared:
        required, _allowed, _node = SHAPES[name]
        assert key not in required, f"{name}.{key} is required AND nullable"


def test_every_schema_def_is_mirrored() -> None:
    """A new ``$defs`` entry must arrive with a row in :data:`SHAPES`.

    Without this, the table above would be exactly the kind of hand-kept list that
    passes forever by describing less and less of the document.
    """
    assert set(SCHEMA["$defs"]) | {"report"} == set(SHAPES)


def test_the_command_prints_the_document_this_test_read() -> None:
    """Law 3, stated as an assertion rather than as an intention."""
    assert rs.schema_document() == SCHEMA
    assert rs.schema_text().lstrip().startswith("{")


# ---------------------------------------------------- the comparison has teeth


def test_a_drifted_document_would_fail_this_check() -> None:
    """Proof the assertions above are load-bearing and not tautological.

    Add a property to the document that the validator does not permit, and the
    key-set comparison must notice. If this test ever passes trivially, the ones
    above are decoration.
    """
    drifted = {
        **SCHEMA,
        "properties": {**SCHEMA["properties"], "worker_model": {"type": "string"}},
    }
    assert set(drifted["properties"]) != rs.TOP_LEVEL_ALLOWED
    # And the validator would go on rejecting a report that used it, while the
    # printed schema advertised it — the exact two-answers failure.
    assert "worker_model" in "".join(
        rs.validate_report(
            {
                "dispatch_id": "KYO-1.d1",
                "record_id": "KYO-1",
                "worked_from_sha": "0123abc",
                "criteria_verdicts": [],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [],
                "worker_model": "claude-opus-5",
            }
        )
    )
