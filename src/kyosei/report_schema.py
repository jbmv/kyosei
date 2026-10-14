"""A stdlib-only validator for ``report.schema.json``, and the reader that serves it.

KYO-7.c3: ``kyo report <dispatch-id> --file .kyosei-report.json`` must validate a
worker's structured report against the JSON Schema shape defined in
``report.schema.json`` — required keys, enum values, the conditional
"a void/unobserved verdict needs a reason" rule, and no unknown keys. Rather than
add a ``jsonschema`` dependency (flagged in the KYO-7 dispatch brief as a decision
to surface, not make silently — this build stays stdlib-first per
``docs/design/charter.md``, "Tech choices"), this module hand-implements exactly
the subset of JSON Schema that file actually uses: ``required``, ``type``,
``enum``, ``pattern``,
``minLength``, ``additionalProperties: false``, ``items``, and one ``if/then``
conditional. It is deliberately not a general JSON Schema engine — if
``report.schema.json`` grows a feature this module does not implement,
``tests/test_kyo149_report_schema_agrees.py`` is where that gap is caught.

**KYO-149: where the file lives, and why this module now reads it.** The schema
used to sit at ``templates/report.schema.json`` — a source-tree directory that
``pyproject.toml`` never shipped, so every brief and every ``enforced-by`` line
citing it named a path that no installed consumer has ever had. It now lives
beside this module inside the package, is listed in ``[tool.setuptools.package-data]``,
and is served by ``kyo report-schema``. :func:`schema_text` is the one reader;
``report_ingest.cmd_report_schema`` prints exactly what it returns.

That move creates a hazard worth naming, because it is worse than the defect it
fixes: a schema a worker can now *read* is a schema a worker will now *believe*,
and this module does not execute it — it mirrors it, by hand, in the constants
below. A reachable document that quietly disagreed with the validator would be a
lie told confidently, where the old broken path at least failed loudly. So the
mirror is derived-and-compared rather than hoped-for:
``tests/test_kyo149_report_schema_agrees.py`` reads the shipped document and
asserts every constant here against it. Add a key to one and the suite fails
until the other agrees.

Every problem found is collected and reported together (the same "collect, don't
stop at the first" style as ``kinds._build_registry`` and
``cmd_records._validate_touches``), so a broken report is fixed in one pass.
"""

from __future__ import annotations

import json
import re
from importlib import resources

from .contracts import (
    CRITERION_STATES,
    CRITERION_STATES_REQUIRING_REASON,
    OBLIGATION_VERDICTS,
    REPORT_AMENDS_KEY,
    VERIFICATION_METHODS,
)

#: The packaged schema document, beside this module. Read through
#: :mod:`importlib.resources` rather than ``Path(__file__).parent`` so it resolves
#: the same way from a wheel, an editable install and a zipimport — the reader who
#: needs it most is in a consumer project that has only the installed package.
SCHEMA_RESOURCE = "report.schema.json"

#: The pattern is the schema's own, byte for byte (the agreement test compares them).
#: KYO-413.c5: it is applied with :meth:`re.Pattern.fullmatch`, never ``.match`` —
#: Python's ``$`` also matches before a trailing newline, so ``"abc1234\n"`` passed
#: ``.match`` while JSON Schema's ECMA ``$`` (and git) would refuse it.
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def schema_text() -> str:
    """The packaged ``report.schema.json``, verbatim.

    Law 3: this is the only read of that file in the shipped code, so "what
    ``kyo report-schema`` prints" and "the document ``kyo report`` is validating
    against" cannot become two different answers by way of a second copy. What
    they *could* still become is two different answers by way of the hand-written
    mirror below drifting from the document — which is why that mirror is asserted
    against this text by ``tests/test_kyo149_report_schema_agrees.py``.
    """
    return resources.files(__package__).joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def schema_document() -> dict:
    """:func:`schema_text`, parsed. The same bytes, not a second description."""
    return json.loads(schema_text())


TOP_LEVEL_REQUIRED = (
    "dispatch_id",
    "record_id",
    "worked_from_sha",
    "criteria_verdicts",
    "premise_false",
    "disagreements",
    "corrected_numbers",
    "commits",
)
#: KYO-90: ``amends`` is optional and carries the id of the report this one
#: supersedes. Optional is load-bearing — every report filed before KYO-90 stays
#: valid, and a first report that omits it is the ordinary case, not a defect.
#: KYO-199: ``docs_verdicts`` is optional for the same reason ``amends`` is — every
#: report filed before it existed stays valid, and a dispatch whose diff raised no
#: obligation has nothing to say here. What is NOT optional is its honesty: an entry
#: naming a section no obligation was raised for is printed as unmatched by
#: ``cmd_dispatch`` and refused by ``kyo docs settle --from-report``.
TOP_LEVEL_OPTIONAL = ("summary", "touched_files", "docs_verdicts", REPORT_AMENDS_KEY)
TOP_LEVEL_ALLOWED = frozenset(TOP_LEVEL_REQUIRED) | frozenset(TOP_LEVEL_OPTIONAL)

CRITERION_VERDICT_REQUIRED = ("criterion_id", "verdict", "method")
CRITERION_VERDICT_OPTIONAL = ("evidence", "reason", "notes")
CRITERION_VERDICT_ALLOWED = frozenset(CRITERION_VERDICT_REQUIRED) | frozenset(
    CRITERION_VERDICT_OPTIONAL
)
#: The schema's ``if/then``: these verdicts require ``reason`` (mirrors
#: contracts.CRITERION_STATES_REQUIRING_REASON, which report.schema.json's own
#: description says this enum must agree with byte-for-byte).
CRITERION_VERDICT_REASON_REQUIRED = CRITERION_STATES_REQUIRING_REASON

PREMISE_FALSE_REQUIRED = ("claim", "correction")
PREMISE_FALSE_OPTIONAL = ("impact",)
PREMISE_FALSE_ALLOWED = frozenset(PREMISE_FALSE_REQUIRED) | frozenset(PREMISE_FALSE_OPTIONAL)

DISAGREEMENT_REQUIRED = ("topic", "brief_said", "worker_did", "rationale")
DISAGREEMENT_ALLOWED = frozenset(DISAGREEMENT_REQUIRED)

CORRECTED_NUMBER_REQUIRED = ("claim", "brief_value", "actual_value", "method")
CORRECTED_NUMBER_ALLOWED = frozenset(CORRECTED_NUMBER_REQUIRED)

DOCS_VERDICT_REQUIRED = ("doc_path", "section", "verdict", "method", "reason")
DOCS_VERDICT_ALLOWED = frozenset(DOCS_VERDICT_REQUIRED)
#: The two verdicts a docs obligation can carry, from the one place that defines them
#: (``contracts.OBLIGATION_VERDICTS``) — the same enum ``kyo docs settle`` validates
#: against, so a report cannot propose a verdict that command would refuse.
DOCS_VERDICT_VERDICTS = OBLIGATION_VERDICTS

COMMIT_REQUIRED = ("sha",)
#: KYO-241.c3: ``subject`` is the deliberately-chosen name for "the commit's first
#: line" (never ``message`` or ``severity`` — the two keys ``additionalProperties:
#: false`` refused on KYO-233.d1 and KYO-234.d1). ``summary`` predated it (KYO-224)
#: and described the same thing under an older name — a second key on one object
#: meaning the same thing, which KYO-270 retires: ``subject`` is now the only
#: accepted first-line field, and a report still carrying ``summary`` is refused
#: by the unknown-key message below, which names ``subject`` as what it accepts.
#: A report already ingested with ``summary`` is history and stays valid to
#: *read* — ``render.commit_label`` handles that tolerantly rather than by
#: validating it (see there) — but a *new* report can no longer write it.
COMMIT_OPTIONAL = ("subject",)
COMMIT_ALLOWED = frozenset(COMMIT_REQUIRED) | frozenset(COMMIT_OPTIONAL)


#: KYO-537: ``human-observed`` is the label that says a person looked (``kyo confirm``
#: exists for exactly that). A dispatched worker's report cannot carry it: workers
#: wrote it when no person had looked, and the orchestrator had to re-record the
#: verdicts. The orchestrator's own ``kyo verify`` / ``kyo confirm`` still
#: accept it; only this report path refuses it.
WORKER_FORBIDDEN_METHOD = "human-observed"
WORKER_REPORT_METHODS = tuple(m for m in VERIFICATION_METHODS if m != WORKER_FORBIDDEN_METHOD)


def _check_worker_method(problems: list[str], where: str, obj: dict) -> None:
    if obj.get("method") == WORKER_FORBIDDEN_METHOD:
        problems.append(
            f"{where}.method: {WORKER_FORBIDDEN_METHOD!r} is reserved for a person who looked "
            "(the orchestrator records it with `kyo confirm`/`kyo verify`); a worker's report "
            f"may use only {', '.join(repr(m) for m in WORKER_REPORT_METHODS)} — "
            "use 'reasoned-about' and say in `reason`/`notes` what a person still has to look at"
        )


def _require_object(problems: list[str], where: str, value: object) -> dict | None:
    if not isinstance(value, dict):
        problems.append(f"{where}: must be an object, got {type(value).__name__}")
        return None
    return value


def _require_string(problems: list[str], where: str, value: object, *, min_length: int = 0) -> None:
    if not isinstance(value, str):
        problems.append(f"{where}: must be a string, got {type(value).__name__}")
        return
    if len(value) < min_length:
        problems.append(f"{where}: must be at least {min_length} character(s) long")


#: Every OPTIONAL field the schema declares as nullable, as ``(where, key)`` — the
#: mirror of the ``["<type>", "null"]`` unions in ``report.schema.json``, asserted
#: against the document by ``tests/test_kyo149_report_schema_agrees.py`` the same way
#: every other constant in this module is (KYO-224.c1).
#:
#: ``where`` is the def name the key belongs to, or ``"report"`` for a top-level key.
NULLABLE_OPTIONAL: frozenset[tuple[str, str]] = frozenset(
    {
        ("report", "summary"),
        ("report", "touched_files"),
        ("report", "docs_verdicts"),
        ("report", REPORT_AMENDS_KEY),
        ("criterionVerdict", "evidence"),
        ("criterionVerdict", "reason"),
        ("criterionVerdict", "notes"),
        ("premiseFalse", "impact"),
        ("commitRef", "subject"),
    }
)


def _is_unset(value: object) -> bool:
    """KYO-224: a JSON null in an optional field is that field being unset.

    A worker's report was refused for one null ``notes``, and the controller fixed it
    by hand before ingesting. Serialising an unset optional as ``null`` is what a typed
    struct does — Python's ``json.dumps`` of a dataclass, Rust's ``Option<String>``,
    Go's ``*string`` — so the refusal was about how the document was written and not
    about anything in it, on a field this schema's own description calls optional.

    Only for fields the schema declares nullable (:data:`NULLABLE_OPTIONAL`). A null in
    a REQUIRED field is not an unset optional and stays refused, naming the field: the
    validator never reaches here for one, because required keys are checked by presence
    and then by type. The one field that is both — ``criteria_verdicts[].reason``,
    optional in the schema and required by its ``if/then`` — is refused by that
    conditional, which reads ``(obj.get("reason") or "").strip()`` and so has always
    treated null as missing.
    """
    return value is None


def _require_array(problems: list[str], where: str, value: object) -> list | None:
    if not isinstance(value, list):
        problems.append(f"{where}: must be an array, got {type(value).__name__}")
        return None
    return value


def _check_object_shape(
    problems: list[str],
    where: str,
    obj: dict,
    *,
    required: tuple[str, ...],
    allowed: frozenset[str],
) -> None:
    missing = sorted(set(required) - set(obj))
    if missing:
        problems.append(f"{where}: missing required key(s) {', '.join(missing)}")
    extra = sorted(set(obj) - allowed)
    if extra:
        # KYO-241: naming the unknown key told a worker what was wrong, not what to
        # do about it -- two reports in a row (KYO-233.d1, KYO-234.d1) were resumed
        # once each just to delete a plausible-but-refused key (`severity`, `message`)
        # because nothing on screen said what the entry DID accept. The accepted set
        # is printed beside the refusal so the fix is one edit against this message,
        # not a second trip to `kyo report-schema`.
        problems.append(
            f"{where}: unknown key(s) {', '.join(extra)} -- this entry accepts: "
            f"{', '.join(sorted(allowed))} (additionalProperties: false)"
        )


def _check_sha(problems: list[str], where: str, value: object) -> None:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        problems.append(f"{where}: must be a lowercase hex git sha, 7-40 characters, got {value!r}")


def _check_criterion_verdict(problems: list[str], index: int, item: object) -> None:
    where = f"criteria_verdicts[{index}]"
    obj = _require_object(problems, where, item)
    if obj is None:
        return
    _check_object_shape(
        problems,
        where,
        obj,
        required=CRITERION_VERDICT_REQUIRED,
        allowed=CRITERION_VERDICT_ALLOWED,
    )
    if "criterion_id" in obj:
        _require_string(problems, f"{where}.criterion_id", obj["criterion_id"], min_length=1)
    if "verdict" in obj:
        if obj["verdict"] not in CRITERION_STATES:
            problems.append(
                f"{where}.verdict: {obj['verdict']!r} is not one of {', '.join(CRITERION_STATES)}"
            )
        # KYO-413.c5: a non-string reason is a type problem reported below, not an
        # AttributeError out of `.strip()`; here it simply is not a usable reason.
        elif obj["verdict"] in CRITERION_VERDICT_REASON_REQUIRED and not (
            isinstance(obj.get("reason"), str) and obj["reason"].strip()
        ):
            problems.append(
                f"{where}: verdict {obj['verdict']!r} requires a non-empty 'reason' "
                "(report.schema.json's if/then)"
            )
    if "method" in obj and obj["method"] not in VERIFICATION_METHODS:
        problems.append(
            f"{where}.method: {obj['method']!r} is not one of {', '.join(VERIFICATION_METHODS)}"
        )
    _check_worker_method(problems, where, obj)
    for key in ("evidence", "reason", "notes"):
        if key in obj and not _is_unset(obj[key]):
            _require_string(problems, f"{where}.{key}", obj[key])


def _check_premise_false(problems: list[str], index: int, item: object) -> None:
    where = f"premise_false[{index}]"
    obj = _require_object(problems, where, item)
    if obj is None:
        return
    _check_object_shape(
        problems, where, obj, required=PREMISE_FALSE_REQUIRED, allowed=PREMISE_FALSE_ALLOWED
    )
    for key in ("claim", "correction"):
        if key in obj:
            _require_string(problems, f"{where}.{key}", obj[key], min_length=1)
    if "impact" in obj and not _is_unset(obj["impact"]):
        _require_string(problems, f"{where}.impact", obj["impact"])


def _check_disagreement(problems: list[str], index: int, item: object) -> None:
    where = f"disagreements[{index}]"
    obj = _require_object(problems, where, item)
    if obj is None:
        return
    _check_object_shape(
        problems, where, obj, required=DISAGREEMENT_REQUIRED, allowed=DISAGREEMENT_ALLOWED
    )
    for key in DISAGREEMENT_REQUIRED:
        if key in obj:
            _require_string(problems, f"{where}.{key}", obj[key], min_length=1)


def _check_corrected_number(problems: list[str], index: int, item: object) -> None:
    where = f"corrected_numbers[{index}]"
    obj = _require_object(problems, where, item)
    if obj is None:
        return
    _check_object_shape(
        problems,
        where,
        obj,
        required=CORRECTED_NUMBER_REQUIRED,
        allowed=CORRECTED_NUMBER_ALLOWED,
    )
    if "claim" in obj:
        _require_string(problems, f"{where}.claim", obj["claim"], min_length=1)
    if "method" in obj:
        _require_string(problems, f"{where}.method", obj["method"], min_length=1)
    for key in ("brief_value", "actual_value"):
        if key in obj:
            _require_string(problems, f"{where}.{key}", obj[key])


def _check_docs_verdict(problems: list[str], index: int, item: object) -> None:
    where = f"docs_verdicts[{index}]"
    obj = _require_object(problems, where, item)
    if obj is None:
        return
    _check_object_shape(
        problems, where, obj, required=DOCS_VERDICT_REQUIRED, allowed=DOCS_VERDICT_ALLOWED
    )
    for key in ("doc_path", "section", "method", "reason"):
        if key in obj:
            _require_string(problems, f"{where}.{key}", obj[key], min_length=1)
    _check_worker_method(problems, where, obj)
    if "verdict" in obj and obj["verdict"] not in DOCS_VERDICT_VERDICTS:
        problems.append(
            f"{where}.verdict: {obj['verdict']!r} is not one of "
            f"{', '.join(DOCS_VERDICT_VERDICTS)}"
        )


def _check_commit(problems: list[str], index: int, item: object) -> None:
    where = f"commits[{index}]"
    obj = _require_object(problems, where, item)
    if obj is None:
        return
    _check_object_shape(problems, where, obj, required=COMMIT_REQUIRED, allowed=COMMIT_ALLOWED)
    if "sha" in obj:
        _check_sha(problems, f"{where}.sha", obj["sha"])
    if "subject" in obj and not _is_unset(obj["subject"]):
        _require_string(problems, f"{where}.subject", obj["subject"])


def validate_report(doc: object) -> list[str]:
    """Return every way ``doc`` fails ``report.schema.json``. Empty means valid."""
    problems: list[str] = []
    obj = _require_object(problems, "report", doc)
    if obj is None:
        return problems

    _check_object_shape(
        problems, "report", obj, required=TOP_LEVEL_REQUIRED, allowed=TOP_LEVEL_ALLOWED
    )

    for key in ("dispatch_id", "record_id"):
        if key in obj:
            _require_string(problems, key, obj[key], min_length=1)
    if "worked_from_sha" in obj:
        _check_sha(problems, "worked_from_sha", obj["worked_from_sha"])
    if "summary" in obj and not _is_unset(obj["summary"]):
        _require_string(problems, "summary", obj["summary"])
    if REPORT_AMENDS_KEY in obj and not _is_unset(obj[REPORT_AMENDS_KEY]):
        _require_string(problems, REPORT_AMENDS_KEY, obj[REPORT_AMENDS_KEY], min_length=1)
    if "touched_files" in obj and not _is_unset(obj["touched_files"]):
        files = _require_array(problems, "touched_files", obj["touched_files"])
        if files is not None:
            for i, f in enumerate(files):
                _require_string(problems, f"touched_files[{i}]", f)

    array_checks = (
        ("criteria_verdicts", _check_criterion_verdict),
        ("premise_false", _check_premise_false),
        ("disagreements", _check_disagreement),
        ("corrected_numbers", _check_corrected_number),
        ("docs_verdicts", _check_docs_verdict),
        ("commits", _check_commit),
    )
    for key, checker in array_checks:
        # A null in a REQUIRED array is still refused: `premise_false: null` is not the
        # same statement as `premise_false: []`, and the empty array is the positive
        # one this schema exists to force ("I checked and found nothing").
        if key not in obj or (_is_unset(obj[key]) and key in TOP_LEVEL_OPTIONAL):
            continue
        items = _require_array(problems, key, obj[key])
        if items is None:
            continue
        for i, item in enumerate(items):
            checker(problems, i, item)
        if key == "criteria_verdicts":
            _check_unique_criterion_ids(problems, items)

    return problems


def _check_unique_criterion_ids(problems: list[str], items: list) -> None:
    """KYO-413.c5: one verdict per criterion. VALIDATOR-ONLY — JSON Schema cannot
    say "unique by key" (``uniqueItems`` compares whole entries), so the schema's
    ``criteria_verdicts`` description states the rule and this enforces it. Two
    entries for one criterion (``met`` and ``unmet-unbuilt``) validated clean, and
    which one ingestion believed was an accident of order."""
    seen: dict[str, int] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("criterion_id"), str):
            continue
        cid = item["criterion_id"]
        if cid in seen:
            problems.append(
                f"criteria_verdicts[{i}]: criterion_id {cid!r} already has a verdict at "
                f"criteria_verdicts[{seen[cid]}] -- one entry per criterion; merge the "
                "two into one verdict (put the other observation in its notes)"
            )
        else:
            seen[cid] = i


__all__ = ["NULLABLE_OPTIONAL", "schema_document", "schema_text", "validate_report"]
