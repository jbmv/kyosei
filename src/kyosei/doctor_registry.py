"""Doctor checks on a project's declaration registries: kinds and their grants,
scopes, gate tags, and the documents `kyo docs suggest` could draft. Registered in
:data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import json

from . import docs_suggest, scaffold
from . import kinds as kinds_module
from . import registry as registry_module
from .config import CONFIG_NAME, KYOSEI_DIR
from .store import Store


def check_docs_suggestions(store: Store) -> list[str]:
    """KYO-303.c2: `kyo docs suggest` has something to draft. A NOTICE, and at most one.

    **This is the only place a suggestion is allowed to reach a reader who did not ask
    for one, and it is the whole of it.** Nothing in this module's PROBLEM tier reads a
    suggestion: a project that never runs `kyo docs suggest` is not failed, not nudged
    twice, and not told its declaration is wrong — because it is not wrong. It is the
    authority, and the criterion this check answers to says so in as many words.

    One finding, never one per suggestion. The count is the whole message: a reader who
    wants the drafts runs the command, which prints each one with its evidence. A
    doctor that reproduced them here would be a second rendering of the same data, one
    of the two would drift, and the noise would be paid on every Stop hook.

    A notice rather than a problem, and the bar :data:`kyosei.doctor.NOTICE_CHECKS` states
    is met the way ``orchestrator_only_setup``'s is. The condition is EXPECTED rather than
    broken — a numbered-looking document that nobody has declared numbered is a perfectly
    good state to be in, and whether to declare it is an editorial judgment about a
    project's own file that Kyosei has no standing to call wrong. And failing would cost
    more than the finding is worth: `kyo doctor` runs from this project's ``Stop`` and
    ``SessionEnd`` hooks, so exit 3 would paint every stop red over a draft.

    Silent in a project that has declared ``docs_suggest_notice: false``, which is the
    one stated line c2 requires, and silent in any project whose ``documents.yml`` does
    not load. That file's own refusal is what a reader gets — `kyo docs check` and every
    other reader of it raise it by name — and a second, vaguer voice saying the same
    thing from a check about drafts is how one error becomes two bugs.
    """
    if store.config.docs_suggest_notice is False:
        return []
    try:
        found = docs_suggest.suggest(store.config.kyosei_dir, store.config.repo_root)
    except registry_module.RegistryError:
        return []
    # KYO-562.c3: a question (a numbered file whose history cannot say it is
    # append-only) drafts nothing, so it is not a draft declaration to count here.
    found = [suggestion for suggestion in found if suggestion.drafted]
    if not found:
        return []
    kinds = ", ".join(sorted({suggestion.kind for suggestion in found}))
    return [
        f"`kyo docs suggest` has {len(found)} draft declaration(s) for "
        f"{KYOSEI_DIR}/documents.yml ({kinds}) — run it to see each one with the "
        "evidence beside it and the exact YAML to paste. Nothing is written and nothing "
        "here is enforced: your declaration is the authority, and no `kyo doctor` "
        "problem depends on any of this. Considered them and want none? "
        f"`docs_suggest_notice: false` in {KYOSEI_DIR}/{CONFIG_NAME} stops this line"
    ]


def check_kind_registry(store: Store) -> list[str]:
    """``kyosei/kinds.yml`` validates, and every record's kind is defined in it."""
    try:
        registry = kinds_module.load_kind_registry(store.config.kyosei_dir)
    except kinds_module.KindRegistryError as exc:
        return [str(exc)]
    known = set(registry.kinds)
    # KYO-509: the remedy is named, and it works -- `kyo edit --kind` re-kinds the
    # record (same archetype only), or the kind goes back into the registry.
    return [
        f"{row['id']} has kind {row['kind']!r}, which is not defined in "
        f"kyosei/kinds.yml (known kinds: {', '.join(sorted(known))}) -- re-kind it with "
        f"`kyo edit {row['id']} --kind <kind>` (a kind of the same archetype), or restore "
        f"{row['kind']!r} to kyosei/kinds.yml"
        for row in store.conn.execute("SELECT id, kind FROM records ORDER BY seq")
        if row["kind"] not in known
    ]


def _default_kind_registry(store: Store) -> kinds_module.KindRegistry | None:
    """The kind registry this project's ``kinds.yml`` was copied from, or ``None`` (KYO-394).

    KYO-309's direction rule, applied to the kinds asset: in the checkout that builds
    this package the source is ``src/kyosei/defaults/kinds.yml``, not the installed
    build's copy of it, which can only be older. Every consumer reads the running
    build's packaged copy. ``None`` when neither loads — there is nothing to compare
    against, and a broken packaged default is not this project's finding.
    """
    asset = next(asset for asset in scaffold.DEFAULT_ASSETS if asset.source == "kinds.yml")
    source = scaffold.repo_asset_source(store.config.repo_root, asset)
    if source is None:
        packaged = scaffold.defaults_dir()
        source = packaged / asset.source if packaged is not None else None
    if source is None or not source.is_file():
        return None
    try:
        return kinds_module.load_kind_registry(source.parent)
    except kinds_module.KindRegistryError:
        return None


def _flow_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _grant_consequence(name: str, missing: list[str]) -> str:
    """What each missing grant refuses, in the command's own terms (KYO-50's three doors)."""
    refused = []
    for token in missing:
        if token == "intake":
            refused.append(f"`kyo new {name}` is refused")
        elif token == "idea":
            refused.append(f"`kyo promote <idea-file> --kind {name}` is refused")
        else:
            refused.append(f"`kyo spawn <{token}-id> --kind {name}` is refused")
    return ", ".join(refused)


def check_kind_grants_missing(store: Store) -> list[str]:
    """KYO-394: a kind whose ``accepts_from`` lacks an entry the packaged default grants.

    No command merges ``kinds.yml``, so the copy `kyo init` made is the copy the project
    keeps, and a grant the default gained later is missing until a command is refused
    (docs/design/doctor.md#kyo-394). The shape is ``procedures_rules_differ``'s: this
    project's copy compared with the default it came from.

    ``accepts_from`` ONLY, never the whole entry: summaries, policy and ``produces`` are
    the project's to edit, and this is the one field whose absence refuses a command
    outright. A kind only one side defines is not compared (a project's own kinds are
    its business; a default kind the project never had is a different question), and a
    grant naming another kind is reported only when that kind exists here — the edit
    could not be made otherwise.

    **The remedy is the printed edit, not a new command.** It can be made exact: the
    finding prints the whole ``accepts_from`` line to write (the project's own entries
    first, the missing ones after), and for a grant naming another kind it prints that
    kind's ``produces`` line as well, because the loader refuses an ``accepts_from`` edge
    whose source does not ``produces`` it back. A merge command would be more surface
    than a one-line hand edit to a file the project owns.

    A NOTICE, and it argues the two things :data:`kyosei.doctor.NOTICE_CHECKS` asks of a
    remedy-naming check. The condition is EXPECTED rather than broken: a registry is
    project-editable, and a project may have narrowed a kind on purpose, as the packaged
    default itself once did. And failing
    would cost more than it is worth: exit 3 from the ``Stop`` hook over a considered
    choice. Unlike ``procedures_rules_differ`` it can be declined, on
    ``docs_suggest_notice``'s convention: ``kind_grants_notice: false`` in
    ``config.yml`` silences it, because a notice about a deliberate narrowing that
    returned on every stop would train the reader to stop reading doctor (KYO-33).
    """
    if store.config.kind_grants_notice is False:
        return []
    try:
        mine = kinds_module.load_kind_registry(store.config.kyosei_dir)
    except kinds_module.KindRegistryError:
        return []  # `kind_registry` reports it by name; one error, one voice
    default = _default_kind_registry(store)
    if default is None:
        return []
    findings = []
    for name in mine.names:
        packaged = default.get(name)
        if packaged is None:
            continue
        have = list(mine[name].accepts_from)
        missing = [
            token
            for token in packaged.accepts_from
            if token not in have and (token in kinds_module.PSEUDO_SOURCES or token in mine)
        ]
        if not missing:
            continue
        edits = [f"under `{name}:` write `accepts_from: {_flow_list([*have, *missing])}`"]
        for token in missing:
            if token in kinds_module.PSEUDO_SOURCES or name in mine[token].produces:
                continue
            produces = [*mine[token].produces, name]
            edits.append(f"under `{token}:` write `produces: {_flow_list(produces)}`")
        findings.append(
            f"kind {name!r} in {KYOSEI_DIR}/kinds.yml does not accept "
            f"{', '.join(repr(token) for token in missing)}, which the packaged default "
            f"grants it, so {_grant_consequence(name, missing)}. No command merges "
            f"kinds.yml; to take the default's grant, {' and '.join(edits)}. Narrowed "
            f"on purpose? `kind_grants_notice: false` in {KYOSEI_DIR}/{CONFIG_NAME} "
            "stops this notice (for every kind: it cannot be scoped to one)"
        )
    return findings


def _policy_value(value: object) -> str:
    """A policy value as a line in kinds.yml spells it (``true``, not ``True``)."""
    return json.dumps(value) if isinstance(value, bool) else str(value)


def check_kind_policy_missing(store: Store) -> list[str]:
    """KYO-556.c5: a knowledge kind whose ``policy`` lacks a key the packaged default sets.

    ``check_kind_grants_missing``'s shape, for the other field a build can add to the
    default that no command merges into a project's copy. KYO-554 gave the packaged
    ``decision`` ``raises_docs_obligations: true``; a store scaffolded before it keeps a
    ``decision`` without the key, the loader reads the absence as ``false``, and the
    store never raises a decision obligation with nothing saying why.

    **Absent keys only.** A key the project's copy states, whatever its value, is the
    project's answer: a ``decision`` declaring ``raises_docs_obligations: false`` has
    declined the behaviour on purpose and is not reported. Only kinds that are
    ``knowledge`` on both sides are compared; work-kind policy and a project's own kinds
    are not this check's business.

    **The remedy is the printed line**: the key and the default's value, under the
    kind's ``policy:`` block. A NOTICE on ``kind_grants_missing``'s two arguments (a
    project may leave the key out on purpose; exit 3 from the ``Stop`` hook over it
    would cost more than it is worth), declined for every kind by
    ``kind_policy_notice: false`` in ``config.yml``.
    """
    if store.config.kind_policy_notice is False:
        return []
    try:
        mine = kinds_module.load_kind_registry(store.config.kyosei_dir)
    except kinds_module.KindRegistryError:
        return []  # `kind_registry` reports it by name; one error, one voice
    default = _default_kind_registry(store)
    if default is None:
        return []
    findings = []
    for name in mine.names:
        packaged = default.get(name)
        kind = mine[name]
        if packaged is None or kind.archetype != "knowledge" or packaged.archetype != "knowledge":
            continue
        for key, value in packaged.policy.items():
            if key in kind.policy:
                continue
            line = f"{key}: {_policy_value(value)}"
            findings.append(
                f"kind {name!r} in {KYOSEI_DIR}/kinds.yml does not set policy key {key!r}, "
                f"which the packaged default sets to {_policy_value(value)}; absent, it "
                "reads as false. No command merges kinds.yml; to take the default, under "
                f"`{name}:`'s `policy:` add `{line}`. Left out on purpose? "
                f"`kind_policy_notice: false` in {KYOSEI_DIR}/{CONFIG_NAME} stops this "
                "notice (for every kind and key)"
            )
    return findings


def kind_registry_notices(store: Store) -> list[str]:
    """KYO-556.c6: every kind-registry notice, for the BUILD CHANGED brief.

    The two checks above, run as `kyo doctor` runs them (each honours its own decline
    key), so an upgrade states what it asks of an existing store at session start
    rather than only to someone who runs `kyo doctor`.
    """
    return [*check_kind_grants_missing(store), *check_kind_policy_missing(store)]


def check_scope_registry(store: Store) -> list[str]:
    """``kyosei/scopes.yml`` validates, and every declared scope is still in it (KYO-100).

    The second half is what makes this worth running rather than a duplicate of the
    write-time check. `kyo new --scope` refuses a scope the registry does not declare,
    but the registry is an ordinary editable file: a scope can be renamed or removed
    the day after the records naming it were written, and nothing at write time is
    watching then. A record pointing at a scope that no longer exists is exactly the
    dangling reference ``check_orphans`` exists for one table over.

    An absent ``scopes.yml`` is not a finding on its own — it is what every store
    written before this release has, and a project may legitimately declare no scopes
    (:mod:`kyosei.registry`). It becomes one only when a record names a scope, because
    then the file's absence is a real dangling reference and the message says so.
    """
    try:
        scopes = registry_module.load_scope_registry(store.config.kyosei_dir)
    except registry_module.RegistryError as exc:
        return [str(exc)]
    return [
        registry_module.unknown_name_refusal(scopes, row["scope"])
        + f" — declared by {row['id']}"
        for row in store.conn.execute(
            "SELECT id, scope FROM records WHERE scope IS NOT NULL ORDER BY seq"
        )
        if row["scope"] not in scopes
    ]


def check_gate_tags(store: Store) -> list[str]:
    """``kyosei/gate-tags.yml`` validates, and every tag on a gate is still in it (KYO-102).

    The third instance of the shape :func:`check_scope_registry` and
    :func:`check_statuses` already hold, and it earns its keep the same way: every path
    that writes a tag refuses an undeclared one at write time — since KYO-130 that is
    `kyo gate --tag` and `kyo retag`, which share the single
    :func:`kyosei.cmd_links._validated_tags` call site rather than holding a refusal
    each — but the registry is an ordinary editable file, so a tag can be renamed or
    dropped the day after the gates carrying it were written and nothing at write time
    is watching then. A gate tagged with a word the registry no longer knows is
    invisible to the one surface the tag exists for — `kyo list --gate-tag` refuses that
    word outright — so the gate is not merely mislabelled, it has silently left the
    query.

    **Cleared gates are checked too**, unlike the ``--gate-tag`` filter, which reads
    only open ones. The filter is a question about what is being waited on *now*; this
    is a question about whether the store's own references resolve, and a dangling
    reference on a cleared gate is exactly as dangling.

    Nothing is rewritten. A rename that stranded gates is a decision for whoever made
    it, and the refusal is the registry's own sentence rather than a second wording of
    it (law 3).
    """
    try:
        tags = registry_module.load_gate_tag_registry(store.config.kyosei_dir)
    except registry_module.RegistryError as exc:
        return [str(exc)]
    failures: list[str] = []
    for row in store.conn.execute(
        "SELECT id, tags_json FROM gates WHERE tags_json IS NOT NULL ORDER BY record_id, seq"
    ):
        for tag in json.loads(row["tags_json"]):
            if tag not in tags:
                failures.append(
                    registry_module.unknown_name_refusal(tags, tag)
                    + f" — carried by gate {row['id']}"
                )
    return failures
