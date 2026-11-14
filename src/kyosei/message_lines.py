"""The lines ``kyo serve`` writes into a message beside the person's words, and the parts of
a turn they head (KYO-934).

A prompt sent from a client may hold several answers, each under the line that says what it
answers: ``on record KYO-12.g1 "the iPad build is checked by..."`` above an answer to a gate,
``on your reply <uuid> "..."`` above a remark on a reply, and ``attached <path>`` for a file
on a paragraph (:mod:`kyosei.serve_notes` states the whole form). The server writes those
lines and the person writes the rest, so a reader that wants the person's answer to one gate
has to tell the two apart and find where the answer ends.

This module is the one place that knows the form. The writers here are what
:mod:`kyosei.serve_notes` builds a record's, a note's, a turn's and an attachment's line with,
and the patterns beside them read the same lines back, so the two cannot drift apart
unnoticed. A file's line is written in :mod:`kyosei.serve_notes`, which holds what it needs,
and is only recognised here. The clients keep their own copies of the patterns, in
TypeScript; a test compares the record line's with :data:`RECORD_ID`.

A PART (:func:`parts`) is the person's lines under one group of such lines, up to the next
anchor line, the next ``Note N (id)`` heading of a notes message, or the end of the turn. An
``attached`` line with no anchor line beside it does not end a part: it is a file on a later
paragraph of the same answer. A part is headed for the records its group's ``on record``
lines name, and for nothing its quoted words mention.

A CUT (:func:`cut_for`) is the text of the parts headed for what a command credits, with
:data:`OMISSION` wherever something of the turn was left out, the server's own lines
included. A turn that holds no such part gives None, and the caller keeps the whole turn.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: How an omission is marked in stored words, the mark a quote file uses.
OMISSION = "[...]"

#: A record's id or the id of one of its elements, as an ``on record`` line carries it. An
#: entry of a stored report has a second part, ``KYO-740.r1.x1`` (KYO-757).
RECORD_ID = r"[A-Z][A-Z0-9]{1,15}-[1-9]\d*(?:\.[a-z][1-9]\d*){0,2}"

_UUID = r"[0-9a-f-]{36}"
_ON_RECORD = "on record "
_ON_NOTE = "on note "
_ATTACHED = "attached "
_NOT_IN_STORE = " (not in the store)"
_TURN_WHO = {True: "your reply", False: "my prompt"}

RECORD_LINE = re.compile(
    rf'^{re.escape(_ON_RECORD)}({RECORD_ID})(?: "(.*)"|{re.escape(_NOT_IN_STORE)})?$'
)
_TURN_LINE = re.compile(rf"^on (?:{'|'.join(_TURN_WHO.values())}) {_UUID}(?: |$)")
_NOTE_LINE = re.compile(rf"^{re.escape(_ON_NOTE)}\S+$")
#: A served file's line, and the older line of an anchor filed with a commit.
_FILE_LINE = re.compile(
    r"^on (?:file \S.*? lines? \d+(?:-\d+)?"
    r"|(?:file|document) \S+ at [0-9a-f]{7,40} (?:lines|chars) \d+-\d+)(?: |$)"
)
#: An attachment is stored under its digest, which is what tells the server's line from a
#: sentence of the person's that begins with the same word.
_ATTACHED_LINE = re.compile(
    rf"^{re.escape(_ATTACHED)}(?:.*/)?[0-9a-f]{{64}}\.[A-Za-z0-9]+$"
)
_NOTE_HEADING = re.compile(r"^Note \d+ \(\S+\)$")
_MESSAGE_HEADING = re.compile(r"^\[kyo-message, \d+ notes?\]$")


# ------------------------------------------------------------------------- writing


def record_line(target: str, quoted: str | None) -> str:
    """A record's or an element's line: its id and its first words already quoted, ``""``
    for an element that holds none, None for an id the store does not hold."""
    if quoted is None:
        return f"{_ON_RECORD}{target}{_NOT_IN_STORE}"
    return f"{_ON_RECORD}{target}{f' {quoted}' if quoted else ''}"


def note_line(target: str) -> str:
    return f"{_ON_NOTE}{target}"


def turn_line_head(target: str, *, agent: bool, session: str | None = None) -> str:
    """The start of a turn's line, before its quoted words: whose turn, its record, and
    its session when that is not the one the message goes to."""
    return f"on {_TURN_WHO[agent]} {target}{f' in session {session}' if session else ''}"


def attached_line(path: str) -> str:
    return f"{_ATTACHED}{path}"


# ------------------------------------------------------------------------- reading


def record_of(element_id: str) -> str:
    """The record an id on an ``on record`` line belongs to: the id up to its first dot."""
    return element_id.split(".", 1)[0]


def record_named(line: str) -> str | None:
    """The id an ``on record`` line names, or None for any other line."""
    read = RECORD_LINE.match(line)
    return read.group(1) if read else None


def is_anchor_line(line: str) -> bool:
    """Whether the server wrote this line to say what the lines under it answer."""
    return any(
        pattern.match(line) for pattern in (RECORD_LINE, _TURN_LINE, _NOTE_LINE, _FILE_LINE)
    )


def is_attached_line(line: str) -> bool:
    return bool(_ATTACHED_LINE.match(line))


def _is_heading(line: str) -> bool:
    return bool(_NOTE_HEADING.match(line) or _MESSAGE_HEADING.match(line))


@dataclass(frozen=True)
class Part:
    """The person's lines under one group of the server's lines."""

    #: The ids the group's ``on record`` lines name, in the order written.
    ids: tuple[str, ...]
    #: Those ``on record`` lines as written.
    headers: tuple[str, ...]
    #: Where each of the part's lines sits among the turn's lines (:func:`_lines`).
    at: tuple[int, ...]


def _lines(segments: Iterable[str]) -> list[str | None]:
    """The turn's lines, with None between two segments, where a paste was left out."""
    out: list[str | None] = []
    for index, segment in enumerate(segments):
        if index:
            out.append(None)
        out += segment.split("\n")
    return out


def _parts(lines: list[str | None]) -> list[Part]:
    found: list[Part] = []
    ids: list[str] = []
    headers: list[str] = []
    at: list[int] = []
    heading = False  # inside a group of the server's lines, before the person's first
    opened = False  # an anchor line or a heading has been read, so the lines are a part's

    def close() -> None:
        if opened and at:
            found.append(Part(tuple(ids), tuple(headers), tuple(at)))

    for index, line in enumerate(lines):
        if line is None:
            continue
        if is_anchor_line(line) or _is_heading(line):
            if not heading:
                close()
                ids, headers, at = [], [], []
            heading = opened = True
            named = record_named(line)
            if named is not None:
                ids.append(named)
                headers.append(line)
            continue
        if is_attached_line(line):
            continue
        if line.strip():
            heading = False
            at.append(index)
        elif heading and ids:
            # The server writes a marked line directly under its lines, so a blank line
            # there is a marked line with no text, and what follows answers nothing named.
            ids, headers = [], []
    close()
    return found


def parts(segments: Iterable[str]) -> list[Part]:
    """The parts of a turn whose typed text is ``segments``, in the order written. A turn
    the server wrote no anchor line into has none."""
    return _parts(_lines(segments))


def _matches(header: str, target: str) -> bool:
    return header == target or record_of(header) == target


def headed_for(found: list[Part], targets: Iterable[str]) -> list[Part]:
    """The parts headed for what is credited. A part whose line names a target is taken,
    and a target that is a record takes every part naming it or one of its elements. When
    no part is found that way, the parts naming anything in a target's record are taken,
    so that an answer written against a gate is found by a command naming its criterion."""
    wanted = list(targets)
    exact = [p for p in found if any(_matches(i, t) for i in p.ids for t in wanted)]
    if exact:
        return exact
    records = {record_of(target) for target in wanted}
    return [p for p in found if any(record_of(i) in records for i in p.ids)]


@dataclass(frozen=True)
class Cut:
    """What is kept of a turn for one credit."""

    text: str
    #: The ``on record`` lines of the parts kept.
    headers: tuple[str, ...]

    @property
    def first_line(self) -> str:
        return first_line(self.text)


def cut_for(segments: Iterable[str], targets: Iterable[str]) -> Cut | None:
    """The parts of the turn headed for ``targets`` as one text, or None when the turn
    holds no such part. Lines are kept as typed and in order; :data:`OMISSION` stands on a
    line of its own wherever lines of the turn that hold anything were left out."""
    lines = _lines(segments)
    kept = headed_for(_parts(lines), targets)
    if not kept:
        return None
    wanted = {index for part in kept for index in part.at}
    first, last = min(wanted), max(wanted)
    out: list[str] = []
    gap = False
    for index, line in enumerate(lines):
        if index in wanted:
            if gap:
                while out and not out[-1].strip():
                    out.pop()
                out.append(OMISSION)
            gap = False
            assert line is not None
            out.append(line)
        elif line is None or line.strip():
            gap = True
        elif first < index < last and not gap:
            out.append(line)  # a blank line between two kept lines is the person's
    while out and not out[-1].strip():
        out.pop()
    if gap:
        out.append(OMISSION)
    headers = tuple(dict.fromkeys(header for part in kept for header in part.headers))
    return Cut("\n".join(out), headers)


def is_partial(words: str) -> bool:
    """Whether stored words say they are part of something longer: they begin or end
    with :data:`OMISSION` on a line of its own."""
    lines = [line.strip() for line in words.strip().split("\n")]
    return len(lines) > 1 and OMISSION in (lines[0], lines[-1])


def first_line(words: str) -> str:
    """The first line of stored words that is not an omission mark or blank."""
    for line in words.split("\n"):
        if line.strip() and line.strip() != OMISSION:
            return line.strip()
    return ""
