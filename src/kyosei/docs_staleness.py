"""Whether a document was touched since the code it governs was, and one document as
a query row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import git_queries
from .docs_parse import Document, GovernsTarget, split_target
from .docs_targets import broken_governs_targets

#: The document has not changed since everything it governs last changed.
STALENESS_CURRENT = "current"
#: Something the document governs changed after the document itself last did. This is
#: the state the `touches x governs` banner is drawn for.
STALENESS_STALE = "stale"
#: The document declares no ``governs:`` target, so it is not in the relation at all.
#: Named rather than folded into ``current``: "nothing governs-stale about this file"
#: and "this file makes no claim about any code" are different facts, and painting the
#: second as the first is a banner asserting currency from no evidence.
STALENESS_UNGOVERNED = "ungoverned"
#: Git could not answer. A third state, so that git's silence is never read as
#: currency or staleness, and the same shape
#: :data:`kyosei.currency.CURRENCY_UNDECIDABLE` carries for the db/export seam: never
#: resolved by guessing, always carrying why.
STALENESS_CANNOT_TELL = "cannot-tell"

STALENESS_STATES: tuple[str, ...] = (
    STALENESS_CURRENT,
    STALENESS_STALE,
    STALENESS_UNGOVERNED,
    STALENESS_CANNOT_TELL,
)


def staleness_answered(state: str) -> bool:
    """Whether ``state`` means the question was actually answered for a governed doc.

    A whitelist of exactly two values, for the reason
    :func:`kyosei.sweep.staleness_answered` gives for its whitelist of one: the
    dangerous failure here is an ABSENT flag, so every state added to this vocabulary
    is unanswered until someone decides otherwise.
    """
    return state in (STALENESS_CURRENT, STALENESS_STALE)


@dataclass(frozen=True)
class Staleness:
    """Whether a document is current with what it governs, and how that was decided.

    ``says`` is filled in every state, including the two decided ones. A banner that
    can show "stale" with a reason and "current" with nothing behind it invites the
    reader to trust the clean case more than the evidence supports — and the evidence
    is exactly what these renderings must be able to show.
    """

    state: str
    says: str
    #: The governed paths that changed after the document last did, in declared order.
    changed: tuple[str, ...] = ()


#: How a governed path's last-change date is looked up: ``(root, relative path) ->``
#: an ISO-8601 string, or ``None`` for "git could not answer".
DateLookup = Callable[[Path, str], "str | None"]


def _dated_paths(target: str | GovernsTarget) -> tuple[str, ...]:
    """The root-relative path(s) git should be asked to date for one target.

    The one place a target becomes a path for a *mechanism* rather than for a reader,
    and it takes both currencies on purpose (KYO-200). A :class:`GovernsTarget` hands
    over its resolved paths, base-substituted and possibly plural — a bare symbol
    governs every file it was found in. A plain string is split with
    :func:`split_target`, which is the pre-KYO-200 behaviour plus the colon separator,
    and is what a direct-API caller that never resolved anything gets.

    An unresolved target contributes no path at all rather than the path it wished it
    had: dating a file that is not there answers nothing, and ``kyo docs check`` is
    already failing the build over it by name.
    """
    if isinstance(target, GovernsTarget):
        return target.paths
    path_part = split_target(target)[0]
    return (path_part,) if path_part else ()


def _instant(stamp: str | None) -> datetime | None:
    """A git ``%cI`` date as an aware instant, or ``None`` when there is none to read.

    KYO-412. An unparseable stamp is ``None`` — undatable, reported as such —
    rather than a guess, and a naive one is read as UTC so it still compares.
    """
    if stamp is None:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def governs_staleness(
    root: Path,
    doc_path: str,
    targets: Sequence[str | GovernsTarget],
    *,
    commit_at: DateLookup | None = None,
) -> Staleness:
    """Is ``doc_path`` current with the code it ``governs:``? (KYO-109.c5)

    Git is the only authority consulted, and its silence is reported rather than
    replaced. The obvious fallback — comparing filesystem mtimes, which
    :func:`kyosei.ideas.last_activity_at` legitimately uses for an *untracked* idea —
    is wrong here and quietly so: a fresh clone stamps every file with the checkout
    time, so an mtime comparison would paint every governed document "current" the
    first time anyone ran it on a new machine. That is precisely the absent-flag
    failure, a clean answer given on no evidence, so a repository git cannot answer for
    lands on :data:`STALENESS_CANNOT_TELL` and says which file it could not date.

    A target whose ``#symbol`` half is present is compared by its *path* half: symbol
    resolution is :func:`_resolve_target`'s job, and git dates files.

    ``commit_at`` is the date lookup, defaulting to
    :func:`kyosei.git_queries.last_commit_at`. It is a parameter so one run over many
    documents can memoise it — the same governed path is named by several docs, and a
    subprocess per naming would make this the expensive part of a query.
    """
    lookup = git_queries.last_commit_at if commit_at is None else commit_at
    if not targets:
        return Staleness(
            STALENESS_UNGOVERNED,
            f"{doc_path} declares no governs: target, so nothing relates it to the code",
        )
    doc_at = lookup(root, doc_path)
    doc_instant = _instant(doc_at)
    if doc_at is None or doc_instant is None:
        return Staleness(
            STALENESS_CANNOT_TELL,
            f"git could not date {doc_path} (no repository here, no commits, or the "
            f"file is untracked), so whether the code it governs moved since cannot be "
            f"decided — not that it did not",
        )
    changed: list[str] = []
    undatable: list[str] = []
    seen: set[str] = set()
    for target in targets:
        for path_part in _dated_paths(target):
            # Deduplicated across targets, not only within one: a bare symbol resolves
            # to every file carrying it, and two symbols from one section routinely land
            # on the same file. Naming that file twice in the message would make one
            # changed path read as two.
            if path_part in seen:
                continue
            seen.add(path_part)
            target_at = _instant(lookup(root, path_part))
            if target_at is None:
                undatable.append(path_part)
            elif target_at > doc_instant:
                # KYO-412: instants, not the `%cI` strings. Each string carries
                # its committer's own UTC offset, so a code commit at 12:00-07:00
                # (19:00Z) sorted before a doc commit at 18:00+00:00 and the doc read
                # `current` although the code moved an hour after it.
                changed.append(path_part)
    if changed:
        return Staleness(
            STALENESS_STALE,
            f"{doc_path} was last committed {doc_at}, and {len(changed)} path(s) it "
            f"governs changed after that: {', '.join(changed)}",
            tuple(changed),
        )
    if undatable:
        return Staleness(
            STALENESS_CANNOT_TELL,
            f"{doc_path} was last committed {doc_at}, but git could not date "
            f"{len(undatable)} path(s) it governs ({', '.join(undatable)}) — untracked, "
            f"or not in this repository's history — so no comparison was possible for "
            f"them and none of the rest went stale",
        )
    return Staleness(
        STALENESS_CURRENT,
        f"{doc_path} was last committed {doc_at}, at or after every one of the "
        f"{len(seen)} path(s) it governs",
    )


# ------------------------------------------------- KYO-139: one document, as a row
#
# The governance facts about a document — what it governs, which of those claims no
# longer resolve, and how current it is — are the same facts whoever is asking. Two
# callers ask: :func:`kyosei.query._docs_rows`, behind a Base declaring ``source:
# docs``, and ``kyo docs check --json``, which is the surface with no Base to declare
# (KYO-139). Spelled once here so the two cannot drift, for the same reason
# :func:`broken_governs_targets` exists rather than each caller re-resolving targets.
#
# Deliberately NOT the whole query row: ``properties`` is the ideas layer's frontmatter
# reader and ``sections`` is a per-caller shape, so both stay with their callers. What
# is shared is what would otherwise be a second answer to one question.


def document_row(
    root: Path, document: Document, *, commit_at: DateLookup | None = None
) -> dict:
    """``document``'s identity and governance state, as a row.

    ``commit_at`` is threaded through to :func:`governs_staleness` so a caller running
    over a whole tree can memoise git's date lookups: the same governed path is named
    by several documents, and a subprocess per naming would make dating the tree the
    expensive half of reading it.
    """
    governs = document.governs
    # KYO-200: the row renders what the document wrote (`governs`) and reasons over what
    # that resolved to (`resolved`). Both come from the one parse the caller already
    # paid for, so nothing here re-splits a target or re-reads a file — which is also
    # what keeps this row's "broken" set identical to the one `check_docs` failed on,
    # bases included, rather than identical-looking.
    #
    # KYO-204: the FALLBACK below reaches for `document.live_governs`, not `governs` —
    # a dead section's targets were never resolved (`resolved` is correctly silent about
    # them), but falling back to the unfiltered `governs` on an empty `resolved` would
    # reintroduce them by a different door, the one live_governs's own docstring names.
    resolved = document.resolved
    staleness = governs_staleness(
        root, document.path, resolved or document.live_governs, commit_at=commit_at
    )
    broken = (
        [target.raw for target in resolved if target.reason is not None]
        if resolved
        else broken_governs_targets(root, document.live_governs)
    )
    return {
        "id": document.path,
        "title": document.title,
        "section_count": sum(1 for section in document.sections if section.level),
        "governs": governs,
        "governs_broken": broken,
        "amended": any(section.metadata["amended-by"] for section in document.sections),
        "staleness": staleness.state,
        "staleness_says": staleness.says,
    }


def section_rows(document: Document) -> list[dict]:
    """Every heading in ``document`` with the metadata its body declares.

    The preamble — content before the first heading, ``level`` 0 — is carried like any
    other section: it is where a document's opening ``governs:`` line often lives, and
    dropping it would hide a real claim from every reader of this shape.

    ``dead`` is :func:`section_is_dead` over the same metadata (KYO-204): the same
    status field is what a section index line should lead with, since a line such as
    '§11 SUPERSEDED → 177' is the most useful thing a worker can see before opening a
    section. This is that field's reader; an index rendering is not built.
    """
    return [
        {
            "heading": section.heading,
            "level": section.level,
            "governs": section.governs,
            "status": section.metadata["status"],
            "amended_by": section.metadata["amended-by"],
            "superseded_by": section.metadata["superseded-by"],
            "dead": section.dead,
        }
        for section in document.sections
    ]


def memoised_commit_at() -> DateLookup:
    """:func:`kyosei.git_queries.last_commit_at`, asked once per distinct path."""
    dates: dict[str, str | None] = {}

    def commit_at(where: Path, relative: str) -> str | None:
        if relative not in dates:
            dates[relative] = git_queries.last_commit_at(where, relative)
        return dates[relative]

    return commit_at


__all__ = [
    "STALENESS_CANNOT_TELL",
    "STALENESS_CURRENT",
    "STALENESS_STALE",
    "STALENESS_STATES",
    "STALENESS_UNGOVERNED",
    "Staleness",
    "document_row",
    "governs_staleness",
    "memoised_commit_at",
    "section_rows",
    "staleness_answered",
]
