"""The one query over records, ideas and docs — shared by ``kyo list`` and every Base.

KYO-45 decided that the board, the status view, the bubbles and the typed-edge graph
are four *renderings* of one saved query, and observed in the same breath that
``kyo list --json`` "is already most of the engine". KYO-108's load-bearing criterion
(c4) is the consequence: a Base must not become a second query path. If it were one,
the two would drift the way any two copies of a query drift — a filter added to
``kyo list`` and not to Bases, a status rule fixed on one side only — and the drift
would be invisible, because both surfaces would keep answering.

So this module is the query, and both surfaces are callers of it:

* :func:`validate` holds every law-6 refusal a filter value can earn. ``kyo list``
  raises them from its flags; a Base raises them at *load* time against its own file,
  which is why they are stated once here rather than twice in two dialects.
* :func:`run` holds the SQL, the open-gate pass, the standing computation and the
  counted/not-counted split. It also runs the db/export currency comparison, because
  every read surface built on it owes its reader that answer (KYO-74).

**The filter set is a data structure, not a list in two places.** :class:`Filters`
declares the filters once; ``kyo list``'s flags are checked against
:data:`FILTER_NAMES` by a test, and a Base's ``filters:`` keys are *derived* from it.
That is what makes "no filter is available to one surface and not the other" a
mechanical property rather than a promise — adding a filter to one side and not the
other fails a test rather than shipping.

**Three sources, one engine, one result shape** (KYO-109). KYO-45 makes a Base
runnable "over records, ideas, or docs". The three read three different things — a
database, a pool of markdown files with zero required ceremony, a governed docs tree —
and they return the same :class:`Result`, so a renderer consuming ``--json`` special-
cases none of them. What differs per source is the *field set* (records carry ids and
statuses; ideas and docs carry file paths and frontmatter properties); what is
identical is the envelope: rows, the columns/sort a Base declared, the grouping, the
counted split, and the db/export currency direction. Each source is a :class:`Source`
value in :data:`SOURCES`; a fourth would be another one, and no second engine.

**The body-prose operator, and why it is the only one** (a binding decision). A file
source's ``contains:`` filter matches through :func:`kyosei.prose.contains_phrase`,
which is :func:`kyosei.prose.phrase_pattern` compiled: the needle is split on
whitespace and rejoined with a whitespace gap, so a phrase that hard-wrapped in the
haystack still matches. That normalisation is on both sides — the needle's own runs of
whitespace are collapsed by the same split. There is exactly ONE such operator, here,
for this reason: v1 had four matchers over stored prose and all four
under-matched in silence until KYO-66. Frontmatter and section metadata are one-line
*values* and are matched as values, never through the prose path.

**What deliberately is NOT here.** No computed columns, no joins, and no operators
beyond the three each source declares — a records filter is exactly the question
``kyo list`` can already ask, and a file-source filter is a glob over the path, a
phrase in the body, or equality against a field the file states.
"""

from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from fnmatch import fnmatch
from typing import Any

from . import currency as currency_module
from . import docs_parse, docs_staleness, docs_targets, prose
from . import ideas as ideas_module
from . import registry as registry_module
from .commands import Context, _load_registry, _load_status_ladder, _require_plan
from .contracts import KNOWLEDGE_STANDINGS, standing
from .errors import KyoseiError, UsageError
from .store import Store


@dataclass(frozen=True)
class Filters:
    """Every way the records source can be narrowed. One declaration, two surfaces.

    Each field is a value to match exactly, or ``None`` for "do not narrow on this".
    The field *names* are the contract: :data:`FILTER_NAMES` is read off this class,
    ``kyo list``'s flags are ``--<name>`` with underscores hyphenated, and a Base's
    ``filters:`` mapping uses the same hyphenated keys. Nothing anywhere writes the
    set of filters down a second time.
    """

    kind: str | None = None
    scope: str | None = None
    #: KYO-420.c4: a tuple when ``kyo list --status`` was repeated — a row matches any
    #: of them. A single value is still the plain string it always was, which is what a
    #: Base's ``status:`` declares (bases.yml values are strings) and what
    #: :meth:`as_data` reports back, so neither surface sees a new shape for one value.
    status: str | tuple[str, ...] | None = None
    plan: str | None = None
    gate_tag: str | None = None

    @classmethod
    def from_args(cls, args: Namespace) -> Filters:
        """The filters one ``kyo list`` invocation asked for.

        ``--status`` is repeatable (``action="append"``), so it arrives as a list: one
        value becomes the plain string a Base would declare, several become a tuple,
        with repeats dropped. A Namespace built by hand with a string is taken as is.
        """
        values = {name: getattr(args, name, None) for name in FILTER_NAMES}
        status = values["status"]
        if isinstance(status, list):
            unique = tuple(dict.fromkeys(status))
            values["status"] = unique[0] if len(unique) == 1 else unique
        return cls(**values)

    @property
    def statuses(self) -> tuple[str, ...]:
        """The status filter as a tuple, whichever shape it was given in; ``()`` when
        no status was asked for."""
        if self.status is None:
            return ()
        return (self.status,) if isinstance(self.status, str) else tuple(self.status)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> Filters:
        """The filters a Base definition declares, keyed as its file spells them.

        Unknown keys are refused by the Base loader against :data:`FILTER_KEYS`
        before this is called; reaching here with one is a programming error, so it
        raises rather than being quietly dropped.
        """
        unknown = sorted(set(mapping) - set(FILTER_KEYS))
        if unknown:  # pragma: no cover - bases.py refuses these on load
            raise KyoseiError(f"unknown filter key(s): {', '.join(unknown)}")
        return cls(**{name: mapping.get(filter_key(name)) for name in FILTER_NAMES})

    def as_data(self) -> dict:
        """The filters that were actually applied, keyed the way a Base spells them.

        Only the ones that narrowed anything: a payload listing every filter with
        ``null`` would read as "five filters were applied", which is the opposite of
        true. A renderer showing "filtered by" wants the ones that are set.
        """
        return {
            filter_key(name): list(value) if isinstance(value, tuple) else value
            for name in FILTER_NAMES
            if (value := getattr(self, name)) is not None
        }


#: The filter names, read off :class:`Filters` rather than listed (law 3).
FILTER_NAMES: tuple[str, ...] = tuple(f.name for f in fields(Filters))

#: KYO-420.c4: ``kyo list``'s "not yet settled" flag. Not ``--active``: ``active`` is a
#: ROLE name in statuses.yml (the ``working`` status carries it), and a flag of that
#: name would read as "role active only".
UNSETTLED_FLAG = "--unsettled"

#: The ``kyo list`` flags that narrow the listing and are NOT :class:`Filters` fields,
#: so a Base cannot declare them. Named here because the parity test between the two
#: surfaces (``tests/test_bases.py``) must exclude exactly these and nothing else.
#:
#: ``--limit`` is not a filter at all: it says how many of the matching rows to print,
#: which no saved question needs. ``--unsettled`` is a filter, and keeping it off the
#: Base surface is a deliberate gap rather than an oversight: a Base's ``filters:``
#: values are strings, so it would need a value vocabulary of its own, and adding the
#: key would change the refusal every mistyped Base filter prints today — a default
#: output KYO-420 was ruled not to move. For the same reason a Base's ``status:``
#: still takes one string, not the list a repeated ``--status`` builds. Widening the
#: Base surface to both is left to a record of its own.
LIST_ONLY_FLAGS: tuple[str, ...] = (UNSETTLED_FLAG, "--limit")


def filter_key(name: str) -> str:
    """How a filter is spelled in a Base's ``filters:`` mapping — ``gate-tag``."""
    return name.replace("_", "-")


def filter_flag(name: str) -> str:
    """How a filter is spelled as a ``kyo list`` flag — ``--gate-tag``."""
    return f"--{filter_key(name)}"


#: Every filter key a Base's ``filters:`` mapping may carry, in declaration order.
FILTER_KEYS: tuple[str, ...] = tuple(filter_key(name) for name in FILTER_NAMES)

#: Every field a row carries, in the order ``kyo list`` prints them. This is the
#: field set a Base's ``columns:``, ``sort:`` and ``group-by:`` are validated against
#: — deliberately the row ``kyo list --json`` already produces and not one field more
#: (law 2: a column a renderer can ask for is a field the engine must produce, and
#: widening the row is the renderings' need to state in step 5, not this task's guess).
ROW_FIELDS: tuple[str, ...] = (
    "id",
    "seq",
    "kind",
    "scope",
    "status",
    "standing",
    "gates_open",
    "gate_tags",
    "gate_types",
    "title",
    "counted",
    # KYO-467: the app's records list sorts by Created and Modified with no Base
    # selected, which is `kyo list --json` -- the renderings' need, stated (law 2).
    "created_at",
    "updated_at",
)

#: The subset a ``sort:`` or ``group-by:`` may name. ``gate_tags`` and ``gate_types``
#: are lists — a row carrying two tags or two open-gate types belongs to two groups
#: and has no single sort key — so they are shown and filtered on and never grouped
#: by. Refused loudly rather than silently stringified, which would group
#: ``[ui, human]`` apart from ``[human, ui]``.
SCALAR_ROW_FIELDS: tuple[str, ...] = tuple(
    f for f in ROW_FIELDS if f not in ("gate_tags", "gate_types")
)

#: The order rows come back in when a Base declares no ``sort:`` — record sequence,
#: which is the order ``kyo list`` has always printed. Named rather than left implicit
#: so a run's ``sort`` is never the empty list a renderer cannot interpret.
DEFAULT_SORT: tuple[str, ...] = ("seq",)


@dataclass(frozen=True)
class Result:
    """One run of the engine: the rows, how they split, and the state of the store."""

    rows: list[dict] = field(default_factory=list)
    counted: int = 0
    not_counted: int = 0
    #: KYO-74. Computed here rather than by each caller so that one run
    #: costs one comparison however many surfaces render it — and so that no read
    #: surface built on this engine can forget to ask.
    currency: currency_module.Currency = field(
        default_factory=lambda: currency_module.Currency(currency_module.CURRENCY_IN_STEP)
    )

    @property
    def summary(self) -> dict:
        return {
            "total": len(self.rows),
            "counted": self.counted,
            "not_counted": self.not_counted,
        }


def unknown_status_refusal(ladder, value: str) -> str:
    """Why ``value`` is not something either surface can filter on.

    Named once because two surfaces refuse it — ``kyo list --status`` when the flag is
    typed and a Base when its file is read — and law 3 does not make an exception for
    the sentence being short. It is the counterpart to
    :func:`kyosei.registry.unknown_name_refusal`, which the other three filters share
    with every write path; statuses need their own because the askable words are the
    ladder *plus* the two knowledge standings, which is a set no registry holds.
    """
    return (
        f"{value!r} is not a status; {ladder.registry.path} declares: "
        f"{ladder.listing()} "
        f"(or {', '.join(KNOWLEDGE_STANDINGS)} for knowledge-archetype records)"
    )


def is_askable_status(ladder, value: str) -> bool:
    """Is ``value`` a word either surface's status filter accepts?

    KYO-44.c2 and KYO-99 in one predicate: this project's declared ladder, plus the two
    standings a knowledge record's status *reads* as. Nothing here compares against a
    status name frozen into the code, so a renamed ladder stays filterable.
    """
    return value in KNOWLEDGE_STANDINGS or value in ladder


def validate(store: Store, filters: Filters) -> None:
    """Refuse every filter value the project's own registries do not declare.

    Law 6, five times over, and in one place so that a Base and a flag are refused
    with the same sentence. An unknown value is never answered with an empty listing:
    "nothing is in that scope" and "there is no such scope" are different facts and a
    reader cannot tell them apart from zero rows.
    """
    registry = _load_registry(store)
    if filters.kind is not None and filters.kind not in registry:
        raise KyoseiError(
            f"{filters.kind!r} is not a known kind; known kinds: {', '.join(registry.names)}"
        )
    # KYO-44.c2: `current`/`void` are what a knowledge record's status *reads* as, so
    # they must also be things you can ask for — a listing that shows a word no filter
    # accepts is a dead end (law 6).
    #
    # KYO-99: and the converse, which is the defect. `--status` used to be TWO filters —
    # the two standings matched against the derived word, every other value pushed into
    # SQL against the stored column — so `--status open` answered from the side of the
    # archetype mapping the display does not use, and returned every knowledge record
    # ever filed (a knowledge record still stores the initial status name in the shared
    # column; KYO-101.r1 settles that it keeps doing so). Nine of twelve rows in this
    # project's own `--status open` were decisions, each of them *rendered* `current` in
    # the very listing that matched them on `open`. The question a controller asks with
    # that flag is "what work is open", and the answer disagreed with its own output.
    #
    # There is now one filter and it runs where the word is computed (in `run` below),
    # against the same `standing()` the row prints. Nothing about the stored values
    # changes, and nothing here compares against a status name this module knows: the
    # askable words are this project's ladder plus the two standings, and the mapping
    # that produces `void` is the ladder's `retired` role, so a renamed ladder stays
    # filterable.
    ladder = _load_status_ladder(store)
    # KYO-420.c4: every value of a repeated `--status` is held to the same rule, and
    # the first undeclared one is refused in the same sentence a single one gets.
    for asked in filters.statuses:
        if not is_askable_status(ladder, asked):
            raise UsageError(unknown_status_refusal(ladder, asked))
    # KYO-27: `plan` is the one kind whose completion is *defined*
    # as a status distribution over its members, and membership is stored on the
    # MEMBER (`plan_id`), so nothing walked the relation the other way. Reading a
    # plan meant globbing kyosei/export/. Law 6: an id that does not exist is an
    # error, so a plan naming nothing fails loudly rather than listing zero rows.
    #
    # A Base's `plan:` filter is checked here and not at load time on purpose: it names
    # a record id, not a registry entry, and a record can be redacted after the file was
    # written. The registries a Base *is* checked against on load are closed vocabularies
    # the project declares; the record set is not one.
    #
    # KYO-271: routed through the same `_require_plan` that `kyo new --plan` and
    # `kyo edit --plan` use since KYO-237, rather than the generic `_require_record`,
    # so an empty, unknown, or non-plan id gets the same exit 2 and the same
    # three-way message shape here as it does at those two write sites.
    if filters.plan is not None:
        _require_plan(store, filters.plan)
    # KYO-100.c2: a filter that selects by scope, and the same law-6 rule the kind and
    # status filters already follow. `require` is the registry's own refusal
    # (registry.unknown_name_refusal), so the sentence a filter gets is the sentence a
    # write gets.
    #
    # Both registries are LOADED whether or not their filter was asked for, which is the
    # behaviour `kyo list` has had since KYO-100/KYO-102 and is deliberately preserved:
    # a malformed scopes.yml or gate-tags.yml is refused by the read surface with every
    # problem in the file at once, rather than staying invisible until the day someone
    # happens to pass the flag it breaks.
    scopes = registry_module.load_scope_registry(store.config.kyosei_dir)
    if filters.scope is not None:
        scopes.require(filters.scope)
    # KYO-102.c2, law 2: the query the free-text payload could never answer. KYO-46's
    # worked example is "everything waiting on a human to review a UI element", and this
    # is where it is asked.
    gate_tags = registry_module.load_gate_tag_registry(store.config.kyosei_dir)
    if filters.gate_tag is not None:
        gate_tags.require(filters.gate_tag)


def _open_gates(
    store: Store,
) -> tuple[dict[str, list[str]], dict[str, int], dict[str, list[str]]]:
    """``({record: tags on its open gates}, {record: how many are open},
    {record: types of its open gates})``.

    OPEN gates only, and that is the whole meaning of the tag filter: a cleared gate is
    something this record *was* waiting on, and listing it under "what is waiting on a
    UI review" would answer a question about the past with a row about the present.
    Read in one query rather than per record.

    KYO-203.c1: ``gate_types`` is the distinct types of a record's open gates, in the
    order those gates were created (``ORDER BY record_id, seq``, the same order
    ``gate_tags`` already carries) — a consumer needs to tell ``awaiting:event`` from
    ``awaiting:human-review`` without opening the record.
    """
    tags: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    types: dict[str, list[str]] = {}
    for gate_row in store.conn.execute(
        "SELECT record_id, gate_type, tags_json FROM gates WHERE cleared_at IS NULL "
        "ORDER BY record_id, seq"
    ):
        record = gate_row["record_id"]
        counts[record] = counts.get(record, 0) + 1
        carried = json.loads(gate_row["tags_json"]) if gate_row["tags_json"] else []
        for tag in carried:
            if tag not in tags.setdefault(record, []):
                tags[record].append(tag)
        gate_type = gate_row["gate_type"]
        record_types = types.setdefault(record, [])
        if gate_type not in record_types:
            record_types.append(gate_type)
    return tags, counts, types


def links_by_record(store: Store, record_ids: list[str]) -> dict[str, list[dict]]:
    """Every typed edge touching ``record_ids``, indexed by record.

    KYO-45's amendment makes the typed-edge graph the fourth rendering, "conditional on
    edge-type filtering" — so the renderer needs the edge *types* per row, and needs
    them from the same pass that produced the rows. A graph view that re-queried per
    node would be the second query path c4 exists to prevent, one level down.

    Both directions, matching ``export.record_document``'s shape (``direction`` /
    ``type`` / ``other``) so a consumer that has read a record document already knows
    this structure. One query over the whole edge set rather than two per row.
    """
    wanted = set(record_ids)
    out: dict[str, list[dict]] = {record_id: [] for record_id in record_ids}
    for row in store.conn.execute(
        "SELECT src_id, edge_type, dst_id FROM links ORDER BY seq"
    ):
        if row["src_id"] in wanted:
            out[row["src_id"]].append(
                {"direction": "outgoing", "type": row["edge_type"], "other": row["dst_id"]}
            )
        if row["dst_id"] in wanted:
            out[row["dst_id"]].append(
                {"direction": "incoming", "type": row["edge_type"], "other": row["src_id"]}
            )
    return out


def run(store: Store, filters: Filters, *, unsettled: bool = False) -> Result:
    """The records matching ``filters``, in record-sequence order.

    ``unsettled`` (KYO-420.c4, ``kyo list --unsettled``) additionally drops every
    record whose STORED status has the ``complete`` or ``retired`` role — the ladder's
    own :meth:`~kyosei.contracts.StatusLadder.is_settled`, which ``kyo sweep`` and
    ``kyo next`` already read "still in flight" from. A keyword rather than a
    :class:`Filters` field for the reason :data:`LIST_ONLY_FLAGS` gives.

    ``validate`` is the caller's to run first — separated because the two surfaces
    refuse at different moments (a flag when it is typed, a Base when its file is
    loaded) and because a caller that has already validated must not pay for it twice.
    """
    registry = _load_registry(store)
    ladder = _load_status_ladder(store)
    gate_tags, gate_counts, gate_types = _open_gates(store)

    query = "SELECT * FROM records"
    conditions: list[str] = []
    params: list[str] = []
    if filters.kind is not None:
        conditions.append("kind = ?")
        params.append(filters.kind)
    if filters.scope is not None:
        conditions.append("scope = ?")
        params.append(filters.scope)
    # KYO-99: no `status = ?` condition here on purpose. The stored column is the wrong
    # side of the archetype mapping to ask, and a prefilter that is *usually* a superset
    # of the answer is a second, subtler argument to keep true every time either the
    # ladder or the standings change — for a scan the row loop below already makes.
    if filters.plan is not None:
        conditions.append("plan_id = ?")
        params.append(filters.plan)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY seq"

    rows: list[dict] = []
    counted_total = 0
    not_counted_total = 0
    for row in store.conn.execute(query, params):
        kind_def = registry.get(row["kind"])
        word = (
            standing(row["status"], kind_def.archetype, ladder) if kind_def else row["status"]
        )
        # KYO-99: the whole status filter, applied to the word this row is about to
        # print. `standing()` is the identity for a work record, so a work status still
        # selects exactly the records that display it; a knowledge record reads
        # `current`/`void` and is therefore reachable by those two words and by no work
        # status at all — which is what makes "an `open` in any listing is a real,
        # dispatchable open" true of the filter and not only of the display.
        if filters.status is not None and word not in filters.statuses:
            continue
        if unsettled and ladder.is_settled(row["status"]):
            continue
        carried_tags = gate_tags.get(row["id"], [])
        if filters.gate_tag is not None and filters.gate_tag not in carried_tags:
            continue
        counted = kind_def.counted if kind_def is not None else False
        if counted:
            counted_total += 1
        else:
            not_counted_total += 1
        rows.append(
            {
                "id": row["id"],
                # KYO-108: the record's allocation order, which is the order this
                # listing has always been in. Shipped as a field because it is the
                # named default sort (`DEFAULT_SORT`) — a run whose ordering could not
                # be stated is one a renderer has to guess at.
                "seq": row["seq"],
                "kind": row["kind"],
                # KYO-100.c2, law 2: `kyo list` is a consumer of the column, not only
                # of the filter. A field that can only be *searched* for and never
                # *seen* is one a reader has to already know the value of.
                "scope": row["scope"],
                "status": row["status"],
                # KYO-44.c2: what that status *means* for this record's archetype.
                # Beside the stored value, never instead of it.
                "standing": word,
                "gates_open": gate_counts.get(row["id"], 0),
                # KYO-102.c2, law 2 again: the tags on this record's open gates are
                # *shown*, not only searched for. A field a reader can filter on and
                # never see is one they have to already know the value of — the same
                # argument `scope` above makes one dimension over.
                "gate_tags": carried_tags,
                # KYO-203.c1: the distinct types of this record's open gates, in the
                # order the gates were created — beside gate_tags for the same reason
                # (law 2): a consumer needs to tell `awaiting:event` from
                # `awaiting:human-review` without opening the record.
                "gate_types": gate_types.get(row["id"], []),
                "title": row["title"],
                "counted": counted,
                # KYO-467: the record's own stamps, as the store keeps them. Read by
                # the desktop's records list (records.ts), whose Created and Modified
                # sorts had nothing to sort by without a Base.
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    return Result(
        rows=rows,
        counted=counted_total,
        not_counted=not_counted_total,
        currency=currency_module.db_export_currency(store),
    )


def validated_run(ctx: Context, filters: Filters, *, unsettled: bool = False) -> Result:
    """``validate`` then ``run`` against the context's store — the whole engine, once."""
    store = ctx.store()
    validate(store, filters)
    return run(store, filters, unsettled=unsettled)


# ------------------------------------------------------- KYO-109: the three sources
#
# KYO-45: a Base is a saved view "over records, ideas, or docs". The records half is
# above and is `kyo list`'s own query. What follows is the other two, and the shape
# that makes them the same engine rather than three.
#
# The temptation, and what is deliberately not done: give ideas and docs their own
# runner, their own row dict and their own --json document, because their data is so
# unlike a record's. That is the failure KYO-109.c4 exists to catch. A renderer would
# then carry three code paths for "which column do I draw", three for "how do I group",
# three for "is this store current" — and the two that are not the board's would rot,
# because nobody looks at them. So the row FIELDS differ per source and nothing else
# does: one Result, one envelope, one arrangement step (`bases.sorted_rows` /
# `bases.grouping`), one currency comparison.


#: A source's name, as a Base's ``source:`` spells it.
SOURCE_RECORDS = "records"
SOURCE_IDEAS = "ideas"
SOURCE_DOCS = "docs"

#: The field a file source carries its file's properties in. Its keys are open —
#: whatever the file states — which is the one thing about these sources that cannot be
#: validated against a vocabulary, because law 13 says the ideas pool has no required
#: ceremony and nothing may enforce one on it. Addressed with a dot:
#: ``properties.author`` as a column, a sort key, a grouping or a filter.
PROPERTIES_FIELD = "properties"

#: How a nested key is addressed. One level: everything after the first dot is the
#: property's own name, so a frontmatter key containing a dot is still reachable.
FIELD_SEPARATOR = "."

#: The glob-over-the-path operator both file sources offer. Equality against ``id``
#: would be the same question only for someone who already knows the whole path, which
#: is the reader a query surface is not for.
PATH_FILTER = "path"
#: The body-prose operator. The only filter in this module that reaches
#: prose, and it reaches it through :mod:`kyosei.prose`.
CONTAINS_FILTER = "contains"

#: Every operator a file source's ``filters:`` may name, beside its own field paths.
FILE_FILTERS: tuple[str, ...] = (PATH_FILTER, CONTAINS_FILTER)


def field_value(row: Mapping[str, Any], name: str) -> Any:
    """One row's value for a column/sort/grouping name, dotted paths included.

    The single rule every surface resolves a field name by (law 3): the terminal
    renderer, the sort, the grouping, and a file source's field filters. A consumer of
    ``--json`` implements this one rule and reads all three sources with it — which is
    what KYO-109.c4's "no per-source special-casing" buys, given that the field *names*
    legitimately differ.

    A dotted name whose head is not a mapping is ``None``, not an error: the head is
    checked against the source's field set when the Base is loaded, and a *property*
    that this particular file does not declare is an absent value rather than a
    mistake.
    """
    head, _, rest = name.partition(FIELD_SEPARATOR)
    value = row.get(head)
    if not rest:
        return value
    if isinstance(value, Mapping):
        return value.get(rest)
    return None


def value_matches(value: Any, wanted: str) -> bool:
    """Does a row's ``value`` answer to the one-line ``wanted`` a Base declared?

    Frontmatter and section metadata are one-line values, so this is value comparison
    and emphatically not the prose path: both sides are flattened
    (:func:`kyosei.prose.flatten`) so that indentation and quoting cannot decide a
    match, and then compared whole. A phrase is never *found inside* a value here —
    ``contains:`` is the operator for that, and it is the one that normalises wrapping.

    A list value matches when any of its items does, which is what makes
    ``promoted: KYO-107`` and ``governs: src/kyosei/brief.py`` ask the obvious
    question of a multi-valued property. A boolean matches ``true``/``false`` in any
    case. ``None`` — a property the file declares with no value, or does not declare —
    matches nothing: "this file says nothing about author" is not an answer to "which
    files say author is X".
    """
    if isinstance(value, list):
        return any(value_matches(item, wanted) for item in value)
    if value is None:
        return False
    if isinstance(value, bool):
        return wanted.strip().lower() == str(value).lower()
    return prose.flatten(str(value)) == prose.flatten(wanted)


@dataclass(frozen=True)
class Source:
    """One thing a Base can be run over: its field set, its filters, and its run.

    A source is a *value*, not a subclass, for the reason
    :class:`kyosei.registry.Dimension` is: the three differ in exactly these fields and
    in nothing else, and that claim is what keeps them one engine. Adding a fourth is a
    declaration here; it is not a loader, a row shape or a ``--json`` document.
    """

    name: str
    #: One line naming what the source reads, printed by refusals.
    what: str
    #: Every field a row carries, in the order a default rendering shows them.
    row_fields: tuple[str, ...]
    #: The subset a ``sort:`` or ``group-by:`` may name. A list-valued field is shown
    #: and filtered on and never grouped by (KYO-108's rule for ``gate_tags``, which is
    #: every multi-valued field's rule).
    scalar_fields: tuple[str, ...]
    #: The order rows come back in when a Base declares no ``sort:``.
    default_sort: tuple[str, ...]
    #: The operator keys this source's ``filters:`` may carry.
    filter_keys: tuple[str, ...]
    #: May a ``filters:`` key name one of this source's *fields*, matched by equality?
    #: True for the file sources, whose properties are the whole point of querying
    #: them; False for records, whose filters are the closed set ``kyo list`` offers.
    field_filters: bool
    #: The field whose keys are open, or ``None``. Only this field may be dotted into.
    open_field: str | None
    #: ``(store, filters) -> Result``. Owns its own refusals.
    run: Callable[[Store, Mapping[str, str]], Result]
    #: Per-row detail added after the rows are arranged, or ``None``. Not columns —
    #: a record's typed edges and an idea file's sections are structures a rendering
    #: draws, not cells a board prints.
    attach: Callable[[Store, list[dict]], None] | None = None

    def field_problem(self, what: str, name: str, *, scalar: bool = False) -> str | None:
        """Why ``name`` is not a field of this source, or ``None`` if it is.

        ``scalar`` narrows to what a ``sort:`` or ``group-by:`` may name. A dotted path
        into the open field is always allowed and never checked further: which
        properties exist is a fact about the files, and the files are never validated
        (law 13). A property no file declares is an empty column, which is the honest
        answer — the alternative would be this tool telling a human which frontmatter
        keys their own vault is allowed to use.
        """
        head, _, rest = name.partition(FIELD_SEPARATOR)
        if rest:
            if self.open_field is None or head != self.open_field:
                return (
                    f"{what} {name!r} addresses a key inside {head!r}, and "
                    + (
                        f"the only field of the {self.name} source with keys inside it "
                        f"is {self.open_field!r}"
                        if self.open_field
                        else f"no field of the {self.name} source has keys inside it"
                    )
                    + f"; the fields the engine produces are: {', '.join(self.row_fields)}"
                )
            return None
        allowed = self.scalar_fields if scalar else self.row_fields
        if name in allowed:
            return None
        return (
            f"{what} {name!r} is not a field of this source; the fields the engine "
            f"produces are: {', '.join(allowed)}"
        )

    def filter_problem(self, key: str, value: str) -> str | None:
        """Why ``key: value`` is not a legal filter on this source, or ``None``.

        Records answer this from the project's registries (see
        :class:`kyosei.bases._Vocabulary`) and never reach here. What is checked here is
        the file sources' own grammar, and it is checked when the Base is *loaded*: a
        ``contains:`` with no words in it matches at every position, so a base that
        silently returned every file is refused at the file rather than discovered from
        a listing that looks plausible.
        """
        if key in self.filter_keys:
            if key == CONTAINS_FILTER and not prose.phrase_words(value):
                return (
                    f"{CONTAINS_FILTER}: needs a phrase with at least one word in it; "
                    f"{value!r} has none, and an empty phrase matches every file"
                )
            if key == PATH_FILTER and not value.strip():
                return f"{PATH_FILTER}: needs a glob to match the file path against"
            return None
        if not self.field_filters:
            return None  # the caller's vocabulary owns this key
        problem = self.field_problem("filter", key)
        if problem is None:
            return None
        return (
            f"{problem}; a {self.name} base may also filter on "
            f"{', '.join(self.filter_keys)}"
        )


def matches_filters(source: Source, row: Mapping[str, Any], filters: Mapping[str, str],
                    body: Callable[[], str]) -> bool:
    """Does one file-source row answer every filter the Base declared?

    ``body`` is a callable so the file's prose is only assembled when a ``contains:``
    filter actually asks for it — the common case is a query over properties, and the
    bodies of an ideas tree are the largest thing this engine could read.
    """
    for key, wanted in filters.items():
        if key == PATH_FILTER:
            if not fnmatch(str(row["id"]), wanted):
                return False
        elif key == CONTAINS_FILTER:
            # The only place in this module that reaches body prose.
            if not prose.contains_phrase(body(), wanted):
                return False
        elif not value_matches(field_value(row, key), wanted):
            return False
    return True


def _run_records(store: Store, filters: Mapping[str, str]) -> Result:
    """The records source: :func:`validate` then :func:`run`, over the declared keys."""
    parsed = Filters.from_mapping(filters)
    validate(store, parsed)
    return run(store, parsed)


def _attach_links(store: Store, rows: list[dict]) -> None:
    links = links_by_record(store, [row["id"] for row in rows])
    for row in rows:
        row["links"] = links[row["id"]]


#: Every field an ideas row carries. ``id`` is the file's path relative to the project
#: root — the ideas layer has no identifiers of its own and must not acquire any (law
#: 13), so the path is what a row is called, and the field is named ``id`` so that a
#: renderer's "which row is this" is one field name across all three sources.
IDEAS_ROW_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "stamped",
    "promoted",
    "promoted_at",
    "section_count",
    "promoted_section_count",
    PROPERTIES_FIELD,
)

#: What an ideas ``sort:``/``group-by:`` may name — everything except the two
#: list-valued stamp fields and the open properties mapping. A dotted property path is
#: allowed on top of this (:meth:`Source.field_problem`).
IDEAS_SCALAR_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "stamped",
    "section_count",
    "promoted_section_count",
)


def _ideas_rows(store: Store, filters: Mapping[str, str]) -> Result:
    """Every idea file, as a row, with the promotion machinery's own reading of it.

    Read-only over ``ideas/``, which is the only thing this may be: law 12 says the
    pool is never validated, swept or enforced against, and a query surface is exactly
    where that could quietly stop being true. Nothing here refuses a file, and a file
    with no frontmatter at all is a row whose ``properties`` is ``{}`` (KYO-109.c1) —
    the state most of an ideas tree is in.

    The stamp fields come from :func:`kyosei.ideas.promotion_stamps`, not from
    ``properties``, and the difference is deliberate: that function is the authority on
    what a promotion is and it also recognises the pre-KYO-30 bottom-of-file block,
    which is not frontmatter and would be invisible to a property read. ``properties``
    carries the frontmatter exactly as the file states it, ``promoted`` included, which
    is what KYO-109.c1 asks to be queryable.
    """
    root = store.config.root
    rows: list[dict] = []
    counted_total = 0
    for path in ideas_module.idea_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        stamps = ideas_module.promotion_stamps(text)
        sections = ideas_module.sections(text)
        promoted_sections = [section for section in sections if section.promoted_to]
        row = {
            "id": path.relative_to(root).as_posix(),
            "title": ideas_module.first_heading(text),
            "stamped": bool(stamps),
            "promoted": [stamp.record_id for stamp in stamps],
            "promoted_at": [stamp.at for stamp in stamps],
            "section_count": len(sections),
            "promoted_section_count": len(promoted_sections),
            PROPERTIES_FIELD: ideas_module.frontmatter_properties(text),
        }
        if not matches_filters(
            IDEAS_SOURCE, row, filters, lambda text=text: ideas_module.promoted_body(text)
        ):
            continue
        # KYO-107's section state, the way the promotion machinery records it: the
        # heading is the address, and the stub under it names the record its text went
        # to. Beside the columns rather than in them, like a record's typed links.
        row["sections"] = [
            {
                "heading": section.heading,
                "level": section.level,
                "line": section.line,
                "promoted_to": list(section.promoted_to),
                "has_text": section.has_text,
            }
            for section in sections
        ]
        if not row["stamped"]:
            counted_total += 1
        rows.append(row)
    return Result(
        rows=rows,
        # The counted split, in this source's own terms: an unpromoted idea is what the
        # pool has that is still waiting to become work, and a stamped one is a pointer
        # to a record that is already counted on the records side. Counting both would
        # weigh every promoted idea twice in "how much is open" — the same double count
        # `kyo ideas review` avoids by dropping stamped files from its listing.
        counted=counted_total,
        not_counted=len(rows) - counted_total,
        currency=currency_module.db_export_currency(store),
    )


#: Every field a docs row carries. ``id`` is the path relative to the *code* root,
#: because that is where the documents live and what a ``governs:`` target resolves
#: against — under the split layout the store root is a different repository entirely.
#: KYO-129 makes that distinction load-bearing rather than incidental: the paths are
#: declared in the store's ``documents.yml`` and globbed against the code root.
DOCS_ROW_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "section_count",
    "governs",
    "governs_broken",
    "amended",
    "staleness",
    "staleness_says",
    PROPERTIES_FIELD,
)

DOCS_SCALAR_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "section_count",
    "amended",
    "staleness",
    "staleness_says",
)


def _docs_rows(store: Store, filters: Mapping[str, str]) -> Result:
    """Every one of this project's documents, as a row: what it governs, how current.

    WHICH files those are is ``kyosei/documents.yml``'s answer since KYO-129, resolved
    by :func:`kyosei.docs_parse.document_set` — the same call ``kyo docs check`` makes,
    so the two enumerate one set (KYO-129.c4). That is not a tidiness point. Discovery
    was hardcoded to ``docs/**/*.md`` and this source shares the check's enumerator, so
    a project whose documents live at the repository root was invisible *here* too: the
    ``stale-docs`` base in ``defaults/bases.yml`` returned no rows on exactly the
    projects whose documents had gone stale, and returned them silently.

    Note the two roots, which are two different repositories under the split layout: the
    globs are declared in the STORE's registry directory and resolved against the CODE
    root, because that is where the documents and the ``governs:`` targets both are.

    KYO-109.c5: the ``touches x governs`` banner is a *rendering* of this source and not
    part of it, but it cannot paint what the row does not carry — so the row carries the
    three things it needs. ``governs`` is what the document claims; ``governs_broken``
    is which of those claims no longer resolve, decided by ``kyo docs check``'s own rule
    so the banner and the build cannot disagree; ``staleness`` is whether the document
    has been touched since the code it governs was, in four named states of which one is
    ``cannot-tell`` — the third state KYO-45's amendment requires a place for, carried
    the same way the records source carries ``db_export_currency``'s.

    One git date per distinct path per run, memoised: several documents govern the same
    file, and a subprocess per naming would make dating the tree the expensive half of a
    query over it.
    """
    root = store.config.repo_root
    documents_in = docs_parse.document_set(store.config.kyosei_dir)
    # One git date per distinct path per run (KYO-139 moved the memo itself into
    # `docs_check` so `kyo docs check --json` gets the same one).
    commit_at = docs_staleness.memoised_commit_at()

    rows: list[dict] = []
    counted_total = 0
    for document in docs_targets.documents(root, documents_in=documents_in):
        row = {
            **docs_staleness.document_row(root, document, commit_at=commit_at),
            # A docs file's YAML frontmatter, if it has any — read with the ideas
            # layer's reader, because "the frontmatter properties of a markdown file"
            # is one question and this project answers it once (law 3). The charter's
            # Layer 3 metadata is section-level and is carried per section below;
            # anything a project puts in a docs frontmatter block (a scope, an owner)
            # is queryable here on the same terms an idea's is.
            PROPERTIES_FIELD: ideas_module.frontmatter_properties(document.text),
        }
        if not matches_filters(
            DOCS_SOURCE, row, filters, lambda document=document: document.body()
        ):
            continue
        row["sections"] = docs_staleness.section_rows(document)
        if row["governs"]:
            counted_total += 1
        rows.append(row)
    return Result(
        rows=rows,
        # This source's counted split: a document that declares a `governs:` target is
        # one the SOT discipline holds to the code, and an ungoverned one is prose the
        # build has no claim to check. "How much is governed" is the question a docs
        # board's summary is answering.
        counted=counted_total,
        not_counted=len(rows) - counted_total,
        currency=currency_module.db_export_currency(store),
    )


RECORDS_SOURCE = Source(
    name=SOURCE_RECORDS,
    what="the records in this project's store",
    row_fields=ROW_FIELDS,
    scalar_fields=SCALAR_ROW_FIELDS,
    default_sort=DEFAULT_SORT,
    filter_keys=FILTER_KEYS,
    field_filters=False,
    open_field=None,
    run=_run_records,
    attach=_attach_links,
)

IDEAS_SOURCE = Source(
    name=SOURCE_IDEAS,
    what="the markdown files in this project's ideas/ pool",
    row_fields=IDEAS_ROW_FIELDS,
    scalar_fields=IDEAS_SCALAR_FIELDS,
    #: Path order, which is the order the pool reads in a file browser. Records sort by
    #: allocation order because they have one; files do not, and inventing one out of
    #: mtimes would be an ordering that changes when nothing did.
    default_sort=("id",),
    filter_keys=FILE_FILTERS,
    field_filters=True,
    open_field=PROPERTIES_FIELD,
    run=_ideas_rows,
)

DOCS_SOURCE = Source(
    name=SOURCE_DOCS,
    what="the markdown files under this project's docs/ tree",
    row_fields=DOCS_ROW_FIELDS,
    scalar_fields=DOCS_SCALAR_FIELDS,
    default_sort=("id",),
    filter_keys=FILE_FILTERS,
    field_filters=True,
    open_field=PROPERTIES_FIELD,
    run=_docs_rows,
)

#: Every source this build can run, in the order a refusal lists them. A Base naming
#: anything else is refused when its file is READ — never answered with an empty view,
#: which is the same law-6 rule an undeclared kind gets.
SOURCES: dict[str, Source] = {
    source.name: source for source in (RECORDS_SOURCE, IDEAS_SOURCE, DOCS_SOURCE)
}


def source_for(name: str) -> Source:
    """The source called ``name``, or a refusal naming the ones this build runs."""
    source = SOURCES.get(name)
    if source is None:
        raise KyoseiError(
            f"{name!r} is not a source this build runs; it runs: {', '.join(SOURCES)}"
        )
    return source


# ------------------------------------------- the ledgers the viewer reads (KYO-454)
#
# KYO-454: the desktop app read these by SQL (`apps/desktop/src/main/store.ts`), so a
# viewer across the network had no way to ask them, and the schema had a second reader.
# Each function below is ONE projection of one table (or one join), and returns plain
# dicts in the column names the store uses — the commands built on them (`kyo briefs`,
# `kyo shift-notes`, `kyo gates`, `kyo activity`) print them as they are. Nothing here
# writes, and nothing opens a transaction: under `kyo serve` the store is opened
# read-only, and these run there like any other read.


def _session_start(value: Any) -> bool | None:
    """``brief_runs.session_start`` as a three-way fact: NULL is a row written before the
    column existed — never asked — and is ``None`` rather than ``False`` (law 6)."""
    return None if value is None else value == 1


def brief_runs(store: Store) -> list[dict]:
    """Every session brief the store recorded, newest first (``brief_runs``)."""
    return [
        {
            "id": row["id"],
            "seq": row["seq"],
            "at": row["at"],
            "actor": row["actor"],
            "session_start": _session_start(row["session_start"]),
            "last_event_seq": row["last_event_seq"],
            "last_shift_note_seq": row["last_shift_note_seq"],
            "build_commit": row["build_commit"],
            "build_commit_time": row["build_commit_time"],
            "build_source": row["build_source"],
            "session_id": row["session_id"],
        }
        for row in store.conn.execute("SELECT * FROM brief_runs ORDER BY seq DESC")
    ]


def shift_notes_between(store: Store, after_seq: int, through_seq: int | None) -> list[dict]:
    """Shift notes with ``after_seq < seq <= through_seq``, newest first.

    ``through_seq`` ``None`` is an open upper end — every note after ``after_seq``. A
    redacted note keeps its row and its marker and has lost its prose, which is what the
    store holds; it is returned as held.
    """
    sql = "SELECT * FROM shift_notes WHERE seq > ?"
    params: list[int] = [after_seq]
    if through_seq is not None:
        sql += " AND seq <= ?"
        params.append(through_seq)
    return [
        {
            "id": row["id"],
            "seq": row["seq"],
            "author": row["author"],
            "created_at": row["created_at"],
            "text": row["text"],
            "redacted_at": row["redacted_at"],
            "redacted_why": row["redacted_why"],
        }
        for row in store.conn.execute(sql + " ORDER BY seq DESC", params)
    ]


def events_between(store: Store, after_seq: int, through_seq: int) -> list[dict]:
    """The ledger entries with ``after_seq < seq <= through_seq``, newest first.

    The row's identity and what ran — not its before/after documents, which are the
    record's whole state and belong to `kyo show`.
    """
    return [
        {
            "id": row["id"],
            "seq": row["seq"],
            "at": row["at"],
            "actor": row["actor"],
            "command": row["command"],
            "record_id": row["record_id"],
        }
        for row in store.conn.execute(
            "SELECT id, seq, at, actor, command, record_id FROM events "
            "WHERE seq > ? AND seq <= ? ORDER BY seq DESC",
            (after_seq, through_seq),
        )
    ]


def open_gates(store: Store, types: tuple[str, ...] | None = None) -> list[dict]:
    """Every gate not yet cleared, with the record it is on, in record then gate order.

    ``types`` narrows to those gate types; ``None`` is every type. Across every record
    status: a gate is open or it is not, whatever the record's status says.
    """
    sql = (
        "SELECT g.*, r.title AS record_title, r.kind AS record_kind, "
        "r.status AS record_status FROM gates g JOIN records r ON r.id = g.record_id "
        "WHERE g.cleared_at IS NULL"
    )
    params: tuple[str, ...] = ()
    if types is not None:
        if not types:
            return []
        sql += f" AND g.gate_type IN ({', '.join('?' for _ in types)})"
        params = types
    return [
        {
            "id": row["id"],
            "record_id": row["record_id"],
            "seq": row["seq"],
            "type": row["gate_type"],
            "do": row["detail"],
            "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
            "created_at": row["created_at"],
            "record_title": row["record_title"],
            "record_kind": row["record_kind"],
            "record_status": row["record_status"],
        }
        for row in store.conn.execute(sql + " ORDER BY r.seq, g.seq", params)
    ]


def open_gate_counts(store: Store) -> dict[str, int]:
    """How many gates each record has open — every type — by record id."""
    return {
        row["record_id"]: row["n"]
        for row in store.conn.execute(
            "SELECT record_id, count(*) AS n FROM gates WHERE cleared_at IS NULL "
            "GROUP BY record_id ORDER BY record_id"
        )
    }


def dispatch_rows(store: Store) -> list[dict]:
    """Every dispatch, newest first, with its record and its CURRENT report's verdicts.

    The report is the one the dispatch's own ``report_id`` names (KYO-90: an amended
    report supersedes its predecessor, and which is current is the store's decision).
    ``covers`` carries each covered record's title beside its id.
    """
    titles = {
        row["id"]: row["title"] for row in store.conn.execute("SELECT id, title FROM records")
    }
    rows = []
    for row in store.conn.execute(
        "SELECT d.*, r.title AS record_title, r.kind AS record_kind, "
        "r.status AS record_status, p.body_json AS report_body_json FROM dispatches d "
        "JOIN records r ON r.id = d.record_id LEFT JOIN reports p ON p.id = d.report_id "
        "ORDER BY d.created_at DESC, d.id DESC"
    ):
        covers = json.loads(row["covers_json"]) if row["covers_json"] else []
        rows.append(
            {
                "id": row["id"],
                "record_id": row["record_id"],
                "record_title": row["record_title"],
                "record_kind": row["record_kind"],
                "record_status": row["record_status"],
                "state": row["state"],
                "branch": row["branch"],
                "worktree": row["worktree"],
                "worker_model": row["worker_model"],
                "worked_from_sha": row["worked_from_sha"],
                "report_id": row["report_id"],
                "terminated_why": row["terminated_why"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "closed_at": row["closed_at"],
                "covers": [{"id": rid, "title": titles.get(rid)} for rid in covers],
                "verdict_counts": verdict_counts(row["report_body_json"]),
            }
        )
    return rows


def verdict_counts(body_json: str | None) -> list[dict]:
    """How many ``criteria_verdicts`` of each verdict one report carried, by verdict.

    Sorted by the verdict word, so two rows compare by eye; an entry with no verdict
    counts as ``(no verdict)`` rather than vanishing. An unparseable body is no counts.
    """
    if not body_json:
        return []
    try:
        body = json.loads(body_json)
    except ValueError:
        return []
    verdicts = body.get("criteria_verdicts") if isinstance(body, dict) else None
    if not isinstance(verdicts, list):
        return []
    counts: dict[str, int] = {}
    for entry in verdicts:
        if not isinstance(entry, dict):
            continue
        word = entry.get("verdict")
        word = word if isinstance(word, str) and word else "(no verdict)"
        counts[word] = counts.get(word, 0) + 1
    return [{"verdict": word, "count": counts[word]} for word in sorted(counts)]


__all__ = [
    "CONTAINS_FILTER",
    "DEFAULT_SORT",
    "DOCS_ROW_FIELDS",
    "DOCS_SOURCE",
    "FIELD_SEPARATOR",
    "FILE_FILTERS",
    "FILTER_KEYS",
    "FILTER_NAMES",
    "IDEAS_ROW_FIELDS",
    "IDEAS_SOURCE",
    "PATH_FILTER",
    "PROPERTIES_FIELD",
    "RECORDS_SOURCE",
    "ROW_FIELDS",
    "SCALAR_ROW_FIELDS",
    "SOURCES",
    "SOURCE_DOCS",
    "SOURCE_IDEAS",
    "SOURCE_RECORDS",
    "Filters",
    "Result",
    "Source",
    "brief_runs",
    "dispatch_rows",
    "events_between",
    "field_value",
    "filter_flag",
    "filter_key",
    "links_by_record",
    "matches_filters",
    "open_gate_counts",
    "open_gates",
    "run",
    "shift_notes_between",
    "source_for",
    "validate",
    "validated_run",
    "value_matches",
    "verdict_counts",
]
