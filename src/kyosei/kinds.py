"""``kyosei/kinds.yml`` — the kind registry: loader, validator, lookup.

Milestone 2's central config surface (``docs/design/charter.md``, "The kind
registry"). Every policy field ships with a named consumer in the same commit
(law 2):

* ``archetype`` — :func:`contracts.check_archetype_allows_status_flow` (``kyo move``/``wont-do``).
* ``policy.requires_criteria`` — :func:`contracts.check_transition` (``kyo move``).
* ``policy.counted`` — ``cmd_views.cmd_list`` (its first consumer, law 2).
* ``policy.worker_model`` (optional, KYO-61) — ``cmd_dispatch.resolve_worker_model``, which
  resolves ``kyo dispatch --model`` over this over ``kyosei/config.yml``'s ``worker_model``,
  and ``brief.generate_brief``, which states the answer to the worker. Nothing enforces it:
  Kyosei does not spawn workers and must not start, so ``kyosei/procedures.md`` records the
  level verbatim as ``enforced-by: aspirational``.
* ``policy.carried`` (optional, KYO-497) — ``cmd_brief`` (the "reminders" section),
  ``contracts.check_carried_settle_why`` and ``cmd_records.clear_gates_on_carried_settle``.
* ``policy.requires_evidence_links`` (optional, knowledge kinds) — ``cmd_records.cmd_new``
  (KYO-521: filing such a kind directly is refused without ``--evidence-file``).
* ``policy.raises_docs_obligations`` (optional, knowledge kinds, KYO-554) —
  ``cmd_records.raise_standing_obligations``, called by `kyo new`, `kyo spawn`,
  `kyo promote` and `kyo edit`: filing or editing such a record raises a standing docs
  obligation on each ssot section its title or description names by id, and on each of
  its ``doc-refs``.
* ``accepts_from`` / ``produces`` — ``cmd_views.cmd_map`` (rendered as the promotion graph);
  flow validation at ``cmd_records.cmd_promote`` (``idea`` in ``accepts_from``), at
  ``cmd_records.cmd_new`` (``intake`` in ``accepts_from`` — KYO-43: the registry stated a
  flow rule that only one of the two write paths consulted, so `kyo new task` filed a
  kind whose entry says in a comment why it must not be filed directly), and at
  ``cmd_records.cmd_spawn`` (**the kind-to-kind edge**: `kyo spawn <source-id> --kind Y`
  refuses unless the source record's kind is in ``Y.accepts_from``, via
  :meth:`KindRegistry.spawn_targets` — KYO-50: until then the third source an
  ``accepts_from`` entry can name was the one no command implemented, so every
  ``bug -> task`` / ``proposal -> decision`` edge in the registry was a picture
  ``kyo map`` could draw and nothing could walk). KYO-64 added a fourth consumer of
  the same field, ``cmd_records._spawn_remedy``: the refusals `kyo new` and
  `kyo promote` raise read the target's kind tokens to name `kyo spawn` as the way
  through, so a registry edit stops being the only suggestion a refused caller gets.
  KYO-68 gave that read an accessor of its own, :meth:`KindRegistry.spawn_sources`,
  and made it unconditional: a refusal now names every *verb* the registry admits for
  the kind, and only downgrades to a placeholder id when no live source exists.

Validation is loud and names the offending kind/field (law 6): every problem found is
collected and raised together, rather than stopping at the first one, so a broken
registry is fixed in one pass instead of death-by-a-thousand-reruns.

**KYO-104: the loading, validating and refusing all happens in** :mod:`kyosei.registry`
**now**, and this module declares rather than implements it. ``kinds.yml`` was the file
the shared registry shape was modelled on and the last dimension still running its own
copy of it — its own name regex, its own collect-and-raise, its own missing-file
message — which meant "the four dimensions share one shape" was mechanically true of
three and only structurally true of the fourth, with nothing stopping the two from
drifting. What is left here is what is genuinely this dimension's own: the
:data:`KIND` declaration, the archetype-dependent policy rules and the edge-symmetry
rule (both reached through :attr:`registry.Dimension.entries_check`, so they land in
the shared loader's single refusal), and the typed :class:`Kind` view the rest of the
package reads. What left is every line that was a second copy of something.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import registry as registry_module
from .registry import Dimension, Entry, Registry, RegistryError, mapping_field, one_of, string_list

#: ``kinds.yml`` failing validation is a registry failing validation, and since KYO-104
#: that is one class for all four dimensions rather than one per module — the same fold
#: as the loader itself, for the same reason: two exception types raised for the same
#: kind of mistake is two ``except`` clauses that can fall out of step. Kept as a name
#: because it reads better at the catch sites that are specifically about this file
#: (``doctor_registry.check_kind_registry``) and because "the kind registry is broken" is what
#: those callers mean. ``contracts.StatusRegistryError`` stays a class of its own for
#: the opposite reason: it is raised for a *different* failure — a file that parses and
#: whose every entry is well formed, arranged into a ladder the contracts cannot bind
#: to — and its remedy differs accordingly.
KindRegistryError = RegistryError

#: Same shape as a record id's kind slug (commands.KIND_RE derives from this — law 3),
#: and since KYO-104 the same *object* as every other registry name's shape rather than
#: a second compilation of the same pattern. The name stays because `commands.KIND_RE`
#: derives from it and because a reader of this module should not have to know which
#: dimension's regex is the canonical one — they are all one regex now.
KIND_NAME_RE = registry_module.NAME_RE

ARCHETYPES: tuple[str, ...] = ("work", "knowledge")

WRITE_SCOPES: tuple[str, ...] = ("none", "queries-only", "code", "prod-data")

#: A ``work`` kind's policy carries exactly these keys — no more, no less.
WORK_POLICY_KEYS: frozenset[str] = frozenset(
    {"requires_criteria", "dispatchable", "unattended_ok", "write_scope", "swept", "counted"}
)
#: KYO-61: ``policy.worker_model`` — which model a dispatch of this kind runs on,
#: overriding ``kyosei/config.yml``'s project-wide default and overridden in turn by
#: ``kyo dispatch --model``. Optional rather than required for the same reason the
#: config key is: every registry written before this release has no line for it, and
#: absence means "this kind has no opinion; use the project's", which is a real
#: answer and the common one. It sits beside ``dispatchable`` / ``unattended_ok`` /
#: ``write_scope`` because it is the same shape of thing — per-kind dispatch policy —
#: and this is the record's own argument for putting it here: what a brief *asks
#: for* is what tracks the difference, and the brief is generated per kind.
#:
#: KYO-497: ``policy.carried`` — a record of this kind is a carried item (a standing
#: decision, an open question or a reminder the next shift must see). Optional and
#: false when absent, so every registry written before it loads unchanged. Its
#: consumers: ``cmd_brief`` (the "reminders" section, with each item's age),
#: ``contracts.check_carried_settle_why`` (entering a complete-role status needs
#: ``--why``), and ``cmd_records.clear_gates_on_carried_settle`` (settling clears the
#: record's open gates). A key rather than a test for a kind's name because projects
#: rename kinds and the registry is where policy lives -- KYO-521 renamed the shipped
#: kind itself (to ``reminder``) without touching a line that reads this key.
WORK_POLICY_OPTIONAL_KEYS: frozenset[str] = frozenset({"worker_model", "carried"})
WORK_POLICY_BOOL_KEYS: frozenset[str] = frozenset(
    {"requires_criteria", "dispatchable", "unattended_ok", "swept", "counted", "carried"}
)

#: A ``knowledge`` kind's policy carries these, plus optionally ``requires_evidence_links``,
#: whose consumer is `kyo new` (KYO-521): filing such a kind directly is refused unless
#: ``--evidence-file`` attaches the evidence in the same act
#: (``cmd_records._evidence_at_filing``).
KNOWLEDGE_POLICY_REQUIRED_KEYS: frozenset[str] = frozenset({"amendable", "swept", "counted"})
#: KYO-554 adds ``raises_docs_obligations``: a record of such a kind raises a standing docs
#: obligation on each section it names (``cmd_records.raise_standing_obligations``).
KNOWLEDGE_POLICY_OPTIONAL_KEYS: frozenset[str] = frozenset(
    {"requires_evidence_links", "raises_docs_obligations"}
)
KNOWLEDGE_POLICY_BOOL_KEYS: frozenset[str] = frozenset(
    {"amendable", "swept", "counted", "requires_evidence_links", "raises_docs_obligations"}
)

#: ``accepts_from`` may name these in addition to another defined kind; ``produces`` may not
#: target either: they are promotion *sources*, not kinds anything can produce
#: (``docs/design/charter.md``, "The kind registry" — the flow edges are validated at
#: promote time, over kinds that are actually defined).
PSEUDO_SOURCES: frozenset[str] = frozenset({"intake", "idea"})


#: KYO-104: kinds, the dimension the shared shape was modelled on and the last
#: one to be loaded by it. There is no ``TOP_LEVEL_KIND_KEYS`` constant any more: the
#: keys a kind entry may carry ARE the three fields below plus ``summary``, and the
#: refusal that lists them reads them off this declaration (law 3).
#:
#: ``required=True``, which no other dimension is: a project with no ``kinds.yml`` has
#: no vocabulary for what a record is, so every command fails and the honest thing is to
#: say which file is missing and how to get it back. ``scopes.yml`` and ``gate-tags.yml``
#: are absent in every store written before their release and that is not an error;
#: ``statuses.yml``'s absence means *the packaged ladder*. This one means stop.
#:
#: ``accepts_from``/``produces`` are optional *and* accept the empty list, which is the
#: opposite convention from ``SCOPE.paths`` — see :func:`registry.string_list`. Their
#: cross-references are checked by :func:`_kind_rule_problems`, not here, because no
#: per-field validator can see the other entries.
KIND = Dimension(
    singular="kind",
    plural="kinds",
    what="what a record IS, and the policy that follows from it",
    required=True,
    required_fields={
        "archetype": one_of(*ARCHETYPES),
        # Required rather than optional, and validated for shape only: a kind whose
        # policy is absent is not a kind with default policy, it is a kind nothing can
        # decide anything about — `requires_criteria`, `dispatchable` and `write_scope`
        # are all read on the assumption the registry stated them. What its keys may be
        # depends on `archetype`, which a field validator cannot see.
        "policy": mapping_field,
    },
    optional_fields={
        "accepts_from": string_list(allow_empty=True),
        "produces": string_list(allow_empty=True),
    },
    # Wrapped in a lambda only to defer the name: the rules read the `Kind` view and the
    # policy tables, so they are written below, and the declaration belongs up here with
    # its siblings rather than trailing the code it points at.
    entries_check=lambda entries, declared: _kind_rule_problems(entries, declared),
)


@dataclass(frozen=True)
class Kind:
    """One parsed, validated entry from the registry."""

    name: str
    archetype: str
    summary: str
    accepts_from: tuple[str, ...]
    produces: tuple[str, ...]
    policy: dict[str, object]

    @property
    def requires_criteria(self) -> bool:
        """``False`` for every ``knowledge`` kind; ``policy.requires_criteria`` for ``work``."""
        return bool(self.policy.get("requires_criteria", False))

    @property
    def counted(self) -> bool:
        return bool(self.policy.get("counted", False))

    @property
    def carried(self) -> bool:
        """``policy.carried`` (KYO-497); false when absent and on every knowledge kind."""
        return self.policy.get("carried", False) is True

    @property
    def requires_evidence_links(self) -> bool:
        """``policy.requires_evidence_links`` (KYO-521's consumer: `kyo new` refuses to
        file this kind without ``--evidence-file``); false when absent and on every work
        kind."""
        return self.policy.get("requires_evidence_links", False) is True

    @property
    def raises_docs_obligations(self) -> bool:
        """``policy.raises_docs_obligations`` (KYO-554): filing or editing a record of this
        kind raises a standing docs obligation on each section it names. False when absent
        and on every work kind."""
        return self.policy.get("raises_docs_obligations", False) is True

    @property
    def accepts_intake(self) -> bool:
        """May a human file this kind directly, with `kyo new`? (KYO-43)

        ``intake`` is the registry header's own word for it: "a human files this kind
        directly (the 'Bugs' surface in the Kyosei charter's 'human's surfaces are
        Scratchpad, Ideas, and Bugs')". Kinds carry it deliberately and
        asymmetrically — ``bug`` has it, ``task`` pointedly does not, with a comment
        in ``kinds.yml`` saying why — and that asymmetry is the encoded design
        decision about which surfaces
        a human may file into without ceremony.
        """
        return "intake" in self.accepts_from

    def accepts_kind(self, source_kind: str) -> bool:
        """May a record of ``source_kind`` spawn one of this kind? (KYO-50)

        The third source an ``accepts_from`` entry can name — after ``intake``
        (`kyo new`) and ``idea`` (`kyo promote`) — is another kind, and this is the
        predicate `kyo spawn` enforces on it. Deliberately reads ``accepts_from``
        rather than the source's ``produces``: the two are validated symmetric at
        load time (see :func:`_kind_rule_problems`), so they cannot disagree, and the
        *target's* entry is the one that states who may create it — the same
        direction :attr:`accepts_intake` and `kyo promote`'s check read.
        """
        return source_kind in self.accepts_from


@dataclass(frozen=True)
class KindRegistry:
    """Every kind defined in one project's ``kyosei/kinds.yml``.

    A typed view over the shared :class:`registry.Registry` that loaded it, the same
    shape ``contracts.StatusLadder`` is over ``statuses.yml`` (KYO-104): the loader
    answers "is this file well formed", and this answers the questions the rest of the
    package actually asks — what may `kyo new` file, what may `kyo spawn` create. It
    keeps the loaded registry rather than only its path so that a refusal it raises is
    the shared refusal, in this dimension's own words, and not a second wording of it.
    """

    registry: Registry
    kinds: dict[str, Kind]

    @property
    def path(self) -> Path:
        """The file these kinds were read from — ``registry.path``, named here because
        four refusal messages in ``cmd_records`` and ``cmd_views`` print it."""
        return self.registry.path

    def __contains__(self, name: str) -> bool:
        return name in self.kinds

    def __getitem__(self, name: str) -> Kind:
        return self.kinds[name]

    def get(self, name: str) -> Kind | None:
        return self.kinds.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self.kinds)

    @property
    def direct_filing_names(self) -> list[str]:
        """Every kind `kyo new` will file directly, read from this project's own
        registry (KYO-43.c2) — never a hardcoded list, so a project that adds
        ``intake`` to a kind can file it directly with no code change."""
        return [name for name in self.names if self.kinds[name].accepts_intake]

    @property
    def promotable_names(self) -> list[str]:
        """Every kind `kyo promote` will accept from an idea. The other half of what
        a caller refused by `kyo new` needs to know: which door is open instead."""
        return [name for name in self.names if "idea" in self.kinds[name].accepts_from]

    def spawn_targets(self, source_kind: str) -> list[str]:
        """Every kind `kyo spawn` will create from a record of ``source_kind``
        (KYO-50) — read from this project's own registry, never a hardcoded list,
        so a project that adds a kind-to-kind edge gets a traversable transition
        with no code change.

        The third member of the same family as :attr:`direct_filing_names` and
        :attr:`promotable_names`, one per source an ``accepts_from`` entry can name,
        and the answer a refused caller needs: which door is open instead.

        A pseudo-source spawns nothing. ``intake`` and ``idea`` are not kinds and no
        record ever *has* one as its kind, so asking what they can spawn is asking
        the wrong question — those two edges are `kyo new`'s and `kyo promote`'s, and
        answering ``['bug']`` here would name a transition `kyo spawn` cannot make.
        """
        if source_kind in PSEUDO_SOURCES:
            return []
        return [name for name in self.names if self.kinds[name].accepts_kind(source_kind)]

    def spawn_sources(self, target_kind: str) -> list[str]:
        """Every kind a ``target_kind`` can be spawned *from* — :meth:`spawn_targets`
        read backwards, and the registry's answer to "is `kyo spawn` a way in to this
        kind at all?" (KYO-68).

        The fourth member of the family, and the one that completes it. The other
        three answer *what can this verb create*, which is what a caller browsing the
        registry wants; this answers *which verbs create THIS kind*, which is what a
        caller who has just been refused wants. KYO-68 was filed because the second
        question had no accessor: each refusal answered it from a hand-written branch
        listing the doors that existed when the message was written, so nothing
        checked the enumeration against the registry after a new door shipped.

        Registry truth only, deliberately. Whether any record exists to spawn *from*
        is a fact about the store, and lives in ``cmd_records._live_spawn_sources``.
        Keeping the two apart is what lets a refusal name the verb whenever this is
        non-empty without ever naming a command that would fail. Order is the
        registry's own ``accepts_from`` order, which is the order a refusal prints.
        """
        return [
            token
            for token in self.require(target_kind).accepts_from
            if token not in PSEUDO_SOURCES
        ]

    def require(self, name: str) -> Kind:
        """Look up a kind, or fail loudly listing every known one (law 6).

        The refusal is :func:`registry.unknown_name_refusal`'s — one sentence for all
        four dimensions, which is the KYO-104 fold reaching the last place kinds had a
        wording of its own. It says more than the sentence it replaces: which file, what
        the file does declare, and the entry that would make the name legal.
        """
        self.registry.require(name)
        return self.kinds[name]


def registry_graph(registry: KindRegistry) -> tuple[list[dict], list[dict]]:
    """The registry as a graph: kinds + ``intake``/``idea`` pseudo-nodes, promotion
    edges from ``accepts_from``/``produces`` (charter: "the viewer renders it as a
    circles-and-arrows kind map"). One source of truth for this shape (law 3) —
    ``cmd_views.cmd_map`` (``kyo map --json``) and the desktop app both read this
    rather than each deriving it themselves (KYO-11.c4).
    """
    nodes = [
        {
            "id": name,
            "type": "kind",
            "archetype": kind.archetype,
            "summary": kind.summary,
            "policy": kind.policy,
        }
        for name, kind in sorted(registry.kinds.items())
    ]
    nodes += [{"id": pseudo, "type": "pseudo"} for pseudo in ("intake", "idea")]

    edges = []
    for name in registry.names:
        for source in registry[name].accepts_from:
            edges.append({"from": source, "to": name})

    return nodes, edges


def _from_registry(loaded: Registry) -> KindRegistry:
    """The typed view over a validated registry — every field already checked.

    Nothing here re-validates: an :class:`registry.Entry` only exists if every required
    field passed, so ``archetype`` is one of :data:`ARCHETYPES` and ``policy`` is a
    mapping by the time this runs, and :func:`_kind_rule_problems` has had its say about
    what they contain. A second "just in case" check here would be a second opinion
    about the same file, which is how two code paths start.
    """
    kinds = {
        name: Kind(
            name=name,
            archetype=str(loaded[name].get("archetype")),
            summary=loaded[name].summary,
            accepts_from=tuple(loaded[name].get("accepts_from") or ()),
            produces=tuple(loaded[name].get("produces") or ()),
            policy=dict(loaded[name].get("policy") or {}),
        )
        for name in loaded.declared_names
    }
    return KindRegistry(registry=loaded, kinds=kinds)


def load_kind_registry(kyosei_dir: Path) -> KindRegistry:
    """Load and validate ``kyosei/kinds.yml``. Raises :class:`RegistryError` loudly.

    One line, because since KYO-104 that is all this is: the shared loader reads the
    file, applies the field rules :data:`KIND` declares and the kind rules it points at,
    and raises one refusal listing everything wrong with it.
    """
    return _from_registry(registry_module.load(kyosei_dir, KIND))


def _bool_problem(name: str, key: str, value: object) -> list[str]:
    if isinstance(value, bool):
        return []
    return [f"kind {name!r}: policy.{key} must be true/false, got {value!r}"]


def _policy_problems(name: str, archetype: str, policy: Mapping[str, object]) -> list[str]:
    """Which policy keys this kind's ``archetype`` requires, permits and refuses.

    The rule the shared field machinery cannot express, and the reason
    :attr:`registry.Dimension.entries_check` exists: a :data:`registry.FieldValidator`
    is handed one value and cannot see that ``policy``'s legal keys are one set for
    ``work`` and another for ``knowledge``. The archetype is trusted here because the
    loader has already refused anything outside :data:`ARCHETYPES`.
    """
    problems: list[str] = []
    keys = set(policy)
    if archetype == "work":
        missing = sorted(WORK_POLICY_KEYS - keys)
        extra = sorted(keys - WORK_POLICY_KEYS - WORK_POLICY_OPTIONAL_KEYS)
        if missing:
            problems.append(
                f"kind {name!r} (archetype work): policy is missing {', '.join(missing)}"
            )
        if extra:
            problems.append(
                f"kind {name!r} (archetype work): policy carries unknown key(s) {', '.join(extra)}"
            )
        for key in sorted(WORK_POLICY_BOOL_KEYS & keys):
            problems += _bool_problem(name, key, policy[key])
        if "write_scope" in keys and policy["write_scope"] not in WRITE_SCOPES:
            problems.append(
                f"kind {name!r}: policy.write_scope {policy['write_scope']!r} is not one of "
                f"{', '.join(WRITE_SCOPES)}"
            )
        # KYO-61: any non-empty string. Deliberately not an enum — the legal model
        # names belong to whatever harness the controller spawns workers with, not
        # to Kyosei, and a hardcoded list here would go stale the week after it
        # shipped while refusing values that work.
        if "worker_model" in keys and (
            not isinstance(policy["worker_model"], str) or not policy["worker_model"].strip()
        ):
            problems.append(
                f"kind {name!r}: policy.worker_model must be a non-empty model name, or be "
                f"omitted to inherit the project default; got {policy['worker_model']!r}"
            )
    elif archetype == "knowledge":
        missing = sorted(KNOWLEDGE_POLICY_REQUIRED_KEYS - keys)
        extra = sorted(keys - KNOWLEDGE_POLICY_REQUIRED_KEYS - KNOWLEDGE_POLICY_OPTIONAL_KEYS)
        if missing:
            problems.append(
                f"kind {name!r} (archetype knowledge): policy is missing {', '.join(missing)}"
            )
        if extra:
            problems.append(
                f"kind {name!r} (archetype knowledge): policy carries unknown key(s) "
                f"{', '.join(extra)}"
            )
        for key in sorted(KNOWLEDGE_POLICY_BOOL_KEYS & keys):
            problems += _bool_problem(name, key, policy[key])
    return problems


def _tokens(body: object, key: str) -> list[str]:
    """One entry's ``accepts_from`` or ``produces``, read off the raw declaration.

    Raw rather than validated, so that a kind with a mistake *elsewhere* in it still has
    its edges checked in the same pass (law 6) — the reason
    :attr:`registry.Dimension.entries_check` is handed the declaration and not only the
    entries that survived it. Anything that is not a list of strings yields nothing: the
    field validator has already said so, and saying it twice in one message is not
    louder, only longer.
    """
    value = body.get(key) if isinstance(body, dict) else None
    if isinstance(value, list) and all(isinstance(token, str) for token in value):
        return value
    return []


def _edge_problems(declared: Mapping[str, object]) -> list[str]:
    """Every promotion edge names a kind that exists, in both directions (law 3).

    ``accepts_from`` and ``produces`` are two statements of one fact, so the file is
    refused unless they agree — that symmetry is what lets :meth:`Kind.accepts_kind`
    read one of them and be right about both.

    "Exists" means *declared*, not *valid*: a kind whose own definition was refused (a
    bad summary, say) is still a kind this file declares, and reporting every reference
    to it as "undefined" would print one mistake once per reference.
    """
    problems: list[str] = []
    known = set(declared)
    accepts_from = {name: _tokens(body, "accepts_from") for name, body in declared.items()}
    produces = {name: _tokens(body, "produces") for name, body in declared.items()}
    for name in sorted(declared):
        for token in accepts_from[name]:
            if token in PSEUDO_SOURCES:
                continue
            if token not in known:
                problems.append(f"kind {name!r}: accepts_from names undefined kind {token!r}")
                continue
            if name not in produces.get(token, []):
                problems.append(
                    f"kind {name!r}: accepts_from includes {token!r}, but kind {token!r}'s "
                    f"produces does not include {name!r} (edges must be symmetric)"
                )
        for token in produces[name]:
            if token in PSEUDO_SOURCES:
                problems.append(
                    f"kind {name!r}: produces must not target {token!r} "
                    "(intake/idea are sources only)"
                )
                continue
            if token not in known:
                problems.append(f"kind {name!r}: produces names undefined kind {token!r}")
                continue
            if name not in accepts_from.get(token, []):
                problems.append(
                    f"kind {name!r}: produces includes {token!r}, but kind {token!r}'s "
                    f"accepts_from does not include {name!r} (edges must be symmetric)"
                )
    return problems


def _kind_rule_problems(
    entries: Mapping[str, Entry], declared: Mapping[str, object]
) -> list[str]:
    """This dimension's own rules, for :attr:`registry.Dimension.entries_check`.

    The two things the shared field machinery cannot see — a field whose legal contents
    depend on a sibling field, and entries that constrain each other — collected the way
    the loader collects its own so that a registry with four mistakes is still fixed in
    one pass (law 6). The loader raises them; this only finds them.

    ``policy`` is read off ``entries``, because its rules branch on ``archetype`` and
    only a validated entry is guaranteed to have one; the edges are read off ``declared``
    for the reason :func:`_tokens` gives.
    """
    problems: list[str] = []
    for name in sorted(entries):
        entry = entries[name]
        policy = entry.get("policy")
        if isinstance(policy, dict):
            problems += _policy_problems(name, str(entry.get("archetype")), policy)
    return problems + _edge_problems(declared)
