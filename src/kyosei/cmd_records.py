"""``new`` / ``promote`` / ``spawn`` / ``edit`` / ``move`` / ``wont-do`` / ``void``.

A record's own life: created (from intake, from an ``ideas/`` file, or from another
record one kind-registry edge upstream), edited, and moved through the status flow
to a terminal state. Touch validation lives here too, because ``--touches-file`` is
accepted by exactly these commands.

The three creation verbs are one per source the kind registry's ``accepts_from`` can
name — ``intake`` -> `kyo new`, ``idea`` -> `kyo promote`, another kind ->
`kyo spawn` (KYO-50) — and each enforces its own edge against the registry rather
than trusting the caller to have read it.
"""

from __future__ import annotations

import re
from argparse import Namespace
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from . import commands, docs_obligations, docs_parse, explanations, export, kinds, prior_art
from . import ideas as ideas_module
from . import registry as registry_module
from . import sweep as sweep_module
from .clock import now
from .commands import (
    _UNMET_CRITERION_STATES,
    KIND_RE,
    Context,
    Output,
    _active_statuses,
    _add_criteria,
    _criterion_views,
    _load_registry,
    _load_status_ladder,
    _open_docs_obligation_views,
    _reason,
    _require_plan,
    _touch_record,
)
from .contracts import (
    ADDITIVE_SYMBOL,
    CONTRACT_CRITERION_TEXT_IS_ATTESTED,
    CONTRACT_FILING_REQUIRES_EVIDENCE,
    CONTRACT_KIND_NOT_DIRECTLY_FILEABLE,
    CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA,
    CONTRACT_KIND_NOT_SPAWNABLE_FROM,
    CONTRACT_RESTATE_REQUIRES_A_LIVE_CRITERION,
    CONTRACT_SPAWN_SOURCE_IS_RETIRED,
    CRITERION_STANDING_SUPERSEDED,
    DEFAULT_CRITERION_STATE,
    EDIT_DIRECT_CRITERION_STATES,
    OBLIGATION_NOT_AFFECTED,
    ROLE_COMPLETE,
    ROLE_RETIRED,
    ROLE_VERIFIED,
    SETTLED_CRITERION_STATES,
    SETTLED_ROLES,
    STALENESS_FLAG_TYPES,
    TOUCH_NEW_FILE_PREFIX,
    VERIFY_VERDICTS,
    VOID_CRITERION_STATES,
    GateView,
    check_archetype_allows_status_flow,
    check_carried_settle_why,
    check_criterion_state,
    check_plan_close_over_open_members,
    check_transition,
    check_void_is_knowledge_only,
    criterion_length_warning,
    gate_payload_first_line,
    is_directory_touch,
    is_new_file_touch,
    knowledge_disposal_remedy,
    missing_touch_remedy,
    restate_reason,
    settled_flag_why,
    standing,
    store_path,
    title_length_warning,
    touch_target,
)
from .errors import ContractViolation, KyoseiError, UsageError
from .prose import (
    STDIN,
    contains_phrase,
    fingerprint,
    normalise,
    read_lines,
    read_prose,
    single_line,
)
from .render import (
    echo_block,
    echo_value,
)
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - the import cycle is real at runtime
    # `cmd_dispatch` imports this module for its touch validation, so the runtime
    # import goes the other way and lives inside `_touch_drift_correction`. The name
    # is needed here only to annotate that function's return (KYO-307).
    from .touch_drift import DriftCorrection

#: How many live spawn sources a refusal names before it summarises the rest. The
#: point of the list is to prove the suggestion is real, which the first few do.
_SPAWN_SOURCES_SHOWN = 5


def _live_spawn_sources(
    store: Store, registry: kinds.KindRegistry, target_kind: str
) -> list[tuple[str, str]]:
    """Records that could spawn a ``target_kind`` *right now*, in id order (KYO-64).

    Reads the target's ``accepts_from`` for kind tokens (``intake``/``idea`` are the
    other two commands' edges, not `kyo spawn`'s) and then asks the store whether any
    record of those kinds actually exists — because a suggestion is only worth
    printing if the caller can act on it. The status filter is the project's own
    terminal statuses (KYO-101: the ``retired`` role, not a frozen name), the same
    predicate `kyo spawn` itself refuses on (``CONTRACT_SPAWN_SOURCE_IS_RETIRED``),
    so this can never name a source the very command it recommends would reject.
    """
    sources = registry.spawn_sources(target_kind)
    if not sources:
        return []
    ladder = _load_status_ladder(store)
    terminal = sorted(ladder.with_role(ROLE_RETIRED))
    rows = store.conn.execute(
        "SELECT id, kind FROM records"
        f" WHERE kind IN ({', '.join('?' * len(sources))})"
        f" AND status NOT IN ({', '.join('?' * len(terminal))})"
        " ORDER BY seq",
        (*sources, *terminal),
    ).fetchall()
    return [(row["id"], row["kind"]) for row in rows]


def _spawn_remedy(store: Store, registry: kinds.KindRegistry, target_kind: str) -> str | None:
    """One refusal line naming `kyo spawn`, or ``None`` when it is not a way in.

    KYO-64: `kyo new`'s and `kyo promote`'s refusals were both written under KYO-43,
    when the kind-to-kind edge had no implementation and "edit the registry" really
    was the only way through. KYO-50 built that edge and did not revisit them, so
    the messages went on offering to weaken the caller's own registry as their only
    actionable suggestion, while the transition the registry had been describing all
    along was one command away.

    KYO-68 separated two things KYO-64 had fused. *Is `kyo spawn` a way into this
    kind* is a question about the registry (:meth:`kinds.KindRegistry.spawn_sources`)
    and its answer never depends on what the store happens to hold; *which command
    line should the caller run* is a question about the store, and its answer is an
    id that may not exist yet. KYO-64 answered both with the store, so an empty store
    silently deleted the verb from the message — the exact shape KYO-68 is about, in
    the message KYO-64 had just fixed.

    So there are two lines, and which one is printed is decided by the store:

    * a live source exists — name it, with a command that runs verbatim. The status
      filter is the one `kyo spawn` itself refuses on, so this never recommends a
      command that is about to refuse in turn (see :func:`_live_spawn_sources`).
    * none does — name the verb and the source kinds the registry admits, with a
      ``<source-id>`` placeholder and the precondition stated. It is not a runnable
      command and does not read as one, which is what KYO-64's "never name an
      impossible remedy" was actually protecting.

    ``None`` now means only what it says: the target's ``accepts_from`` carries no
    kind token, so no `kyo spawn` could ever create one, however full the store is.
    """
    sources = registry.spawn_sources(target_kind)
    if not sources:
        return None
    live = _live_spawn_sources(store, registry, target_kind)
    if not live:
        source_kinds = " or ".join(repr(source) for source in sources)
        return (
            f"a {target_kind!r} is spawnable from a record of kind {source_kinds} — the "
            f"transition the registry already describes — with "
            f"`kyo spawn <source-id> --kind {target_kind} --title ...`; this store holds no "
            f"open record of {source_kinds} yet, so there is no id to put there: create or "
            "reopen one and that command becomes real"
        )
    first_id, _first_kind = live[0]
    shown = ", ".join(f"{rid} (a {rkind})" for rid, rkind in live[:_SPAWN_SOURCES_SHOWN])
    if len(live) > _SPAWN_SOURCES_SHOWN:
        shown += f", +{len(live) - _SPAWN_SOURCES_SHOWN} more"
    return (
        f"a {target_kind!r} can be spawned from a record one edge upstream, which is the "
        f"transition the registry already describes: "
        f"`kyo spawn {first_id} --kind {target_kind} --title ...`; live sources here: {shown}"
    )


def _check_kind_accepts_intake(store: Store, registry: kinds.KindRegistry, kind: str) -> None:
    """KYO-43: `kyo new` is the ``intake`` edge, and must consult the registry.

    `kyo promote` already validated its own edge (``idea`` in ``accepts_from``) and
    its ``--kind`` help says so. `kyo new` performed no such check, so every kind in
    the default registry could be filed directly — including ``task``, whose entry
    carries a comment written specifically to explain why it must not be
    ("NOT accepts_from: [intake] — the Kyosei charter's human surfaces are
    Scratchpad/Ideas/Bugs only"). A registry that states a flow rule which one of
    two write paths ignores is advisory, not enforced, and the `/kyosei` skill tells
    the controller to treat it as the source of truth.

    The refusal is deliberately not escapable by a flag. See ``docs`` in the KYO-43
    report: the escape hatch is the registry itself — a project that wants a kind
    filed directly adds ``intake`` to its ``accepts_from`` (KYO-43.c2), which is one
    reviewable line in a versioned file, whereas a ``--force`` would restore today's
    behaviour in the first script that reached for it and leave no trace that it had.

    Collect-then-report style, matching :func:`_validate_touches` and
    ``kinds._build_registry``: the caller gets every open door in one message.

    KYO-64: `kyo spawn` is one of those doors, and for the whole of KYO-50's life
    this message did not say so — it named the two edges that will not work and then
    offered editing the registry, making "weaken your own rule" the only actionable
    suggestion for a caller whose registry already described a working transition.
    The spawn line comes before the registry edit for that reason (follow the rule
    first, change it second) and only when :func:`_spawn_remedy` finds a real source.

    KYO-68: it is now printed whenever the registry admits `kyo spawn` at all, live
    source or not — see :func:`_spawn_remedy`. The three checks in this family
    (:func:`_check_kind_accepts_idea`, :func:`_check_kind_accepts_kind`) are the
    creation verbs' refusals, they are each other's remedies, and
    ``tests/test_kyo5_error_remedies.py`` now walks every (kind x verb) pair in the
    project's own registry asserting that each names the others.
    """
    kind_def = registry.require(kind)
    if kind_def.accepts_intake:
        return
    detail = [
        f"kind {kind!r} does not accept direct filing; its accepts_from is "
        f"[{', '.join(kind_def.accepts_from) or '(empty)'}] and `kyo new` is the "
        "'intake' edge (kyosei/kinds.yml)",
        f"kinds you can file directly: {', '.join(registry.direct_filing_names) or '(none)'}",
    ]
    if "idea" in kind_def.accepts_from:
        detail.append(
            f"to file a {kind!r}, write the idea under ideas/ and run "
            f"`kyo promote <idea-file> --kind {kind}` — that is the ceremony boundary "
            "this rule protects"
        )
    else:
        detail.append(
            f"`kyo promote --kind {kind}` will refuse it too ('idea' is not in its "
            f"accepts_from); kinds promotable from an idea: "
            f"{', '.join(registry.promotable_names) or '(none)'}"
        )
    spawn = _spawn_remedy(store, registry, kind)
    if spawn:
        detail.append(spawn)
    if "idea" not in kind_def.accepts_from:
        detail.append(
            f"if this project wants {kind!r} filed directly, add 'intake' to its "
            f"accepts_from in {registry.path} — the registry is project-editable and is "
            "the rule, so changing the rule is the supported way through, not a flag"
        )
    raise ContractViolation(CONTRACT_KIND_NOT_DIRECTLY_FILEABLE, detail)


def _evidence_at_filing(
    registry: kinds.KindRegistry,
    kind: str,
    args: Namespace,
    *,
    spawn_source: str | None = None,
    spawn_source_kind: str | None = None,
) -> str | None:
    """KYO-521: the ``--evidence-file`` text `kyo new` stores with the record, or ``None``.

    KYO-522: `kyo spawn` reuses this one path. A spawn whose source kind is ``carried``
    (a reminder) is refused exactly as `kyo new` is, since a reminder that was waiting on
    the operator becomes a decision when they answer in conversation, and their words are
    what the evidence rule exists for. A spawn from any other source (a proposal, an
    investigation) asks for none: the source record and the ``produced-by`` link are the
    provenance. ``--evidence-file`` is still accepted on any spawn and stored.

    A kind whose policy sets ``requires_evidence_links`` (the default ``decision``) is
    refused without it: the registry opened ``intake`` on ``decision`` so a decision made
    in conversation is filed where it was made, and the evidence -- the operator's words,
    who, when and where, then the words verbatim -- is what makes it a decision rather
    than an assertion. A flag rather than a section of ``--from``'s file, so the words
    are stored as their own bytes with their own sha256, exactly as `kyo attach` and `kyo
    confirm` store them, with nothing of the record's file framing them; the flag works
    beside ``--from`` as it does beside ``--title``.

    Read before anything else in `kyo new` reads stdin, and refused when another prose
    flag also names stdin, since one stream cannot be read twice.
    """
    source = getattr(args, "evidence_file", None)
    kind_def = registry.require(kind)
    if source is None:
        if spawn_source is None:
            if kind_def.requires_evidence_links:
                raise ContractViolation(
                    CONTRACT_FILING_REQUIRES_EVIDENCE,
                    [
                        f"kind {kind!r} sets policy.requires_evidence_links, so `kyo new "
                        f"{kind}` stores the evidence in the same act: pass --evidence-file "
                        "FILE ('-' for stdin) holding the operator's words -- who, when, "
                        "where, then the words verbatim",
                        f"a {kind} that needs design work first is not filed directly: file a "
                        f"proposal, then `kyo spawn <proposal-id> --kind {kind} --title ...` "
                        "once it is approved",
                    ],
                )
            return None
        source_def = registry.require(spawn_source_kind) if spawn_source_kind else None
        if kind_def.requires_evidence_links and source_def is not None and source_def.carried:
            raise ContractViolation(
                CONTRACT_FILING_REQUIRES_EVIDENCE,
                [
                    f"kind {kind!r} sets policy.requires_evidence_links and "
                    f"{spawn_source} is a {spawn_source_kind!r} (policy.carried), so "
                    f"`kyo spawn {spawn_source} --kind {kind}` stores the evidence in the "
                    "same act: pass --evidence-file FILE ('-' for stdin) holding the "
                    "operator's words -- who, when, where, then the words verbatim",
                    f"a {kind} spawned from a proposal or an investigation needs no "
                    "evidence flag: the source record is its provenance",
                ],
            )
        return None
    if source == STDIN:
        others = [
            flag
            for attr, flag in (
                ("from_file", "--from"),
                ("description_file", "--description-file"),
                ("criteria_file", "--criteria-file"),
                ("touches_file", "--touches-file"),
            )
            if getattr(args, attr, None) == STDIN
        ]
        if others:
            raise UsageError(
                f"--evidence-file and {others[0]} both name stdin ('-'), which can be "
                "read once; write one of them to a file"
            )
    text = read_prose(source, what="--evidence-file")
    if not text.strip():
        raise UsageError("--evidence-file must not be empty: it is stored as evidence")
    return text


def _check_kind_accepts_idea(store: Store, registry: kinds.KindRegistry, kind: str) -> None:
    """`kyo promote` is the ``idea`` edge, and must consult the registry.

    KYO-64.c2 gave this refusal the collect-then-report treatment its sibling above
    already had: it used to name the failed edge and stop — no spawn, no `kyo new`,
    not even the registry edit — so a caller refused here was told nothing they could
    act on. The spawn line comes first for the same reason it does above: a
    transition the registry already describes beats changing the registry.

    KYO-68.c3 made it a named contract. It was a bare :class:`KyoseiError` (exit 1)
    while `kyo new`'s and `kyo spawn`'s refusals of the *same* class of rejection — a
    creation verb turning a kind away because the registry's flow rules do not admit
    it — were :class:`ContractViolation` (exit 2). Exit codes are a documented
    contract here (0/1/2/3/64, ``errors.py``), so a consumer scripting against "a
    creation verb refused my kind" had to handle two of them for one situation, and
    which one it got depended on nothing the caller could see. The message text is
    unchanged apart from the ``contract ... not met`` header
    :class:`ContractViolation` adds.
    """
    kind_def = registry.require(kind)
    if "idea" in kind_def.accepts_from:
        return
    detail = [
        f"kind {kind!r} does not accept promotion from idea; its accepts_from is "
        f"[{', '.join(kind_def.accepts_from) or '(empty)'}] and `kyo promote` is the "
        "'idea' edge (kyosei/kinds.yml)",
    ]
    spawn = _spawn_remedy(store, registry, kind)
    if spawn:
        detail.append(spawn)
    if kind_def.accepts_intake:
        detail.append(
            f"a {kind!r} can be filed directly instead: `kyo new {kind} --title ...`"
        )
    detail.append(
        f"kinds promotable from an idea: {', '.join(registry.promotable_names) or '(none)'}"
    )
    detail.append(
        f"if this project wants {kind!r} promotable from an idea, add 'idea' to "
        f"its accepts_from in {registry.path} — the registry is the rule, so changing "
        "the rule is the supported way through, not a flag"
    )
    raise ContractViolation(CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA, detail)


# ------------------------------------------------------------- KYO-330: --from FILE
#
# `kyo new`/`kyo promote`/`kyo spawn` already file a record from one-line flags plus
# separate `--*-file` flags for the multi-line parts (law 5: prose enters by file or
# stdin, never as an argument). This adds one more way IN, not a second way to store:
# `--from FILE` parses a single markdown file into exactly the values those flags
# would have produced -- front matter for `title`/`scope`/`plan`/`touches`, a
# `## Criteria` section holding one criterion per list item, and everything else the
# description -- and hands them to the same write path the flags already use. That is
# what makes KYO-330.c2 ("`--from` files the identical record") true by construction,
# not by two code paths a future edit could let drift apart.


@dataclass(frozen=True)
class _RecordFile:
    """One `--from FILE`, parsed into what the separate flags would have produced.

    `plan` is ``None`` whether the file omitted `plan:` or the calling command has no
    `--plan` for `--from` to stand in for (`kyo promote`/`kyo spawn`) -- a caller that
    cares about the difference rejects the key before this is built
    (:func:`_parse_record_file`'s `accepts_plan`). Same reasoning for `description`
    and `accepts_description`.
    """

    title: str
    scope: str | None
    plan: str | None
    touch_lines: list[str]
    criteria_texts: list[str]
    description: str


#: The one heading `--from` reads specially. Matched by text alone (any heading
#: level), the same way `ideas.sections_named` matches every other addressable
#: section -- this file format stays one dialect of "a section", not a second one.
_RECORD_FILE_CRITERIA_HEADING = "Criteria"

#: A markdown list item: `-`/`*`/`+` or `1.`/`1)`, then the text. `_parse_criteria_items`
#: below is the only place this grammar is read, and the example file in the reference
#: (docs/reference/topics/operating.md, "One record, one file") is written to it.
_CRITERION_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(.*)$")


def _parse_criteria_items(lines: list[str], *, source: str, first_line: int) -> list[str]:
    """One criterion per markdown list item under `## Criteria` (KYO-330.c1).

    A line starting a new bullet/number begins a criterion; a following line with no
    marker of its own is a wrapped continuation, joined with a space -- the same
    hard-wrap convention this store's own prose keeps (`prose.py`'s KYO-66 section). A
    blank line ends a continuation run without ending the list, so a criterion may be
    followed by a blank line before the next one purely for readability.

    KYO-330.c4: a line that is neither a bullet nor a continuation of one is refused,
    named by the file's own line number (`first_line` counts from 1 over the WHOLE
    file, frontmatter included -- the same convention as `ideas.Section.line`) with
    the remedy stated, because "not a list item" is not something a caller can see
    from a stored id the way a bad touch or a bad scope already can.
    """
    items: list[str] = []
    current: list[str] | None = None
    for offset, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            if current is not None:
                items.append(" ".join(current))
                current = None
            continue
        match = _CRITERION_ITEM_RE.match(raw)
        if match:
            if current is not None:
                items.append(" ".join(current))
            current = [match.group(1).strip()]
        elif current is not None:
            current.append(stripped)
        else:
            raise KyoseiError(
                f"{source}:{first_line + offset}: text under '## "
                f"{_RECORD_FILE_CRITERIA_HEADING}' that is not a list item "
                f"({stripped!r}); every criterion starts its own line with '- ' (or "
                "'1. '); add the marker, or join this line onto the item above it as "
                "a wrapped continuation by removing the blank line between them"
            )
    if current is not None:
        items.append(" ".join(current))
    return [item for item in items if item]


def _reject_non_string_touch_lines(touches_value: list, *, source: str) -> None:
    """KYO-333.c5: refuse a `touches:` entry that PyYAML parsed to something other
    than a string.

    A line with a colon and a space -- a section heading copied verbatim, e.g.
    ``- .claude/skills/kyosei/SKILL.md#4. Ingesting reports: read, don't retype`` --
    reads to a YAML list as a ONE-ENTRY MAPPING, not a scalar. Met filing KYO-337:
    before this check, that entry fell through to a bare `str(item)` and stored a
    Python dict repr as the touch path, and the refusal that followed (a touch
    naming that repr, "no such file") named nothing a human could act on -- the
    front matter itself was never at fault, only unquoted.
    """
    bad = next((item for item in touches_value if not isinstance(item, str)), None)
    if bad is None:
        return
    raise KyoseiError(
        f"{source}: front matter 'touches:' has an entry that is not a string "
        f"({bad!r}) -- a line with a colon and a space (e.g. a heading fragment "
        "like 'path#4. Section: subsection') parses in YAML as a mapping rather "
        "than a scalar; edit the file to quote that whole line (e.g. \"path#4. "
        "Section: subsection\") so it reads back as one string"
    )


def _parse_record_file(
    text: str, *, source: str, accepts_plan: bool, accepts_description: bool
) -> _RecordFile:
    """`--from`'s whole grammar: front matter, a `## Criteria` section, the rest.

    `accepts_plan`/`accepts_description` are which of those two fields the CALLING
    command has a flag for at all: `kyo promote`/`kyo spawn` have no `--plan`, and
    `kyo promote` has no `--description-file` either (its description is always the
    idea being promoted, never authored prose). A file that states a field its
    command cannot use is refused by name -- exactly as passing that flag would be
    refused if the command had it -- rather than silently dropped, because a dropped
    field makes a different record than the one the file describes, and KYO-330.c2
    is that they must never differ.
    """
    fm_lines, body = ideas_module.split_frontmatter(text)
    if fm_lines is None:
        raise KyoseiError(
            f"{source}: no front matter -- a --from file needs a YAML block between "
            "a '---' line at the very top and a closing '---', naming at least "
            "'title:' (docs/reference/topics/operating.md (in Kyosei's own repository), "
            "\"One record, one file\")"
        )
    try:
        front = yaml.safe_load("\n".join(fm_lines))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"{source}:{2 + mark.line}" if mark is not None else source
        raise KyoseiError(f"{where}: front matter is not valid YAML: {exc}") from exc
    if front is None:
        front = {}
    if not isinstance(front, dict):
        raise KyoseiError(
            f"{source}: front matter must be a YAML mapping ('title:', 'scope:', "
            f"'plan:', 'touches:'), not a {type(front).__name__}"
        )

    known_keys = {"title", "scope", "touches"} | ({"plan"} if accepts_plan else set())
    unknown = sorted(set(front) - known_keys)
    if "plan" in unknown:
        raise KyoseiError(
            f"{source}: front matter names 'plan:', but this command has no --plan "
            "flag for --from to stand in for; drop 'plan:' from the file"
        )
    if unknown:
        raise KyoseiError(
            f"{source}: front matter has key(s) {unknown} that --from does not read; "
            f"only {sorted(known_keys)} are"
        )

    title_value = front.get("title")
    if title_value is None or not str(title_value).strip():
        raise KyoseiError(
            f"{source}: front matter has no 'title:' (or it is empty) -- that is "
            "--from's one-line title, the same as --title"
        )
    title = single_line(str(title_value), what=f"{source} front matter 'title:'")

    scope_value = front.get("scope")
    scope = None if scope_value is None else str(scope_value)

    plan_value = front.get("plan") if accepts_plan else None
    plan = None if plan_value is None else str(plan_value)

    touches_value = front.get("touches")
    if touches_value is None:
        touch_lines: list[str] = []
    elif isinstance(touches_value, list):
        _reject_non_string_touch_lines(touches_value, source=source)
        touch_lines = touches_value
    else:
        raise KyoseiError(
            f"{source}: front matter 'touches:' must be a YAML list, one entry per "
            "line ('path', 'path#symbol', 'path#+', 'dir/', '+path'), not "
            f"{touches_value!r}"
        )

    found = ideas_module.sections_named(text, _RECORD_FILE_CRITERIA_HEADING)
    if len(found) > 1:
        raise KyoseiError(
            f"{source}: has {len(found)} '## {_RECORD_FILE_CRITERIA_HEADING}' "
            f"sections (lines {[s.line for s in found]}); exactly one is read -- "
            "rename the others"
        )
    body_lines = body.split("\n")
    if found:
        section = found[0]
        heading_index = section.start - 1
        criteria_texts = _parse_criteria_items(
            body_lines[section.start : section.end],
            source=source,
            first_line=section.line + 1,
        )
        description_lines = body_lines[:heading_index] + body_lines[section.end :]
    else:
        criteria_texts = []
        description_lines = body_lines
    # A leading blank line here is the artifact of the blank line every frontmatter
    # block is followed by, not authored content -- `--description-file` has no such
    # artifact to strip, and KYO-330.c2 needs the two paths to agree.
    description = normalise("\n".join(description_lines)).lstrip("\n")
    if description and not accepts_description:
        raise KyoseiError(
            f"{source}: has description text outside the front matter and '## "
            f"{_RECORD_FILE_CRITERIA_HEADING}' section, but this command has no "
            "--description-file flag for --from to stand in for -- its description "
            "always comes from elsewhere; remove that text from the file"
        )
    if not accepts_description:
        description = ""

    return _RecordFile(
        title=title,
        scope=scope,
        plan=plan,
        touch_lines=touch_lines,
        criteria_texts=criteria_texts,
        description=description,
    )


def _whitespace_normalised(text: str) -> str:
    """Collapse every run of whitespace to one space and trim the ends.

    KYO-340.c2's one comparison: whether `--from`'s body is the SAME text as the
    idea being promoted, modulo how it wraps -- nothing else. Not
    :func:`sweep.normalise_text` (that also folds case and strips punctuation, built
    for near-duplicate *detection* across different prose, a looser question than
    "is this literally the same text").
    """
    return re.sub(r"\s+", " ", text).strip()


def _promote_description(
    promoted_text: str, source: str, *, from_body: str
) -> tuple[str, str | None]:
    """KYO-340.c2: the description `kyo promote` (with or without `--from`) files.

    The idea's own prose is the description by default -- law 13 says promotion
    authors *structure*, prose is what is being promoted. `--from`'s body only
    displaces it when the body actually differs, whitespace aside: a body that
    merely echoes the idea back (the shape `kyo new --from` already accepts, so a
    file drafted once needn't be edited to fit whichever route its kind demands)
    changes nothing, and a body that differs is filed as the author wrote it --
    with the echo saying which text was stored, since the two no longer agree by
    inspection the way "the idea is always the description" let a reader assume.
    """
    if from_body and _whitespace_normalised(from_body) != _whitespace_normalised(promoted_text):
        note = (
            f"description: stored --from's body, not {source}'s own text -- they differ"
        )
        return from_body + f"\n\n---\nPromoted from: {source}\n", note
    return promoted_text + f"\n\n---\nPromoted from: {source}\n", None


def _refuse_from_combo(
    from_flag: str, args: Namespace, other_flags: list[tuple[str, str]]
) -> None:
    """KYO-330: `--from` REPLACES the flags it parses to, rather than layering on top
    of them, so passing both is refused by name instead of one silently winning --
    that silent choice is exactly what would make c2 (`--from` files the identical
    record) untestable.
    """
    given = [flag for dest, flag in other_flags if getattr(args, dest, None)]
    if given:
        raise UsageError(
            f"{from_flag} cannot be combined with {', '.join(given)} -- put that in "
            f"the file {from_flag} reads instead"
        )


def _from_check_output(
    *,
    command: str,
    kind: str,
    invocation: str,
    from_file: str,
    title: str,
    plan: str | None,
    scope: str | None,
    scopes: registry_module.Registry,
    description: str,
    criteria_texts: list[str],
    touches: list[tuple[str, str | None]],
    extra_lines: list[str] | None = None,
    extra_data: dict | None = None,
) -> Output:
    """KYO-330.c3: `--from FILE --check`'s output for `new`/`promote`/`spawn` alike --
    validated against the repo, nothing filed. One renderer so the three commands'
    --check cannot describe the same validated file three slightly different ways.

    ``invocation`` is the runnable form of the command being checked -- `new`'s kind
    is a positional (`kyo new task`), but `promote`/`spawn`'s is `--kind` beside a
    positional this function never sees (the idea file, the source id), so it cannot
    be reconstructed from ``command``/``kind`` alone the way `new`'s can.
    """
    lines = [f"{from_file}: valid for `{invocation}` (--check); nothing filed"]
    lines += extra_lines or []
    lines.append(f"title: {title}")
    if plan:
        lines.append(f"plan: {plan}")
    lines.append(f"scope: {scope or '(none declared)'}")
    lines.append(
        f"description ({len(description)} chars, sha256:{fingerprint(description)}):"
    )
    lines += [f"  | {line}" for line in description.split("\n")]
    lines.append(f"criteria: {len(criteria_texts)}")
    lines += [f"  - {text}" for text in criteria_texts]
    lines.append(f"touches: {len(touches)}")
    lines += [
        f"  - {path}" + (f"#{symbol}" if symbol else "") for path, symbol in touches
    ]
    scope_lines, scope_data = _scope_suggestion(scopes, declared=scope, touches=touches)
    lines += scope_lines
    data = {
        "ok": True,
        "command": command,
        "check": True,
        "record_id": None,
        "from_file": from_file,
        "kind": kind,
        "title": title,
        "plan": plan,
        "scope": scope,
        "description": description,
        "criteria": criteria_texts,
        "touches": [{"path": path, "symbol": symbol} for path, symbol in touches],
        "scope_suggestion": scope_data,
    }
    data.update(extra_data or {})
    return Output(lines, data)


def cmd_new(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    kind = args.kind
    if not KIND_RE.match(kind):
        raise UsageError(f"kind {kind!r} must be a lowercase slug")
    registry = _load_registry(store)
    if kind not in registry:
        raise KyoseiError(
            f"{kind!r} is not a known kind; known kinds: {', '.join(registry.names)} "
            "(defined in kyosei/kinds.yml)"
        )
    _check_kind_accepts_intake(store, registry, kind)
    evidence_text = _evidence_at_filing(registry, kind, args)

    # KYO-330: `--from FILE` is an alternative to the six flags below, not a second
    # write path -- it parses to exactly what they would have produced and falls
    # into the same variables, so everything from here down is unchanged by which
    # branch supplied them.
    from_file = getattr(args, "from_file", None)
    check_only = bool(getattr(args, "check", False))
    if check_only and not from_file:
        raise UsageError(
            "--check only makes sense with --from; there is nothing else to "
            "validate without filing"
        )
    if from_file:
        _refuse_from_combo(
            "--from",
            args,
            [
                ("title", "--title"),
                ("plan", "--plan"),
                ("scope", "--scope"),
                ("description_file", "--description-file"),
                ("criteria_file", "--criteria-file"),
                ("touches_file", "--touches-file"),
            ],
        )
        parsed = _parse_record_file(
            read_prose(from_file, what="--from"),
            source=from_file,
            accepts_plan=True,
            accepts_description=True,
        )
        title = parsed.title
        plan = parsed.plan
        description = parsed.description
        criteria_texts = parsed.criteria_texts
        touches = _validate_touches(ctx.code_root, parsed.touch_lines)
        scope, scopes = _checked_scope(store, parsed.scope)
    else:
        if args.title is None:
            raise UsageError("--title is required (or pass --from FILE instead)")
        title = single_line(args.title, what="--title")
        plan = args.plan
        description = (
            read_prose(args.description_file, what="--description-file")
            if args.description_file
            else ""
        )
        criteria_texts = (
            read_lines(args.criteria_file, what="--criteria-file") if args.criteria_file else []
        )
        # Validated against the repo *now*, exactly as `kyo promote` does — the intake
        # path is zero-ceremony about whether a scope is declared, never about whether a
        # declared one is real. Optional here so filing a bug stays one line, but a bug
        # that does declare a scope is now sweepable and dispatch-disjointness-visible,
        # which `kyo promote --kind bug` cannot deliver: bug.accepts_from is [intake].
        touches = _validate_touches(
            ctx.code_root,
            read_lines(args.touches_file, what="--touches-file") if args.touches_file else [],
        )
        scope, scopes = _checked_scope(store, args.scope)
    since_note_arg = getattr(args, "since_note", None)

    if check_only:
        if plan is not None:
            _require_plan(store, plan)
        return _from_check_output(
            command="new",
            kind=kind,
            invocation=f"kyo new {kind}",
            from_file=from_file,
            title=title,
            plan=plan,
            scope=scope,
            scopes=scopes,
            description=description,
            criteria_texts=criteria_texts,
            touches=touches,
        )

    # KYO-8.c4: creation-time duplicate check, against the corpus as it stands
    # *before* this record is created — warns, never blocks (charter: "flags,
    # never enforces" extends to this creation-time cousin of `kyo sweep`).
    _refuse_identical_recent(store, title, criteria_texts, force=args.force)
    existing_titles, existing_criteria = _dedupe_corpus(store)
    duplicate_warnings = sweep_module.find_creation_time_duplicates(
        new_title=title,
        new_criteria_texts=criteria_texts,
        existing_titles=existing_titles,
        existing_criteria=existing_criteria,
    )
    warning_lines, warning_data = _duplicate_warning_output(duplicate_warnings)

    # KYO-101: a new record is filed in whatever status this project declares
    # `role: initial`, which is `open` unless the project renamed it.
    initial = _load_status_ladder(store).initial

    with store.transaction():
        if plan is not None:
            _require_plan(store, plan)
        record_id = store.new_record_id()
        seq = int(record_id.rsplit("-", 1)[1])
        stamp = now()
        # KYO-498: checked against this record's own filing stamp, inside the same
        # transaction, so "the note is not later than the record" is the stored fact.
        since_note = (
            _checked_since_note(
                store,
                registry,
                record_id=record_id,
                kind=kind,
                note_id=since_note_arg,
                created_at=stamp,
            )
            if since_note_arg is not None
            else None
        )
        store.conn.execute(
            "INSERT INTO records (id, seq, kind, title, status, plan_id, scope, since_note,"
            " description, wont_do_reason, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                record_id,
                seq,
                kind,
                title,
                initial,
                plan,
                scope,
                since_note,
                description,
                stamp,
                stamp,
            ),
        )
        added = _add_criteria(store, record_id, criteria_texts)
        added_touches = _add_touches(store, record_id, touches)
        # KYO-521: the evidence is stored in the same transaction as the record, by the
        # helper `kyo attach` uses, so a record that requires it never exists without it.
        evidence = (
            commands._add_evidence(
                store, record_id, evidence_text, supports_id=None, author=ctx.actor
            )
            if evidence_text is not None
            else None
        )
        # KYO-554: inside the same transaction, so the export carries them.
        standing_summary = raise_standing_obligations(ctx, store, record_id, stamp=stamp)
        after, events = commands._commit_and_export(
            store,
            command=f"new {kind}",
            actor=ctx.actor,
            record_ids=[record_id],
            before={record_id: None},
        )
    commands._finish_mutation(store, [record_id], events)

    title_warning_lines, title_warning = _title_warning_output(title)
    criteria_warning_lines, criteria_warning_data = _criteria_warning_output(added)

    lines = [
        f"created {record_id}",
        *warning_lines,
        echo_value("kind", kind),
        echo_value("title", title),
        *title_warning_lines,
    ]
    if plan:
        lines.append(echo_value("plan", plan))
    lines.append(echo_value("scope", scope or "(none declared)"))
    if since_note is not None:
        lines.append(echo_value("since note", since_note))
    lines += echo_block("description", description)
    lines.append(f"stored criteria: {len(added)}")
    lines += [f"  {cid}  {DEFAULT_CRITERION_STATE}  {text}" for cid, text in added]
    lines += criteria_warning_lines
    if registry.require(kind).requires_criteria and not added:
        # KYO-26: this succeeded silently and produced a record
        # that `kyo move` refuses to take to `working` and that `kyo next` filters
        # out of ready work. That is the intended zero-ceremony intake path — the
        # gap was that nothing said what was now true of the record.
        lines.append(
            f"  note: {kind!r} has policy.requires_criteria: true, so {record_id} is parked "
            "until criteria are authored — `kyo move` will refuse `working` and `kyo next` "
            f"will not list it. Add them with `kyo edit {record_id} --add-criteria-file f.txt`."
        )
    lines.append(f"stored touches: {len(added_touches)}")
    lines += [
        f"  {tid}  {path}" + (f"#{symbol}" if symbol else "")
        for tid, path, symbol in added_touches
    ]
    if evidence is not None:
        lines.append(f"attached {evidence[0]}")
        lines += echo_block("evidence", evidence_text)
        lines.append(echo_value("sha256", evidence[1]))
    lines += standing_obligation_receipt(standing_summary)
    scope_lines, scope_data = _scope_suggestion(scopes, declared=scope, touches=touches)
    lines += scope_lines
    # KYO-585: after the record's own transaction has committed and exported, never
    # inside it -- the search is advisory and the record must not wait on it.
    prior_art_lines, prior_art_data = prior_art.after_create(
        ctx, args, record_id=record_id, kind=kind, command="new"
    )
    lines += prior_art_lines
    return Output(
        lines,
        {
            "ok": True,
            "command": "new",
            "prior_art": prior_art_data,
            # KYO-538: the new record's id under the key every receipt shares.
            "id": record_id,
            "record_id": record_id,
            # KYO-521: the evidence `--evidence-file` attached, or null.
            "evidence_id": evidence[0] if evidence is not None else None,
            # KYO-554: what this filing raised, or null for a kind that raises nothing.
            "standing_obligations": standing_summary,
            "record": after[record_id],
            "duplicate_warnings": warning_data,
            "title_length_warning": title_warning,
            "criteria_length_warnings": criteria_warning_data,
            "scope_suggestion": scope_data,
        },
    )


def _checked_since_note(
    store: Store,
    registry,
    *,
    record_id: str,
    kind: str,
    note_id: str,
    created_at: str,
) -> str:
    """Validate ``--since-note`` for a record (KYO-498); return the id.

    Three refusals, each naming its way forward: the kind is not ``policy.carried`` (only
    a carried item has an age the brief counts); the note is not in this store's shift
    log; the note was written after the record was filed (it cannot have first recorded
    an item that already existed). Only the id is stored: the time is read from the
    note's tool-stamped ``created_at`` whenever an age is computed (Law 16).
    """
    kind_def = registry.get(kind)
    if kind_def is None or not kind_def.carried:
        carried = [name for name in registry.names if registry[name].carried]
        raise KyoseiError(
            f"--since-note refused: kind {kind!r} does not carry policy.carried, so "
            f"{record_id} has no carried age for a note to set. File the item as a "
            "carried kind instead"
            + (f" ({', '.join(carried)})" if carried else "")
            + f", or add `carried: true` to {kind!r}'s policy in kyosei/kinds.yml"
        )
    note = store.shift_note(note_id)
    if note is None:
        raise KyoseiError(
            f"--since-note refused: no shift note {note_id!r} in this store. "
            "`kyo shift-notes` lists every shift note with its id; name one of those"
        )
    if note["created_at"] > created_at:
        raise KyoseiError(
            f"--since-note refused: {note_id} was written at {note['created_at']}, after "
            f"{record_id} was filed at {created_at}, so it cannot be the note that first "
            "recorded it. Name an earlier shift note (`kyo shift-notes` lists them with "
            "their times), or leave --since-note off and the age counts from filing"
        )
    return note_id


def _checked_scope(store: Store, name: str | None) -> tuple[str | None, registry_module.Registry]:
    """Validate ``--scope`` against this project's registry, and hand back both.

    KYO-100.c1. The registry is loaded whether or not a scope was given, because the
    *other* consumer needs it either way: :func:`_scope_suggestion` reads the same
    registry's ``paths`` to say what the touches would have suggested, and a record
    filed with no scope is precisely the case that suggestion exists for.

    One function for all four write paths (`new`, `promote`, `spawn`, `edit`) on
    purpose. The refusal sentence lives in ``registry.unknown_name_refusal`` and the
    check lives here, so neither can end up written four slightly different ways —
    which is what happened to the pre-KYO-68 refusals that each enumerated the doors
    that existed when they were written.
    """
    scopes = registry_module.load_scope_registry(store.config.kyosei_dir)
    if name is None:
        return None, scopes
    scopes.require(name)
    return name, scopes


def _scope_suggestion(
    scopes: registry_module.Registry,
    *,
    declared: str | None,
    touches: list[tuple[str, str | None]],
) -> tuple[list[str], dict]:
    """KYO-100.c3: what the touches would suggest, said out loud and stored nowhere.

    Every caller below prints these lines *after* the record has been written, and
    none of them passes the result to anything that writes. That is the whole design:
    KYO-46 says derivation from touches is "a suggester and validator, never the
    source", and the cheapest way to keep that true is for the derivation to have no
    write path at all rather than a guarded one.
    """
    return registry_module.suggestion_lines(
        scopes, declared=declared, paths=[path for path, _ in touches]
    )


def _derivation_inputs(document: dict) -> tuple[str | None, tuple[str, ...]]:
    """The two things :func:`_scope_suggestion` reads, and nothing else.

    ``suggestion_lines`` is a pure function of the declared scope and the touch
    *paths* -- not of the touch rows, whose ids are minted fresh by every
    ``--touches-file`` even when the paths are identical, and not of their symbols,
    which the matcher never sees. Comparing the inputs rather than the rows is what
    keeps "replaced the touches with the same touches" out of the changed set, and
    comparing them rather than the rendered lines is what keeps a real touch change
    *in* it: two different path sets under one registry entry render the same
    sentence, so a lines-level comparison would silence exactly the edit KYO-100.c3
    exists for.
    """
    return (
        document["scope"],
        tuple(touch["path"] for touch in document["touches"]),
    )


def _edit_could_change_the_derivation(before: dict, after: dict, args: Namespace) -> bool:
    """KYO-195: did this edit touch anything the scope derivation reads?

    KYO-100.c3 put the derivation on the edit path because an edit that replaces the
    touches and leaves the scope alone is where a mismatch becomes visible, and the
    flags alone cannot see it. That reasoning covers the edits that can change the
    answer; it does not cover the ones that cannot, and those are most edits. A record
    with touches under a registry entry and no declared scope printed the same offer on
    every ``--note-file``, every ``--add-criteria-file`` and every ``--doc-refs-file``,
    for the life of the record -- routine noise on a channel whose whole value is that
    it is not routine (KYO-33).

    Two conditions, OR'd, and they are asked of different things on purpose:

    * the derivation's inputs differ between the document before this edit and the one
      after it -- the document, not the flags, for KYO-100.c3's reason, and it is what
      catches ``--touches-file``, ``--scope``, ``--no-scope`` and any future flag that
      moves either without knowing this function exists;
    * ``--scope``/``--no-scope`` was passed at all. Redundant in every case where it
      changes something, and there for the one case where it does not: re-declaring the
      scope a record already carries is a deliberate assertion by the caller about the
      very thing being derived, and answering it with silence would be answering a
      question that was asked.
    """
    return (
        _derivation_inputs(before) != _derivation_inputs(after)
        or args.scope is not None
        or bool(args.no_scope)
    )


def _add_touches(
    store: Store, record_id: str, touches: list[tuple[str, str | None]]
) -> list[tuple[str, str, str | None]]:
    stamp = now()
    added: list[tuple[str, str, str | None]] = []
    for path, symbol in touches:
        touch_id, seq = store.new_child_id(record_id, "touch")
        store.conn.execute(
            "INSERT INTO touches (id, record_id, seq, path, symbol, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (touch_id, record_id, seq, path, symbol, stamp),
        )
        added.append((touch_id, path, symbol))
    return added


def _replace_touches(
    store: Store, record_id: str, touches: list[tuple[str, str | None]]
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str | None]]]:
    """Swap a record's whole touches list for ``touches``; return (new, dropped).

    A touches list is a *working scope*, not a claim — the distinction that decides
    this. Criterion text is immutable because a criterion is a claim and rewriting
    claims destroys provenance (law 9/10); a scope that cannot be corrected when the
    work proves it wrong just decays into a lie that `kyo sweep` and `kyo dispatch`
    still trust (a promoted list can name files with no role in the work and miss the
    ones the change actually lives in).

    Provenance is preserved by the events ledger, not by keeping the rows: the caller
    captures ``export.record_document`` *before* this runs and `_commit_and_export`
    writes the prior list into the event -- its ``before_json`` on a row written
    before schema_version 25, its ``changed_json`` since (KYO-418), the ``touches``
    key being one that moved -- so the prior list stays readable forever. Deleted
    rows never re-issue their ids either — ``allocations`` counters
    are monotonic and rebuild reads them from ``_allocations.json`` rather than
    ``max(seq)``, exactly so a deleted high-numbered row cannot let the next
    allocation collide (law 4; see rebuild.py's module docstring).
    """
    dropped = [
        (row["path"], row["symbol"]) for row in store.touches(record_id)
    ]
    store.conn.execute("DELETE FROM touches WHERE record_id = ?", (record_id,))
    return _add_touches(store, record_id, touches), dropped


def _directory_touch_problem(
    root: Path, path_part: str, symbol_part: str | None, line: str
) -> str | None:
    """What is wrong with a directory touch, or ``None`` (KYO-209).

    A file touch is checked by existence; a directory touch cannot be, so these four
    checks are what stands between the form and a claim nobody can act on. Each names
    the remedy that clears it, and each has a test proving that remedy does (AGENTS.md).
    """
    if symbol_part:
        return (
            f"{path_part}#{symbol_part}: a directory touch claims a whole tree and takes "
            f"no '#symbol' (touch {line!r}); drop the '#{symbol_part}', or drop the "
            "trailing '/' to claim one file's symbol"
        )
    stored = store_path(root, root / path_part)
    if stored == ".":
        return (
            f"{path_part}: a touch is a working scope, not the whole project — this one "
            f"names the project root {root} (touch {line!r}); name the subdirectory the "
            "work lives in instead"
        )
    if stored == ".." or stored.startswith("../"):
        return (
            f"{path_part}: resolves outside the project root {root} (touch {line!r}); "
            "every stored path is relative to that root, so name a directory inside it"
        )
    if (root / path_part).is_file():
        return (
            f"{path_part}: names a file, not a directory (touch {line!r}); drop the "
            "trailing '/' to claim that file"
        )
    return None


def _new_file_touch_problem(
    root: Path, named: str, symbol_part: str | None, line: str
) -> str | None:
    """What is wrong with a new-file touch, or ``None`` (KYO-225.c1).

    :func:`_directory_touch_problem`'s sibling, and deliberately the same four
    questions asked of the other form: existence cannot do the checking for either, so
    what stands between the form and a claim nobody can act on is this list, and each
    entry names the remedy that clears it.

    The one that differs is the last, and it is this form's own honesty check: a `+` on
    a file that is ALREADY there is a claim to create something that exists. It is
    refused rather than quietly accepted as an ordinary file touch, because the two say
    different things and a controller reading the record back deserves the one that is
    true. Note that this is a check at DECLARATION time only — a touch stored while the
    file was absent goes on reading correctly after the work lands, which is c3 and is
    why nothing here ever has to be edited back.
    """
    if not named:
        return (
            f"{line!r}: a new-file touch needs a path after its "
            f"'{TOUCH_NEW_FILE_PREFIX}' — write it as "
            f"`{TOUCH_NEW_FILE_PREFIX}path/to/the/file/this/work/creates`"
        )
    if symbol_part:
        return (
            f"{TOUCH_NEW_FILE_PREFIX}{named}#{symbol_part}: a new-file touch names a "
            f"file that does not exist yet, so there is nothing to find "
            f"{symbol_part!r} in (touch {line!r}); drop the '#{symbol_part}', or drop "
            f"the '{TOUCH_NEW_FILE_PREFIX}' to claim a symbol in a file that is there"
        )
    if is_directory_touch(named):
        return (
            f"{TOUCH_NEW_FILE_PREFIX}{named}: a directory touch is already accepted "
            f"whether or not the tree exists, so the '{TOUCH_NEW_FILE_PREFIX}' adds "
            f"nothing to it (touch {line!r}); write `{named}` for the whole tree, or "
            f"`{TOUCH_NEW_FILE_PREFIX}{named}<filename>` for one file inside it"
        )
    stored = store_path(root, root / named)
    if stored == ".." or stored.startswith("../"):
        return (
            f"{TOUCH_NEW_FILE_PREFIX}{named}: resolves outside the project root {root} "
            f"(touch {line!r}); every stored path is relative to that root, so name a "
            "file inside it"
        )
    if (root / named).exists():
        return (
            f"{TOUCH_NEW_FILE_PREFIX}{named}: that path is already there, so this work "
            f"does not create it (touch {line!r}); drop the "
            f"'{TOUCH_NEW_FILE_PREFIX}' to claim the file as it stands"
        )
    return None


def _validate_touches(root: Path, lines: list[str]) -> list[tuple[str, str | None]]:
    """Parse ``path`` / ``path#symbol`` lines and check each against the repo *now*.

    Charter, Layer 1: "A stale idea fails loudly exactly when someone tries to act
    on it." Every problem is collected before raising (law 6's "collect and raise
    together" style, matching ``kinds._build_registry``), so a promote attempt with
    several bad touches is fixed in one pass, not by rerunning once per line.

    KYO-52: what is *stored* is normalised through ``contracts.store_path``, so an
    absolute touch (``/Users/someone/proj/src/x.py``, which ``root / path_part``
    accepts unchanged and which used to be stored verbatim) lands in the
    git-canonical export as ``src/x.py``. ``touches.path`` is a path-typed column
    like ``dispatches.worktree``, and the same rule applies to it.

    KYO-209: a path ending in ``/`` is a **directory touch** and is accepted whether
    or not it exists. That is the one exception to the paragraph above, and it exists
    because the rule above had no way to express "this work creates this tree" — a
    consumer committed a stub ``README.md`` under an empty ``apps/desktop/`` purely so
    four records could name a path, and every dispatch off them then reported that the
    Touches table did not cover the work. The trailing slash is what keeps that
    distinguishable from the typo the existence check is really for: a mistyped
    filename does not grow one by accident. Everything else about a directory touch is
    checked *harder* than a file touch, because existence can no longer do the
    checking — it takes no ``#symbol``, it may not name the project root, it may not
    resolve outside the root, and it may not name a file that is already there.

    KYO-300.c3: ``path#+`` is an **additive claim** — "this work adds to that file and
    edits nothing already in it" — and is the one ``#symbol`` not required to be found,
    because what it claims does not exist yet. See the branch below for why that is not
    the same hole the existence check is guarding.
    """
    parsed: list[tuple[str, str | None]] = []
    problems: list[str] = []
    for line in lines:
        path_part, _, symbol_part = line.partition("#")
        path_part = path_part.strip()
        symbol_part = symbol_part.strip() or None
        if not path_part:
            problems.append(f"{line!r}: a touch needs a path before any '#symbol'")
            continue
        # KYO-225: the new-file marker is tested FIRST, because `+dir/` carries both
        # and the directory branch would otherwise accept it as a tree literally named
        # `+dir`. Asked in this order, that line reaches the one check that refuses it
        # as two markers for one intent and names the two ways out.
        if is_new_file_touch(path_part):
            named = touch_target(path_part)
            problem = _new_file_touch_problem(root, named, symbol_part, line)
            if problem:
                problems.append(problem)
            else:
                parsed.append((TOUCH_NEW_FILE_PREFIX + store_path(root, root / named), None))
            continue
        if is_directory_touch(path_part):
            problem = _directory_touch_problem(root, path_part, symbol_part, line)
            if problem:
                problems.append(problem)
            else:
                parsed.append((store_path(root, root / path_part) + "/", None))
            continue
        target = root / path_part
        if not target.is_file():
            problems.append(
                f"{path_part}: no such file relative to {root} (touch {line!r}); "
                + missing_touch_remedy(path_part)
            )
            continue
        if sweep_module.is_additive_symbol(symbol_part):
            # KYO-300.c3: the additive claim, and the one symbol that is NOT looked for
            # in the file. `path#+` says "this work adds to that file and edits nothing
            # already in it", which is the ordinary case for a file that grows by
            # addition — a new type in `protocol.ts`, a new rule in `shell.css` — and
            # the thing being added has no name yet, so there is nothing to find. Before
            # this, validation refused every such claim as "not found" and the only
            # honest touch left was the whole file, which collides with every symbol in
            # it. The existence check above still ran: an additive claim is a claim on a
            # file that IS there (`+path` is the form for one that is not).
            parsed.append((store_path(root, target), ADDITIVE_SYMBOL))
            continue
        if symbol_part:
            content = target.read_text(encoding="utf-8", errors="replace")
            # KYO-66: wrap-tolerant, because a symbol is not always an identifier — a
            # `path.md#Some section heading` touch is matched against prose, and prose
            # in this repo is hard-wrapped. A fixed-string match refused those with
            # "not found" about text that was right there: the same absence claim this
            # record is about, made by the tool, in its costliest direction. A
            # single-word symbol matches exactly what it matched before.
            if not contains_phrase(content, symbol_part, whole_word=True):
                problems.append(
                    f"{path_part}#{symbol_part}: {symbol_part!r} not found in {path_part}"
                )
                continue
        parsed.append((store_path(root, target), symbol_part))
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise KyoseiError(f"touch validation failed:\n{detail}")
    return parsed


# ------------------------------------------------- KYO-137.c1: the doc-refs back path


def _document_set_or_none(store: Store) -> docs_parse.DocumentSet | None:
    """This project's document set, or ``None`` when ``documents.yml`` cannot be read: a
    broken registry is ``kyo docs check``'s to report, and refusing a doc-ref over it
    would be a refusal about the registry dressed up as one about the file (KYO-600)."""
    try:
        return docs_parse.document_set(store.config.kyosei_dir)
    except KyoseiError:
        return None


def _validate_doc_refs(
    root: Path, lines: list[str], documents_in: docs_parse.DocumentSet | None = None
) -> list[tuple[str, str | None]]:
    """Parse ``path`` / ``path#section`` lines and check each against the repo *now*.

    "Validated against the repository the way ``touches`` is" (KYO-137.c1) is meant
    literally: this is :func:`_validate_touches`' rule, through the same
    :func:`kyosei.prose.contains_phrase` whole-word, wrap-tolerant match (KYO-66) and the
    same :func:`kyosei.contracts.store_path` normalisation (KYO-52), collected and raised
    together (law 6) so a declaration with three bad lines is fixed in one pass.

    Two differences from a touch, both in what the words *mean* rather than in what is
    checked:

    - the ``#`` half names a **section heading**, not a symbol. It is the same match
      either way — a heading is prose, and KYO-66 made that match wrap-tolerant precisely
      because a ``path.md#Some section heading`` touch already existed — so the
      distinction is one of vocabulary, and it is stated because a reader who thinks
      "symbol" will write the wrong thing.
    - a document need not be in this project's ``documents.yml`` set to be named here.
      That set decides what `kyo docs check` scans; a person declaring "my work affects
      this file" is making a different claim, and refusing it because a glob does not
      match would be a refusal about the registry dressed up as one about the file.

    What this does NOT check is that the target is a markdown document at all. A
    ``doc-refs`` line pointing at ``src/kyosei/cli.py`` resolves and is stored, which
    looks lax and is deliberate: this is the skeleton validator the charter asks for —
    "the path must exist" — and inventing a file-type rule here would be this function
    deciding what a document is, while :mod:`kyosei.registry` is what decides that.

    KYO-600: under a ``recx-manifest`` set (``documents_in``), RECX.yaml's sections are
    derived from its entries, so ``RECX.yaml#module <id>`` names a section whose heading
    is not text in the file. A ``#`` half equal to one of those headings is accepted.
    """
    parsed: list[tuple[str, str | None]] = []
    problems: list[str] = []
    for line in lines:
        path_part, _, section_part = line.partition("#")
        path_part = path_part.strip()
        section_part = section_part.strip() or None
        if not path_part:
            problems.append(f"{line!r}: a doc-ref needs a path before any '#section'")
            continue
        target = root / path_part
        if not target.is_file():
            problems.append(
                f"{path_part}: no such file relative to {root} (doc-ref {line!r})"
            )
            continue
        if section_part:
            content = target.read_text(encoding="utf-8", errors="replace")
            rel = store_path(root, target)
            derived = (
                {
                    " ".join(section.heading.split())
                    for section in docs_parse.read_document(rel, content, documents_in).sections
                }
                if documents_in is not None and documents_in.is_recx_manifest(rel)
                else set()
            )
            if " ".join(section_part.split()) in derived:
                section_part = " ".join(section_part.split())
            elif not contains_phrase(content, section_part, whole_word=True):
                problems.append(
                    f"{path_part}#{section_part}: {section_part!r} is not a heading or "
                    f"any other text in {path_part}"
                )
                continue
        parsed.append((store_path(root, target), section_part))
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise KyoseiError(f"doc-ref validation failed:\n{detail}")
    return parsed


def _add_doc_refs(
    store: Store, record_id: str, doc_refs: list[tuple[str, str | None]]
) -> list[tuple[str, str, str | None]]:
    stamp = now()
    added: list[tuple[str, str, str | None]] = []
    for path, section in doc_refs:
        ref_id, seq = store.new_child_id(record_id, "doc-ref")
        store.conn.execute(
            "INSERT INTO doc_refs (id, record_id, seq, path, symbol, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ref_id, record_id, seq, path, section, stamp),
        )
        added.append((ref_id, path, section))
    return added


def _replace_doc_refs(
    store: Store, record_id: str, doc_refs: list[tuple[str, str | None]]
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str | None]]]:
    """Swap a record's whole doc-refs list for ``doc_refs``; return (new, dropped).

    Replaces rather than soft-deletes, on :func:`_replace_touches`' reasoning and for
    exactly its reason: a doc-refs list is a working scope written before the work, not
    a claim, and the prior list stays readable in the events ledger.
    """
    dropped = [(row["path"], row["symbol"]) for row in store.doc_refs(record_id)]
    store.conn.execute("DELETE FROM doc_refs WHERE record_id = ?", (record_id,))
    return _add_doc_refs(store, record_id, doc_refs), dropped


# ------------------------------------------- KYO-554: standing docs obligations


def raise_standing_obligations(
    ctx: Context, store: Store, record_id: str, *, stamp: str | None = None
) -> dict | None:
    """Raise the standing docs obligations ``record_id`` owes, inside the caller's
    transaction and before its export; ``None`` when its kind does not raise any.

    Called by `kyo new`, `kyo spawn`, `kyo promote` and by `kyo edit` when the title,
    description, doc-refs or kind changed. Reads the record as it now stands, so an edit
    is judged on its new words. A section the record already holds a standing obligation
    on, open or settled, raises nothing again (c5): a settled verdict is an answer, and
    re-asking the same question on every edit would make editing punish itself, the
    reason `kyo report` keys an amendment's obligations the same way.

    A record filed before this build is never visited by anything but its own next write,
    so upgrading a store raises nothing by itself (c12). A broken ``documents.yml`` does
    not refuse the write: the obligations are then not computed, and the output says so.
    """
    row = store.record(record_id)
    kind_def = _load_registry(store).get(row["kind"])
    if kind_def is None or not kind_def.raises_docs_obligations:
        return None
    doc_refs = [(ref["path"], ref["symbol"]) for ref in store.doc_refs(record_id)]
    try:
        computed = docs_obligations.standing_obligations(
            ctx.code_root,
            docs_parse.document_set(store.config.kyosei_dir),
            text=f"{row['title']}\n{row['description'] or ''}",
            doc_refs=doc_refs,
        )
    except KyoseiError as exc:
        return {"raised": [], "unresolved": [], "skipped": [], "not_checked": str(exc)}
    held = {
        (held_row["doc_path"], held_row["section"])
        for held_row in store.docs_obligations(record_id)
        if held_row["dispatch_id"] is None
    }
    stamp = stamp or now()
    raised: list[dict] = []
    for obligation in computed.found:
        if (obligation.doc_path, obligation.section) in held:
            continue
        obligation_id, seq = store.new_child_id(record_id, "docs-obligation")
        store.conn.execute(
            "INSERT INTO docs_obligations (id, record_id, seq, dispatch_id, doc_path,"
            " section, cause, detail, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                obligation_id,
                record_id,
                seq,
                obligation.doc_path,
                obligation.section,
                obligation.cause,
                obligation.says,
                stamp,
            ),
        )
        raised.append(
            {
                "id": obligation_id,
                "doc_path": obligation.doc_path,
                "section": obligation.section,
                "cause": obligation.cause,
                "detail": obligation.says,
            }
        )
    return {
        "raised": raised,
        "unresolved": list(computed.unresolved),
        "skipped": list(computed.skipped),
        "not_checked": None,
    }


def standing_obligation_receipt(summary: dict | None) -> list[str]:
    """What a write prints about the standing obligations it raised (KYO-554)."""
    if summary is None:
        return []
    if summary["not_checked"]:
        return [
            "standing docs obligations: not computed, so none were raised — "
            f"{summary['not_checked']}"
        ]
    lines = [f"standing docs obligations raised: {len(summary['raised'])}"]
    lines += [
        f"  {item['id']}  {item['doc_path']} [{item['section']}]  ({item['cause']})"
        for item in summary["raised"]
    ]
    if summary["unresolved"]:
        lines.append(f"  ids that resolve to no section ({len(summary['unresolved'])}):")
        lines += [f"    {line}" for line in summary["unresolved"]]
    if summary["skipped"]:
        lines.append(f"  named and not raised ({len(summary['skipped'])}):")
        lines += [f"    {line}" for line in summary["skipped"]]
    if summary["raised"]:
        lines.append(
            "  settle each with `kyo docs settle <id> --verdict updated|not-affected "
            "--method <how>`; `kyo brief` lists them until then"
        )
    return lines


def _settle_standing_on_void(store: Store, record_id: str, *, why: str | None, author: str,
                             stamp: str) -> list[str]:
    """Settle ``record_id``'s open standing obligations as ``not-affected`` (KYO-554.c11).

    A voided record decided nothing, so nothing it named needs to change; the void's
    reason and author are the verdict's, under the method ``void``.
    """
    ids = [
        row["id"]
        for row in store.open_docs_obligations(record_id)
        if row["dispatch_id"] is None
    ]
    store.conn.executemany(
        "UPDATE docs_obligations SET verdict = ?, method = ?, author = ?, settled_at = ?,"
        " settled_why = ? WHERE id = ?",
        [
            (OBLIGATION_NOT_AFFECTED, STANDING_VOID_METHOD, author, stamp, why, obligation_id)
            for obligation_id in ids
        ],
    )
    return ids


#: The method a void records on the standing obligations it settles (KYO-554.c11).
STANDING_VOID_METHOD = "void"


def _section_listing(found: list[ideas_module.Section]) -> str:
    """The sections a file holds, as a refusal names them (law 6).

    Each one is quoted exactly as it would have to be typed back to `--section`, and a
    section already promoted says so with its record id — the reader turned away from one
    name is choosing between the rest, and "that one is gone too" is what they would
    otherwise find out one refusal at a time.
    """
    return "; ".join(
        f"{section.heading!r}"
        + (f" (promoted -> {', '.join(section.promoted_to)})" if section.promoted_to else "")
        for section in found
    )


def _require_section(
    idea_file: str, text: str, wanted: str, stamps: list[ideas_module.Stamp]
) -> ideas_module.Section:
    """The one section ``wanted`` names, or a refusal that says why there isn't one.

    Kept out of :func:`cmd_promote` on purpose. Every refusal here is a new error site, and
    the coverage tables in ``tests/test_kyo5_error_remedies.py`` are keyed by
    ``module.py:function#ordinal`` — raising these inline would renumber `kyo promote`'s
    existing sites and make entries stale that describe messages nobody changed.

    None of the four messages names a command to run. That follows KYO-106's whole-file
    refusal, and for the same reason it gave: what the reader needs here is *data* — which
    sections exist, which record already holds this one, which lines the duplicates are on —
    and a message that carries the data needs no imperative after it.
    """
    found = ideas_module.sections_named(text, wanted)
    if not found:
        available = ideas_module.sections(text)
        if not available:
            promoted = (
                ", and its whole text lives in "
                + ", ".join(stamp.record_id for stamp in stamps)
                if stamps
                else ""
            )
            raise KyoseiError(
                f"{idea_file} has no section titled {wanted!r}; it holds no markdown"
                f" headings at all{promoted}"
            )
        raise KyoseiError(
            f"{idea_file} has no section titled {wanted!r}; the sections it holds are:"
            f" {_section_listing(available)}"
        )
    if len(found) > 1:
        where = ", ".join(str(section.line) for section in found)
        raise KyoseiError(
            f"{idea_file} has {len(found)} sections titled {wanted!r}, at lines {where};"
            f" --section addresses a section by a heading only that section has, and"
            f" choosing between them is not one of its options"
        )
    section = found[0]
    if section.promoted_to:
        raise KyoseiError(
            f"{idea_file} section {wanted!r} is already promoted; its text lives in"
            f" {', '.join(section.promoted_to)}, not in this file; refusing"
        )
    if not section.has_text:
        raise KyoseiError(
            f"{idea_file} section {wanted!r} holds no text under its heading; a promotion"
            f" would move nothing into the record"
        )
    return section


def cmd_promote(ctx: Context, args: Namespace) -> Output:
    """``kyo promote <idea-file> [--section <heading>] --kind <k>`` — law 13's boundary.

    Structure (title, criteria, touches) is authored here, at the moment a human or
    controller judges an idea worth acting on; the idea pool itself stays zero-
    ceremony (law 12/13). Promotion is meant to behave as one transaction across two
    storage systems (the db, and the idea file's promotion stamp): the stamp is
    written *inside* the db transaction, so an IO failure there rolls the record back
    (never persisted without its stamp); if anything else in the transaction then
    fails, the idea file is restored to its pre-stamp text (never left stamped
    without a persisted record).

    KYO-106 raises the stakes on that transaction rather than changing its shape. The
    prose does not merely get *copied* into the record any more — it is **moved**: the
    idea file comes back holding its frontmatter stamp over a stub, and the record is
    the single home of the text (leaving the full copy in place was rejected: that is
    two artifacts holding one claim). So the same rollback that used to
    protect a stamp now protects the prose, and it protects it by restoring
    ``original_text`` — the bytes read off disk, not a reconstruction.

    KYO-107 adds ``--section``, and the transaction is unchanged again: the only thing that
    moves is *how much* of the file the record takes and what goes back in its place. A
    section promotion writes one stub under one heading and leaves the rest of the file — the
    other sections, the frontmatter, the order — byte-identical, so the same
    restore-``original_text`` rollback still covers exactly the bytes at risk.

    KYO-228 reversed that: promotion consumes the idea. The stub's reason was a future
    graph of ideas flowing to records, judged not worth keeping, and the promoted text
    already lives in the record with a ``Promoted from:`` line. So this is now the
    *default*: a whole-file promotion deletes the idea file once the record is written,
    and a section promotion removes that section — heading included — rather than
    stubbing it. ``--keep`` reaches the pre-KYO-228 behaviour above, byte for byte. The
    rollback shape is unchanged either way: on any failure after the file is touched,
    ``original_text`` is written back — which recreates a deleted file exactly as well as
    it restores a stamped one, because a write is a write.
    """
    store = ctx.store()
    registry = _load_registry(store)
    kind_def = registry.require(args.kind)
    _check_kind_accepts_idea(store, registry, args.kind)

    idea_arg = Path(args.idea_file)
    idea_path = (idea_arg if idea_arg.is_absolute() else Path.cwd() / idea_arg).resolve()
    ideas_root = ideas_module.ideas_dir(ctx.root).resolve()
    try:
        idea_path.relative_to(ideas_root)
    except ValueError as exc:
        raise KyoseiError(
            f"{args.idea_file} is not under this project's ideas/ directory ({ideas_root})"
        ) from exc
    if not idea_path.is_file():
        raise KyoseiError(f"{args.idea_file} is not a file")

    original_text = idea_path.read_text(encoding="utf-8")
    frozen = normalise(original_text)
    existing_stamps = ideas_module.promotion_stamps(frozen)
    section: ideas_module.Section | None = None
    if args.section is not None:
        section = _require_section(
            args.idea_file, frozen, single_line(args.section, what="--section"), existing_stamps
        )
    elif existing_stamps:
        # KYO-30: `promoted` is a list, so the refusal names every record the file
        # already points at — a message shaped for one record would understate a file
        # that carries several promotions.
        #
        # KYO-106 changes what the refusal is *about*. Before the stub, re-promoting made a
        # duplicate record; now the file's prose is not in the file at all, so a second
        # promotion would copy a stub into a new record and the text would be nowhere the
        # caller was looking. The message therefore says where the text went, by id — the
        # one place it can be read from.
        named = ", ".join(
            f"{stamp.record_id} at {stamp.at}" if stamp.at else stamp.record_id
            for stamp in existing_stamps
        )
        raise KyoseiError(
            f"{args.idea_file} already carries a promotion stamp; its text lives in"
            f" {named}, not in this file; refusing"
        )

    idea_rel = idea_path.relative_to(ctx.root).as_posix()

    # KYO-330/KYO-340: `--from FILE` stands in for --title/--criteria-file/
    # --touches-file/--scope -- `kyo promote` has no --plan flag of its own, so a
    # file's `plan:` (accepted here, KYO-340.c1) is the only way to set one, on the
    # same validation `kyo new --plan` applies (`_require_plan`, below). `kyo
    # promote` also has no --description-file flag: the description is the idea
    # being promoted UNLESS the file's own body differs from it, whitespace aside
    # (KYO-340.c2, `_promote_description`) -- so the same file shape `kyo new
    # --from` accepts (title, plan:, a body, '## Criteria') is now accepted here
    # too, rather than refused for carrying fields this command "has no flag for".
    from_file = getattr(args, "from_file", None)
    check_only = bool(getattr(args, "check", False))
    if check_only and not from_file:
        raise UsageError(
            "--check only makes sense with --from; there is nothing else to "
            "validate without filing"
        )
    if from_file:
        _refuse_from_combo(
            "--from",
            args,
            [
                ("title", "--title"),
                ("scope", "--scope"),
                ("criteria_file", "--criteria-file"),
                ("touches_file", "--touches-file"),
            ],
        )
        parsed = _parse_record_file(
            read_prose(from_file, what="--from"),
            source=from_file,
            accepts_plan=True,
            accepts_description=True,
        )
        title = parsed.title
        plan = parsed.plan
        criteria_texts = parsed.criteria_texts
        touch_lines = parsed.touch_lines
        scope_arg = parsed.scope
        from_body = parsed.description
    else:
        # The title defaults to the heading of whatever is being promoted: the file's
        # first top-level heading for a whole file, and the addressed section's own
        # heading for a section — which is the name the caller just typed, so
        # `--section` alone is enough to promote and the record comes out called what
        # the vault calls it. `--from` always states its own title (KYO-330: one file,
        # one grammar), so this default is a flags-path-only convenience.
        default_title = section.heading if section else ideas_module.first_heading(frozen)
        title = single_line(args.title, what="--title") if args.title else default_title
        if not title:
            raise KyoseiError(
                f"no --title given and {args.idea_file} has no top-level '# heading' "
                "to default to"
            )
        criteria_texts = (
            read_lines(args.criteria_file, what="--criteria-file") if args.criteria_file else []
        )
        touch_lines = (
            read_lines(args.touches_file, what="--touches-file") if args.touches_file else []
        )
        scope_arg = args.scope
        # `kyo promote` has no --plan flag (only --from's front matter states one)
        # and no --description-file (the idea being promoted is always the
        # description on this branch, below) -- both true whether or not --from is
        # in play, so they are set here rather than left to whatever the last
        # branch happened to leave in scope.
        plan = None
        from_body = ""

    if kind_def.requires_criteria and not criteria_texts:
        raise KyoseiError(
            f"kind {args.kind!r} has policy.requires_criteria: true; pass "
            "--criteria-file" + (" or a '## Criteria' section in --from" if from_file else "")
        )
    # The separate-flag door asks whether a touches file was PASSED, as it always has
    # (an empty file is a declaration of no touches, and tests file records that way);
    # only the --from door, which has no file to pass, asks whether any were named.
    if kind_def.archetype == "work" and not (touch_lines if from_file else args.touches_file):
        raise KyoseiError(
            f"kind {args.kind!r} is archetype 'work'; pass --touches-file (one 'path' "
            "or 'path#symbol' per line, relative to the project root)"
            + (", or 'touches:' in --from's front matter" if from_file else "")
        )
    touches = _validate_touches(ctx.code_root, touch_lines)
    # Law 13: promotion is the moment structure is authored — "criteria, touches, and
    # scope" is the charter's own list, and scope is the third of the three that had no
    # schema representation to be authored into until now.
    scope, scopes = _checked_scope(store, scope_arg)

    if check_only:
        if plan is not None:
            _require_plan(store, plan)
        promoted_text = section.text if section else ideas_module.promoted_body(frozen)
        promote_source = f"{args.idea_file}#{section.heading}" if section else args.idea_file
        description, description_note = _promote_description(
            promoted_text, promote_source, from_body=from_body
        )
        extra_lines = [f"promoting {promote_source}"]
        if description_note:
            extra_lines.append(description_note)
        return _from_check_output(
            command="promote",
            kind=args.kind,
            invocation=f"kyo promote {args.idea_file} --kind {args.kind}",
            from_file=from_file,
            title=title,
            plan=plan,
            scope=scope,
            scopes=scopes,
            description=description,
            criteria_texts=criteria_texts,
            touches=touches,
            extra_lines=extra_lines,
            extra_data={"idea_file": idea_rel, "description_note": description_note},
        )

    # KYO-8.c4: same creation-time duplicate check as `kyo new`, against the
    # corpus as it stands before this record is created.
    _refuse_identical_recent(store, title, criteria_texts, force=args.force)
    existing_titles, existing_criteria = _dedupe_corpus(store)
    duplicate_warnings = sweep_module.find_creation_time_duplicates(
        new_title=title,
        new_criteria_texts=criteria_texts,
        existing_titles=existing_titles,
        existing_criteria=existing_criteria,
    )
    warning_lines, warning_data = _duplicate_warning_output(duplicate_warnings)

    initial = _load_status_ladder(store).initial
    # KYO-106: the record takes the prose *over* — the file's body is replaced by a stub
    # below, so this is the only copy afterwards, not one of two. The frontmatter block is
    # not prose and does not travel: it stays in the idea file, where the stamp is written.
    #
    # KYO-107: for a section the same sentence holds one level down. What stays behind is the
    # heading — a section's frontmatter, and the anchor `[[file#Heading]]` resolves against —
    # and the provenance pointer is written in that same anchor form, so it names the region
    # the text came from rather than only the file.
    promoted_text = section.text if section else ideas_module.promoted_body(frozen)
    source = f"{idea_rel}#{section.heading}" if section else idea_rel
    description, description_note = _promote_description(
        promoted_text, source, from_body=from_body
    )

    keep = bool(args.keep)
    stamp_time = now()
    idea_mutated = False
    record_id: str | None = None
    events: list[dict] = []
    new_text: str | None = None  # None only for a default (consuming) whole-file promotion
    try:
        with store.transaction():
            if plan is not None:
                _require_plan(store, plan)
            record_id = store.new_record_id()
            seq = int(record_id.rsplit("-", 1)[1])
            store.conn.execute(
                "INSERT INTO records (id, seq, kind, title, status, plan_id, scope,"
                " description, wont_do_reason, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    record_id,
                    seq,
                    args.kind,
                    title,
                    initial,
                    plan,
                    scope,
                    description,
                    stamp_time,
                    stamp_time,
                ),
            )
            added_criteria = _add_criteria(store, record_id, criteria_texts)
            added_touches = _add_touches(store, record_id, touches)
            # KYO-554: inside the transaction, so the export carries them.
            standing_summary = raise_standing_obligations(
                ctx, store, record_id, stamp=stamp_time
            )

            if keep:
                # Pre-KYO-228 behaviour: a stub replaces the promoted prose and the
                # frontmatter carries the stamp, so a second promotion can still be
                # refused by naming the record the text lives in.
                new_text = (
                    ideas_module.section_stub_text(frozen, section, record_id, stamp_time)
                    if section
                    else ideas_module.stub_text(frozen, record_id, stamp_time)
                )
                try:
                    idea_path.write_text(new_text, encoding="utf-8")
                except OSError as exc:
                    raise KyoseiError(
                        f"could not write promotion stamp to {args.idea_file}: {exc}"
                    ) from exc
            elif section:
                # KYO-228 default: the section -- heading and all -- is gone, not stubbed.
                new_text = ideas_module.consumed_section_text(frozen, section)
                try:
                    idea_path.write_text(new_text, encoding="utf-8")
                except OSError as exc:
                    raise KyoseiError(
                        f"could not remove section {args.section!r} from {args.idea_file}:"
                        f" {exc}"
                    ) from exc
            else:
                # KYO-228 default: the record is now the file's only home, so the file
                # goes rather than a pointer being left where it was.
                try:
                    idea_path.unlink()
                except OSError as exc:
                    raise KyoseiError(f"could not remove {args.idea_file}: {exc}") from exc
            idea_mutated = True

            after, events = commands._commit_and_export(
                store,
                command=f"promote {source} -> {args.kind}",
                actor=ctx.actor,
                record_ids=[record_id],
                before={record_id: None},
            )
    except BaseException:
        # Restoring `original_text` recreates a deleted file exactly as well as it
        # restores a stamped one -- `write_text` makes no distinction, and neither does
        # the guarantee this protects: the idea file is never left mutated without a
        # persisted record, whichever of the three writes above ran.
        if idea_mutated:
            idea_path.write_text(original_text, encoding="utf-8")
        raise
    commands._finish_mutation(store, [record_id], events)

    title_warning_lines, title_warning = _title_warning_output(title)
    criteria_warning_lines, criteria_warning_data = _criteria_warning_output(added_criteria)

    lines = [
        f"created {record_id} (promoted from {source})",
        *warning_lines,
        echo_value("kind", args.kind),
        echo_value("title", title),
        *title_warning_lines,
    ]
    if plan:
        lines.append(echo_value("plan", plan))
    lines.append(echo_value("scope", scope or "(none declared)"))
    lines += echo_block("description", description)
    if description_note:
        lines.append(description_note)
    lines.append(f"stored criteria: {len(added_criteria)}")
    lines += [f"  {cid}  {DEFAULT_CRITERION_STATE}  {text}" for cid, text in added_criteria]
    lines += criteria_warning_lines
    lines.append(f"stored touches: {len(added_touches)}")
    lines += [
        f"  {tid}  {path}" + (f"#{symbol}" if symbol else "") for tid, path, symbol in added_touches
    ]
    if keep:
        stub = ideas_module.promotion_stub(record_id, stamp_time)
        lines.append(f"stamped {idea_rel}: promoted: {record_id}, promoted_at: {stamp_time}")
        # KYO-106: the file's body changed, so the write-echo says so rather than leaving
        # the caller to discover it. The stub is echoed in full because it is the whole of
        # what ideas/ holds afterwards — a one-line "replaced the body" would be the
        # summary of a write, and law 5 asks for the text.
        #
        # KYO-107: a section promotion says WHICH region it replaced, because the rest of
        # the file is untouched and "the file now holds the stub" would be false about it.
        lines.append(f"{source} now holds the stub; its text lives in {record_id}:")
        lines += [f"  | {line}" for line in stub.rstrip("\n").split("\n")]
        if section:
            remaining = [
                found.heading
                for found in ideas_module.sections(new_text)
                if found.has_text and not found.promoted_to
            ]
            lines.append(
                f"{idea_rel} survives; {len(remaining)} section(s) still unpromoted"
                + (f": {', '.join(repr(name) for name in remaining)}" if remaining else "")
            )
    elif section:
        # KYO-228: no stub, no stamp -- the section is simply gone, so the echo says
        # what happened rather than pointing at a pointer.
        lines.append(f"{source} removed; its text lives in {record_id}")
        remaining = [found.heading for found in ideas_module.sections(new_text)]
        lines.append(
            f"{idea_rel} survives; {len(remaining)} section(s) remain"
            + (f": {', '.join(repr(name) for name in remaining)}" if remaining else "")
        )
    else:
        lines.append(f"{idea_rel} removed; its text lives in {record_id}")
    lines += standing_obligation_receipt(standing_summary)
    scope_lines, scope_data = _scope_suggestion(scopes, declared=scope, touches=touches)
    lines += scope_lines
    prior_art_lines, prior_art_data = prior_art.after_create(
        ctx, args, record_id=record_id, kind=args.kind, command="promote"
    )  # KYO-585
    lines += prior_art_lines
    return Output(
        lines,
        {
            "ok": True,
            "command": "promote",
            "prior_art": prior_art_data,
            "id": record_id,  # KYO-538
            "record_id": record_id,
            "record": after[record_id],
            "standing_obligations": standing_summary,  # KYO-554
            "idea_file": idea_rel,
            # KYO-340.c2: null when the idea's own text was stored (the default), the
            # sentence the "description" write-echo line also carries when it is not.
            "description_note": description_note,
            # KYO-107, law 6: present on every promotion and null for a whole-file one, so a
            # consumer reads "the whole file" as a stated fact rather than inferring it from
            # a key that is not there.
            "section": section.heading if section else None,
            "kept": keep,
            # KYO-228, same law 6 reasoning: null (rather than absent) states plainly that
            # no stamp was written, for the now-default consuming promotion.
            "stamped_at": stamp_time if keep else None,
            "duplicate_warnings": warning_data,
            "title_length_warning": title_warning,
            "criteria_length_warnings": criteria_warning_data,
            "scope_suggestion": scope_data,
        },
    )


def _check_kind_accepts_kind(
    registry: kinds.KindRegistry, source_id: str, source_kind: str, target_kind: str
) -> None:
    """KYO-50: `kyo spawn` is the kind-to-kind edge, and must consult the registry.

    Same shape and same reasoning as :func:`_check_kind_accepts_intake` one edge
    over: the registry defines three sources an ``accepts_from`` entry can name, and
    each one is a command that enforces it. No ``--force`` here either — the escape
    hatch is the registry, which is one reviewable line in a versioned file, and
    unlike a flag it stays visible afterwards.

    Collect-then-report style (law 6): the caller gets every open door in one
    message — which kinds this source *can* spawn, whether the target is reachable
    by some other route (`kyo new` / `kyo promote`), and the exact registry edit that
    would make this transition legal.
    """
    target = registry.require(target_kind)
    if target.accepts_kind(source_kind):
        return
    spawnable = registry.spawn_targets(source_kind)
    detail = [
        f"kind {target_kind!r} does not accept a {source_kind!r}; its accepts_from is "
        f"[{', '.join(target.accepts_from) or '(empty)'}] and `kyo spawn` is the "
        "kind-to-kind edge (kyosei/kinds.yml)",
        f"kinds {source_id} (a {source_kind}) can spawn: {', '.join(spawnable) or '(none)'}",
    ]
    if target.accepts_intake:
        detail.append(
            f"a {target_kind!r} can be filed directly instead: "
            f"`kyo new {target_kind} --title ...`"
        )
    if "idea" in target.accepts_from:
        detail.append(
            f"or promoted from the ideas layer: `kyo promote <idea-file> --kind {target_kind}`"
        )
    detail.append(
        f"if this project wants {source_kind!r} -> {target_kind!r} to be a real transition, "
        f"add {source_kind!r} to {target_kind!r}'s accepts_from and {target_kind!r} to "
        f"{source_kind!r}'s produces in {registry.path} — both, because the edges are "
        "validated symmetric at load time"
    )
    raise ContractViolation(CONTRACT_KIND_NOT_SPAWNABLE_FROM, detail)


def cmd_spawn(ctx: Context, args: Namespace) -> Output:
    """``kyo spawn <source-id> --kind <target>`` — the kind-to-kind edge (KYO-50).

    The registry names three sources an ``accepts_from`` entry can carry: ``intake``
    (`kyo new`), ``idea`` (`kyo promote`), and another kind — and until this command
    the third one had no implementation at all. Every ``bug -> task``,
    ``investigation -> evidence``, ``proposal -> decision`` edge was a transition
    `kyo map` could draw, `kyo doctor` would validate the symmetry of, and nothing
    could walk. That is law 2 in its subtler form: the fields had a consumer, but the
    consumer rendered them instead of acting on them.

    Three questions the record (KYO-50.c2) left open, decided here and stated where
    the behaviour is, not only in a report:

    **1. The source's text is referenced, not copied.** `kyo promote` copies the
    whole idea body because promotion *consumes* the idea — the file is stamped and
    stops being a live parallel copy. A spawn source stays live and editable, so
    copying its body would make two records that drift (law 3). The target gets a
    provenance stub naming the source and the ``produced-by`` edge; anything the
    author actually wants said arrives through ``--description-file`` (law 5). Two
    conventions in this codebase, not the three a fresh answer would have made:
    promotion copies a consumed source, `kyo split` and `kyo spawn` reference a live
    one.

    **2. Spawning does not change the source's status.** A status is a claim about
    the work, and `check_transition` is what decides when that claim may be made — a
    spawn that moved a proposal to ``done`` would either bypass those contracts or
    fail inside a command the caller asked to create a record. It also cannot be made
    uniform: half the registry's kind-to-kind edges start at a ``knowledge`` kind,
    which has no status flow to advance (`kyo move` refuses it outright). And with
    the edge repeatable (below), the first of five evidence records would otherwise
    settle an investigation that is nowhere near finished. The output says the status
    is unchanged, on the `kyo gate` precedent ("status is unchanged: gates are
    orthogonal to status"), so nobody has to infer it from silence.

    **3. The edge is repeatable.** One investigation may produce five evidence
    records; refusing the second would force the caller into `kyo new`, which is the
    "add ``intake`` to everything" flattening this record was filed to stop. The
    ``produced-by`` edge carries it without help: one row per (successor, source)
    pair, and each spawn creates a *new* successor id, so the pairs are distinct and
    the "already exists" check in `kyo link` never fires. What repeatability costs is
    the ability to notice a re-spawn nobody meant, so the creation-time duplicate
    check `kyo new`/`kyo promote` run applies here too — a warning, never a refusal
    (charter: flags, never enforces).

    Ceremony matches `kyo promote`, deliberately: a target whose
    ``policy.requires_criteria`` is true needs ``--criteria-file``, and an archetype
    ``work`` target needs ``--touches-file``. Spawning is a judgment about what to do
    next, which is exactly the moment law 13 says structure is worth authoring.
    """
    store = ctx.store()
    registry = _load_registry(store)
    ladder = _load_status_ladder(store)
    source_id = args.id
    source = store.record(source_id)
    source_kind = source["kind"]
    source_archetype = registry.require(source_kind).archetype

    _check_kind_accepts_kind(registry, source_id, source_kind, args.kind)

    if ladder.is_terminal(source["status"]):
        # A retired record produces nothing further. The registry edge is fine here,
        # so the caller is pointed at the other live sources of the target kind
        # rather than at kinds.yml.
        others = [
            token for token in registry.require(args.kind).accepts_from if token != source_kind
        ]
        raise ContractViolation(
            CONTRACT_SPAWN_SOURCE_IS_RETIRED,
            [
                f"{source_id} stands at "
                f"{standing(source['status'], source_archetype, ladder)!r}; a "
                "retired record is settled and produces nothing further (its id is never "
                "reused either)",
                f"other sources {args.kind!r} accepts: [{', '.join(others) or '(none)'}] — "
                f"spawn from a live one, or file the {args.kind!r} through `kyo new`/"
                "`kyo promote` if its accepts_from carries intake/idea",
            ],
        )

    target = registry.require(args.kind)

    # KYO-522: the same evidence path `kyo new` uses (KYO-521), read before anything else
    # reads stdin; required only when the source is a carried kind (a reminder).
    evidence_text = _evidence_at_filing(
        registry, args.kind, args, spawn_source=source_id, spawn_source_kind=source_kind
    )

    # KYO-330: `--from FILE` stands in for --title/--description-file/--criteria-file/
    # --touches-file -- `kyo spawn` has no --plan, so a file's `plan:` is refused
    # rather than silently unused.
    from_file = getattr(args, "from_file", None)
    check_only = bool(getattr(args, "check", False))
    if check_only and not from_file:
        raise UsageError(
            "--check only makes sense with --from; there is nothing else to "
            "validate without filing"
        )
    if from_file:
        _refuse_from_combo(
            "--from",
            args,
            [
                ("title", "--title"),
                ("scope", "--scope"),
                ("description_file", "--description-file"),
                ("criteria_file", "--criteria-file"),
                ("touches_file", "--touches-file"),
            ],
        )
        parsed = _parse_record_file(
            read_prose(from_file, what="--from"),
            source=from_file,
            accepts_plan=False,
            accepts_description=True,
        )
        title = parsed.title
        criteria_texts = parsed.criteria_texts
        touch_lines = parsed.touch_lines
        scope_arg = parsed.scope
        body = parsed.description
    else:
        if args.title is None:
            raise UsageError("--title is required (or pass --from FILE instead)")
        title = single_line(args.title, what="--title")
        criteria_texts = (
            read_lines(args.criteria_file, what="--criteria-file") if args.criteria_file else []
        )
        touch_lines = (
            read_lines(args.touches_file, what="--touches-file") if args.touches_file else []
        )
        scope_arg = args.scope
        body = (
            read_prose(args.description_file, what="--description-file")
            if args.description_file
            else ""
        )

    if target.requires_criteria and not criteria_texts:
        raise KyoseiError(
            f"kind {args.kind!r} has policy.requires_criteria: true; pass "
            "--criteria-file" + (" or a '## Criteria' section in --from" if from_file else "")
        )
    # The separate-flag door asks whether a touches file was PASSED, as it always has
    # (an empty file is a declaration of no touches, and tests file records that way);
    # only the --from door, which has no file to pass, asks whether any were named.
    if target.archetype == "work" and not (touch_lines if from_file else args.touches_file):
        raise KyoseiError(
            f"kind {args.kind!r} is archetype 'work'; pass --touches-file (one 'path' "
            "or 'path#symbol' per line, relative to the project root)"
            + (", or 'touches:' in --from's front matter" if from_file else "")
        )
    touches = _validate_touches(ctx.code_root, touch_lines)
    # Deliberately NOT defaulted from the source, on decision 1's own reasoning above:
    # a spawn crosses a kind boundary — proposal to decision, investigation to evidence
    # — and what the source's work was about is not automatically what the successor's
    # is. `kyo split` is the opposite case and inherits (see `cmd_dispatch._cmd_split`):
    # its successor is the same work, carrying some of the same criteria.
    scope, scopes = _checked_scope(store, scope_arg)

    head = body or f"Spawned from {source_id} ({source_kind}): {source['title']}"
    description = f"{head}\n\n---\nSpawned from: {source_id}\n"

    if check_only:
        return _from_check_output(
            command="spawn",
            kind=args.kind,
            invocation=f"kyo spawn {source_id} --kind {args.kind}",
            from_file=from_file,
            title=title,
            plan=None,
            scope=scope,
            scopes=scopes,
            description=description,
            criteria_texts=criteria_texts,
            touches=touches,
            extra_lines=[f"spawning from {source_id} ({source_kind} -> {args.kind})"],
        )

    _refuse_identical_recent(store, title, criteria_texts, force=args.force)
    existing_titles, existing_criteria = _dedupe_corpus(store)
    duplicate_warnings = sweep_module.find_creation_time_duplicates(
        new_title=title,
        new_criteria_texts=criteria_texts,
        existing_titles=existing_titles,
        existing_criteria=existing_criteria,
    )
    warning_lines, warning_data = _duplicate_warning_output(duplicate_warnings)

    with store.transaction():
        before = {source_id: export.record_document(store, source_id)}
        record_id = store.new_record_id()
        seq = int(record_id.rsplit("-", 1)[1])
        stamp = now()
        store.conn.execute(
            "INSERT INTO records (id, seq, kind, title, status, plan_id, scope, description,"
            " wont_do_reason, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?)",
            (record_id, seq, args.kind, title, ladder.initial, scope, description, stamp, stamp),
        )
        added_criteria = _add_criteria(store, record_id, criteria_texts)
        added_touches = _add_touches(store, record_id, touches)
        evidence = (
            commands._add_evidence(
                store, record_id, evidence_text, supports_id=None, author=ctx.actor
            )
            if evidence_text is not None
            else None
        )

        # The same direction and the same edge type `kyo split` writes (successor
        # produced-by original), reused rather than reinvented: `kyo doctor`'s
        # split_backlink_symmetry check, `kyo show`'s link rendering and the html
        # link graph already read this shape.
        link_id, lseq = store.new_link_id()
        store.conn.execute(
            "INSERT INTO links (id, seq, src_id, edge_type, dst_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (link_id, lseq, record_id, "produced-by", source_id, stamp),
        )
        # KYO-554: inside the transaction, so the export carries them.
        standing_summary = raise_standing_obligations(ctx, store, record_id, stamp=stamp)

        _touch_record(store, source_id)
        after, events = commands._commit_and_export(
            store,
            command=f"spawn {source_id} -> {record_id} ({source_kind} -> {args.kind})",
            actor=ctx.actor,
            record_ids=[source_id, record_id],
            before={**before, record_id: None},
        )
    commands._finish_mutation(store, [source_id, record_id], events)

    title_warning_lines, title_warning = _title_warning_output(title)
    criteria_warning_lines, criteria_warning_data = _criteria_warning_output(added_criteria)

    lines = [
        f"created {record_id} (spawned from {source_id}: {source_kind} -> {args.kind})",
        *warning_lines,
        echo_value("kind", args.kind),
        echo_value("title", title),
        *title_warning_lines,
        echo_value("scope", scope or "(none declared)"),
    ]
    lines += echo_block("description", description)
    lines.append(f"stored criteria: {len(added_criteria)}")
    lines += [f"  {cid}  {DEFAULT_CRITERION_STATE}  {text}" for cid, text in added_criteria]
    lines += criteria_warning_lines
    lines.append(f"stored touches: {len(added_touches)}")
    lines += [
        f"  {tid}  {path}" + (f"#{symbol}" if symbol else "")
        for tid, path, symbol in added_touches
    ]
    lines.append(f"link: {link_id}  {record_id} produced-by {source_id}")
    if evidence is not None:
        lines.append(f"attached {evidence[0]}")
        lines += echo_block("evidence", evidence_text)
        lines.append(echo_value("sha256", evidence[1]))
    # KYO-50.c2, decision 2: said out loud, because "the source is now settled" is
    # the assumption a spawn invites and the one thing this command does not do.
    unchanged = (
        f"{source_id} is unchanged at "
        f"{standing(source['status'], source_archetype, ladder)}: "
        "spawning is not a status change"
    )
    lines.append(
        f"{unchanged} — move it yourself with `kyo move` if producing this settled it"
        if source_archetype == "work"
        else f"{unchanged}, and a {source_kind} has no status flow to advance"
    )
    lines += standing_obligation_receipt(standing_summary)
    scope_lines, scope_data = _scope_suggestion(scopes, declared=scope, touches=touches)
    lines += scope_lines
    prior_art_lines, prior_art_data = prior_art.after_create(
        ctx, args, record_id=record_id, kind=args.kind, command="spawn"
    )  # KYO-585
    lines += prior_art_lines
    return Output(
        lines,
        {
            "ok": True,
            "command": "spawn",
            "prior_art": prior_art_data,
            "id": record_id,  # KYO-538
            "record_id": record_id,
            "standing_obligations": standing_summary,  # KYO-554
            "link_id": link_id,
            # KYO-522: the evidence `--evidence-file` attached, or null.
            "evidence_id": evidence[0] if evidence is not None else None,
            "scope_suggestion": scope_data,
            "record": after[record_id],
            "source": after[source_id],
            "link": {
                "id": link_id,
                "src": record_id,
                "type": "produced-by",
                "dst": source_id,
            },
            "duplicate_warnings": warning_data,
            "title_length_warning": title_warning,
            "criteria_length_warnings": criteria_warning_data,
        },
    )


def _correct_criterion_text(store: Store, criterion, text: str) -> tuple[list[str], list[dict]]:
    """KYO-58.c1: rewrite an **unattested** criterion's text, in place.

    Immutability is right and its start line was wrong. The hazard it exists for is
    a verdict attesting to text that later changed: a verdict records "this was met,
    by this method, on this date, with this evidence", and if the wording moves
    underneath it, that evidence silently re-attaches to a different claim. That
    argument is decisive and it is *entirely about verdicts*. A criterion nobody has
    verified has no attestation to protect, and treating "wrong when written" the
    same as "superseded" cost this project a criterion (KYO-33.c2 named
    ``path.py:105`` as a flagged example; the worker proved the token was never
    flagged, before or after the fix — a plain authoring error, caught while the
    criterion was ``unmet-unbuilt`` with no verdict, uncorrectable, and verified
    ``met`` still containing it).

    **Where the boundary sits (KYO-58.c4), decided rather than assumed.** The
    window closes at the first verdict, NOT at "first quoted into a frozen brief":

    * A brief is an instruction, not an attestation. It says "go and do this"; a
      verdict says "this is true". Only the second is damaged by the text moving.
    * A brief keeps its own verbatim copy — ``dispatches.brief`` is frozen at
      dispatch time and is git-canonical — so what a worker actually read stays
      recoverable forever no matter what the criterion says later. A verdict stores
      no copy of the text it attested to, which is exactly why *it* has to freeze
      the text instead.
    * The decisive case: KYO-33.c2 had been dispatched AND read by a worker before
      any verdict existed. A "never quoted into a brief" boundary would have refused
      the one correction the whole mechanism exists for — it would not fix its own
      motivating example.
    * And it would barely be a window at all. A criterion is quoted into every brief
      its record generates, so that boundary would freeze every criterion on any
      record that has ever been dispatched — the records most likely to have been
      shown to be wrong.

    What the brief case gets instead is loudness, not refusal: the correction names
    every dispatch whose frozen brief quoted the old wording, because someone read
    it (law 6). The edit is an event like any other, so the original wording stays
    readable in the ledger.
    """
    if not text.strip():
        raise UsageError("--text-file must not be empty: a criterion with no text is not a claim")
    corrected = single_line(text, what="--text-file")
    if commands.criterion_has_a_verdict(store, criterion["id"], criterion["state"]):
        raise ContractViolation(
            CONTRACT_CRITERION_TEXT_IS_ATTESTED,
            [
                f"{criterion['id']} is {criterion['state']} and something has already "
                "attested to this wording; correcting it now would silently re-attach that "
                "verdict to a different claim",
                f"`kyo restate {criterion['id']} --text-file FILE` is the path from here: it "
                "voids this one and adds the new wording as its own criterion, back-linked, "
                "leaving this one's verdict history intact and attached to the text it was "
                "actually about",
            ],
        )
    store.conn.execute(
        "UPDATE criteria SET text = ?, updated_at = ? WHERE id = ?",
        (corrected, now(), criterion["id"]),
    )
    lines = [
        echo_value(f"criterion {criterion['id']} text", corrected),
        f"  was: {criterion['text']}",
        "  corrected in place: no verdict had been recorded against the old wording, so "
        "nothing attests to it (the old text stays readable in the events ledger)",
    ]
    # KYO-333.c2/c3: a correction is one of this command's stated sites, on the same
    # terms as `--add-criteria-file` -- the corrected wording gets exactly the same
    # check the originally-filed one would have.
    warning_lines, warning_data = _criteria_warning_output([(criterion["id"], corrected)])
    lines += warning_lines
    quoted_by = commands.briefs_quoting(store, criterion["record_id"])
    if quoted_by:
        lines.append(
            f"  NOTE: {len(quoted_by)} frozen brief(s) quoted the old wording "
            f"({', '.join(quoted_by)}) — those workers read it. The briefs are frozen and "
            "still say what they said; check whether any of them acted on the error"
        )
    return lines, warning_data


def _touch_drift_correction(ctx: Context, store, dispatch_id: str) -> DriftCorrection:
    """The corrected touches list for ``dispatch_id`` (KYO-147.c4).

    Imported inside the function on purpose: ``cmd_dispatch`` imports this module for
    the same reason (it needs this module's touch validation), and a module-level
    import in both directions is a cycle. The computation lives there because that is
    where the drift finding is computed and printed — one computation, read here
    rather than reimplemented, so what `kyo touch-drift` shows a controller and what
    this stores cannot come to differ (law 3).

    ``against`` is the default trunk: this path exists for the post-ingest case, where
    :func:`touch_drift.touch_drift` prefers the report's ``touched_files[]`` and the
    ref matters only to the git-diff fallback. A controller who needs another ref
    reads it with `kyo touch-drift --against REF` and applies it with
    ``--touches-file`` as before.
    """
    from . import touch_drift

    return touch_drift.touch_drift_correction(ctx, store, dispatch_id, "main")


#: KYO-509: `kyo edit --kind` refused because the target is not in ``kinds.yml``.
CONTRACT_REKIND_UNKNOWN_KIND = "rekind-target-kind-unknown"
#: KYO-509: `kyo edit --kind` refused because the target's archetype is not the record's.
CONTRACT_REKIND_ARCHETYPE_DIFFERS = "rekind-archetype-differs"
#: KYO-509: `kyo edit --kind` refused because the change would strand something the
#: current kind licenses (a plan's members, a carried item's since-note).
CONTRACT_REKIND_STRANDS_STATE = "rekind-strands-state"


def _current_archetype(
    store: Store, registry: kinds.KindRegistry, row
) -> tuple[str | None, str]:
    """The archetype a record has now, and where that answer came from (KYO-509).

    The kind is normally defined in this project's ``kinds.yml``, which is the answer.
    The case `kyo edit --kind` exists for is the one where it is NOT -- the kind was
    removed and `kyo doctor`'s ``kind_registry`` check names the record at every stop --
    so two fallbacks, in order: the packaged default registry this project's copy came
    from (a removed default kind such as ``chore`` is still described there), then the
    record's own status, since only a ``work`` record ever holds a status whose role is
    neither initial nor retired (a ``knowledge`` record has no status flow). ``None``
    when none of the three can say, and the caller states that the archetype was not
    checked rather than guessing.
    """
    kind = row["kind"]
    if kind in registry.kinds:
        return registry[kind].archetype, "kinds.yml"
    from .doctor_registry import _default_kind_registry

    default = _default_kind_registry(store)
    if default is not None and kind in default.kinds:
        return default[kind].archetype, "the packaged default kinds.yml"
    ladder = _load_status_ladder(store)
    if ladder.role(row["status"]) not in (None, ladder.roles.get(ladder.initial), ROLE_RETIRED):
        return "work", f"its status {row['status']!r}, which only a work record can hold"
    return None, ""


def _rekind(store: Store, record_id: str, row, target: str) -> list[str]:
    """Change ``record_id``'s kind to ``target`` inside the caller's transaction (KYO-509).

    A plain column update, carried by the ordinary edit event and the record's export
    document -- ``kind`` is already a field of both, and `kyo rebuild` reads it back from
    the export, so no schema change is needed. Refuses an undefined target, an archetype
    change (work and knowledge records are different shapes: one has a status flow and
    dispatches, the other has none), and a change that would strand state the current
    kind licenses. Returns the receipt lines, which name the old and the new kind.
    """
    registry = _load_registry(store)
    old = row["kind"]
    if target not in registry.kinds:
        raise ContractViolation(
            CONTRACT_REKIND_UNKNOWN_KIND,
            [
                f"kind {target!r} is not defined in kyosei/kinds.yml, so {record_id} "
                f"cannot become one; known kinds: {', '.join(registry.names)}",
                f"pass one of those, or define {target!r} in kyosei/kinds.yml first",
            ],
        )
    target_def = registry[target]
    if target == old:
        raise UsageError(f"{record_id} is already kind {old!r}; --kind {target} changes nothing")
    archetype, source = _current_archetype(store, registry, row)
    if archetype is not None and archetype != target_def.archetype:
        same = [n for n in registry.names if registry[n].archetype == archetype and n != old]
        raise ContractViolation(
            CONTRACT_REKIND_ARCHETYPE_DIFFERS,
            [
                f"{record_id} is kind {old!r} (archetype {archetype}, from {source}) and "
                f"{target!r} is archetype {target_def.archetype}; a re-kind keeps the "
                "archetype, because a work record (status flow, dispatches) and a knowledge "
                "record (claims, no status flow) are different shapes",
                f"{archetype} kinds it can become: {', '.join(same) or '(none)'}",
            ],
        )
    if old == "plan":
        members = [
            r["id"]
            for r in store.conn.execute(
                "SELECT id FROM records WHERE plan_id = ? ORDER BY seq", (record_id,)
            )
        ]
        if members:
            raise ContractViolation(
                CONTRACT_REKIND_STRANDS_STATE,
                [
                    f"{record_id} is a plan with {len(members)} member(s) "
                    f"({', '.join(members)}); as a {target!r} it could not be their plan",
                    "detach them first with `kyo edit <member> --no-plan`, then re-kind",
                ],
            )
    if row["since_note"] is not None and not target_def.carried:
        raise ContractViolation(
            CONTRACT_REKIND_STRANDS_STATE,
            [
                f"{record_id} carries since-note {row['since_note']}, which only a kind "
                f"with policy.carried keeps, and {target!r} is not carried",
                "re-kind it to a carried kind instead: "
                + (", ".join(n for n in registry.names if registry[n].carried) or "(none)"),
            ],
        )
    store.conn.execute("UPDATE records SET kind = ? WHERE id = ?", (target, record_id))
    lines = [echo_value("kind", f"{old} -> {target}")]
    if archetype is None:
        lines.append(
            f"  note: {old!r} is defined neither in kyosei/kinds.yml nor in the packaged "
            "default, and the record's status does not show its archetype, so the "
            "archetype was not compared"
        )
    if target_def.requires_criteria and not store.conn.execute(
        "SELECT 1 FROM criteria WHERE record_id = ? LIMIT 1", (record_id,)
    ).fetchone():
        lines.append(
            f"  note: {target!r} has policy.requires_criteria: true and {record_id} has "
            "no criteria, so it is parked until some are authored -- add them with "
            f"`kyo edit {record_id} --add-criteria-file f.txt`"
        )
    return lines


def cmd_edit(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    changed_anything = any(
        [
            args.title is not None,
            args.description_file is not None,
            args.note_file is not None,
            args.supersede_note is not None,
            args.add_criteria_file is not None,
            args.touches_file is not None,
            getattr(args, "touches_from_drift", None) is not None,
            args.doc_refs_file is not None,
            args.criterion is not None,
            args.text_file is not None,
            args.plan is not None,
            args.no_plan,
            args.scope is not None,
            args.no_scope,
            getattr(args, "since_note", None) is not None,
            getattr(args, "kind", None) is not None,
        ]
    )
    if not changed_anything:
        raise UsageError("kyo edit needs something to change; see `kyo edit --help`")
    if args.supersede_note and not args.note_file:
        raise UsageError("--supersede-note requires --note-file (the note that replaces it)")
    if args.criterion and not (args.state or args.text_file):
        raise UsageError("--criterion requires --state (to void it) or --text-file (to correct it)")
    if args.state and not args.criterion:
        raise UsageError("--state requires --criterion")
    if args.text_file and not args.criterion:
        raise UsageError("--text-file requires --criterion (it corrects one criterion's wording)")
    if args.state and args.text_file:
        raise UsageError(
            "pass --state or --text-file, not both: correcting a criterion's wording and "
            "changing its state are different acts, and doing them in one call would make "
            "the event ledger unable to say which one the reason belongs to"
        )
    if args.state and args.state in VERIFY_VERDICTS:
        raise UsageError(
            f"kyo edit --state only sets {' or '.join(sorted(EDIT_DIRECT_CRITERION_STATES))} "
            f"directly (with --why); {args.state!r} is a verified fact and must come from "
            f"`kyo verify {args.criterion} --verdict {args.state} --method ...` "
            "(KYO-4.c3: a met criterion carries method + evidence, not a bare state flip)"
        )
    if args.plan and args.no_plan:
        raise UsageError("pass --plan or --no-plan, not both")
    if args.scope and args.no_scope:
        raise UsageError("pass --scope or --no-scope, not both")
    # Validated before the transaction opens, exactly like touches: a refusal must not
    # arrive half way through a multi-flag edit that has already rewritten a title.
    scope, scopes = _checked_scope(store, args.scope)
    from_drift = getattr(args, "touches_from_drift", None)
    if args.touches_file is not None and from_drift is not None:
        raise UsageError(
            "pass --touches-file or --touches-from-drift, not both: each REPLACES the "
            "whole touches list, so together they would silently mean 'whichever one "
            "this build happens to read second'"
        )
    # KYO-147.c4: same replacement, same validation, same echo — only the source of
    # the lines differs. Deliberately not a second write path: the correction has to
    # be refused by exactly the checks a hand-written --touches-file is refused by,
    # including the working-tree existence check that catches a file the branch
    # created and the merge has not landed yet (KYO-71's own caveat).
    # KYO-307: the correction carries what it CHANGED on the way out as well as the
    # lines, so the echo below can name it. Nothing else about this path differs: the
    # lines still go through `_validate_touches` unmodified, and a line this flag would
    # be refused for is one `--touches-file` is refused for.
    correction = _touch_drift_correction(ctx, store, from_drift) if from_drift is not None else None
    touch_lines = (
        correction.lines
        if correction is not None
        else (read_lines(args.touches_file, what="--touches-file")
              if args.touches_file is not None else None)
    )
    new_touches = (
        _validate_touches(ctx.code_root, touch_lines) if touch_lines is not None else None
    )
    new_doc_refs = (
        _validate_doc_refs(
            ctx.code_root,
            read_lines(args.doc_refs_file, what="--doc-refs-file"),
            _document_set_or_none(store),
        )
        if args.doc_refs_file is not None
        else None
    )

    record_id = args.id
    lines: list[str] = []
    # KYO-333.c1/c2/c3: set only by the branches below that actually touch a title
    # or criterion text -- `None`/`[]` when this edit did neither, the same way
    # `document["scope"]` and the rest of this command's --json states "unchanged"
    # by carrying the unchanged value rather than omitting the key.
    title_warning: str | None = None
    criteria_warning_data: list[dict] = []

    with store.transaction():
        row = store.record(record_id)
        before = {record_id: export.record_document(store, record_id)}

        # KYO-509: first, so every refusal arrives before any other flag has written,
        # and so the receipt's header below can name the kind the record now has.
        if getattr(args, "kind", None) is not None:
            lines += _rekind(store, record_id, row, args.kind)
            row = store.record(record_id)

        if args.title is not None:
            title = single_line(args.title, what="--title")
            store.conn.execute(
                "UPDATE records SET title = ? WHERE id = ?", (title, record_id)
            )
            lines.append(echo_value("title", title))
            title_warning_lines, title_warning = _title_warning_output(title)
            lines += title_warning_lines

        if args.description_file is not None:
            # Law 6: an empty file means an empty description, not "no change".
            description = read_prose(args.description_file, what="--description-file")
            store.conn.execute(
                "UPDATE records SET description = ? WHERE id = ?", (description, record_id)
            )
            lines += echo_block("description", description)

        if args.plan is not None:
            if args.plan == record_id:
                raise KyoseiError(f"{record_id} cannot be its own plan")
            _require_plan(store, args.plan)
            store.conn.execute(
                "UPDATE records SET plan_id = ? WHERE id = ?", (args.plan, record_id)
            )
            lines.append(echo_value("plan", args.plan))
        elif args.no_plan:
            store.conn.execute("UPDATE records SET plan_id = NULL WHERE id = ?", (record_id,))
            lines.append(echo_value("plan", "(none)"))

        # KYO-100. `--no-scope` exists for the same reason `--no-plan` does: a nullable
        # column needs a way back to NULL, and "pass an empty --scope" would collide
        # with the registry check, which has nothing to say about the empty string.
        if scope is not None:
            store.conn.execute("UPDATE records SET scope = ? WHERE id = ?", (scope, record_id))
            lines.append(echo_value("scope", scope))
        elif args.no_scope:
            store.conn.execute("UPDATE records SET scope = NULL WHERE id = ?", (record_id,))
            lines.append(echo_value("scope", "(none declared)"))

        # KYO-498: set later for an item a project carried before this shipped, judged
        # against the record's own stored filing stamp.
        if getattr(args, "since_note", None) is not None:
            since_note = _checked_since_note(
                store,
                _load_registry(store),
                record_id=record_id,
                kind=row["kind"],
                note_id=args.since_note,
                created_at=row["created_at"],
            )
            store.conn.execute(
                "UPDATE records SET since_note = ? WHERE id = ?", (since_note, record_id)
            )
            lines.append(echo_value("since note", since_note))

        if args.add_criteria_file is not None:
            texts = read_lines(args.add_criteria_file, what="--add-criteria-file")
            added = _add_criteria(store, record_id, texts)
            lines.append(f"stored criteria: {len(added)} added")
            lines += [f"  {cid}  {DEFAULT_CRITERION_STATE}  {text}" for cid, text in added]
            added_warning_lines, added_warning_data = _criteria_warning_output(added)
            lines += added_warning_lines
            criteria_warning_data += added_warning_data

        if new_touches is not None:
            # Law 6's established reading of an empty file (see --description-file
            # above): an empty touches file means an empty list, not "no change".
            replaced, dropped = _replace_touches(store, record_id, new_touches)
            lines.append(f"touches: {len(dropped)} -> {len(replaced)}")
            for path, symbol in dropped:
                lines.append(f"  - {path}" + (f"#{symbol}" if symbol else ""))
            for tid, path, symbol in replaced:
                lines.append(f"  + {tid}  {path}" + (f"#{symbol}" if symbol else ""))
            if correction is not None:
                # KYO-307.c1: which lines this converted, named at the moment it
                # converts them, in `kyo touch-drift`'s own words. A `+path` the merge
                # has landed is stored as the plain path — the record still says the
                # same thing about the file, in the form validation accepts now that
                # the file is there. Empty whenever nothing was converted.
                lines += correction.landed_lines()
            if from_drift is not None:
                # KYO-147.c4 says it out loud at the moment it would be easiest to
                # believe otherwise. The reporter measured this on their own project:
                # they fed drift back after every merge, and the NEXT
                # dispatch's table was wrong anyway — four of five declared sets
                # insufficient. Feeding drift back corrects the record that just
                # finished; the guard reads the record not yet dispatched.
                # KYO-489: the measurement is explained once, in `kyo reference edit`.
                lines.append(
                    f"  (from {from_drift}'s touch drift: "
                    f"{explanations.say('drift-fed-back')})"
                )

        if new_doc_refs is not None:
            # KYO-137.c1. Same empty-file reading as touches above, and the same
            # replace-not-append semantics, for the same reason.
            refs, dropped_refs = _replace_doc_refs(store, record_id, new_doc_refs)
            lines.append(f"doc-refs: {len(dropped_refs)} -> {len(refs)}")
            for path, section in dropped_refs:
                lines.append(f"  - {path}" + (f"#{section}" if section else ""))
            for ref_id, path, section in refs:
                lines.append(f"  + {ref_id}  {path}" + (f"#{section}" if section else ""))

        if args.criterion:
            criterion = store.criterion(args.criterion)
            if criterion["record_id"] != record_id:
                raise KyoseiError(
                    f"{args.criterion} belongs to {criterion['record_id']}, not {record_id}"
                )
        if args.criterion and args.text_file:
            correction_lines, correction_warning_data = _correct_criterion_text(
                store, criterion, read_prose(args.text_file, what="--text-file")
            )
            lines += correction_lines
            criteria_warning_data += correction_warning_data
        elif args.criterion:
            reason = _reason(args)
            check_criterion_state(args.criterion, args.state, reason)
            store.conn.execute(
                "UPDATE criteria SET state = ?, reason = ?, updated_at = ? WHERE id = ?",
                (args.state, reason, now(), args.criterion),
            )
            lines.append(
                echo_value(
                    f"criterion {args.criterion}",
                    f"{criterion['state']} -> {args.state}",
                )
            )
            if reason is not None:
                lines += echo_block(f"criterion {args.criterion} reason", reason)

        if args.note_file is not None:
            text = read_prose(args.note_file, what="--note-file")
            if not text.strip():
                raise UsageError("a note must not be empty")
            note_id, seq = store.new_child_id(record_id, "note")
            store.conn.execute(
                "INSERT INTO notes (id, record_id, seq, text, created_at, superseded_by,"
                " superseded_at) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (note_id, record_id, seq, text, now()),
            )
            if args.supersede_note:
                old = store.note(args.supersede_note)
                if old["record_id"] != record_id:
                    raise KyoseiError(
                        f"{args.supersede_note} belongs to {old['record_id']}, not {record_id}"
                    )
                if old["superseded_by"]:
                    raise KyoseiError(
                        f"{args.supersede_note} was already superseded by {old['superseded_by']}"
                    )
                store.conn.execute(
                    "UPDATE notes SET superseded_by = ?, superseded_at = ? WHERE id = ?",
                    (note_id, now(), args.supersede_note),
                )
                lines.append(echo_value("superseded note", f"{args.supersede_note} -> {note_id}"))
            lines.append(f"appended note {note_id}")
            lines += echo_block(f"note {note_id}", text)

        # KYO-554.c5: an edit that changed what the record says or names raises what
        # it newly names, and nothing it already held an obligation on.
        standing_summary = (
            raise_standing_obligations(ctx, store, record_id)
            if args.title is not None
            or args.description_file is not None
            or new_doc_refs is not None
            or getattr(args, "kind", None) is not None
            else None
        )

        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"edit {record_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    lines.insert(0, f"edited {record_id} ({row['kind']})")
    # Read off the document this edit just produced, not off the flags: an edit that
    # replaced the touches and left the scope alone is exactly the case where a
    # mismatch becomes visible, and the flags alone cannot see it.
    document = after[record_id]
    scope_lines, scope_data = _scope_suggestion(
        scopes,
        declared=document["scope"],
        touches=[(touch["path"], touch["symbol"]) for touch in document["touches"]],
    )
    # KYO-195: the derivation runs on every edit; it SPEAKS only on an edit that could
    # have changed its answer. The JSON block below is deliberately not gated with it.
    if not _edit_could_change_the_derivation(before[record_id], document, args):
        scope_lines = []
    lines += standing_obligation_receipt(standing_summary)
    lines += scope_lines
    return Output(
        lines,
        {
            "ok": True,
            "command": "edit",
            "record_id": record_id,
            "standing_obligations": standing_summary,  # KYO-554
            "record": document,
            "scope_suggestion": scope_data,
            "title_length_warning": title_warning,
            "criteria_length_warnings": criteria_warning_data,
        },
    )


def cmd_restate(ctx: Context, args: Namespace) -> Output:
    """``kyo restate <criterion-id> --text-file FILE`` (KYO-58.c2).

    Voids the old criterion and adds the new wording in one transaction, with a
    mechanical ``replaces``/``replaced-by`` relation between them. Before this,
    restating meant two commands and a prose reason, and the connection between
    "c1, void-withdrawn" and "c3, the wording that replaced it" existed only if the
    controller happened to write it — measured on Kyosei's own board at three records
    (KYO-54, KYO-30, KYO-47), and readable from neither `kyo show`, nor the
    brief, nor the export.

    **The relation reuses `kyo split`'s shape rather than inventing a second one.**
    Split already produces a successor criterion, records it in the voided one's
    ``reason`` through a single canonical string, and has
    ``doctor_dispatch.check_split_backlink_symmetry`` proving the pair stays consistent.
    Split solved this for moving a criterion to another *record*; restating one
    inside the same record never got the equivalent. So:
    :func:`contracts.restate_reason` writes it, :data:`contracts.RESTATE_REASON_RE`
    parses it, and ``doctor_dispatch.check_restate_backlink_symmetry`` is the matching
    validator. No new column and no schema bump — the "superseded" reading is
    derived from ``(state, reason)`` by :func:`contracts.criterion_standing`, on
    KYO-44's precedent.

    **The replacement starts unverified**, whatever the original's state was. That
    is the point: a criterion's verdicts belong to the text they were recorded
    against, so carrying ``met`` across to new wording would be the exact
    evidence-re-attachment this whole record exists to prevent. The original keeps
    its history, in the state it earned, readable forever.

    **A criterion whose history is non-empty may still be restated** — the
    conservative answer, and the one `kyo split` already takes across records: the
    old text and its verdicts remain intact and reachable, and nothing is claimed
    about the new wording that was not observed of it.
    """
    store = ctx.store()
    text = read_prose(args.text_file, what="--text-file")
    if not text.strip():
        raise UsageError("--text-file must not be empty: a criterion with no text is not a claim")
    replacement_text = single_line(text, what="--text-file")

    with store.transaction():
        original = store.criterion(args.criterion_id)
        record_id = original["record_id"]
        if original["state"] in VOID_CRITERION_STATES:
            raise ContractViolation(
                CONTRACT_RESTATE_REQUIRES_A_LIVE_CRITERION,
                [
                    f"{args.criterion_id} is already {original['state']}: "
                    f"{original['reason'] or '(no reason recorded)'}",
                    "a restatement would overwrite that reason with its own back-link, "
                    "and the judgment that voided this criterion is the only thing that "
                    "column holds",
                    f"a settled criterion needs no replacement — if the record needs a new "
                    f"claim, add it with `kyo edit {record_id} --add-criteria-file FILE`",
                ],
            )
        before = {record_id: export.record_document(store, record_id)}
        stamp = now()

        (replacement_id, replacement), = _add_criteria(store, record_id, [replacement_text])
        reason = restate_reason(replacement_id)
        check_criterion_state(args.criterion_id, "void-withdrawn", reason)
        store.conn.execute(
            "UPDATE criteria SET state = 'void-withdrawn', reason = ?, updated_at = ?"
            " WHERE id = ?",
            (reason, stamp, args.criterion_id),
        )

        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"restate {args.criterion_id} -> {replacement_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)

    # KYO-333.c2/c3: restating is one of this command's stated sites -- the
    # replacement wording gets the same check a freshly-filed criterion would.
    warning_lines, warning_data = _criteria_warning_output([(replacement_id, replacement)])

    lines = [
        f"restated {args.criterion_id} -> {replacement_id}",
        echo_value(
            f"criterion {args.criterion_id}",
            f"{original['state']} -> void-withdrawn ({CRITERION_STANDING_SUPERSEDED})",
        ),
        f"  was: {original['text']}",
        f"  now: {replacement_id}  {DEFAULT_CRITERION_STATE}  {replacement}",
        *warning_lines,
        f"  {args.criterion_id} keeps its verdict history, attached to the text it was "
        f"recorded against; {replacement_id} starts unverified because nothing has been "
        "observed of the new wording yet",
    ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "restate",
            "record_id": record_id,
            "criterion_id": args.criterion_id,
            "replacement_id": replacement_id,
            "record": after[record_id],
            "criteria_length_warnings": warning_data,
        },
    )


def _close_settled_staleness_flags(
    store: Store, record_id: str, status: str, actor: str
) -> list[dict]:
    """KYO-346.c1: the flags this record's own move settles, closed inside the
    caller's transaction — the completion move, `kyo wont-do`, `kyo void`, whatever
    status this record just entered. Only :data:`STALENESS_FLAG_TYPES`
    (touch-changed, touch-missing, description-predates-claim); KYO-346.c2 leaves
    `near-duplicate-criteria` and `gate-cleared-stale-criteria` open on purpose.

    Returns what it closed — the caller says how many in its own output (KYO-346.c1)
    rather than this function printing anything, on the same seam every other
    ``cmd_*`` helper here keeps (rendering is the command's job).
    """
    open_staleness = [
        row
        for row in store.flags(record_id)
        if row["resolved_at"] is None and row["flag_type"] in STALENESS_FLAG_TYPES
    ]
    if not open_staleness:
        return []
    why = settled_flag_why(record_id, status, actor)
    stamp = now()
    for row in open_staleness:
        store.conn.execute(
            "UPDATE flags SET resolved_at = ?, resolved_by = ?, resolved_why = ? WHERE id = ?",
            (stamp, actor, why, row["id"]),
        )
    return [{"flag_id": row["id"], "flag_type": row["flag_type"]} for row in open_staleness]


def _closed_flags_line(closed_flags: list[dict]) -> str:
    """KYO-346.c1: 'the command's output says how many it closed' — the shared
    rendering `kyo move`, `kyo wont-do` and `kyo void` each print one of."""
    ids = ", ".join(f"{f['flag_id']} ({f['flag_type']})" for f in closed_flags)
    return f"closed {len(closed_flags)} sweep flag(s) this move settles (KYO-346): {ids}"


def _unapplied_met_verdicts(store: Store, record_id: str) -> dict[str, list[str]]:
    """KYO-478: criterion id -> the dispatches whose current, ingested report holds a
    ``met`` verdict for it that no ``kyo verify --from-report`` ever applied.

    "Applied" is not defined again here: it is :func:`cmd_evidence.
    _verifications_from_report`, the derivation ``kyo verify --from-report`` (and so
    ``kyo accept``) skips an already-applied entry by. A report counts as applied for
    a criterion when any verification on that criterion came from it — not only the
    latest, because a verdict recorded by hand after the report's was applied is a
    newer decision, and naming the report as the remedy would send the controller to
    overwrite it. Only ``met`` is named: an unmet verdict applied would not settle
    the criterion, so it is no remedy for this refusal (KYO-478.c4).

    Read only on the refusal's path (a completion move over a requires_criteria
    record), from dispatches on the record or formally covering it.
    """
    unsettled = {
        row["id"]
        for row in store.conn.execute(
            "SELECT id, state FROM criteria WHERE record_id = ?", (record_id,)
        )
        if row["state"] not in SETTLED_CRITERION_STATES
    }
    return _unapplied_report_verdicts(store, record_id, unsettled, met_only=True)


def _unapplied_report_verdicts(
    store: Store, record_id: str, criteria: set[str], *, met_only: bool
) -> dict[str, list[str]]:
    """Criterion id (of ``criteria``) -> the dispatches whose current, ingested report
    holds a verdict for it that was never applied -- ``met`` only when ``met_only``.

    The walk :func:`_unapplied_met_verdicts` was written as (KYO-478), lifted out so
    KYO-546's verified refusal finds the same reports by the same derivation: a move to
    the verified role wants *a* verdict, of any value, so it asks with ``met_only``
    false.
    """
    import json

    from . import cmd_evidence  # local: the evidence layer imports `commands` too

    found: dict[str, list[str]] = {}
    if not criteria:
        return found
    for dispatch in store.conn.execute(
        "SELECT id, record_id, covers_json, report_id FROM dispatches"
        " WHERE report_id IS NOT NULL ORDER BY created_at, id"
    ):
        covers = json.loads(dispatch["covers_json"]) if dispatch["covers_json"] else []
        if dispatch["record_id"] != record_id and record_id not in covers:
            continue
        report_id, verdicts = cmd_evidence._report_verdicts(store, dispatch["id"])
        wanted = [
            entry["criterion_id"]
            for entry in verdicts
            if (not met_only or entry.get("verdict") == "met")
            and entry.get("criterion_id") in criteria
        ]
        if not wanted:
            continue
        from_report = cmd_evidence._verifications_from_report(
            store, dispatch["id"], report_id
        )
        for criterion_id in wanted:
            recorded = {v["id"] for v in store.verifications_for_criterion(criterion_id)}
            if recorded & from_report:
                continue
            if dispatch["id"] not in found.setdefault(criterion_id, []):
                found[criterion_id].append(dispatch["id"])
    return found


def _waiting_reports(store: Store, record_id: str) -> list[str]:
    """KYO-546.c3: the dispatches whose ingested report holds an unapplied verdict for
    a criterion of ``record_id`` that has none recorded -- what ``kyo accept`` would
    record one from. Found by :func:`_unapplied_report_verdicts`, the KYO-478 walk."""
    unverdicted = {
        view.id
        for view in _criterion_views(store, record_id)
        if view.state == DEFAULT_CRITERION_STATE and not view.has_verdict
    }
    found = _unapplied_report_verdicts(store, record_id, unverdicted, met_only=False)
    ordered: list[str] = []
    for dispatch_ids in found.values():
        for dispatch_id in dispatch_ids:
            if dispatch_id not in ordered:
                ordered.append(dispatch_id)
    return ordered


def _open_gate_views(store: Store, record_id: str) -> list[GateView]:
    """KYO-490: the uncleared gates ``check_transition`` refuses a settling move over.

    Sibling of :func:`commands._open_docs_obligation_views`: store rows turned into the
    value type ``contracts`` reads, so that module goes on knowing no schema.
    """
    return [
        GateView(id=row["id"], gate_type=row["gate_type"], detail=row["detail"])
        for row in store.gates(record_id)
        if not row["cleared_at"]
    ]


def _forced_gates_line(status: str, gates: list[GateView]) -> list[str]:
    """What ``--force`` settled over (KYO-490): said, not silently absorbed."""
    if not gates:
        return []
    return [
        f"--force entered {status} with {len(gates)} gate(s) left open:",
        *(
            f"  {g.id}  {g.gate_type}  {gate_payload_first_line(g.detail)}"
            for g in gates
        ),
    ]


def clear_gates_on_carried_settle(
    store: Store, record_id: str, *, settled_by: str, reason: str
) -> list[dict]:
    """KYO-497: settling a carried record clears its open gates first.

    A wait on the maintainer is a carried record with an open gate, so the decision that
    settles the item is the answer the gate waited for. Clearing it here, with the
    settle's own reason as ``cleared_why``, is how such a record meets KYO-490's
    ``settled-requires-no-open-gates`` rather than an exception to it: the caller runs
    this and THEN the transition contracts, which find no open gate. Same write as
    `kyo ungate` (``cmd_links.cmd_ungate``) minus its stale-criteria flag, which a
    carried kind — no criteria (``requires_criteria: false``) — has nothing to raise.
    Returns the gates it cleared, for the command to say so.
    """
    cleared_at = now()
    why = f"cleared by `{settled_by}` (KYO-497, a carried record settling): {reason}"
    cleared = []
    for row in store.gates(record_id):
        if row["cleared_at"]:
            continue
        store.conn.execute(
            "UPDATE gates SET cleared_at = ?, cleared_why = ? WHERE id = ?",
            (cleared_at, why, row["id"]),
        )
        cleared.append({"gate_id": row["id"], "gate_type": row["gate_type"]})
    return cleared


def _cleared_gates_lines(cleared: list[dict]) -> list[str]:
    """What a carried settle cleared (KYO-497): said, never silently absorbed."""
    if not cleared:
        return []
    return [
        f"cleared {len(cleared)} open gate(s) on settling this carried record:",
        *(f"  {g['gate_id']}  {g['gate_type']}" for g in cleared),
    ]


def _transition(
    ctx: Context,
    store: Store,
    record_id: str,
    status: str,
    reason: str | None,
    *,
    knowledge_ok: bool = False,
    flow_remedy: str | None = None,
    force: bool = False,
    settled_by: str | None = None,
    standing_settled: list[str] | None = None,
):
    """One status write, contracts enforced inside the caller's transaction.

    ``standing_settled`` (KYO-554.c11, `kyo void` only): when given, the record's open
    standing docs obligations are settled ``not-affected`` with the reason and the actor,
    after the event's ``before`` is taken so the ledger records the settlement, and their
    ids are appended to it.

    ``knowledge_ok`` is set by `kyo void` alone (KYO-44.c1). Void is a disposal, not
    a flow step: the archetype check below is what keeps ``open → working → built →
    verified → done`` work-only, and `kyo void` never asks for any of those.

    ``force`` overrides KYO-490's open-gate refusal for a settling move and nothing
    else; the gates it settled over are returned so the caller can say so.

    ``settled_by`` names the command for a carried record's cleared gates (KYO-497,
    :func:`clear_gates_on_carried_settle`); it defaults to the `kyo move` spelling.

    Returns ``(old_status, record_document, events, closed_flags, forced_gates,
    cleared_gates)`` — every caller
    (`kyo move`, `kyo wont-do`, `kyo void`) shares this one status-write seam, so
    KYO-346.c1's flag settlement lives here once rather than three times that could
    drift (law 3). ``kyo accept --close`` walks a record to completion through
    `cmd_move` (KYO-331), so it inherits this without a change of its own.
    """
    row = store.record(record_id)
    kind_def = _load_registry(store).require(row["kind"])
    ladder = _load_status_ladder(store)
    if knowledge_ok:
        check_void_is_knowledge_only(record_id, row["kind"], kind_def.archetype)
    else:
        remedy = flow_remedy
        if remedy is not None and ladder.is_terminal(row["status"]):
            # KYO-5's lesson applied here: a remedy that has already been run is
            # not a remedy. Pointing an already-voided record at `kyo void` would
            # send the caller to a command that refuses it as terminal.
            remedy = (
                f"{record_id} is already void — it was retired, its id is never reused, "
                "and there is nothing further to dispose of"
            )
        check_archetype_allows_status_flow(
            record_id, row["kind"], kind_def.archetype, ladder, also=remedy
        )
    before = {record_id: export.record_document(store, record_id)}
    if standing_settled is not None:
        standing_settled += _settle_standing_on_void(
            store, record_id, why=reason, author=ctx.actor, stamp=now()
        )
    # KYO-497: a carried record settles with a reason, and settling it clears its open
    # gates with that reason — checked, then cleared, BEFORE the gates are read below,
    # so KYO-490's contract still runs and finds them answered. Inside the caller's
    # transaction: a later refusal rolls the clearing back with everything else.
    cleared_gates: list[dict] = []
    if (
        kind_def.carried
        and ladder.role(status) in SETTLED_ROLES
        and not ladder.is_terminal(row["status"])
    ):
        check_carried_settle_why(
            record_id=record_id, kind=row["kind"], to_status=status, reason=reason,
            ladder=ladder,
        )
        if (reason or "").strip():
            cleared_gates = clear_gates_on_carried_settle(
                store,
                record_id,
                settled_by=settled_by or f"kyo move {record_id} {status}",
                reason=reason or "",
            )
    # KYO-490: only a settling move can be refused over an open gate, so only that move
    # reads them; `--force` passes none to the contract and keeps them to report.
    open_gates = (
        _open_gate_views(store, record_id)
        if ladder.role(status) in SETTLED_ROLES and not ladder.is_terminal(row["status"])
        else []
    )
    check_transition(
        record_id=record_id,
        kind=row["kind"],
        from_status=row["status"],
        to_status=status,
        criteria=_criterion_views(store, record_id),
        reason=reason,
        requires_criteria=kind_def.requires_criteria,
        ladder=ladder,
        open_docs_obligations=_open_docs_obligation_views(store, record_id),
        # KYO-478: only a completion move over a requires_criteria record can be
        # refused over unsettled criteria, so only that move pays for the read.
        unapplied_met=(
            _unapplied_met_verdicts(store, record_id)
            if ladder.role(status) == ROLE_COMPLETE and kind_def.requires_criteria
            else None
        ),
        open_gates=None if force else open_gates,
        # KYO-546: only a move into the verified role can be refused over a criterion
        # with no verdict, so only that move reads the reports that would record one.
        waiting_reports=(
            _waiting_reports(store, record_id)
            if ladder.role(status) == ROLE_VERIFIED and kind_def.requires_criteria
            else None
        ),
    )
    store.conn.execute(
        "UPDATE records SET status = ?, wont_do_reason = ?, updated_at = ? WHERE id = ?",
        (
            status,
            # KYO-101: the reason belongs to the *retirement* status, whatever this
            # project calls it — a rename must not quietly stop storing it.
            reason if ladder.role(status) == ROLE_RETIRED else row["wont_do_reason"],
            now(),
            record_id,
        ),
    )
    # KYO-346.c1: a record that just reached a completion or retired status
    # (SETTLED_ROLES) has no open work left for a staleness flag to ask about — in
    # the same transaction as the status write above, never a separate act a
    # controller has to remember to run.
    closed_flags: list[dict] = []
    if ladder.role(status) in SETTLED_ROLES:
        closed_flags = _close_settled_staleness_flags(store, record_id, status, ctx.actor)
    # KYO-497: the reason a carried record was completed with is the record of how the
    # item settled, and `records` stores a reason only for the retired role
    # (`wont_do_reason`). A record note holds it -- the object `kyo move --force` on a
    # plan already reuses, with `kyo show`, the export document and redaction as its
    # readers -- so the required `--why` is never echoed and then dropped.
    if (
        kind_def.carried
        and ladder.role(status) == ROLE_COMPLETE
        and not ladder.is_terminal(row["status"])
        and (reason or "").strip()
    ):
        note_id, note_seq = store.new_child_id(record_id, "note")
        store.conn.execute(
            "INSERT INTO notes (id, record_id, seq, text, created_at, superseded_by,"
            " superseded_at) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
            (note_id, record_id, note_seq, f"settled ({status}): {reason}", now()),
        )
    after, events = commands._commit_and_export(
        store,
        command=f"move {record_id} {status}",
        actor=ctx.actor,
        record_ids=[record_id],
        before=before,
    )
    return (
        row["status"],
        after[record_id],
        events,
        closed_flags,
        (open_gates if force else []),
        cleared_gates,
    )


def cmd_move(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    # An unknown enum value is a usage error (64); a *known* status the record may not
    # enter is a contract violation (2). Keeping those apart is what makes exit code 2
    # mean exactly one thing to a caller.
    ladder = _load_status_ladder(store)
    if args.status not in ladder:
        raise UsageError(
            f"{args.status!r} is not a status; {ladder.registry.path} declares: "
            f"{ladder.listing()}"
        )
    reason = _reason(args)
    # KYO-173, the smaller defect underneath the finished-but-not-moved one, and the
    # thing that let it hide: `kyo move X verified` on a record already at `verified`
    # used to print "moved X" and `verified -> verified`. Transition contracts fire on
    # a real transition, so a self-move fired nothing, changed nothing, and reported
    # itself in exactly the words a working command uses — which is how a controller
    # runs the remedy a surface named, believes the class is cleared, and moves on.
    #
    # Answered by saying what happened rather than by refusing: the call is harmless
    # and idempotence is worth keeping, so this exits 0. What it must not do is claim a
    # transition. A record sitting in a terminal status deliberately falls through to
    # `_transition`, because `terminal-is-terminal` is a real refusal and staying at
    # `wont-do` must not become a quiet success.
    row = store.record(args.id)
    current = row["status"]
    if current == args.status and not _load_status_ladder(store).is_terminal(current):
        return Output(
            [
                f"nothing to do: {args.id} is already {args.status}",
                echo_value("status", f"{current} (unchanged — no transition recorded)"),
            ],
            {
                "ok": True,
                "command": "move",
                "record_id": args.id,
                "changed": False,
                "record": export.record_document(store, args.id),
            },
        )
    plan_members: list[dict] | None = None
    unsettled: list[dict] = []
    with store.transaction():
        # KYO-308.c1: `plan`'s completion is a status distribution over its members,
        # not criteria (kinds.yml: `requires_criteria: false`), so none of
        # `_transition`'s completion contracts ever have anything to check for this
        # archetype — the one fact that matters at the moment a plan closes was the
        # one thing this command did not say. Gated on `kind == "plan"` exactly as
        # `_require_plan` reads plan-ness (KYO-237): a `--plan` id naming anything
        # else is refused before it is ever stored, so no other kind can carry
        # members to check here. A non-plan record, or a plan moving to a
        # non-completion status, never enters this branch at all — its move is
        # byte-for-byte what it was before this criterion. Read and checked inside
        # the same transaction as the write below, on the same reasoning
        # `_transition`'s own re-read of ``row`` has: a refusal has to be checked
        # against the state it is about to write over, not a state read before the
        # transaction opened.
        # `not ladder.is_terminal(current)` defers to `_transition`'s own
        # terminal-status-is-terminal refusal below when the plan is already
        # retired: that refusal is unconditional (a terminal status cannot be left
        # at all, force or not), and is the more fundamental fact when both apply —
        # this check must not report "unsettled members" over a move nothing could
        # ever complete.
        if (
            row["kind"] == "plan"
            and ladder.role(args.status) == ROLE_COMPLETE
            and not ladder.is_terminal(current)
        ):
            # Same membership read as `kyo list --plan` / `_plan_members` (KYO-27):
            # membership lives on the MEMBER (`plan_id`), in record-sequence order.
            plan_members = [
                dict(m)
                for m in store.conn.execute(
                    "SELECT id, kind, status, title FROM records WHERE plan_id = ?"
                    " ORDER BY seq",
                    (args.id,),
                )
            ]
            unsettled = check_plan_close_over_open_members(
                args.id, plan_members, ladder, force=bool(getattr(args, "force", False))
            )
        old_status, doc, events, closed_flags, forced_gates, cleared_gates = _transition(
            ctx, store, args.id, args.status, reason,
            force=bool(getattr(args, "force", False)),
        )
        forced_note_id: str | None = None
        if unsettled:
            # A stored field needs a consumer in the same commit; a record note
            # already has three (`kyo show`, the per-record export document, and
            # redaction) so this reuses that object rather than adding a new column
            # nothing else would read. The event this note travels in — a second one
            # for this invocation, distinct from `_transition`'s own status-change
            # event above — is what a later `kyo brief` surfaces, the same way every
            # other write here does.
            note_text = (
                f"`kyo move {args.id} {args.status} --force` closed this plan over "
                f"{len(unsettled)} open member(s): "
                + ", ".join(f"{m['id']} ({m['status']})" for m in unsettled)
            )
            forced_note_id, note_seq = store.new_child_id(args.id, "note")
            store.conn.execute(
                "INSERT INTO notes (id, record_id, seq, text, created_at, superseded_by,"
                " superseded_at) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (forced_note_id, args.id, note_seq, note_text, now()),
            )
            after_forced, note_events = commands._commit_and_export(
                store,
                command=f"move {args.id} {args.status} --force",
                actor=ctx.actor,
                record_ids=[args.id],
                before={args.id: doc},
            )
            doc = after_forced[args.id]
            events += note_events
    commands._finish_mutation(store, [args.id], events)
    lines = [f"moved {args.id}", echo_value("status", f"{old_status} -> {args.status}")]
    if reason is not None:
        lines += echo_block("reason", reason)
    lines += _forced_gates_line(args.status, forced_gates)
    lines += _cleared_gates_lines(cleared_gates)
    if closed_flags:
        lines.append(_closed_flags_line(closed_flags))
    if plan_members is not None:
        counts: dict[str, int] = {}
        for member in plan_members:
            counts[member["status"]] = counts.get(member["status"], 0) + 1
        spread = ", ".join(f"{status}:{n}" for status, n in sorted(counts.items()))
        lines.append(
            f"plan members ({len(plan_members)})"
            + (f" — {spread}" if plan_members else "")
            + ":"
        )
        if unsettled:
            lines.append(
                f"  --force closed this plan over {len(unsettled)} open member(s):"
            )
            lines += [f"    {m['id']}  {m['status']}  {m['title']}" for m in unsettled]
            if forced_note_id:
                lines.append(f"  recorded as {forced_note_id}")
    return Output(
        lines,
        {
            "ok": True,
            "command": "move",
            "record_id": args.id,
            "changed": True,
            "record": doc,
            # KYO-490: present only when --force settled over a gate, so every
            # other move's --json is byte-for-byte what it was.
            **({"forced_over_gates": [g.id for g in forced_gates]} if forced_gates else {}),
            # KYO-497: present only when a carried record's settle cleared a gate.
            **({"cleared_gates": cleared_gates} if cleared_gates else {}),
            "flags_closed": len(closed_flags),
            "closed_flags": closed_flags,
        },
    )


def cmd_wont_do(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    # Deliberately not pre-checked here: the missing reason must surface as the named
    # contract retired-requires-reason (exit 2), the same way it does via `kyo move`.
    reason = _reason(args)
    # KYO-101: `kyo wont-do` takes no status argument, so the retirement status has to
    # be one the registry can name — which is why exactly one status may carry the
    # `retired` role (contracts.SINGLETON_ROLES).
    retired = _load_status_ladder(store).retired
    with store.transaction():
        old_status, doc, events, closed_flags, forced_gates, cleared_gates = _transition(
            ctx,
            store,
            args.id,
            retired,
            reason,
            # KYO-44.c1: a knowledge record refused here is told the command that
            # does dispose of it, rather than being handed a contract name and no
            # way forward.
            flow_remedy=knowledge_disposal_remedy(args.id),
            force=bool(getattr(args, "force", False)),
            settled_by=f"kyo wont-do {args.id}",
        )
    commands._finish_mutation(store, [args.id], events)
    lines = [
        f"retired {args.id} — its id is never reused",
        echo_value("status", f"{old_status} -> {retired}"),
        *echo_block("why", reason or ""),
        *_forced_gates_line(retired, forced_gates),
        *_cleared_gates_lines(cleared_gates),
    ]
    if closed_flags:
        lines.append(_closed_flags_line(closed_flags))
    return Output(
        lines,
        {
            "ok": True,
            "command": "wont-do",
            "record_id": args.id,
            "record": doc,
            # KYO-490: present only when --force settled over a gate, so every
            # other move's --json is byte-for-byte what it was.
            **({"forced_over_gates": [g.id for g in forced_gates]} if forced_gates else {}),
            # KYO-497: present only when a carried record's settle cleared a gate.
            **({"cleared_gates": cleared_gates} if cleared_gates else {}),
            "flags_closed": len(closed_flags),
            "closed_flags": closed_flags,
        },
    )


def cmd_void(ctx: Context, args: Namespace) -> Output:
    """``kyo void <id> --why`` — the knowledge archetype's disposal path (KYO-44.c1).

    A knowledge record filed in error previously had none: `kyo wont-do` is a status
    transition and the archetype has no status flow, while the charter forbids
    deleting or archiving a record. So it could only sit at ``open`` forever.

    Stored as the same terminal the work archetype uses (``wont-do``) rather than as
    a new status value: retirement is one fact about a record and one column already
    holds it, so a second value would be two things that can disagree (law 3), and
    every reader of that column — `kyo doctor`'s status check, the export, rebuild —
    would need to learn it. What differs between the archetypes is the *word a reader
    sees*, and that is derived at render time by :func:`contracts.standing`: a voided
    knowledge record reads ``void``, never ``wont-do``, and never ``open`` (KYO-44.c2).
    """
    store = ctx.store()
    reason = _reason(args)
    ladder = _load_status_ladder(store)
    retired = ladder.retired
    with store.transaction():
        # KYO-554.c11: the transition settles the record's open standing obligations,
        # inside this transaction, so a refused void settles nothing.
        settled_standing: list[str] = []
        # A knowledge kind is never carried, so a void clears no gate (KYO-497).
        old_status, doc, events, closed_flags, forced_gates, _cleared = _transition(
            ctx,
            store,
            args.id,
            retired,
            reason,
            knowledge_ok=True,
            force=bool(getattr(args, "force", False)),
            standing_settled=settled_standing,
        )
    commands._finish_mutation(store, [args.id], events)
    kind_def = _load_registry(store).require(doc["kind"])
    lines = [
        f"voided {args.id} — its id is never reused",
        echo_value(
            "standing",
            f"{standing(old_status, kind_def.archetype, ladder)} -> "
            f"{standing(retired, kind_def.archetype, ladder)}",
        ),
        *echo_block("why", reason or ""),
        *_forced_gates_line(retired, forced_gates),
    ]
    if closed_flags:
        lines.append(_closed_flags_line(closed_flags))
    if settled_standing:
        lines.append(
            f"settled {len(settled_standing)} standing docs obligation(s) "
            f"{OBLIGATION_NOT_AFFECTED} (method {STANDING_VOID_METHOD}, the why above): "
            f"{', '.join(settled_standing)}"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "void",
            # KYO-554.c11: the standing obligations this void settled.
            "standing_settled": settled_standing,
            "record_id": args.id,
            "record": doc,
            # KYO-490: present only when --force settled over a gate, so every
            # other move's --json is byte-for-byte what it was.
            **({"forced_over_gates": [g.id for g in forced_gates]} if forced_gates else {}),
            "standing": standing(retired, kind_def.archetype, ladder),
            "flags_closed": len(closed_flags),
            "closed_flags": closed_flags,
        },
    )


#: KYO-538: how long an identical record counts as "just filed".
_IDENTICAL_FILING_WINDOW = timedelta(hours=1)


def _refuse_identical_recent(
    store: Store, title: str, criteria_texts: list[str], *, force: bool
) -> None:
    """KYO-538.c2: refuse to file a record whose title and criteria are identical to one
    filed in this project within the last hour (any status: a duplicate that was
    since retired still counts). Near duplicates still only warn, via
    :func:`_duplicate_warning_output`; ``--force`` files anyway."""
    if force:
        return
    cutoff = (datetime.now(UTC) - _IDENTICAL_FILING_WINDOW).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    wanted = sorted(text.strip() for text in criteria_texts)
    for row in store.conn.execute(
        "SELECT id FROM records WHERE title = ? AND created_at >= ? ORDER BY seq",
        (title, cutoff),
    ):
        if sorted(str(c["text"]).strip() for c in store.criteria(row["id"])) == wanted:
            raise KyoseiError(
                f"refused: {row['id']} was filed within the last hour with this identical "
                "title and criteria, so this would be a duplicate. Use "
                f"{row['id']}, or pass --force to file it anyway"
            )


def _dedupe_corpus(store: Store) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """Existing ``(record_id, title)`` and ``(record_id, criterion_id, text)``
    for every still-active record — the corpus both the creation-time check
    (KYO-8.c4) and `kyo sweep`'s near-duplicate class (KYO-8.c3) compare
    against. Unlike :func:`_swept_active_records`, not filtered by
    ``policy.swept`` or archetype: a duplicate title on a non-swept kind is
    still worth a warning at the moment someone is about to create it.
    """
    active = set(_active_statuses(store))
    titles: list[tuple[str, str]] = []
    criteria: list[tuple[str, str, str]] = []
    for row in store.conn.execute("SELECT id, status, title FROM records ORDER BY seq"):
        if row["status"] not in active:
            continue
        titles.append((row["id"], row["title"]))
        for c in store.criteria(row["id"]):
            if c["state"] in _UNMET_CRITERION_STATES:
                criteria.append((row["id"], c["id"], c["text"]))
    return titles, criteria


def _duplicate_warning_output(
    warnings: list[sweep_module.DuplicateWarning],
) -> tuple[list[str], list[dict]]:
    lines: list[str] = []
    data: list[dict] = []
    for w in warnings:
        lines.append(
            f"WARNING: possible duplicate {w.kind} ({w.ratio:.2f} similar) to "
            f"{w.existing_record_id}: {w.existing_text!r}"
        )
        data.append(
            {
                "kind": w.kind,
                "new_text": w.new_text,
                "existing_record_id": w.existing_record_id,
                "existing_text": w.existing_text,
                "ratio": w.ratio,
            }
        )
    return lines, data


def _title_warning_output(title: str) -> tuple[list[str], str | None]:
    """KYO-333.c1/c3: the printed line and the ``--json`` value for a title-length
    warning, on the bare-measurement warning's own terms (`cmd_dispatch`'s
    `_measurement_warnings`) -- printed after the echo, carried as data, never a
    refusal and never an exit-code change."""
    warning = title_length_warning(title)
    if warning is None:
        return [], None
    return [f"  warning: {warning}"], warning


def _criteria_warning_output(criteria: list[tuple[str, str]]) -> tuple[list[str], list[dict]]:
    """KYO-333.c2/c3: same shape as :func:`_title_warning_output`, one entry per
    ``(criterion_id, text)`` pair that warns. ``criteria`` is whatever the caller
    just stored or corrected -- newly added criteria, or a single corrected one --
    so a caller with one criterion to check passes a one-item list."""
    lines: list[str] = []
    data: list[dict] = []
    for cid, text in criteria:
        warning = criterion_length_warning(text)
        if warning is None:
            continue
        lines.append(f"  warning: {cid}: {warning}")
        data.append({"criterion_id": cid, "warning": warning})
    return lines, data
