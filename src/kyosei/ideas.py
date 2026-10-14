"""``kyosei/ideas.py`` — Layer 1: the ideas pool and the promotion boundary.

Charter, Layer 1: "Structure appears at promotion, not at intake" (law 13). The pool
itself is never validated, swept, or enforced against (law 12) — this module holds
only the two *read-side* mechanisms that keep the promotion boundary honest:

* recognising a promotion stamp already on a file (so a stamped idea is refused a
  second promotion, and drops out of ``kyo ideas review`` by default);
* flagging — never fixing — path-like mentions that no longer resolve, for the
  human's periodic purge (``kyo ideas review``).

Nothing here writes to an idea file. The functions that *produce* new file text —
:func:`stub_text` (which builds on :func:`apply_stamp`) and :func:`migrate_text` — return
a string and leave the write to their one caller each: ``cmd_records.cmd_promote``, inside
the promotion transaction, and ``cmd_views`` for ``kyo ideas migrate-stamps``, which writes
nothing at all unless given ``--apply``.

KYO-106 adds the third read-side mechanism and the write text that goes with it:
promotion moves the prose into the store (:func:`promoted_body`) and leaves a stub
where it was (:func:`promotion_stub`), so the record is the single home of the text and the
idea file holds a pointer that cannot go stale.

KYO-107 adds the fourth: a file is *cut into sections* (:func:`sections`) so one of them can
be promoted on its own (:func:`section_stub_text`) and the rest of the file survives
untouched. Reading is still all this module does to a file it is not being asked to rewrite.

KYO-228 reverses the earlier "leave a pointer" decision: `kyo promote` now
*consumes* by default -- a whole-file promotion deletes the file outright (no function
here does that; there is no text left to hand back) and a section promotion removes
that section, heading included, via :func:`consumed_section_text`. `stub_text` and
`section_stub_text` are unchanged and still exact: ``kyo promote --keep`` reaches them
for the pre-KYO-228 behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import git_queries
from .clock import TIMESTAMP_FORMAT
from .prose import flatten
from .scaffold import IDEAS_DIR, SCRATCHPAD_BASENAME

# --------------------------------------------------------------------- the pool


def ideas_dir(root: Path) -> Path:
    return root / IDEAS_DIR


def idea_files(root: Path) -> list[Path]:
    """Every ``ideas/**/*.md`` file, excluding ``scratchpad.md`` at any depth."""
    directory = ideas_dir(root)
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*.md")
        if path.is_file() and path.name != SCRATCHPAD_BASENAME
    )


# --------------------------------------------------------------------- the stamp
#
# KYO-30: the stamp is **top-of-file YAML frontmatter**, and ``promoted`` is a
# **list**.
#
# Two decisions are load-bearing here and neither is cosmetic:
#
# *Top of file*, because Obsidian only reads YAML frontmatter at the top of a file —
# a trailing block is invisible to its properties pane, and an Obsidian Base query
# reads that frontmatter. The stamp stopped being decoration the
# moment a query depended on it.
#
# *A list*, because a promotion can replace the promoted section with a stub,
# and a single file may hold several sections. One file can therefore name several
# records, so ``promoted:`` is plural from the first commit rather than being widened
# later — a scalar written today is a migration owed tomorrow. KYO-107 built the section
# case onto that shape without altering it, which is what the plural bought: a file with
# three promoted sections names three records in one list, and no second stamp convention
# appeared beside this one. WHICH record a given section went to is said by the stub sitting
# under that heading, not by a third parallel list whose alignment nothing could check.
#
# ``ideas/`` is a human's directory, editable by any external editor (law 12/13: never
# validated, never enforced against). Everything below therefore edits frontmatter
# *surgically* — the human's other keys, their order, their comments and the whole body
# are carried through byte-for-byte — and every reader degrades to "no stamp" rather
# than raising on YAML a human broke.


@dataclass(frozen=True)
class Stamp:
    """One promotion a file carries: the record, and when it was promoted.

    ``at`` is ``None`` only when a human deleted the matching ``promoted_at`` entry —
    the record id is what identifies a promotion, so a missing timestamp degrades the
    stamp rather than voiding it.
    """

    record_id: str
    at: str | None


PROMOTED_KEY = "promoted"
PROMOTED_AT_KEY = "promoted_at"

#: A frontmatter fence: ``---`` opening, ``---`` or ``...`` closing (both are YAML
#: document-end markers, and Obsidian writes the former).
_FENCE_RE = re.compile(r"^(?:---|\.\.\.)[ \t]*$")

#: A top-level key line inside frontmatter: no indentation, not a comment, not a list
#: item. Used to cut the block into per-key segments so a rewrite can replace exactly
#: one key's lines and leave every other line untouched.
_KEY_RE = re.compile(r"^([^\s:#-][^:]*):(?:\s|$)")

#: A flow sequence — ``promoted: [KYO-1, KYO-2]``. Accepted on read (a human may write
#: it, and Obsidian's own YAML writer sometimes does); never written.
_FLOW_RE = re.compile(r"^\[(.*)\]$")


def split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """``(frontmatter lines without the fences, everything after it)``.

    ``None`` for the first element means the file has no frontmatter — including the
    case of an opening ``---`` that is never closed, which is a horizontal rule or an
    unfinished edit, not a property block.
    """
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if _FENCE_RE.match(lines[index]):
            return lines[1:index], "\n".join(lines[index + 1 :])
    return None, text


def _segments(fm_lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Frontmatter cut into ``(key, its lines)`` runs, losing nothing.

    A run starts at a top-level ``key:`` line and holds every line up to the next one,
    so a key's value — inline, block list, nested mapping, trailing comment — travels
    with its key. Lines before the first key (a leading comment, a stray blank) become a
    single ``(None, lines)`` preamble, which is why reassembling the segments in order
    reproduces the input exactly.
    """
    segments: list[tuple[str | None, list[str]]] = []
    key: str | None = None
    current: list[str] = []
    for line in fm_lines:
        match = _KEY_RE.match(line)
        if match:
            if current or key is not None:
                segments.append((key, current))
            key = match.group(1).strip()
            current = [line]
        else:
            current.append(line)
    if current or key is not None:
        segments.append((key, current))
    return segments


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _values_in(lines: list[str]) -> list[str]:
    """The scalar values one ``key:`` segment carries.

    Three shapes are read — ``key: one``, ``key: [one, two]``, and a block list of
    ``- one`` items — because all three are things a human or another editor may leave
    behind. Only the block list is ever *written* (:func:`_stamp_lines`), so the tool
    speaks one dialect and understands three.
    """
    _, _, inline = lines[0].partition(":")
    values: list[str] = []
    inline = inline.strip()
    if inline:
        flow = _FLOW_RE.match(inline)
        values.extend(flow.group(1).split(",") if flow else [inline])
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("-"):
            values.append(stripped[1:])
    return [cleaned for cleaned in (_unquote(value) for value in values) if cleaned]


def _frontmatter_stamps(fm_lines: list[str]) -> list[Stamp]:
    """The stamps a frontmatter block declares, in the order it declares them.

    ``promoted`` is the authority: a record id with no matching ``promoted_at`` entry is
    still a promotion, and a ``promoted_at`` with no ``promoted`` is not one. Pairing is
    positional, which is the price of both keys rendering as ordinary Obsidian list
    properties instead of as a nested object Obsidian cannot show.
    """
    ids: list[str] = []
    ats: list[str] = []
    for key, lines in _segments(fm_lines):
        if key == PROMOTED_KEY:
            ids = _values_in(lines)
        elif key == PROMOTED_AT_KEY:
            ats = _values_in(lines)
    return [
        Stamp(record_id, ats[index] if index < len(ats) else None)
        for index, record_id in enumerate(ids)
    ]


#: A frontmatter line that continues a block list — ``  - one``. The one shape that
#: makes a key's value a *list* rather than a scalar, and the shape :func:`_stamp_lines`
#: writes.
_ITEM_RE = re.compile(r"^\s*-(?:\s|$)")


def frontmatter_properties(text: str) -> dict[str, object]:
    """Every top-level frontmatter key an idea file declares, and its value.

    This function's subject is load-bearing because Obsidian Bases query
    **frontmatter properties**, and KYO-30 moved the promotion stamp into that block
    for exactly this reason. A Base over ideas selects on what comes back from here, so
    the properties it can ask about are *whatever the file states* — ``promoted`` and
    ``promoted_at`` alongside the human's own ``author:``, ``scope:``, ``area:``, with
    no key list anywhere in this tool deciding which are legitimate. Law 13: the pool
    has zero required ceremony, so a file with no frontmatter is an empty mapping and
    never an error.

    Three value shapes come back, and they are the three the block can hold:

    * a **list** for ``key: [a, b]`` and for a block list of ``- item`` lines — the
      shape ``promoted`` has had since KYO-30, read here through the same
      :func:`_values_in` the stamp reader uses so the two cannot disagree about what a
      list is;
    * a **string** for a one-line scalar, unquoted;
    * ``None`` for a key declared with no value at all — which Obsidian writes for an
      empty property, and which is a different fact from the key being absent.

    A key whose value is a nested *mapping* reads as ``None``: Obsidian's property pane
    neither writes nor shows one, and inventing a dotted sub-addressing for a shape the
    editor this layer exists for cannot produce would be a query grammar with no files
    behind it. The key is still reported, so it is visible rather than silently dropped.
    """
    fm_lines, _ = split_frontmatter(text)
    if fm_lines is None:
        return {}
    properties: dict[str, object] = {}
    for key, lines in _segments(fm_lines):
        if key is None:
            continue
        values = _values_in(lines)
        _, _, inline = lines[0].partition(":")
        is_list = bool(_FLOW_RE.match(inline.strip())) or any(
            _ITEM_RE.match(line) for line in lines[1:]
        )
        if is_list:
            properties[key] = values
        else:
            properties[key] = values[0] if values else None
    return properties


#: The pre-KYO-30 stamp: a trailing block appended at the *bottom* of the file, which
#: Obsidian never rendered. Still read (a consumer's ideas/ is full of them, and one
#: must not be offered for promotion a second time) and migrated by
#: `kyo ideas migrate-stamps`; never written. ``[^\S\n]`` rather than ``\s`` so a match
#: can never straddle a newline and mistake the block form for the inline one.
_LEGACY_STAMP_RE = re.compile(
    r"(?:^|\n)---\npromoted:[^\S\n]*(?P<record_id>\S+)\n"
    r"promoted_at:[^\S\n]*(?P<at>\S+)\n---[^\S\n]*\n?$"
)


def legacy_trailing_stamp(body: str) -> tuple[Stamp, str] | None:
    """``(stamp, the body with the trailing block removed)``, or ``None``.

    Takes the text *after* any frontmatter (see :func:`split_frontmatter`), never the
    whole file: a short file whose only content is a top-of-file stamp would otherwise
    satisfy an end-anchored pattern and be read as a bottom-stamped one.
    """
    match = _LEGACY_STAMP_RE.search(body)
    if match is None:
        return None
    return Stamp(match.group("record_id"), match.group("at")), body[: match.start()]


def promotion_stamps(text: str) -> list[Stamp]:
    """Every promotion an idea file declares — frontmatter first, legacy block second.

    Frontmatter wins outright when it names anything: a migrated file that a human then
    hand-appended something to must not have its stamp read twice.
    """
    fm_lines, body = split_frontmatter(text)
    stamps = _frontmatter_stamps(fm_lines or [])
    if stamps:
        return stamps
    found = legacy_trailing_stamp(body)
    return [found[0]] if found else []


def is_stamped(text: str) -> bool:
    return bool(promotion_stamps(text))


def _stamp_lines(stamps: list[Stamp]) -> list[str]:
    """The two property blocks, always as YAML block lists.

    Timestamps are quoted so YAML hands them back as the strings the tool wrote. Left
    bare, ``2026-08-25T17:00:00.000Z`` is a YAML *timestamp*, and every consumer that
    parses this file properly would get back a datetime whose text form is not the one
    in ``kyosei/export/`` (law 16 in the small: the stored fact and the displayed fact
    must be the same characters).
    """
    lines = [f"{PROMOTED_KEY}:"]
    lines.extend(f"  - {stamp.record_id}" for stamp in stamps)
    lines.append(f"{PROMOTED_AT_KEY}:")
    lines.extend(f'  - "{stamp.at}"' if stamp.at else '  - ""' for stamp in stamps)
    return lines


def _rewritten_frontmatter(fm_lines: list[str], stamps: list[Stamp]) -> list[str]:
    """``fm_lines`` with the stamp keys replaced in place and everything else verbatim.

    In place, not appended: a second promotion of the same file must produce a one-block
    diff, not move the human's properties around underneath them.
    """
    kept: list[str] = []
    placed = False
    for key, lines in _segments(fm_lines):
        if key == PROMOTED_KEY:
            if not placed:
                kept.extend(_stamp_lines(stamps))
                placed = True
        elif key != PROMOTED_AT_KEY:
            kept.extend(lines)
    if not placed:
        while kept and not kept[-1].strip():
            kept.pop()
        kept.extend(_stamp_lines(stamps))
    return kept


def _assemble(fm_lines: list[str] | None, body: str, stamps: list[Stamp]) -> str:
    if fm_lines is None:
        block = _stamp_lines(stamps)
        body = body.lstrip("\n")
        separator = "\n" if body else ""
    else:
        block = _rewritten_frontmatter(fm_lines, stamps)
        separator = ""
    fenced = "---\n" + "".join(f"{line}\n" for line in block) + "---\n"
    text = fenced + separator + body
    return text if text.endswith("\n") else text + "\n"


def apply_stamp(text: str, record_id: str, at: str) -> str:
    """Add a promotion to ``text``'s frontmatter, creating the block if there is none.

    Merges rather than clobbers, on both axes: other frontmatter keys survive, and an
    existing ``promoted`` list is appended to rather than replaced. Re-stamping the same
    record id is a no-op on the list, which is what makes migration and promotion safe
    to run over a file that already knows about the record.
    """
    fm_lines, body = split_frontmatter(text)
    stamps = _frontmatter_stamps(fm_lines or [])
    if not any(stamp.record_id == record_id for stamp in stamps):
        stamps = [*stamps, Stamp(record_id, at)]
    return _assemble(fm_lines, body, stamps)


def migrate_text(text: str) -> str | None:
    """The frontmatter form of a bottom-stamped file, or ``None`` if nothing to do.

    ``None`` is the whole idempotence guarantee (KYO-30.c5): a migrated file has no
    trailing block left to find, so a second run sees no work rather than stamping it
    twice. Nothing but the trailing block is removed and nothing but the two stamp keys
    is written, so a file a human has edited around comes back with their edits intact.
    """
    fm_lines, body = split_frontmatter(text)
    found = legacy_trailing_stamp(body)
    if found is None:
        return None
    legacy, stripped = found
    stamps = _frontmatter_stamps(fm_lines or [])
    if not any(stamp.record_id == legacy.record_id for stamp in stamps):
        stamps = [*stamps, legacy]
    return _assemble(fm_lines, stripped.rstrip("\n") + "\n", stamps)


def needs_stamp_migration(text: str) -> bool:
    return migrate_text(text) is not None


# ---------------------------------------------------------------------- the stub
#
# The stub design: promotion moves the prose **into** the store and leaves a pointer
# where it was (``kyo promote --keep`` since KYO-228). KYO-106 builds the whole-file half of that.
#
# The alternative this replaces is not neutral. `kyo promote` used to copy the text into
# the record's description and leave the same text in the file, which is two artifacts
# holding one claim with nothing linking them — the failure mode law 3 exists to prevent,
# and one observed in practice: files stamped under v1 carry a body that is a stale
# pre-amendment copy of a live record. The design rejected the other two exits
# for reasons that are not re-litigated here: locking the region in place (unenforceable —
# ``ideas/`` is openable by any editor, law 1), and deleting it outright (too lossy for the
# vault navigation the ideas layer exists for).
#
# What is left in the file is a stub, and the whole point of a stub is that it carries **no
# claim that can go stale** — only a pointer, whose breakage the existing broken-mention
# check already catches. The file survives at its path, so backlinks from the vault keep
# resolving, which is why nothing here rewrites any other file.
#
# This is the WHOLE-FILE half. KYO-107 adds the section half below (see "sections"), and it
# widens this rather than replacing it: the same stub block, written at a different scale.
# ``promoted`` was already a list (KYO-30) precisely so that one file could name one record
# per promoted section without a second stamp convention appearing beside this one.

#: The stub's second line: the pointer's payload, and deliberately not a summary. A
#: sentence that described the text would be a second copy of the claim, in miniature,
#: with the same drift the stub exists to end.
STUB_SENTENCE = "Text now lives in the record."


def promotion_stub(record_id: str, at: str) -> str:
    """The stub block for one promotion.

    A markdown blockquote, because it must read as an annotation rather than as the
    idea's own prose in every editor that opens ``ideas/`` — and because Obsidian renders
    ``#promoted`` and ``#{record_id}`` inside one as live tags, which is what makes a
    promoted idea findable from the vault side without Kyosei.

    ``at`` is the full stamp timestamp; the stub shows its date. The date is *derived*
    from the timestamp the same write stores (law 16) rather than formatted a second time
    from a clock, so the stub and the ``promoted_at`` property can never disagree.
    """
    return (
        f"> **Promoted -> {record_id}** · {at[:10]} · #promoted #{record_id}\n"
        f"> {STUB_SENTENCE}\n"
    )


def promoted_body(text: str) -> str:
    """The prose a whole-file promotion moves into the store, byte for byte.

    Everything after the frontmatter block, and nothing else is touched: no wrapping, no
    trimming inside the prose, no rewriting of the links it contains (those travel across
    with the copy). Only two things are dropped, and neither is prose — the frontmatter
    block itself, which is properties rather than text and stays in the idea file, and the
    blank lines separating that block from the body, which are the fence's punctuation.
    Trailing newlines are already gone: the caller freezes the file through
    :func:`prose.normalise` first, exactly as every other stored prose field is.

    KYO-109 gave it a second reader, and deliberately not a second implementation: a
    Base's ``contains:`` operator over the ideas source matches against exactly these
    bytes, so "the body of an idea file" is one answer here rather than one answer for
    promotion and a subtly different one for querying.
    """
    fm_lines, body = split_frontmatter(text)
    return body.lstrip("\n") if fm_lines is not None else body


def stub_text(text: str, record_id: str, at: str) -> str:
    """The idea file's new text: its frontmatter, stamped, over a stub body.

    The stamp is applied by :func:`apply_stamp`, so the merge guarantees hold unchanged —
    the human's other keys, their order and their comments survive, and ``promoted`` is
    appended to rather than replaced. What this adds is that the body the stamp sat on top
    of is *gone*, replaced by :func:`promotion_stub`, which is the half that makes the
    record the single home of the prose.

    Applied only at the moment of promoting. Nothing walks ``ideas/`` looking for bodies to
    replace: a file stamped before this behaviour existed keeps its text, and the two copies
    that already exist are v1's cost, not something a later run silently cleans up.
    """
    fm_lines, _ = split_frontmatter(apply_stamp(text, record_id, at))
    return _assemble(
        fm_lines, "\n" + promotion_stub(record_id, at), _frontmatter_stamps(fm_lines or [])
    )


# ------------------------------------------------------------------- sections
#
# KYO-107, the section half of the stub design: "a file holding several ideas survives
# promotion of one of them; only that section becomes a stub". The storage was ready for it —
# ``promoted`` has been a list since KYO-30 — so what this adds is the *addressing*, and
# the addressing is the whole design question.
#
# A section is addressed BY ITS HEADING, because that is what an Obsidian user can see and
# type: it is what the outline pane shows, what ``[[file#Heading]]`` links to, and what
# survives the file being reordered. The rejected alternatives were an index ("promote
# section 3", positional, and law 4 says ids are never positional), a line range (invalid
# the moment anything above it is edited), and a machine-written anchor id (invisible in
# the editor the ideas layer exists to be readable in, and a second convention to keep).
#
# The heading LINE STAYS IN THE FILE and only the prose beneath it is replaced by the stub.
# Three things make that the shape rather than replacing heading-and-all:
#
# * the heading is the anchor. ``[[file#Heading]]`` from elsewhere in the vault keeps
#   resolving, which is the same reason the whole-file stub keeps the file at its path;
# * a promoted section has to stay ADDRESSABLE, or the refusal KYO-107.c3 asks for —
#   promoting an already-stubbed section names the record its text is in — could never
#   fire, because the section it names would have ceased to exist;
# * it keeps the section case exactly parallel to the whole-file case, where the file's own
#   identifying furniture (its path, its frontmatter) stays behind and the prose moves. The
#   heading is a section's frontmatter.
#
# So no promoted text is left anywhere under ``ideas/``: the heading is a label, the stub is
# a pointer, and the prose is in the record.

#: An opening or closing code fence, indented by up to three spaces. Tracked because a
#: fenced block is prose, not structure, and a shell or Python comment inside one is not a
#: heading. The pre-KYO-107 heading scan had no notion of a fence at all.
_FENCE_LINE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")

#: An ATX heading — one to six ``#`` and then whitespace (CommonMark: ``#tag`` with no
#: space is not a heading, which matters because ``ideas/`` is an Obsidian vault full of
#: ``#tags``). Setext headings are deliberately not recognised: nothing in the ideas
#: trees this layer was built against writes them, and a half-supported second syntax
#: would make the refusal that
#: lists a file's sections a lie about the file.
_ATX_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<text>.*?))?[ \t]*$")

#: A closing run of hashes on an ATX heading — ``## Title ##``.
_CLOSING_HASHES_RE = re.compile(r"[ \t]+#+$")

#: The stub's own first line, read back. Derived from :func:`promotion_stub`'s shape and
#: asserted against it by a test, so recognising a stub and writing one cannot drift apart.
_STUB_RE = re.compile(r"^[ \t]*>[ \t]*\*\*Promoted -> (?P<record_id>[^*\s]+)\*\*", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    """One heading in an idea file, and the prose it owns.

    ``text`` is the section's prose with the heading line itself removed and the blank
    lines at either end trimmed — the bytes a promotion moves into the record. ``line`` is
    1-based and counted in the whole file (frontmatter included), because it exists to be
    printed in the refusal for two sections that share a heading, and a human reading that
    refusal is looking at the whole file in an editor.

    ``start``/``end`` are half-open line indices into the *body* (the text after any
    frontmatter), naming the lines the stub replaces. They are what makes the rewrite a
    splice rather than a re-render: everything outside ``[start, end)`` is carried through
    byte for byte.
    """

    heading: str
    level: int
    line: int
    text: str
    promoted_to: tuple[str, ...]
    start: int
    end: int

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


def _heading_text(raw: str) -> str:
    return flatten(_CLOSING_HASHES_RE.sub("", raw))


def _scan_headings(body: str) -> list[tuple[int, int, str]]:
    """``(line index, level, heading text)`` for every ATX heading outside a code fence."""
    found: list[tuple[int, int, str]] = []
    fence: str | None = None
    for index, line in enumerate(body.split("\n")):
        marker = _FENCE_LINE_RE.match(line)
        if marker:
            character = marker.group("fence")[0]
            if fence is None:
                fence = character
            elif fence == character:
                fence = None
            continue
        if fence is not None:
            continue
        heading = _ATX_RE.match(line)
        if heading:
            found.append(
                (index, len(heading.group("hashes")), _heading_text(heading.group("text") or ""))
            )
    return found


def stub_record_ids(text: str) -> list[str]:
    """Every record a promotion stub in ``text`` points at, in order."""
    return [match.group("record_id") for match in _STUB_RE.finditer(text)]


def sections(text: str) -> list[Section]:
    """The addressable sections of an idea file, in the order the file writes them.

    Frontmatter is excluded before anything is scanned, not filtered afterwards: a YAML
    comment (``# a comment of mine``) is a ``#`` at the start of a line and would otherwise
    read as a level-1 heading — which the pre-KYO-107 title default did, and this is why
    :func:`first_heading` is now derived from this function rather than from its own regex.

    A section ends at the next heading of the SAME OR HIGHER level, so a subsection travels
    with its parent: promoting "## An idea" takes the ``###`` parts of that idea with it,
    which is what a human means by promoting the idea.
    """
    fm_lines, body = split_frontmatter(text)
    offset = 0 if fm_lines is None else len(fm_lines) + 2
    lines = body.split("\n")
    headings = _scan_headings(body)
    found: list[Section] = []
    for position, (index, level, heading) in enumerate(headings):
        end = len(lines)
        for next_index, next_level, _ in headings[position + 1 :]:
            if next_level <= level:
                end = next_index
                break
        own = "\n".join(lines[index + 1 : end]).strip("\n")
        found.append(
            Section(
                heading=heading,
                level=level,
                line=offset + index + 1,
                text=own,
                promoted_to=tuple(stub_record_ids(own)),
                start=index + 1,
                end=end,
            )
        )
    return found


def sections_named(text: str, heading: str) -> list[Section]:
    """Every section whose heading reads as ``heading``.

    A list, not an Optional: two sections may share a heading text, and law 6 says the
    caller must be told that rather than handed whichever one came first.
    """
    wanted = flatten(heading)
    return [section for section in sections(text) if section.heading == wanted]


def _blank_run(lines: list[str]) -> int:
    count = 0
    for line in lines:
        if line.strip():
            break
        count += 1
    return count


def section_stub_text(text: str, section: Section, record_id: str, at: str) -> str:
    """``text`` with one section's prose replaced by its stub, and the stamp added.

    Two guarantees, and the implementation is shaped by both. *In place*: the stub goes
    exactly where the prose was, between the same blank lines, so the diff is one block and
    the file's order is unchanged. *Byte-identical elsewhere*: every line outside the
    section is carried across untouched, including the other sections, the heading of this
    one, and — via :func:`apply_stamp`'s surgical rewrite — every frontmatter key but the
    two the stamp owns.
    """
    fm_lines, body = split_frontmatter(text)
    lines = body.split("\n")
    inner = lines[section.start : section.end]
    lead = _blank_run(inner)
    trail = _blank_run(list(reversed(inner)))
    stub = promotion_stub(record_id, at).rstrip("\n").split("\n")
    # An all-blank section has no prose to promote and is refused before it reaches here;
    # the clamp is so that if one ever did, the stub would be inserted rather than the
    # blank lines duplicated around it.
    replaced = inner[:lead] + stub + inner[max(len(inner) - trail, lead) :]
    new_body = "\n".join(lines[: section.start] + replaced + lines[section.end :])
    if fm_lines is None:
        new_body = "\n" + new_body.lstrip("\n")
    stamped_fm, _ = split_frontmatter(apply_stamp(text, record_id, at))
    return _assemble(stamped_fm, new_body, _frontmatter_stamps(stamped_fm or []))


# ------------------------------------------------------------------- KYO-228: consume
#
# Promotion used to leave a stub; KYO-228 reversed that, because a kept copy of the
# promoted text had no remaining use once the record held it. The stub's
# reason was a future graph of ideas flowing to records, now judged a gimmick -- the
# promoted text already lives in the record with a ``Promoted from:`` line, and the
# store's ``promoted`` list on the idea is superseded by the record's own provenance.
#
# So this is now `kyo promote`'s DEFAULT: a whole-file promotion deletes the idea file
# outright (``cmd_records.cmd_promote``, no function here -- there is no text left to
# hand back), and a section promotion removes that section -- heading and all -- rather
# than replacing its prose with a pointer. :func:`section_stub_text` and
# :func:`promotion_stub` above are untouched and still exact: ``--keep`` reaches them
# for the pre-KYO-228 behaviour, byte for byte.


def _collapse_seam(lines: list[str], index: int) -> list[str]:
    """The blank-line run straddling ``index`` -- where a splice just closed a gap --
    collapsed to at most one blank line, or none if the seam now sits at either edge of
    ``lines``. Removing a whole section (rather than replacing it with a stub) can leave
    the blank line that led into it directly against the blank line that trailed out of
    it; this is what keeps that from reading as a double blank line in the survivor.
    """
    start = index
    while start > 0 and lines[start - 1] == "":
        start -= 1
    end = index
    while end < len(lines) and lines[end] == "":
        end += 1
    replacement = [] if start == 0 or end == len(lines) else [""]
    return lines[:start] + replacement + lines[end:]


def consumed_section_text(text: str, section: Section) -> str:
    """``text`` with one section -- its heading and every line of prose beneath it --
    removed outright. No stub, no frontmatter stamp: the section leaves no trace in the
    idea file at all, the same way a whole-file promotion now deletes the file.

    Every line outside the section, including the frontmatter block, is carried across
    byte for byte -- this never routes through :func:`apply_stamp` or :func:`_assemble`,
    so nothing here can add a ``promoted``/``promoted_at`` key whose *reason* KYO-228
    retired. Operating on the whole file's own lines (rather than
    reassembling from ``split_frontmatter``'s pieces) is what buys that: there is no
    frontmatter round-trip to drift from the bytes a human wrote.

    ``kyo promote``'s default; :func:`section_stub_text` is what ``--keep`` still uses.
    """
    fm_lines, _ = split_frontmatter(text)
    whole = text.split("\n")
    offset = 0 if fm_lines is None else len(fm_lines) + 2
    heading_index = offset + section.start - 1
    end_index = offset + section.end
    remaining = _collapse_seam(whole[:heading_index] + whole[end_index:], heading_index)
    new_text = "\n".join(remaining)
    return new_text if new_text.endswith("\n") else new_text + "\n"


# ---------------------------------------------------------------- title default


def first_heading(text: str) -> str | None:
    """`kyo promote --title`'s default: the idea's first top-level heading.

    Reads the same scan `--section` addresses by, so the tool has one notion of what a
    heading is. That is a behaviour change as well as a tidy-up: the regex this replaced
    searched the whole file, so a ``# a comment of mine`` in YAML frontmatter, or a ``#``
    comment inside a ```` ``` ```` block, became the record's title.
    """
    for section in sections(text):
        if section.level == 1:
            return section.heading
    return None


# --------------------------------------------------------------------------- age


def _format_utc(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _git_last_commit_at(root: Path, path: Path) -> datetime | None:
    """The last commit's author date for ``path``, or ``None`` if untracked/no repo.

    Law 16: never trust a hand-typed date — this asks git, the tool's own authority
    on a file's history, rather than reading anything a human wrote in the file.

    The git call itself is :func:`kyosei.git_queries.last_commit_at` since KYO-109,
    which needed the same question asked of a doc and of the files it ``governs:``.
    What stays here is the parse into a ``datetime``, which is this caller's need.
    """
    line = git_queries.last_commit_at(root, str(path.relative_to(root)))
    if line is None:
        return None
    try:
        return datetime.fromisoformat(line)
    except ValueError:
        return None


def last_activity_at(root: Path, path: Path) -> tuple[str, str]:
    """``(iso_timestamp, source)`` — ``source`` is ``git`` or the ``mtime`` fallback
    for untracked files (law 16: the tool derives this, never the idea's own prose).
    """
    commit_at = _git_last_commit_at(root, path)
    if commit_at is not None:
        return _format_utc(commit_at), "git"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return _format_utc(mtime), "mtime"


def age_days(reference: datetime, at_iso: str) -> int:
    at = datetime.strptime(at_iso, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    return max((reference - at).days, 0)


# ------------------------------------------------------------------- flagged mentions

#: A backticked token, allowing the one internal line break hard wrapping can put in it
#: (KYO-66). The pattern was ``` `([^`\n]+)` ``` — a code span that had to sit entirely on
#: one physical line — so a mention long enough to wrap, which is exactly the mention most
#: likely to be a real path, was invisible to the scan. `kyo ideas review` then reported no
#: broken mentions, which reads identically to "there are none". This repo's own
#: ``procedures.md`` wraps ``` `kyo ideas review` `` across a break in the rule that
#: describes this very command.
#:
#: Exactly one break is allowed, and it may not be a blank line. CommonMark pairs code-span
#: backticks across newlines, so this is also what the markdown *renders* as — but an
#: unbalanced backtick can now pair with one on the next line, where before it could pair
#: with nothing. Bounding it at a single non-blank continuation keeps that blast radius to
#: two adjacent lines rather than the rest of the file, and the token still has to look
#: path-like (:func:`_looks_path_like`) before anything is flagged.
_BACKTICK_RE = re.compile(r"`([^`\n]+(?:\n[^`\n]+)?)`")

#: A backticked token counts as "path-like" if it contains a path separator or ends
#: in a recognisable source/doc extension — the two shapes ``docs/design/charter.md``,
#: "Layer 1 — Ideas", and this module's docstring both describe as "file/symbol mentions".
_SOURCE_EXTENSIONS: tuple[str, ...] = (
    ".py", ".pyi", ".md", ".yml", ".yaml", ".json", ".toml", ".txt", ".sh",
    ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".ini", ".cfg",
)


def _looks_path_like(token: str) -> bool:
    if "/" in token:
        return True
    return any(token.endswith(ext) for ext in _SOURCE_EXTENSIONS)


#: A trailing editor/grep line reference — ``ideas.py:105``, ``ideas.py:105:12``.
#: KYO-33.c2: stripped only as a *fallback* (see :func:`_resolves`), never before the
#: path-like test, so this can only ever remove a flag that a real file answers for.
#: It cannot promote a token into being path-like, and it cannot silence a mention
#: whose stripped path is equally absent.
_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")


def _resolves(root: Path, path_part: str) -> bool:
    """Does ``path_part`` name something that exists under ``root``?

    KYO-33.c1: ``exists()``, not ``is_file()`` — a mention of a directory that is
    really there (``kyosei/export/``, ``ideas/``, ``docs/reference/``) is not a
    broken mention, and this repo's own docs and briefs use that notation constantly.

    KYO-33.c2: a line suffix is tried *second*, and only when the literal path did
    not resolve. A file genuinely named ``foo.py:105`` therefore still answers for
    itself before the suffix is ever stripped, which is why stripping digits off a
    real filename is unreachable here rather than merely unlikely.
    """
    if (root / path_part).exists():
        return True
    trimmed = _LINE_SUFFIX_RE.sub("", path_part)
    return trimmed != path_part and (root / trimmed).exists()


def flagged_mentions(root: Path, text: str) -> list[str]:
    """Backticked, path-like tokens in ``text`` that do not resolve against ``root``.

    Advisory only (law 12/13: the ideas layer is never enforced against) — this
    feeds ``kyo ideas review``'s human purge, and nothing else reads it. Its whole
    value is that a human trusts the list enough to act on it, so a routine false
    positive costs more here than a missed one: both directions are guarded by
    tests (KYO-33.c3), because both are one character apart in :func:`_resolves`.

    KYO-66: the token is whitespace-flattened before it is tested or reported, so a
    mention that hard-wrapped resolves against the same file its unwrapped twin does,
    and the flag names the token rather than the wrapping.
    """
    flagged: list[str] = []
    seen: set[str] = set()
    for raw in _BACKTICK_RE.findall(text):
        token = flatten(raw)
        if not token or token in seen or not _looks_path_like(token):
            continue
        seen.add(token)
        path_part = token.split("#", 1)[0]
        if not _resolves(root, path_part):
            flagged.append(token)
    return flagged
