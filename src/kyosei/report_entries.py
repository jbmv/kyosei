"""Ids for the entries of a stored report (KYO-757).

A report's false premises, disagreements, corrected numbers and verdicts are entries of
arrays in its stored body (``reports.body_json``), and had no id: a note could be anchored to
the report's criteria and not to what the worker said about the brief. Each such entry is
named by the report's id, a letter for its array, and its place in that array counted from 1:

==================  =====================  ==========================
Entry               Id                     Array of the report's body
==================  =====================  ==========================
false premise       ``KYO-12.r1.p2``       ``premise_false``
disagreement        ``KYO-12.r1.x1``       ``disagreements``
corrected number    ``KYO-12.r1.n1``       ``corrected_numbers``
verdict             ``KYO-12.r1.v3``       ``criteria_verdicts``
==================  =====================  ==========================

THE ID IS DERIVED, NOT STORED. :mod:`kyosei.ids` says an id is never positional, because a
criterion can be voided or a note added and a position would then name something else. A
report's body is written once, at ingest, and no command edits its arrays: an amendment is a
second report with an id of its own (``"amends"``), and a redaction rewrites words inside an
entry and removes no entry. The place of an entry in its array is therefore fixed for as long
as the report exists, and it is read from the body each time. Nothing is stored, so no schema
version moves and ``kyo rebuild`` has nothing to reconstruct.

A SUPERSEDED REPORT keeps its ids. ``KYO-12.r1.p1`` goes on naming the first false premise of
``KYO-12.r1`` after ``KYO-12.r2`` amends it, as the superseded report itself stays readable.
The amending report's entries are its own, ``KYO-12.r2.p1`` and so on; nothing says that an
entry of one is the entry of the other, because the body does not say so either. ``kyo show``
of an entry states its report's standing.

The letters are scoped to a report, so ``p`` and ``x`` here do not collide with a record's
credit (``KYO-12.p1``) and doc-ref (``KYO-12.x1``), which have one dot. A verdict is named by
place and not by its criterion, because a report whose dispatch covers other records carries
verdicts on their criteria too, and two of those can share a criterion number. Docs verdicts
and commits get no id: a docs verdict is settled into an obligation, which has one, and a
commit has its sha.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import ids
from .errors import NotFound

#: The letter of each array of a report's body whose entries have ids, in the order a
#: report is read.
ARRAY_BY_LETTER = {
    "p": "premise_false",
    "x": "disagreements",
    "n": "corrected_numbers",
    "v": "criteria_verdicts",
}
LETTER_BY_ARRAY = {array: letter for letter, array in ARRAY_BY_LETTER.items()}

#: What an entry of each array is called in a sentence.
NAMES = {
    "premise_false": "false premise",
    "disagreements": "disagreement",
    "corrected_numbers": "corrected number",
    "criteria_verdicts": "verdict",
}

#: The keys of an entry in the order ``kyo show`` prints them, which is the report schema's.
KEYS = {
    "premise_false": ("claim", "correction", "impact"),
    "disagreements": ("topic", "brief_said", "worker_did", "rationale"),
    "corrected_numbers": ("claim", "brief_value", "actual_value", "method"),
    "criteria_verdicts": ("criterion_id", "verdict", "method", "evidence", "reason", "notes"),
}

#: The key whose text says which entry this is, tried in order: what an anchor quotes.
_WORDS = {
    "premise_false": ("claim",),
    "disagreements": ("topic",),
    "corrected_numbers": ("claim",),
    "criteria_verdicts": ("evidence", "reason", "notes"),
}

ENTRY_RE = re.compile(
    r"^([A-Z][A-Z0-9]{1,15}-[1-9][0-9]*\.r[1-9][0-9]*)\."
    rf"([{''.join(sorted(ARRAY_BY_LETTER))}])"
    r"([1-9][0-9]*)$"
)


def parse(value: str) -> tuple[str, str, int] | None:
    """``(report id, array, place counted from 1)`` of an entry id, or None when ``value``
    is not one."""
    match = ENTRY_RE.match(value)
    if match is None:
        return None
    return match.group(1), ARRAY_BY_LETTER[match.group(2)], int(match.group(3))


def is_entry_id(value: str) -> bool:
    return ENTRY_RE.match(value) is not None


def entry_ids(report_id: str, body: dict[str, Any]) -> dict[str, list[str]]:
    """The id of every entry of ``body`` that has one, by array, in the array's order."""
    return {
        array: [
            f"{report_id}.{letter}{place}"
            for place in range(1, len(body.get(array) or []) + 1)
        ]
        for letter, array in ARRAY_BY_LETTER.items()
    }


def words(array: str, entry: dict[str, Any]) -> str:
    """The words that say which entry this is: a premise's claim, a disagreement's topic, a
    corrected number's claim, and a verdict's evidence, or its reason or notes when it gave
    none, or its criterion and verdict when it gave nothing else."""
    for key in _WORDS[array]:
        text = entry.get(key)
        if isinstance(text, str) and text.strip():
            return text
    if array == "criteria_verdicts":
        return f"{entry.get('criterion_id', '')} {entry.get('verdict', '')}".strip()
    return ""


def find(store: Any, entry_id: str) -> dict[str, Any] | None:
    """The entry ``entry_id`` names, as ``{"id", "kind", "report_id", "record_id",
    "dispatch_id", "entry"}``, or None when the store holds no such report or the report no
    entry at that place. ``kind`` is the array's name."""
    parsed = parse(entry_id)
    if parsed is None:
        return None
    report_id, array, place = parsed
    try:
        row = store.report(report_id)
    except NotFound:
        return None
    entries = json.loads(row["body_json"]).get(array) or []
    if place > len(entries) or not isinstance(entries[place - 1], dict):
        return None
    return {
        "id": entry_id,
        "kind": array,
        "report_id": report_id,
        "record_id": ids.record_of_child(report_id),
        "dispatch_id": row["dispatch_id"],
        "entry": entries[place - 1],
    }
