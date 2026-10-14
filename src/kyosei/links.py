"""Typed edges and cycle refusal.

Law 6: *dependency writes refuse cycles at write time*. Not at doctor time — by
then the graph is already wrong and something has already read it.
"""

from __future__ import annotations

from collections import deque

from .contracts import ACYCLIC_LINK_TYPES
from .render import MISSING_TITLE
from .store import Store


def _adjacency(store: Store, edge_type: str) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for row in store.conn.execute(
        "SELECT src_id, dst_id FROM links WHERE edge_type = ? ORDER BY seq", (edge_type,)
    ):
        graph.setdefault(row["src_id"], []).append(row["dst_id"])
    return graph


def path_between(store: Store, edge_type: str, start: str, goal: str) -> list[str] | None:
    """Shortest existing path ``start -> ... -> goal`` over one edge type."""
    graph = _adjacency(store, edge_type)
    queue: deque[list[str]] = deque([[start]])
    seen = {start}
    while queue:
        path = queue.popleft()
        for nxt in graph.get(path[-1], ()):
            if nxt == goal:
                return [*path, nxt]
            if nxt not in seen:
                seen.add(nxt)
                queue.append([*path, nxt])
    return None


def cycle_if_added(store: Store, edge_type: str, src: str, dst: str) -> list[str] | None:
    """Return the cycle a new ``src -> dst`` edge would close, or ``None``.

    Only edge types in :data:`kyosei.contracts.ACYCLIC_LINK_TYPES` are checked;
    ``duplicates`` and ``same-question-as`` are legitimately mutual.
    """
    if edge_type not in ACYCLIC_LINK_TYPES:
        return None
    if src == dst:
        return [src, dst]
    back = path_between(store, edge_type, dst, src)
    return [src, *back] if back else None


def find_cycle(store: Store, edge_type: str) -> list[str] | None:
    """Find any existing cycle over one edge type (used by ``kyo doctor``)."""
    graph = _adjacency(store, edge_type)
    colour: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = 1
        stack.append(node)
        for nxt in graph.get(node, ()):
            if colour.get(nxt, 0) == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if colour.get(nxt, 0) == 0:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        colour[node] = 2
        return None

    for node in graph:
        if colour.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


# --------------------------------------------------------------------------- KYO-350

#: What ``src EDGE_TYPE dst`` means, in one sentence, for the two records. A pair of
#: ids and an edge type ("APP-57 blocks APP-58") reads as well-formed whichever way
#: round it is written, which is how a consumer got the direction backwards and found
#: out only on re-reading — they had piped `kyo link`'s output through `tail -1`, so
#: the one line they saw was the id pair, not a statement of consequence. Every entry
#: is keyed on ``src``/``dst`` exactly as the edge is stored, so getting the sentence
#: right is inseparable from getting the direction right: reversing the call reverses
#: the words, not just the ids.
#:
#: Every member of :data:`kyosei.contracts.LINK_TYPES` needs an entry here —
#: ``tests/test_links.py`` fails the day a type is added and this dict is not.
EDGE_CONSEQUENCE: dict[str, str] = {
    # KYO-570: says what is enforced. `kyo next` and the brief withhold {dst} and
    # `kyo dispatch` refuses it (without --force) until {src} is settled on the ladder.
    "blocks": "{dst} is not offered by `kyo next` or dispatched until {src} settles",
    "duplicates": "{src} and {dst} are the same work; finishing one finishes the other",
    "supersedes": "{dst} is superseded by {src} and no longer needs its own resolution",
    "same-question-as": "{src} and {dst} ask the same question; answering one answers both",
    "needs-same-change-as": "whatever change resolves {src} must also be made to {dst}",
    "produced-by": "{src} was produced by {dst}, and traces back to it",
}


def _named(record_id: str, title: str | None) -> str:
    return f"{record_id} {MISSING_TITLE}" if title is None else f'{record_id} "{title}"'


def edge_consequence(
    edge_type: str,
    src_id: str,
    src_title: str | None,
    dst_id: str,
    dst_title: str | None,
) -> str:
    """``src edge_type dst``, said as a sentence of consequence rather than a pair
    (KYO-350).

    ``kyo link``'s existing echo already showed both titles, stacked vertically —
    and a caller who read only the last line of that still saw nothing but a title,
    because the words that say WHAT the edge means were above it. This is the
    sentence meant to be read alone: both ids, both titles, and the one fact a
    reversed direction gets wrong.
    """
    template = EDGE_CONSEQUENCE[edge_type]
    return (
        template.format(
            src=_named(src_id, src_title),
            dst=_named(dst_id, dst_title),
        )
        + "."
    )
