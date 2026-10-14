"""Saved Base definitions — the store's query primitive (KYO-108).

A **Base** is a saved view definition: a source, filters, columns, sort, and the
optional ``group-by`` binding a renderer may change. The design holds that the board,
the status view, the bubble view and the typed-edge graph are not four features but
four *renderings* of one Base. This module is the definition half; :mod:`kyosei.query`
is the engine half, shared verbatim with ``kyo list`` (KYO-108.c4).

"A renderer may change it" is a claim this module owes a surface, and KYO-112.r1
caught it standing without one: the sentence was here from day one while ``kyo base
run`` took a name and nothing else, so the only grouping any caller could obtain was
the one the file happened to declare. KYO-120 closed that with ``--group-by FIELD``,
routed through :func:`group_by_problem` — the one rule the stored key is validated by,
asked a second time rather than reimplemented — and answered in the run document's
``applied`` block, which is where a field that was *applied* rather than *declared*
has always belonged.

**The layout: one file, ``kyosei/bases.yml``, in the registry shape.**

KYO-108 left the layout open and asked that it follow the existing registries.
It does more than follow them — it is the same loader. A Base file is
``bases:`` mapping *name* -> *entry*, entries carry a required ``summary``, names are
lowercase slugs, every problem in the file is collected and raised together, and an
unknown key is refused. All of that is :func:`kyosei.registry.load` over the
:data:`BASE` dimension below, with no second copy of any of it.

Why one file rather than a file per base:

* The shared loader reads *one file per dimension*. A directory of one-base files
  would need a walk, a name-from-filename rule and a merge — a second loader beside
  the one that already refuses malformed registries, which is the drift
  :mod:`kyosei.registry` exists to prevent.
* A set of bases is read as a set. ``kyo base list`` renders the whole file, and a
  reviewer of a proposed change (KYO-72 step 6, when it lands) reviews one diff.
* ``bases.yml`` sits beside ``kinds.yml``, ``scopes.yml``, ``statuses.yml`` and
  ``gate-tags.yml`` and is edited exactly as they are in v1.

The cost, stated: a project with many bases gets one long file, and two people adding
a base touch the same file. That is the same trade ``kinds.yml`` already makes, and
splitting later is additive (a ``bases/`` directory could be read *in addition*);
merging a directory back into a file would not be.

**Declared here rather than in ``registry.py``**, for the reason ``contracts.STATUS``
and ``kinds.KIND`` are declared where they are: validating a Base means knowing what a
kind, a status ladder and a row field are, and :mod:`kyosei.registry` must not know any
of those. What is shared is the loader, not the vocabulary.

**Validated against the step-1 registries on load** (KYO-108.c1). A Base cannot express
a filter the project's own registries do not declare: an unknown kind, status, scope or
gate tag is refused naming the registry that would make it valid, in that registry's own
words (:func:`kyosei.registry.unknown_name_refusal`), and an unknown column, sort key or
grouping field is refused naming the field set. This is the synergy step 1 was built
for, and it is a *load*-time refusal rather than a run-time one so that ``kyo base
list`` — which runs nothing — still says the file is wrong.

**Deliberately left out**, per KYO-108's instruction that the expensive mistake here is
a wide abstraction:

* ``color-by`` / ``size-by``. The design names them beside ``group-by`` as bindings, and
  KYO-108 says explicitly that the grouping computation lives in the engine while
  "color/size stay rendering-side". Nothing in v1 would consume a stored ``color-by``,
  and a stored field with no consumer is exactly what law 2 forbids. The renderings
  (KYO-72 step 5) are where those two bindings acquire a reader; the file shape takes
  one entry each on that day.
* Operators. A filter is equality against a declared value, which is precisely what
  ``kyo list`` can already ask. Free-text and body-prose operators belong with the
  ideas/docs sibling (they must normalise whitespace via
  ``kyosei.prose.phrase_pattern`` from day one), and records have no prose field a Base
  would search anyway.
* Computed columns and cross-source joins. Neither is named by KYO-108, and
  either would make the definition format a language rather than a saved question.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import contracts, currency, kinds
from . import query as query_module
from . import registry as registry_module
from .errors import KyoseiError
from .registry import Dimension, Entry, Registry, RegistryError, string_list
from .store import Store

#: The dimension's two names, spelled once. ``plural`` is both the filename stem and
#: the top-level YAML key, exactly as it is for every other registry.
BASE_SINGULAR = "base"
BASE_PLURAL = "bases"

#: The sources a Base may read: records, ideas, or docs. Read off
#: :data:`kyosei.query.SOURCES` rather than listed, so a source the engine can run and
#: a source a file may name are one fact (law 3). Closed rather than open on purpose —
#: a Base naming a source no engine can run must be refused when the file is read, not
#: answered with an empty view (law 6).
BASE_SOURCES: tuple[str, ...] = tuple(query_module.SOURCES)

#: The field a Base declares its query target in.
SOURCE_FIELD = "source"
#: The mapping of ``<filter>: <value>`` narrowing the source. Keys are
#: :data:`kyosei.query.FILTER_KEYS` — derived, never listed here.
FILTERS_FIELD = "filters"
#: Which of the source's fields a rendering shows, in order.
COLUMNS_FIELD = "columns"
#: Sort keys, most significant first; a leading ``-`` reverses one key.
SORT_FIELD = "sort"
#: The default ``group-by`` binding, if this Base has one.
GROUP_BY_FIELD = "group-by"

#: How a descending sort key is spelled.
DESCENDING = "-"


def _mapping_of_strings(value: object) -> str | None:
    """A field holding ``str -> str``. The key *set* is dimension knowledge and is
    checked in :func:`_entries_check`, where the refusal can name the legal keys
    alongside every other problem in the file (one raise, law 6)."""
    if not isinstance(value, dict):
        return "must be a mapping"
    bad = sorted(str(k) for k, v in value.items() if not isinstance(v, str))
    if bad:
        return f"values must be strings; not for {', '.join(bad)}"
    return None


def _sort_key(spec: str) -> tuple[str, bool]:
    """``("-title") -> ("title", True)`` — the field and whether it descends."""
    if spec.startswith(DESCENDING):
        return spec[1:], True
    return spec, False


def _check_filters(
    declared: object,
    problems: list[str],
    label: str,
    ctx: _Vocabulary,
    source: query_module.Source,
) -> None:
    """Every reason this base's ``filters:`` mapping is not askable, collected.

    Two vocabularies meet here, and which one owns a key depends on the source. The
    records source's filters are the closed set ``kyo list`` offers, checked against
    this project's registries (KYO-108.c1). A file source's are its own operators plus
    its own fields, checked against the field set — and a *property* path is checked no
    further than its head, because the files it reads are never validated (law 13).
    """
    if not isinstance(declared, dict):
        return  # already reported by the field validator
    if not source.field_filters:
        unknown = sorted(set(declared) - set(source.filter_keys))
        if unknown:
            problems.append(
                f"{label}: unknown filter(s) {', '.join(unknown)}; a {source.name} base "
                f"may filter on {', '.join(source.filter_keys)} — the same set "
                "`kyo list` offers, read from kyosei.query.Filters so neither surface "
                "can gain one without the other"
            )
    for key, value in declared.items():
        if not isinstance(value, str):
            continue  # already reported
        trouble = source.filter_problem(key, value) or (
            ctx.filter_problem(key, value) if not source.field_filters else None
        )
        if trouble:
            problems.append(f"{label}: filter {key}: {trouble}")


@dataclass(frozen=True)
class _Vocabulary:
    """The step-1 registries a Base's filter values are validated against.

    Loaded once per :func:`load_bases` call and handed to the per-entry check, so a
    file declaring six bases reads each registry once rather than six times.
    """

    kind_registry: kinds.KindRegistry
    ladder: contracts.StatusLadder
    scopes: Registry
    gate_tags: Registry

    def filter_problem(self, key: str, value: str) -> str | None:
        """Why ``key: value`` is not a legal filter, in the owning registry's words."""
        if key == "kind":
            if value in self.kind_registry:
                return None
            return registry_module.unknown_name_refusal(self.kind_registry.registry, value)
        if key == "status":
            if query_module.is_askable_status(self.ladder, value):
                return None
            # The engine's own sentence, not a second wording of it (law 3): a base
            # naming an impossible status is refused in the words `kyo list --status`
            # would use, because it is the same filter.
            return query_module.unknown_status_refusal(self.ladder, value)
        if key == "scope":
            if value in self.scopes:
                return None
            return registry_module.unknown_name_refusal(self.scopes, value)
        if key == "gate-tag":
            if value in self.gate_tags:
                return None
            return registry_module.unknown_name_refusal(self.gate_tags, value)
        # `plan` names a record id, not a registry entry. Checked by the engine at run
        # time (`query.validate` -> `_require_record`), because the record set is not a
        # vocabulary this project *declares* and a record can be redacted after the file
        # was written — refusing the whole file then would take every other base with it.
        return None


def _entries_check(vocabulary: _Vocabulary):
    """The per-file rules :class:`kyosei.registry.Dimension` runs after field validation.

    Every problem joins the shared loader's one collected raise, so a bases.yml with a
    bad summary *and* an unknown kind *and* a misspelled column is fixed in one pass.
    """

    def check(entries: Mapping[str, Entry], raw: Mapping[str, object]) -> list[str]:
        problems: list[str] = []
        for name, entry in entries.items():
            label = f"base {name!r}"
            # An entry only reaches here with a `source` the `one_of` validator
            # accepted, so this lookup cannot fail — and every rule below is asked of
            # THAT source, which is what makes a column legal in one base and refused
            # in the next (KYO-109: the envelope is one, the field sets are three).
            source = query_module.source_for(str(entry.get(SOURCE_FIELD)))
            _check_filters(entry.get(FILTERS_FIELD, {}), problems, label, vocabulary, source)
            for column in entry.get(COLUMNS_FIELD) or []:
                trouble = source.field_problem("column", column)
                if trouble:
                    problems.append(f"{label}: {trouble}")
            for spec in entry.get(SORT_FIELD) or []:
                key, _ = _sort_key(spec)
                trouble = source.field_problem("sort key", key, scalar=True)
                if trouble:
                    problems.append(
                        f"{label}: {trouble}"
                        f" (prefix a key with {DESCENDING!r} to reverse it)"
                    )
            group_by = entry.get(GROUP_BY_FIELD)
            if isinstance(group_by, str):
                # The same call `kyo base run --group-by` makes (KYO-120). A field the
                # file may not declare is a field the flag may not name, from one rule,
                # so an override cannot reach a binding a saved base is refused.
                trouble = group_by_problem(source, group_by)
                if trouble:
                    problems.append(f"{label}: {trouble}")
        return problems

    return check


def base_dimension(vocabulary: _Vocabulary) -> Dimension:
    """The ``bases`` dimension, bound to one project's registries.

    Built per load rather than declared as a module constant because its
    ``entries_check`` has to see *this project's* kinds, ladder, scopes and gate tags —
    which is the whole of KYO-108.c1. The shape is otherwise a plain sibling of
    :data:`kyosei.registry.SCOPE`.
    """
    return Dimension(
        singular=BASE_SINGULAR,
        plural=BASE_PLURAL,
        what="a saved view definition — source, filters, columns, sort — over the store",
        required_fields={SOURCE_FIELD: registry_module.one_of(*BASE_SOURCES)},
        optional_fields={
            FILTERS_FIELD: _mapping_of_strings,
            COLUMNS_FIELD: string_list(),
            SORT_FIELD: string_list(),
            GROUP_BY_FIELD: _group_by_validator,
        },
        entries_check=_entries_check(vocabulary),
    )


def _group_by_validator(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return "must be a non-empty string naming one field"
    return None


def group_by_problem(
    source: query_module.Source, group_by: object, *, what: str = GROUP_BY_FIELD
) -> str | None:
    """Why ``group_by`` cannot be this source's grouping binding, or ``None``.

    The one rule behind both places a grouping field can be named: the ``group-by:``
    key in ``kyosei/bases.yml`` and ``kyo base run --group-by`` (KYO-120). Written as
    a function with a ``what`` label rather than copied into the flag's parser because
    the failure mode being avoided is precisely a *second* rule — a flag that accepted
    ``gate_tags`` because its author forgot that a row with two values belongs to two
    groups, or that refused a dotted property path the file may legally declare.

    ``what`` is the surface's own word for the thing being named, so the same rule
    reads as ``group-by 'nonsense' is not a field…`` under a file and
    ``--group-by 'nonsense' is not a field…`` under the flag. The sentence is
    :meth:`kyosei.query.Source.field_problem`'s either way, naming the field set —
    a closed set the caller cannot see is a guessing game (law 6).
    """
    shape = _group_by_validator(group_by)
    if shape is not None:
        return f"{what} {group_by!r} {shape}"
    return source.field_problem(what, str(group_by), scalar=True)


@dataclass(frozen=True)
class Base:
    """One validated saved view definition.

    ``filters`` is the mapping the file declared, not a parsed filter object: the three
    sources parse it differently (the records source into
    :class:`kyosei.query.Filters`, a file source into operators and field paths) and
    each owns that step. What is shared is the *declaration*, which is what a renderer
    showing "filtered by" reads.
    """

    name: str
    summary: str
    source: str
    filters: dict[str, str]
    columns: tuple[str, ...]
    sort: tuple[str, ...]
    group_by: str | None

    @classmethod
    def from_entry(cls, entry: Entry) -> Base:
        source = query_module.source_for(str(entry.get(SOURCE_FIELD)))
        return cls(
            name=entry.name,
            summary=entry.summary,
            source=source.name,
            filters=dict(entry.get(FILTERS_FIELD) or {}),  # type: ignore[arg-type]
            # The defaults are the SOURCE's, which is the whole of "the field set
            # differs per source while the envelope does not": a base over ideas that
            # declares no columns shows every field an ideas row has, exactly as one
            # over records shows every field a record row has.
            columns=tuple(entry.get(COLUMNS_FIELD) or source.row_fields),
            sort=tuple(entry.get(SORT_FIELD) or source.default_sort),
            group_by=entry.get(GROUP_BY_FIELD),  # type: ignore[arg-type]
        )

    def as_definition(self) -> dict:
        """The definition as declared, for a ``--json`` consumer that renders it."""
        return {
            "name": self.name,
            "summary": self.summary,
            SOURCE_FIELD: self.source,
            FILTERS_FIELD: dict(self.filters),
            COLUMNS_FIELD: list(self.columns),
            SORT_FIELD: list(self.sort),
            GROUP_BY_FIELD: self.group_by,
        }


def _vocabulary(kyosei_dir: Path) -> _Vocabulary:
    """This project's step-1 registries, read once."""
    return _Vocabulary(
        kind_registry=kinds.load_kind_registry(kyosei_dir),
        ladder=contracts.load_status_ladder(kyosei_dir),
        scopes=registry_module.load_scope_registry(kyosei_dir),
        gate_tags=registry_module.load_gate_tag_registry(kyosei_dir),
    )


def load_base_registry(store: Store) -> Registry:
    """Every Base this project declares, as the shared registry object.

    Raises :class:`kyosei.registry.RegistryError` naming every problem in the file at
    once. An absent ``kyosei/bases.yml`` is an empty registry that *says* it is absent
    (``Registry.exists``), not an error: it is what every store written before this
    release has, and a project may legitimately save no views — the same rule
    ``scopes.yml`` and ``gate-tags.yml`` follow.
    """
    return registry_module.load(
        store.config.kyosei_dir, base_dimension(_vocabulary(store.config.kyosei_dir))
    )


def load_bases(store: Store) -> dict[str, Base]:
    """The same, read into :class:`Base` values — the view the runner consumes."""
    return {
        name: Base.from_entry(entry)
        for name, entry in load_base_registry(store).entries.items()
    }


def bases_path(kyosei_dir: Path) -> Path:
    """Where this project's Base definitions live, existing or not."""
    return kyosei_dir / f"{BASE_PLURAL}.yml"


def sorted_rows(rows: list[dict], sort: tuple[str, ...]) -> list[dict]:
    """``rows`` ordered by ``sort``, least significant key first.

    Stable sorts applied in reverse order, which is the standard way to get a
    multi-key ordering out of one comparison-free sort. ``None`` sorts last in
    ascending order and first in descending — a record with no scope is not a record
    whose scope is the empty string, and putting it at the top of every board grouped
    by scope would say it is.
    """
    ordered = list(rows)
    for spec in reversed(sort):
        key, descending = _sort_key(spec)
        ordered.sort(
            key=lambda row, key=key: (
                query_module.field_value(row, key) is None,
                _comparable(query_module.field_value(row, key)),
            ),
            reverse=descending,
        )
    return ordered


def _comparable(value: object) -> tuple[int, float | str]:
    """A sort key that never raises across mixed types in one column.

    ``scope`` is ``str | None`` and ``counted`` is a bool; a column mixing them would
    otherwise make a saved view crash on the day a record with no scope is filed. A
    file source's open properties widen that: one file's ``area:`` may be a string and
    the next file's a list, and neither is wrong, because nothing validates an idea
    (law 13). A list sorts by its items' text — deterministic, and the column shows the
    list itself so the ordering is readable. Grouping by one is refused instead, which
    is a different question and a different answer (:func:`grouping`).
    """
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, float(value))
    if isinstance(value, int | float):
        return (1, float(value))
    if isinstance(value, list):
        return (2, ", ".join(str(item) for item in value))
    return (2, str(value))


def grouping(rows: list[dict], group_by: str | None) -> list[dict] | None:
    """The ``group-by`` binding computed once, here, for all four renderings.

    ``None`` when the Base sets no binding — distinct from ``[]``, which is a Base that
    groups and matched nothing. KYO-108 puts this in the engine rather than in each
    renderer for the reason behind the whole design: the board, the status
    view, the bubbles and the graph are four renderings of one query, and four
    implementations of "count the rows per value" is where they would first disagree.

    Groups come out in row order — the first row of each value fixes that value's
    position — so a Base's ``sort`` decides the group order too, rather than a second,
    invisible rule.
    """
    if group_by is None:
        return None
    groups: dict[object, dict] = {}
    for row in rows:
        value = query_module.field_value(row, group_by)
        if isinstance(value, list):
            # KYO-108 refuses a list-valued FIELD at load time (`gate_tags`). An open
            # property cannot be refused there — which keys a file declares, and what
            # shape their values take, is a fact about the files and never a rule this
            # tool imposes (law 13) — so the same refusal fires here, at the first row
            # that proves it, naming the file. Silently stringifying would put
            # `[ui, human]` in a different bucket from `[human, ui]`; silently taking
            # the first item would answer a different question than the one asked.
            raise KyoseiError(
                f"cannot group by {group_by!r}: {row['id']} declares it as a list "
                f"({', '.join(str(item) for item in value) or 'empty'}), and a row with "
                f"two values belongs to two groups. Group by a single-valued field, or "
                f"filter on {group_by!r} instead — a filter matches any item in a list"
            )
        bucket = groups.get(value)
        if bucket is None:
            bucket = groups[value] = {"value": value, "count": 0, "ids": []}
        bucket["count"] += 1
        bucket["ids"].append(row["id"])
    return list(groups.values())


@dataclass(frozen=True)
class BaseRun:
    """One Base, run: the ``--json`` document and the seam comparison behind it.

    The :class:`kyosei.currency.Currency` rides alongside rather than being re-derived
    from ``data`` so that the warning a terminal reads and the block a renderer reads
    are one comparison, not two (law 3 — and KYO-74's own reason for taking the
    computed value rather than a ``Store``).
    """

    data: dict
    currency: currency.Currency


def run_base(store: Store, base: Base, group_by: str | None = None) -> BaseRun:
    """Run one Base read-only and return its ``--json`` document.

    Everything about *which rows* comes from :mod:`kyosei.query`; everything about
    *how they are arranged* — sort, projection order, grouping — is here. That split is
    KYO-108.c4: the Base surface adds arrangement and adds no query.

    KYO-109 makes this function source-agnostic, which is c4's other half. There is no
    ``if source == ...`` below: the source decides which rows exist and what per-row
    detail rides along, and the arrangement, the summary and the currency block are the
    same code for all three. A renderer reading this document does not know, and does
    not need to know, which source produced it.

    ``group_by`` overrides the file's declared binding and nothing else (KYO-120). It
    reaches *arrangement* only — the rows this run produces are the rows it would have
    produced without it, because which rows exist is ``base.filters`` over
    :mod:`kyosei.query` and is untouched here. Validated before the query runs, against
    :func:`group_by_problem`, so a refused override costs no read and renders nothing.

    The override lands in ``applied`` and NOT in ``base``, which is the distinction
    those two blocks were named for and the first time it has had teeth: ``base`` is
    the definition the reviewable file declares, ``applied`` is what this run actually
    did. A document echoing the declared key while having grouped by the flag's would
    be worse than no flag at all.
    """
    source = query_module.source_for(base.source)
    if group_by is not None:
        trouble = group_by_problem(source, group_by, what="--group-by")
        if trouble:
            raise KyoseiError(f"cannot run base {base.name!r}: {trouble}")
    applied_group_by = base.group_by if group_by is None else group_by
    result = source.run(store, base.filters)
    rows = sorted_rows(result.rows, base.sort)
    if source.attach is not None:
        # The typed-edge graph is the fourth rendering, and it draws
        # from the same run rather than from a second query per node. Beside the row's
        # fields rather than inside `columns`, because an edge list is not a column any
        # board would print — and the same is true of an idea file's sections, which
        # its own source attaches as it reads.
        source.attach(store, rows)
    data = {
        "base": base.as_definition(),
        "applied": {
            "source": base.source,
            "filters": dict(base.filters),
            "columns": list(base.columns),
            "sort": list(base.sort),
            GROUP_BY_FIELD: applied_group_by,
        },
        "rows": rows,
        "groups": grouping(rows, applied_group_by),
        "summary": result.summary,
        # KYO-74: a rendering needs somewhere to paint "could not tell" instead of
        # defaulting to clean, and it cannot paint what the result does not carry.
        "db_export_currency": currency.as_data(result.currency),
    }
    return BaseRun(data=data, currency=result.currency)


__all__ = [
    "BASE_PLURAL",
    "BaseRun",
    "BASE_SOURCES",
    "Base",
    "RegistryError",
    "bases_path",
    "group_by_problem",
    "grouping",
    "load_base_registry",
    "load_bases",
    "run_base",
    "sorted_rows",
]
