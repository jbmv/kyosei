"""Id shapes.

Law 4: ids are stable, opaque, never reused, never positional.

============  ===================  =========================================
Object        Shape                Allocation scope
============  ===================  =========================================
record        ``KYO-12``           ``record``
criterion     ``KYO-12.c3``        ``criterion:KYO-12``
note          ``KYO-12.n2``        ``note:KYO-12``
gate          ``KYO-12.g1``        ``gate:KYO-12``
touch         ``KYO-12.t4``        ``touch:KYO-12``
claim         ``KYO-12.m1``        ``claim:KYO-12``
verification  ``KYO-12.v2``        ``verification:KYO-12``
evidence      ``KYO-12.e1``        ``evidence:KYO-12``
dispatch      ``KYO-12.d1``        ``dispatch:KYO-12``
report        ``KYO-12.r1``        ``report:KYO-12``
flag          ``KYO-12.f3``        ``flag:KYO-12``
link          ``KYO-L7``           ``link``
event         ``KYO-E41``          ``event``
sweep run     ``KYO-W3``           ``sweep``
brief run     ``KYO-B9``           ``brief``
shift note    ``KYO-S12``          ``shift_note``
============  ===================  =========================================

The ``c``/``n``/``g`` numbers are *allocation counters*, not positions: voiding
criterion ``c2`` leaves ``c3`` named ``c3`` forever, so a citation written into a
commit message stays true.
"""

from __future__ import annotations

import re

PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
RECORD_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15})-([1-9][0-9]*)$")
LINK_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15})-L([1-9][0-9]*)$")
SWEEP_RUN_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15})-W([1-9][0-9]*)$")
BRIEF_RUN_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15})-B([1-9][0-9]*)$")
SHIFT_NOTE_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15})-S([1-9][0-9]*)$")

#: ``c``/``n``/``g``/``t`` are milestones 1-3; milestone 4 (provenance) adds
#: ``m`` (claim — ``c`` was already taken by criterion), ``v`` (verification),
#: ``e`` (evidence artifact); milestone 5 (KYO-7, dispatch loop) adds ``d``
#: (dispatch) and ``r`` (report); milestone 6 (KYO-8, sweep + brief) adds ``f``
#: (flag — a child of the record it primarily concerns). KYO-137 (docs governance)
#: adds ``x`` (doc-ref) and ``o`` (docs obligation).
#:
#: ``x`` reads as *cross-reference*, which is what a ``doc-refs`` entry is, and it is
#: that rather than a mnemonic letter because every mnemonic letter was gone: ``d`` is
#: the dispatch, ``r`` the report. Naming the relation's own first letter would have
#: meant renaming a child kind that ids in committed commit messages already use, and
#: law 4 says an id never moves.
CHILD_KIND_BY_LETTER = {
    "c": "criterion",
    "n": "note",
    "g": "gate",
    "t": "touch",
    "m": "claim",
    "v": "verification",
    "e": "evidence",
    "d": "dispatch",
    "r": "report",
    "f": "flag",
    "x": "doc-ref",
    "o": "docs-obligation",
}
CHILD_LETTER_BY_KIND = {v: k for k, v in CHILD_KIND_BY_LETTER.items()}

#: Derived from :data:`CHILD_KIND_BY_LETTER` rather than spelled a second time (law 3).
#: It was a hand-written ``[cngtmvedrf]`` character class until KYO-137 added two kinds
#: and had to remember to widen the regex as well — two copies of one vocabulary, which
#: is the drift this project's third law exists to prevent, found the moment something
#: needed both. Sorted so the pattern is stable across dict insertion order.
CHILD_RE = re.compile(
    r"^([A-Z][A-Z0-9]{1,15}-[1-9][0-9]*)\."
    rf"([{''.join(sorted(CHILD_KIND_BY_LETTER))}])"
    r"([1-9][0-9]*)$"
)


def record_id(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq}"


def child_id(record: str, kind: str, seq: int) -> str:
    return f"{record}.{CHILD_LETTER_BY_KIND[kind]}{seq}"


def link_id(prefix: str, seq: int) -> str:
    return f"{prefix}-L{seq}"


def event_id(prefix: str, seq: int) -> str:
    return f"{prefix}-E{seq}"


def sweep_run_id(prefix: str, seq: int) -> str:
    return f"{prefix}-W{seq}"


def brief_run_id(prefix: str, seq: int) -> str:
    return f"{prefix}-B{seq}"


def shift_note_id(prefix: str, seq: int) -> str:
    return f"{prefix}-S{seq}"


def is_record_id(value: str) -> bool:
    return bool(RECORD_RE.match(value))


def record_of_child(value: str) -> str | None:
    """Return the owning record id of a child id, or ``None`` if not a child id."""
    match = CHILD_RE.match(value)
    return match.group(1) if match else None


def child_kind(value: str) -> str | None:
    match = CHILD_RE.match(value)
    return CHILD_KIND_BY_LETTER[match.group(2)] if match else None


def prefix_of(value: str) -> str | None:
    for pattern in (RECORD_RE, LINK_RE, SWEEP_RUN_RE, BRIEF_RUN_RE, SHIFT_NOTE_RE):
        match = pattern.match(value)
        if match:
            return match.group(1)
    child = record_of_child(value)
    if child:
        return prefix_of(child)
    return None


def normalise_prefix(raw: str) -> str:
    """Derive a legal project prefix from arbitrary text (e.g. a directory name)."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    cleaned = re.sub(r"^[0-9]+", "", cleaned)
    if len(cleaned) < 2:
        cleaned = (cleaned + "KYO")[:3]
    return cleaned[:4]
