"""Status flow, criterion states, link types, gate types, and transition contracts.

Every contract in this module is a **named constant**. Milestone 2 replaces the
hardcoded sets with per-kind policy from ``kyosei/kinds.yml``; the contract *names*
survive that change, so error messages and tests do not churn.

Law 7: contracts are enforced at the transition, and the error names the unmet item.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import registry as registry_module
from .errors import ContractViolation, KyoseiError

# ------------------------------------------------------------------ stored paths

# KYO-52. Every path-typed column in the store is written **relative to the
# project root**, never resolved to an absolute path.
#
# ``kyosei/export/`` is git-canonical on purpose, which makes everything in it
# permanent and publishable-by-accident. An absolute path is machine-specific
# (it means nothing on another checkout), and personally identifying (it carries
# a username and a directory layout). Storing ``../kyosei-wt-31`` instead of
# ``/Users/<someone>/projects/kyosei-wt-31`` keeps the fact — *a sibling
# directory one level up, called kyosei-wt-31* — and drops the identity.
#
# A path outside the project root still has a root-relative form: ``os.path.relpath``
# expresses it with a ``..`` chain, and a ``..`` chain is exactly the part that
# carries no ancestor directory names. A sibling worktree is the NORMAL case for
# ``kyo dispatch --worktree``, so it is stored, not refused (KYO-52.c2).
#
# :func:`resolve_stored_path` is the other half: display and any check that needs
# a real directory resolves against the root, and — because ``Path("/root") /
# "/absolute"`` yields the absolute path — the same call reads a legacy absolute
# value written before schema_version 9 without special-casing it.
def store_path(root: Path, path: str | Path) -> str:
    """The value to store for ``path``: root-relative, POSIX separators.

    ``root`` itself stores as ``"."``. Raises :class:`KyoseiError` for a path with
    no relative form at all (a different drive on Windows) rather than falling back
    to the absolute form, which would silently reintroduce exactly what this
    function exists to keep out of the store.
    """
    resolved = Path(path).expanduser().resolve()
    base = Path(root).expanduser().resolve()
    try:
        relative = os.path.relpath(resolved, base)
    except ValueError as exc:  # pragma: no cover on POSIX - Windows cross-drive
        raise KyoseiError(
            f"{path} has no path relative to the project root {base} ({exc}); Kyosei stores "
            "paths relative to the project root (KYO-52), so this one cannot be stored — "
            "put the worktree under the same drive as the project"
        ) from exc
    return Path(relative).as_posix()


def resolve_stored_path(root: Path, stored: str) -> Path:
    """The directory a stored path names, resolved against ``root``.

    Accepts both forms deliberately: the root-relative value written from
    schema_version 9 on, and the absolute value written before it (schema_version
    8 and earlier). ``Path(root) / stored`` returns ``stored`` unchanged when it is
    absolute, so no version flag is needed to tell the two apart — the value says
    which it is.
    """
    return (Path(root).expanduser() / stored).resolve()


# ------------------------------------------------------ KYO-209: directory touches
#
# A touches table is a *working scope*, and `_validate_touches` checks every path
# against the repository at the moment it is declared, so "a stale idea fails loudly
# exactly when someone tries to act on it" (charter, Layer 1). That check has one
# blind spot, reported by a consumer: work whose whole point is to CREATE a tree has
# no path to name. That consumer committed
# a stub `README.md` under `apps/desktop/` purely so four records could declare a
# scope, and every dispatch off them then reported "the Touches table did not cover
# what the task required."
#
# The fix cannot be "accept a missing path": that is the typo case, and refusing it is
# the check's whole value. So intent is declared in the path itself — a trailing `/`
# says "this is a directory, existing or not, and everything under it is mine". A
# typo in a filename does not grow a trailing slash by accident, so the two are
# distinguishable by construction rather than by guesswork.
#
# The suffix is kept in the STORED value, and that is load-bearing: `touches.path` is
# what the brief renders, what the disjointness check compares, what drift measures
# against the diff and what sweep asks about. A form that survived validation and then
# normalised away would leave every one of those readers unable to tell a directory
# from a file that has since been deleted.

#: The one character that makes a touch a directory touch (KYO-209).
TOUCH_DIRECTORY_SUFFIX = "/"

# KYO-225. KYO-209's criterion c1 named two forms — "a directory with a trailing slash,
# OR AN EXPLICIT MARKER" — and its worker built the first only. The gap is not
# cosmetic: a record that will create `src/kyosei/new_module.py` beside a dozen
# existing files in `src/kyosei/` can claim the whole directory, which overstates its
# scope and collides with every other dispatch under it, or leave the file undeclared
# and eat the drift. A consumer on the build that includes KYO-209 hit the second on
# every dispatch: every worker reported touch drift, because a new file cannot be
# declared as a touch until it exists.
#
# The marker is a LEADING `+`, which is what a diff puts in front of a line a patch
# adds and what every reader of a patch already reads as "this is new". It satisfies
# the same test the trailing slash does — a mistyped filename does not grow a leading
# `+` by accident — and it is at the START of the line, where the eye scanning a
# touches file is already looking.
#
# Like the directory suffix, the marker is kept in the STORED value, and for the same
# reason: `touches.path` is what the brief renders, what disjointness compares, what
# drift measures and what sweep asks about, and a form that validated and then
# normalised away would leave every one of those readers unable to tell "this work
# creates it" from "this file has been deleted". Unlike the directory suffix, it is
# NOT part of the path — `+src/x.py` claims exactly `src/x.py`, one file — so every
# reader that compares paths compares :func:`touch_target`, and the marker is a
# statement about the touch rather than a different scope.
TOUCH_NEW_FILE_PREFIX = "+"


def is_directory_touch(path: str) -> bool:
    """Does this stored touch path declare a directory rather than a single file?"""
    return path.endswith(TOUCH_DIRECTORY_SUFFIX)


def is_new_file_touch(path: str) -> bool:
    """Does this stored touch path declare one file the work will create (KYO-225)?"""
    return path.startswith(TOUCH_NEW_FILE_PREFIX)


def touch_target(path: str) -> str:
    """The path a stored touch names, with any ``will create`` marker removed.

    The one place the marker is taken off (law 3). A directory touch's trailing slash
    is deliberately NOT removed here — it is part of what that form claims, where the
    ``+`` is not: ``+src/x.py`` claims ``src/x.py`` and nothing else, and every reader
    that compares a touch against a real path compares this.
    """
    return path[len(TOUCH_NEW_FILE_PREFIX) :] if is_new_file_touch(path) else path


def touch_covers(touch_path: str, path: str) -> bool:
    """Is ``path`` inside what the single touch ``touch_path`` claims?

    A file touch covers exactly itself — the equality every reader used before this
    existed. A directory touch covers itself and everything beneath it, which is what
    lets a branch create ``apps/desktop/main.js`` under a declared ``apps/desktop/``
    and have drift call it claimed instead of undeclared.

    ``path`` may itself be a directory touch: two claims are compared with this same
    function in both directions (:func:`shared_touch_paths`), so ``apps/`` and
    ``apps/desktop/`` overlap rather than reading as two unrelated strings.

    KYO-225: either side may also carry the ``will create`` marker, which is stripped
    before anything is compared. That is what makes the new-file form work everywhere
    at once rather than at each reader separately — drift calls the created file
    covered, and two dispatches that both declare ``+src/x.py`` (or one declaring it
    and one declaring ``src/x.py``) collide, because after this line they are the same
    string. A marker that changed what a touch COVERED would be a second directory
    form; it changes only what the touch says about itself.
    """
    touch_path = touch_target(touch_path)
    path = touch_target(path)
    if not is_directory_touch(touch_path):
        return path == touch_path
    return path == touch_path or path.startswith(touch_path)


def claim_covers(claim_paths: Iterable[str], path: str) -> bool:
    """Is ``path`` inside anything a whole claim declares?"""
    return any(touch_covers(declared, path) for declared in claim_paths)


def shared_touch_paths(mine: Iterable[str], theirs: Iterable[str]) -> set[str]:
    """The entries of two claims that overlap — from **both** sides.

    Set intersection was enough while every touch was a file, and it is exactly wrong
    now: ``{"apps/desktop/"}`` and ``{"apps/desktop/main.js"}`` intersect in nothing
    while naming the same work. Both sides' entries are returned because a refusal has
    to be readable by whoever opens either record — a controller told only
    ``apps/desktop/`` cannot find that string anywhere in the other record's touches,
    which is the same "the tool is wrong" reading :func:`collision_detail` exists to
    prevent (KYO-65.c2).
    """
    theirs = list(theirs)
    shared: set[str] = set()
    for a in mine:
        for b in theirs:
            if touch_covers(a, b) or touch_covers(b, a):
                shared.add(a)
                shared.add(b)
    return shared


#: What the brief's Touches table puts in the symbol column for a directory touch, and
#: the sentence under the table that says what the form means. A directory takes no
#: ``#symbol`` (``_validate_touches`` refuses one), so the column is free to say what
#: the row IS rather than repeating "(whole file)" about something that is not a file.
TOUCH_DIRECTORY_SYMBOL = "(the whole tree, including files this work creates)"
TOUCH_DIRECTORY_BRIEF_NOTE = (
    "A path ending in `/` is a **directory touch**: that tree is your scope whether or "
    "not it exists in this checkout yet, and files your branch creates under it are "
    "inside your claim, not drift. It is not a missing file."
)

#: KYO-225's pair of the two above, for the single-file form. A new-file touch takes no
#: ``#symbol`` either — there is no file to find it in — so the column says what the row
#: IS rather than "(whole file)" about a file that is not there yet.
TOUCH_NEW_FILE_SYMBOL = "(a file this work creates)"
TOUCH_NEW_FILE_BRIEF_NOTE = (
    "A path written `+path/to/file` is a **new-file touch**: this work CREATES that one "
    "file, it is inside your claim the moment your branch adds it, and it is not drift "
    "and not a missing file. It claims that path and nothing else — use it instead of "
    "the whole enclosing directory when the work adds one file beside existing ones."
)

#: The stored ``touches.symbol`` value that means "this work only ADDS to the file"
#: (KYO-300.c3). The additive claim rides the symbol column on purpose: no new stored
#: field, no export format change, no ``schema_version`` bump — a touch is still
#: ``(path, symbol)`` and every existing row keeps meaning exactly what it meant. ``+``
#: is spelled the same as the new-file marker for the reason the two are cousins: one
#: says "I create this file", the other "I add to this file", and neither claims a line
#: that is already there.
ADDITIVE_SYMBOL = "+"

#: What the brief's Touches table and `kyo show` put in the symbol column for it —
#: sibling of ``TOUCH_DIRECTORY_SYMBOL`` / ``TOUCH_NEW_FILE_SYMBOL`` above.
TOUCH_ADDITIVE_SYMBOL = "(additions only — nothing that is already in the file)"

TOUCH_ADDITIVE_BRIEF_NOTE = (
    "A touch written `path#+` is an **additive claim**: this work ADDS to that existing "
    "file and edits nothing already in it. Use it when the thing you are adding has no "
    "name yet — a new type in `protocol.ts`, a new rule in `shell.css` — so the only "
    "other honest claim would be the whole file. It is disjoint from every other "
    "additive claim and from every `#symbol` claim on the same file, and collides only "
    "with a claim on the plain path. It is held to: `kyo touch-drift` reports a branch "
    "that changed or removed lines of a file it claimed only additively."
)


def missing_touch_remedy(path_part: str) -> str:
    """The tail of the "no such file" refusal: how to declare a path work will create.

    The remedy the plain missing-path refusal names, so whoever is filing a record for
    work that creates something is told the form rather than left to commit a stub file
    to satisfy the validator — which is what the consumer actually did, four times.

    BOTH forms, since KYO-225 built the second (c1). Which one is right is a judgment
    about the work and not one this refusal can make: a dispatch that creates a tree
    wants the directory, and a dispatch that adds one file beside a dozen existing ones
    wants the file, because claiming the directory there overstates the scope and
    collides with every other dispatch under it. So the message states the two and what
    separates them, in that order, rather than offering the one it happens to know how
    to spell.

    The directory example is the refused path's own parent when it has one, because a
    remedy a reader has to translate is a remedy half of them get wrong. A path with no
    parent gets the rule and no example: the only tree above it is the project root,
    and that is refused the directory form on purpose. The new-file example is always
    exact — it is the refused path itself with one character in front of it.
    """
    parent = path_part.rsplit("/", 1)[0] if "/" in path_part else ""
    example = f" (e.g. {parent}/)" if parent else ""
    return (
        "if this work CREATES that path, say so in the touch rather than committing a "
        f"stub: `{TOUCH_NEW_FILE_PREFIX}{path_part}` claims that one file and nothing "
        "else, and a trailing slash on the tree it lands in"
        f"{example} claims the whole tree. Both are accepted whether or not they exist "
        "yet; a path with neither marker must exist now"
    )


# --------------------------------------------------------------------------- status
#
# KYO-101. Status *names* are per-project, declared in `kyosei/statuses.yml`;
# transition contracts key off the **archetype role** each name declares, never off the
# name. A project may call its completion status `shipped` and
# `complete-requires-settled-criteria` still binds, because it was always a statement
# about the role and only incidentally about the word `done`.
#
# The two halves of that sentence are deliberately owned by different parties:
#
#   names   the project's, in kyosei/statuses.yml, editable as reviewable config
#   roles   this module's, closed, and not extensible per project (STATUS_ROLES)
#
# A per-project role would be a per-project contract, which is the enforcement core
# made optional — precisely what roles were invented to avoid in the first place.

#: The archetype roles a declared status may carry.
#:
#: **This set is six, where the original sketch had four**, and both additions are forced
#: by v1's ladder rather than chosen. The sketch had ``initial / active /
#: needs-verification / terminal``; the brief for this work says outright that if the
#: ladder needs more roles than the sketch, extend the set rather than flatten distinct
#: contracts onto one role. It does, twice:
#:
#: * ``verified`` — v1's ladder has a rung between "built, nothing checked" and "done",
#:   and it means something the sketch has no slot for: *a verdict has been recorded,
#:   and only the decision to complete remains*. Mapping ``verified`` onto
#:   ``needs-verification`` would put `kyo next`'s awaiting-verification queue (which
#:   keys on that role) back on records somebody has already verified, which is a
#:   behaviour change the default-ladder criterion forbids.
#: * ``complete`` / ``retired`` — the sketch's single ``terminal`` covers two states
#:   whose contracts are *different in both directions*. Entering ``done`` demands
#:   settled criteria and at least one met; entering ``wont-do`` demands a reason and
#:   nothing else. Leaving ``wont-do`` is refused; leaving ``done`` is not (the charter
#:   is explicit that transitions may move backward, and ``wont-do`` alone "retires the
#:   ID permanently"). One ``terminal`` role would have had to either drop the reason
#:   requirement, drop the met-criterion requirement, or make ``done`` unleavable —
#:   three ways of weakening a contract to make a role fit, which is the one thing the
#:   brief rules out.
ROLE_INITIAL = "initial"
ROLE_ACTIVE = "active"
ROLE_NEEDS_VERIFICATION = "needs-verification"
ROLE_VERIFIED = "verified"
ROLE_COMPLETE = "complete"
ROLE_RETIRED = "retired"

STATUS_ROLES: tuple[str, ...] = (
    ROLE_INITIAL,
    ROLE_ACTIVE,
    ROLE_NEEDS_VERIFICATION,
    ROLE_VERIFIED,
    ROLE_COMPLETE,
    ROLE_RETIRED,
)

#: What each role means, printed by the registry's own default file and by the refusal
#: a bad ``role:`` raises. One sentence each, here, so the packaged ``statuses.yml``
#: comments and the error message cannot describe the vocabulary differently (law 3).
ROLE_MEANINGS: dict[str, str] = {
    ROLE_INITIAL: "filed; nothing has started",
    ROLE_ACTIVE: "work is underway",
    ROLE_NEEDS_VERIFICATION: "built, but no verdict has been recorded yet",
    ROLE_VERIFIED: "a verdict is recorded; only the decision to complete remains",
    ROLE_COMPLETE: "finished, with the criteria to say so",
    ROLE_RETIRED: "settled without completing; the id is retired and never reused",
}

#: Roles exactly one declared status may carry, because each is a status the tool has
#: to be able to *name* rather than merely test for: where `kyo new` files a record,
#: what `kyo wont-do` moves to, and what "finished" means to `kyo next` and the
#: duplicate-detection corpus. "Which of your three completions did you mean" has no
#: answer, so the registry refuses the question instead of picking.
SINGLETON_ROLES: frozenset[str] = frozenset({ROLE_INITIAL, ROLE_COMPLETE, ROLE_RETIRED})

#: The role a transition may not leave. Terminality is a property of ``retired`` and
#: not a per-status flag, because the charter grants it to exactly one thing — the
#: status that "retires the ID permanently" — and a declarable flag would let a project
#: make its completion status unleavable, which is a different design nobody decided.
TERMINAL_ROLES: frozenset[str] = frozenset({ROLE_RETIRED})

#: The roles that mean "this record is no longer in flight". Read by `kyo sweep`, `kyo
#: next` and the creation-time duplicate check through
#: :meth:`StatusLadder.active_names`, which is the one place "still open" is defined.
SETTLED_ROLES: frozenset[str] = frozenset({ROLE_COMPLETE, ROLE_RETIRED})

#: KYO-101, the second of the four config dimensions to become a registry.
#: Declared here rather than beside ``registry.SCOPE`` because ``role`` is validated
#: against :data:`STATUS_ROLES` above — see the note in ``registry.py`` for why the
#: import may only run this way.
#:
#: ``required=False``: every store written before this release has no ``statuses.yml``
#: at all, and that absence is not an error — it loads as :func:`default_ladder`, which
#: is v1's ladder exactly, so such a store keeps every status name and every behaviour
#: it had. `kyo init` writes the file for new projects so the ladder is visible and
#: editable rather than implied by a tuple in this module.
STATUS = registry_module.Dimension(
    singular="status",
    plural="statuses",
    what="a rung on the work archetype's ladder, and the archetype role it plays",
    required_fields={"role": registry_module.one_of(*STATUS_ROLES)},
)

#: The filename this dimension's *packaged default* lives under, inside
#: ``src/kyosei/defaults/``. The project-side name is ``STATUS.filename`` and they are
#: the same string by construction: the default file is copied verbatim into a new
#: project by `kyo init` (``scaffold.DEFAULT_ASSETS``) and read directly by
#: :func:`default_ladder` for a project that has none, so there is exactly one copy of
#: v1's ladder in this codebase and it is a reviewable YAML file, not a tuple.
DEFAULT_STATUSES_ASSET = STATUS.filename


@dataclass(frozen=True)
class StatusLadder:
    """One project's declared statuses, indexed by the roles the contracts bind to.

    Everything a caller used to read off a module-level tuple is a method here, and
    that is the whole change: ``ALL_STATUSES``/``INITIAL_STATUS``/``TERMINAL_STATUSES``
    were true of every project because there was only one ladder. Loaded once per
    command from ``kyosei/statuses.yml`` (or from the packaged default when a project
    has none), and passed to the checks rather than imported by them, so a renamed
    status can never be compared against a name frozen at import time.
    """

    registry: registry_module.Registry
    #: Declaration order — the ladder as its author wrote it down.
    names: tuple[str, ...]
    roles: Mapping[str, str]
    #: The single status carrying each of :data:`SINGLETON_ROLES`.
    initial: str
    complete: str
    retired: str

    def __contains__(self, status: str) -> bool:
        return status in self.roles

    def role(self, status: str) -> str | None:
        """The role ``status`` declares, or ``None`` if it declares no status at all.

        ``None`` rather than a raise: a stored value can outlive the registry entry
        that licensed it (the file is ordinary editable config), and every reader of a
        stored status has to be able to render that record rather than crash on it.
        `kyo doctor`'s ``status_registry`` check is what makes the state loud.
        """
        return self.roles.get(status)

    def with_role(self, *roles: str) -> tuple[str, ...]:
        """Every declared status carrying one of ``roles``, in ladder order."""
        wanted = set(roles)
        return tuple(name for name in self.names if self.roles[name] in wanted)

    def is_terminal(self, status: str) -> bool:
        """May a transition leave ``status``? An undeclared status is not terminal:
        refusing to move a record whose status the registry no longer knows would trap
        it in exactly the state the caller is trying to fix."""
        return self.roles.get(status) in TERMINAL_ROLES

    def is_settled(self, status: str) -> bool:
        """Complete or retired — "no longer in flight"."""
        return self.roles.get(status) in SETTLED_ROLES

    def active_names(self) -> tuple[str, ...]:
        """Declared statuses that are neither complete nor retired.

        The "still in flight" reading `kyo sweep`, `kyo next`'s readiness check and the
        creation-time duplicate check share (law 3: one definition, not three guesses).
        """
        return tuple(name for name in self.names if not self.is_settled(name))

    @property
    def flow(self) -> str:
        """The ladder as a human sentence — ``open → working → built → verified → done``
        for the default registry, and the project's own words for anyone else. Used by
        the refusals that have to say what the work archetype's flow *is*."""
        rungs = [name for name in self.names if self.roles[name] != ROLE_RETIRED]
        return " → ".join(rungs)

    def listing(self) -> str:
        """Every declared status, comma-separated, for a "known statuses:" line."""
        return ", ".join(self.names)


class StatusRegistryError(KyoseiError):
    """``kyosei/statuses.yml`` declares a ladder the contract machinery cannot bind to
    — a role missing, unknown, or carried by the wrong number of statuses."""


def _ladder_from(registry: registry_module.Registry) -> StatusLadder:
    """Validate the role *cardinalities* the shared loader cannot know about.

    The shared shape checks that every entry has a role and that the role is one of
    six. What it cannot check is that the six are arranged into a usable ladder, which
    is this dimension's own rule and lives with the contracts that depend on it. Every
    problem is collected and raised together, matching the loader's own style (law 6).
    """
    names = registry.declared_names
    roles = {name: str(registry[name].get("role")) for name in names}
    problems: list[str] = []
    singletons: dict[str, str] = {}
    for role in sorted(SINGLETON_ROLES):
        carriers = [name for name in names if roles[name] == role]
        if len(carriers) == 1:
            singletons[role] = carriers[0]
            continue
        if not carriers:
            problems.append(
                f"no status declares `role: {role}` ({ROLE_MEANINGS[role]}); exactly one "
                "must, because the tool has to be able to name that status and not "
                "merely test for it"
            )
        else:
            problems.append(
                f"{len(carriers)} statuses declare `role: {role}` "
                f"({', '.join(carriers)}); exactly one may, because the tool has to be "
                "able to name that status and 'which of these did you mean' has no answer"
            )
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise StatusRegistryError(
            f"{registry.path} declares a ladder the transition contracts cannot bind "
            f"to:\n{detail}"
        )
    return StatusLadder(
        registry=registry,
        names=names,
        roles=roles,
        initial=singletons[ROLE_INITIAL],
        complete=singletons[ROLE_COMPLETE],
        retired=singletons[ROLE_RETIRED],
    )


@lru_cache(maxsize=1)
def default_ladder() -> StatusLadder:
    """v1's ladder, read from the packaged ``defaults/statuses.yml``.

    Not a tuple in this module: the packaged file is the one `kyo init` copies into a
    new project, so reading it here is what makes "the default registry reproduces v1's
    ladder exactly" true by construction rather than by two lists agreeing today. A
    project with no ``kyosei/statuses.yml`` — which is every store written before this
    release — gets exactly this.
    """
    # Imported here, not at module scope: `scaffold` reaches `store`, which reaches
    # this module. The cycle is real and the lazy import is the narrow way through.
    from .scaffold import defaults_dir

    source = defaults_dir()
    if source is None or not (source / DEFAULT_STATUSES_ASSET).is_file():
        raise StatusRegistryError(
            f"this build ships no defaults/{DEFAULT_STATUSES_ASSET}, so it cannot say "
            "what a project that declares no statuses of its own should use; reinstall "
            "kyosei"
        )
    return _ladder_from(registry_module.load(source, STATUS))


def load_status_ladder(kyosei_dir: Path) -> StatusLadder:
    """This project's ``kyosei/statuses.yml``, or v1's ladder if it declares none.

    The absent-file case is not an empty registry the way an absent ``scopes.yml`` is.
    A project with no scopes is a real, workable project; a project with no statuses
    could not move a record at all, and every store written before this release is in
    that state through no decision of its own. So absence means *the default*, and
    :func:`default_ladder` is a file rather than a fallback tuple so the two can never
    be different ladders.
    """
    loaded = registry_module.load(kyosei_dir, STATUS)
    if not loaded.exists:
        return default_ladder()
    return _ladder_from(loaded)

# ------------------------------------------------------------ knowledge standing

#: KYO-44.c2. A ``knowledge`` kind has no status flow, so :data:`WORK_STATUSES` is
#: not a vocabulary any of its values are true in. The stored ``status`` column is
#: still ``open`` on such a record (it is one column shared by both archetypes), and
#: rendering that word put "open, dispatchable, waiting" and "open, and structurally
#: incapable of being anything else" in the same glyphs — the exact failure the
#: criterion-states design exists to prevent, one level up in the status field
#: (law 6: inapplicability must be visible, never rendered as an ordinary value).
#:
#: So every human-facing surface renders a **standing** for a knowledge record
#: instead of its status. Derived, not stored: the archetype is already known
#: wherever a record is rendered, and adding a column would make two things that
#: can disagree out of one fact (law 3). ``kyo show --json`` and ``kyo list --json``
#: carry it beside ``status`` for the same reason; ``kyosei/export/`` deliberately
#: does not, because a derived field in the git-canonical export is a second copy
#: of the same fact that a stale rebuild could contradict.
STANDING_CURRENT = "current"
STANDING_VOID = "void"
KNOWLEDGE_STANDINGS: tuple[str, ...] = (STANDING_CURRENT, STANDING_VOID)


def standing(status: str, archetype: str, ladder: StatusLadder) -> str:
    """What ``status`` means to a reader of a record of this ``archetype``.

    ``work`` records keep their own vocabulary unchanged. A ``knowledge`` record
    reads ``current`` until it is retired by `kyo void`, and ``void`` after — the
    two states its lifecycle actually has.

    KYO-101: the retirement test is now ``ladder``'s, not a frozen name, so a project
    that renames ``wont-do`` still sees its voided knowledge records read ``void``.
    That is criterion c2's other half — renaming the retirement terminal must not be a
    way to make a retired record look live.
    """
    if archetype == "work":
        return status
    return STANDING_VOID if ladder.is_terminal(status) else STANDING_CURRENT

# ------------------------------------------------------------------------ criteria

#: Law 10 — criteria are states, not booleans. ``void-withdrawn`` (deliberately dropped,
#: premise intact) is distinct from ``void-premise-gone`` (premise died) so the first can
#: never be used as a lie about the second (law 4; a milestone-1 build flag). This tuple's
#: membership must agree with ``src/kyosei/report.schema.json``'s ``criterionVerdict.verdict``
#: enum byte-for-byte — law 3, derived agreement, not hoped-for; see
#: ``tests/test_criteria.py::test_states_match_the_report_schema_enum``.
CRITERION_STATES: tuple[str, ...] = (
    "met",
    "unmet-unbuilt",
    "unmet-unobserved",
    "void-premise-gone",
    "void-withdrawn",
)

DEFAULT_CRITERION_STATE = "unmet-unbuilt"

#: These states are claims about the world (why it wasn't observed, why the premise died,
#: why it was withdrawn on purpose), so they must carry a reason.
CRITERION_STATES_REQUIRING_REASON: frozenset[str] = frozenset(
    {"unmet-unobserved", "void-premise-gone", "void-withdrawn"}
)

#: A criterion in one of these states no longer blocks completion.
SETTLED_CRITERION_STATES: frozenset[str] = frozenset(
    {"met", "void-premise-gone", "void-withdrawn"}
)

#: KYO-58.c3. The states that mean "this criterion is dead" — as opposed to
#: ``met``, which is dead-but-achieved and still part of what the record claims.
#: A dispatch brief renders the criteria NOT in this set and states how many it
#: left out (:func:`kyosei.brief._criteria_table`); a worker's reading budget is a
#: measured constraint, and a dead criterion invites exactly the wrong question.
VOID_CRITERION_STATES: frozenset[str] = frozenset({"void-premise-gone", "void-withdrawn"})

# ------------------------------------------------------ criterion restatement (KYO-58)

#: KYO-58.c2. The canonical reason `kyo restate` writes onto the criterion it voids.
#:
#: One stored fact, both directions derived from it. The forward direction
#: ("c1 was replaced by c7") is this string; the reverse ("c7 replaces c1") is a
#: lookup over the same record's criteria. The alternative — writing the reverse
#: onto the NEW criterion as well — would put the same fact in two places that can
#: disagree (law 3), and worse, would not survive: `kyo verify` overwrites a
#: criterion's ``reason`` with the verdict's reason (cmd_evidence.cmd_verify), and
#: the new criterion is precisely the one that gets verified. The voided one never
#: is (``void-withdrawn`` is disjoint from :data:`VERIFY_VERDICTS`), so the fact is
#: stored on the only half of the pair that holds it stably.
#:
#: Shape borrowed from `kyo split`, which encodes its own successor relation the
#: same way and has ``doctor_dispatch.check_split_backlink_symmetry`` proving the pair stays
#: consistent — KYO-58 asked for split's proven shape, not a second convention. No
#: DDL and therefore no schema bump: what is added is a *reading* of two columns
#: that already exist, exactly as KYO-44 derived a knowledge record's standing from
#: ``(status, archetype)`` rather than adding a column.
def restate_reason(replacement_id: str) -> str:
    """The reason text that records "this criterion was restated as X"."""
    return f"restated as {replacement_id}"


#: The parser for the above. Declared here beside the writer, and imported by
#: ``doctor``, ``brief`` and ``render`` rather than re-typed, so a
#: change to the wording cannot leave one reader behind (law 3).
RESTATE_REASON_RE = re.compile(r"^restated as (\S+)$")


def replaced_by(state: str, reason: str | None) -> str | None:
    """The criterion that superseded this one, or ``None``.

    Reads only ``(state, reason)`` — the two columns every criterion-rendering
    surface already has — so `kyo show`, `kyo list` and the dispatch brief cannot
    disagree about whether a void criterion was withdrawn or replaced.
    """
    if state != "void-withdrawn":
        return None
    match = RESTATE_REASON_RE.match((reason or "").strip())
    return match.group(1) if match else None


#: KYO-58.c5. What a criterion voided by a restatement *reads* as. Deliberately not
#: a member of :data:`CRITERION_STATES`: a new stored state would have to be added
#: to ``src/kyosei/report.schema.json``'s verdict enum (a worker could then file it),
#: to the compat machinery, and to every reader — for a fact that is already
#: implied by the two columns :func:`replaced_by` reads.
CRITERION_STANDING_SUPERSEDED = "superseded"


def criterion_standing(state: str, reason: str | None) -> str:
    """What ``state`` means to a human reader of this criterion.

    ``void-withdrawn`` covers two different things: dropped on purpose, and
    replaced by a better wording of the same claim. Law 6 says a reader must be
    able to tell them apart, so the second reads ``superseded``. Every surface
    prints the stored state beside this word, never instead of it (the KYO-44
    rule for record standings, applied one level down).
    """
    return CRITERION_STANDING_SUPERSEDED if replaced_by(state, reason) else state

# ---------------------------------------------------------------------- provenance

#: Milestone 4 (law 9). A claim's confidence in itself, independent of whether
#: anyone has checked it.
CLAIM_LEVELS: tuple[str, ...] = ("asserted", "derived", "verified")

#: Milestone 4 (law 9/10). How a ``kyo verify`` verdict was reached.
VERIFICATION_METHODS: tuple[str, ...] = ("machine-checked", "human-observed", "reasoned-about")

#: KYO-4.c3: the states ``kyo verify`` may record — verified facts about the world,
#: derived from a verification event, never set as a bare flip. Disjoint from
#: :data:`EDIT_DIRECT_CRITERION_STATES` below; the union of the two is exactly
#: :data:`CRITERION_STATES`.
VERIFY_VERDICTS: frozenset[str] = frozenset({"met", "unmet-unbuilt", "unmet-unobserved"})

#: KYO-4.c3: the only states ``kyo edit --criterion --state`` may still set directly.
#: Both are premise judgments ("this no longer applies"/"we chose not to pursue
#: this"), not verified facts, and both already require ``--why``; a verified fact
#: ("met" chief among them) must instead carry a verification's method + evidence
#: (``kyo verify``), never a bare state flip.
EDIT_DIRECT_CRITERION_STATES: frozenset[str] = frozenset({"void-premise-gone", "void-withdrawn"})

# --------------------------------------------------------------------------- links

#: Typed edges. ``depends-on-decision`` from the charter needs claim ids and lands
#: with milestone 4; it is deliberately absent here rather than half-modelled.
LINK_TYPES: tuple[str, ...] = (
    "blocks",
    "duplicates",
    "supersedes",
    "same-question-as",
    "needs-same-change-as",
    "produced-by",
)

#: Law 6 — dependency writes refuse cycles at write time.
ACYCLIC_LINK_TYPES: frozenset[str] = frozenset({"blocks"})

# --------------------------------------------------------------------------- gates

#: Gates are orthogonal to status: a record can be ``working`` *and* gated.
GATE_TYPES: tuple[str, ...] = (
    "awaiting:event",
    "awaiting:human-run",
    "awaiting:human-review",
    "holding-for",
)

#: The charter's grammar embeds a payload in three of the four gate types
#: (``awaiting:event <condition>``, ``awaiting:human-run <the exact thing to run>``,
#: ``holding-for:<observation>``). A gate without its payload is a gate nobody can
#: clear, so ``--do`` is required for those three.
GATE_TYPES_REQUIRING_DO: frozenset[str] = frozenset(
    {"awaiting:event", "awaiting:human-run", "holding-for"}
)

# ------------------------------------------------------------------- dispatches (M5)

#: KYO-7 — a dispatch is its own dimension from record status
#: (``docs/design/charter.md``, "Core schema": "dispatches: ... state (running |
#: reported | terminated-no-report)").
#: ``running`` -> ``reported`` only via `kyo report` ingest; ``running`` ->
#: ``terminated-no-report`` only via `kyo dispatch close --terminated-no-report
#: --why` (law 8: this is its own loud state, never rendered as anything that
#: reads like "done"). Both ``reported`` and ``terminated-no-report`` are
#: terminal — a dispatch is a frozen record of one attempt, never reopened.
#: KYO-90 does not change that: an amended report supersedes the report a
#: ``reported`` dispatch already carries, and the dispatch's ``state`` never
#: moves back to ``running`` for it (see the report-amendment section below).
#:
#: KYO-345 adds a FOURTH state, ``withdrawn``, and it is the one reading law 8
#: did not have. A frozen brief is immutable by design, so a dispatch frozen by
#: mistake — the wrong model, the wrong branch, the wrong record — and never handed
#: to a worker had exactly one exit: ``terminated-no-report``, which MEANS "died
#: mid-work" and which the session brief tells the next controller to flag. A
#: consumer's controller hit it, closing a dispatch that no worker had
#: ever been spawned for, and filed it: the store's account of that dispatch was a
#: death that never happened, and the brief carried it forward as one. Law 8 says a
#: dispatch without a report artifact is its own loud state; it does not say the two
#: ways of reaching that state are the same fact, and rendering them as one is the
#: "success without evidence" failure inverted — a *failure* without a failure.
#:
#: So ``withdrawn`` is reached only via `kyo dispatch close --withdrawn --why` and
#: only from ``running`` with no ingested report, it carries a controller-written
#: reason exactly as ``terminated-no-report`` does, and it is terminal exactly as the
#: other two are (a withdrawn dispatch is not reopened — the controller dispatches a
#: ``.d2``, which is what makes the mistake visible as two records rather than
#: silently edited out of one). What it is NOT is loud: nothing about a dispatch
#: nobody ever ran needs a later controller's attention, which is why `kyo brief`
#: lists it nowhere while `kyo show <record-id>` prints it in the record's dispatch
#: history under its own word (KYO-345.c2).
DISPATCH_STATE_RUNNING = "running"
DISPATCH_STATE_REPORTED = "reported"
DISPATCH_STATE_TERMINATED_NO_REPORT = "terminated-no-report"
DISPATCH_STATE_WITHDRAWN = "withdrawn"
DISPATCH_STATES: tuple[str, ...] = (
    DISPATCH_STATE_RUNNING,
    DISPATCH_STATE_REPORTED,
    DISPATCH_STATE_TERMINATED_NO_REPORT,
    DISPATCH_STATE_WITHDRAWN,
)
DISPATCH_TERMINAL_STATES: frozenset[str] = frozenset(
    {
        DISPATCH_STATE_REPORTED,
        DISPATCH_STATE_TERMINATED_NO_REPORT,
        DISPATCH_STATE_WITHDRAWN,
    }
)

#: KYO-345: the terminal states that carry a controller-written reason in
#: ``dispatches.terminated_why`` instead of a report. Named once because three
#: places ask the same question of a row — `kyo dispatch close` (which reason flag
#: is required), ``doctor_dispatch.check_dispatch_state_consistency`` (a state here without a
#: recorded why is a bare label, law 8) and the two renderings below — and law 3
#: says three independently retyped pairs get to disagree.
#:
#: ONE COLUMN, not two, and that is a decision rather than an economy. "Why did this
#: dispatch end with no report" is one fact; splitting it by state would put the
#: same sentence in two places and make every reader ask which one is set. What the
#: state decides is the WORD a reader sees, which is what
#: :data:`DISPATCH_CLOSE_REASON_LABELS` is for — the column's meaning widens from
#: "why it was terminated" to "why it ended with no report", which is a format change
#: no column shape can express and is therefore recorded in ``schema.SCHEMA_VERSION``
#: (22) exactly as versions 9 and 14 recorded theirs.
DISPATCH_STATES_WITHOUT_A_REPORT: frozenset[str] = frozenset(
    {DISPATCH_STATE_TERMINATED_NO_REPORT, DISPATCH_STATE_WITHDRAWN}
)

#: KYO-345.c2: how the stored reason READS, per state — derived from the state at
#: every rendering site rather than typed at each of them, so a withdrawn dispatch
#: can never print "terminated because" anywhere. Keyed by the constants above, so a
#: fifth state that carries a reason and no label is a ``KeyError`` at the call site
#: rather than a blank or a wrong word in front of a controller (the shape
#: :data:`MODEL_SOURCE_LABELS` already uses one module over).
DISPATCH_CLOSE_REASON_LABELS: dict[str, str] = {
    DISPATCH_STATE_TERMINATED_NO_REPORT: "terminated because",
    DISPATCH_STATE_WITHDRAWN: "withdrawn because",
}


def dispatch_close_reason_label(state: str) -> str:
    """The phrase a stored close reason is printed under, for ``state`` (KYO-345.c2).

    A state that is not one of :data:`DISPATCH_STATES_WITHOUT_A_REPORT` has no reason
    to print and no label for one; the neutral fallback exists so a renderer reading
    a row from a store whose state vocabulary this build does not know prints
    something true rather than a word it made up, which is the same reading law 6
    asks of every other "unknown must not read as either answer" site.
    """
    return DISPATCH_CLOSE_REASON_LABELS.get(state, "closed because")

# ------------------------------------------------------- report amendment (KYO-90)
#
# The workflow this tool is embedded in resumes workers: a report is ingested, the
# branch is merged, something is found wrong with the merged tree, and the same
# worker is sent back to the same worktree and branch. Before KYO-90 the corrected
# report had nowhere to go — `kyo report` refused it as
# ``dispatch-already-terminal`` — so the store's account stayed the pre-fix one
# (measured on KYO-83.d1: two commits recorded where three landed, two
# disagreements where three were made, and the third disagreement was the single
# most valuable output of the dispatch).
#
# The contract that refused it is NOT relaxed. A second report still cannot
# silently overwrite the first; what changes is that a report may *declare* which
# report it supersedes, and that declaration is the only thing that gets it past
# the refusal.
#
# **The declaration lives in the report document, not in a CLI flag.** The party
# who knows the earlier report was wrong is the worker, and the worker cannot type
# a controller's flag — under a `--amend` flag a resumed worker can only ask the
# controller to remember one. A field in the document it already rewrites is
# reachable from where the worker actually stands, keeps `kyo report`'s standing
# promise that a report is ingested and never retyped by the controller, and names
# *which* account is being superseded rather than "whatever is current".
#
# Shape borrowed from `kyo restate` (KYO-58), which this project argued through
# once for exactly the "the old thing must stay attached to what it was about"
# problem: one stored fact, both directions derived from it, and a `doctor` check
# proving the pair stays consistent. Here the stored fact is ``amends`` inside
# ``reports.body_json`` — already stored verbatim, already exported, already
# rebuilt — so there is no new column and therefore no schema bump.

#: KYO-90.c1. The optional top-level key in ``src/kyosei/report.schema.json`` that
#: carries the declaration. Named here so the validator, the ingest, the refusal
#: message and every reader spell it one way (law 3).
REPORT_AMENDS_KEY = "amends"


def report_amends(body: dict | None) -> str | None:
    """The report this report declares it supersedes, or ``None``.

    Reads the ingested document, which is the only place the fact is stored.
    """
    if not isinstance(body, dict):
        return None
    value = body.get(REPORT_AMENDS_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


#: KYO-90.c3. What a report *reads* as to a human. Like
#: :data:`CRITERION_STANDING_SUPERSEDED`, deliberately not a stored column: a
#: report is current exactly when its dispatch's ``report_id`` names it, and
#: superseded otherwise, and both facts are already in the store.
REPORT_STANDING_CURRENT = "current"
REPORT_STANDING_SUPERSEDED = "superseded"


def report_standing(report_id: str, current_report_id: str | None) -> str:
    """Is this the account of what happened, or an account that was replaced?"""
    return REPORT_STANDING_CURRENT if report_id == current_report_id else REPORT_STANDING_SUPERSEDED


def amended_by(report_id: str, reports: Iterable[tuple[str, str | None]]) -> str | None:
    """The report that superseded ``report_id``, or ``None``.

    ``reports`` is ``(id, amends)`` for the reports of one dispatch. The reverse
    direction is a lookup over the same stored fact rather than a second copy of
    it written onto the new report — the same choice, for the same law-3 reason,
    that :func:`replaced_by` makes for restated criteria.
    """
    for candidate_id, amends in reports:
        if amends == report_id:
            return candidate_id
    return None


def amend_remedy(dispatch_id: str, current_report_id: str) -> str:
    """The remedy a controller is given when a second report arrives for a
    dispatch that already reported. One string, so the refusal, the docs and
    ``tests/test_report.py``'s remedy-clears-the-error test cannot drift."""
    return (
        f'to correct the account instead of duplicating it, add "{REPORT_AMENDS_KEY}": '
        f'"{current_report_id}" to the report document and file it again — {current_report_id} '
        "stays readable, marked superseded, and the new report becomes the current one"
    )


#: KYO-90.c1. A report declared an ``amends`` that is not the report its dispatch
#: currently carries — a stale correction written against an account that has
#: itself already been superseded, or against a dispatch with no report at all.
#: Refused rather than reconciled: superseding an account this report never read
#: is the silent overwrite ``dispatch-already-terminal`` exists to prevent.
CONTRACT_REPORT_AMENDS_WRONG_REPORT = "report-amends-wrong-report"

# ---------------------------------------------------------------- flags (M6, KYO-8)

#: The flag classes `kyo sweep` produces (KYO-8.c1/c2/c3). ``docs/design/charter.md``'s
#: "Sweep" section names several more (claims aged, doc-``governs:`` staleness,
#: moved-but-inverse/moved-but-not-merged); those are not covered by any of
#: KYO-8's own acceptance criteria and are deliberately not implemented here —
#: see this dispatch's report, `disagreements[]`, for why building unspecified
#: flag classes was judged worse than shipping exactly what KYO-8.c1-c3 name.
FLAG_TYPE_TOUCH_CHANGED = "touch-changed"
FLAG_TYPE_TOUCH_MISSING = "touch-missing"
FLAG_TYPE_NEAR_DUPLICATE_CRITERIA = "near-duplicate-criteria"

#: The three classes `kyo sweep` itself produces. Named apart from
#: :data:`FLAG_TYPES` from KYO-59 on, because a flag class is no longer the same
#: thing as a sweep class: the `/kyosei` skill states what *sweep* flags, and
#: ``tests/test_skill_cli_sync.py`` checks that sentence against this tuple —
#: against the whole of ``FLAG_TYPES`` it would demand the skill credit sweep with
#: a class sweep does not produce.
#: KYO-137.c7. The one part of the docs-governance record that needs no repository at
#: all: a record's own description going stale against its own claims, where the store
#: holds both halves and can compare them without reading a single file. It is the
#: cheapest of the three staleness cases the consumer report named — its APP-58 instance,
#: which "has no document in it at all" — and the only one whose evidence never leaves
#: the database.
#:
#: **Bounded deliberately, and the bound is the design.** A claim being newer than a
#: description is NOT proof the description is wrong; the claim may confirm exactly what
#: the description already said. So this is a prompt and never a refusal, it never edits
#: the record, and it compares only against claims nothing has superseded — measuring a
#: description against a claim that has itself been replaced would flag history. KYO-33
#: measured what routine false positives cost an advisory check, and of the classes in
#: this build this is the one most able to become wallpaper.
FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM = "description-predates-claim"

SWEEP_FLAG_TYPES: tuple[str, ...] = (
    FLAG_TYPE_TOUCH_CHANGED,
    FLAG_TYPE_TOUCH_MISSING,
    FLAG_TYPE_NEAR_DUPLICATE_CRITERIA,
    FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM,
)

#: KYO-59.c1. Raised by `kyo ungate`, not by `kyo sweep` — the first flag class in
#: this build that fires at a *moment* rather than on a periodic scan, and that is
#: the whole point: a gate's meaning is "this record is waiting on something that
#: will change what it should be", so clearing one always implies something just
#: changed, and every criterion authored before that clearing was written under the
#: assumption it resolved. Sweep watches for drift over time and would notice this
#: never, because nothing about the record's text or touches changes when the gate
#: clears.
FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA = "gate-cleared-stale-criteria"

FLAG_TYPES: tuple[str, ...] = (
    *SWEEP_FLAG_TYPES,
    FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA,
)

#: KYO-346.c1/c2. A record at a completion or retired status (:data:`SETTLED_ROLES`,
#: below) has no open work left for one of these three to ask a question about — a
#: worked-on record's declared files not changing again is what files do once nobody
#: is touching them, and a description not outrunning its own claims is likewise a
#: fact about work that has stopped. So the move that settles a record — the
#: completion move, `kyo wont-do`, `kyo void` — closes these automatically (KYO-346.c1)
#: and `kyo flag resolve --settled` is the repair for a store that accumulated them
#: before that existed (KYO-346.c3).
#:
#: Deliberately narrower than :data:`SWEEP_FLAG_TYPES`: `near-duplicate-criteria` is a
#: fact about the CRITERIA'S TEXT, unrelated to whether the record itself moved, and
#: `gate-cleared-stale-criteria` asks whether an unobserved criterion still makes sense
#: after a gate cleared — a judgement a record reaching its own completion or
#: retirement does not make for itself. KYO-346.c2 leaves both open on purpose; a test
#: says so.
STALENESS_FLAG_TYPES: tuple[str, ...] = (
    FLAG_TYPE_TOUCH_CHANGED,
    FLAG_TYPE_TOUCH_MISSING,
    FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM,
)


#: KYO-346.c1: the reason text an automatic settlement closure records, so a reader of
#: a single flag (`kyo show`) is told this was not a human judging the finding —
#: paired with :data:`SETTLED_WHY_PREFIX` below exactly as `sweep.superseded_why` is
#: paired with `sweep.is_superseded` (law 3: one writer, one reader, never a second
#: guess at the prefix).
SETTLED_WHY_PREFIX = "settled automatically: "


def settled_flag_why(record_id: str, status: str, actor: str) -> str:
    return (
        f"{SETTLED_WHY_PREFIX}{record_id} moved to {status!r} by {actor} — a record "
        "at a completion or retired status has no open work left for a staleness "
        "flag to ask about (KYO-346)."
    )


def is_settled_resolution(resolved_why: str | None) -> bool:
    return bool(resolved_why) and str(resolved_why).startswith(SETTLED_WHY_PREFIX)


#: KYO-59.c3, decided rather than assumed. Only ``holding-for`` fires the flag
#: above.
#:
#: The four gate types do not mean the same kind of thing when they clear.
#: ``holding-for:<observation>`` is the one whose clearing is a *decision*: the
#: charter's grammar makes the payload an observation the record is waiting to be
#: settled, and settling it is what rewrites what the record should be (the live
#: case: KYO-30 sat on "the v2 promoted-table design decides what a promotion stamp
#: is", and when that design was decided, both of KYO-30's criteria described the
#: pre-decision world). ``awaiting:human-run`` and ``awaiting:event`` clear when
#: something *happened*; ``awaiting:human-review`` clears when someone looked. Each
#: of those can in principle invalidate a criterion, but none of them does so by
#: construction — and a flag that fires on every gate clearing becomes the routine
#: false positive KYO-33 exists about, at which point it stops being read at all.
#:
#: Widening this is one line plus a test, and the flag's own detail names the gate
#: type, so a project that finds the narrow version misses cases has the evidence
#: to argue with.
GATE_TYPES_FLAGGING_STALE_CRITERIA: frozenset[str] = frozenset({"holding-for"})

# ----------------------------------------------------------------- contract names

# KYO-101. Four of these were keyed to a v1 status *name* and are now keyed
# to the archetype role they were always about. `done-requires-settled-criteria` was
# never a fact about the word `done`; it was a fact about the status a project
# completes work in, which this project happens to call `done` and another may call
# `shipped`. The rename is the identifier catching up with what the check does.
#
#   working-requires-criteria      -> active-requires-criteria
#   done-requires-settled-criteria -> complete-requires-settled-criteria
#   done-requires-a-met-criterion  -> complete-requires-a-met-criterion
#   wont-do-requires-reason        -> retired-requires-reason
#
# The original sketch's worked example names the first of these `terminal-requires-
# settled-criteria`, because that sketch has one `terminal` role. This build has two (see
# STATUS_ROLES): flattening `done` and `wont-do` onto one role would have had to weaken
# one of their contracts. So the role is `complete` and the contract is named for it.
# The full argument, and the fact that it diverges from the decision's wording, is in
# this dispatch's report under `disagreements[]`.
#
# `terminal-status-is-terminal` is deliberately NOT renamed: it never named a status in
# the first place, and terminality is a property of the `retired` role (TERMINAL_ROLES)
# rather than of any one name.
CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA = "complete-requires-settled-criteria"
CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION = "complete-requires-a-met-criterion"
#: KYO-137.c5: an outstanding docs obligation blocks, and only the controller clears it.
#: This is what makes raising obligations at report time and settling them by verdict a
#: mechanism rather than an exhortation — a prompt at report time that nothing enforces
#: is a prompt, and law 1's
#: whole argument is that prompts decay and checks hold.
#:
#: It binds to :data:`ROLE_COMPLETE` and deliberately not to :data:`ROLE_RETIRED`, though
#: only the second is ``is_terminal``. "Reaching its terminal status" is the completion
#: move: a record being abandoned ships no code, so the document it would have made stale
#: is not stale, and refusing `kyo wont-do` would trap a record nobody intends to finish
#: behind a documentation task nobody owes. Retiring leaves the obligations open and
#: readable rather than settling them by side effect, because nothing observed the
#: documents and a settled row means somebody did.
#:
#: Unlike the two contracts above it, this one does NOT consult ``requires_criteria``:
#: whether a kind carries criteria says nothing about whether its work moved a file some
#: document speaks for.
CONTRACT_COMPLETE_REQUIRES_DOCS_OBLIGATIONS_SETTLED = (
    "complete-requires-docs-obligations-settled"
)

#: KYO-308: `kyo move <plan> <a completion status>` used to succeed over a plan whose
#: members were still open, printing only the plan's own transition — the one fact
#: that matters at the moment a plan closes (its member distribution) was the one
#: thing the command did not say. `plan`'s ``requires_criteria: false`` means neither
#: contract above it ever fires for this archetype, so this is the only safeguard
#: standing between "printed as done" and "is done" for a plan.
#:
#: Binds to :data:`ROLE_COMPLETE`, like the two contracts above it, and not to
#: :data:`ROLE_RETIRED` — abandoning a plan (`kyo wont-do`) makes no claim that its
#: members finished, so nothing here has anything to check on that path.
CONTRACT_COMPLETE_REQUIRES_SETTLED_PLAN_MEMBERS = "complete-requires-settled-plan-members"

#: KYO-490: a settle either clears the gate with its evidence first or refuses. A
#: gate says the record is waiting on something; a record settled with one still open
#: says it both is and is not finished, and every brief goes on listing the gate.
#: A record has reached that state by the retirement route, so this binds to every role in
#: :data:`SETTLED_ROLES` — completion and retirement alike — unlike the completion-only
#: contracts above. Status and gates stay orthogonal for every other move (charter,
#: "Status and gates are orthogonal dimensions"). ``--force`` on the settling command
#: overrides it and leaves the gate open.
CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES = "settled-requires-no-open-gates"

#: KYO-497 (from KYO-483). A record of a ``policy.carried`` kind is a decision, an open
#: question or a reminder carried across shifts, and the only record of how it settled is
#: the reason given when it did — the "settled and dropped" lines a closing note used to
#: carry. So entering a ``complete``-role status on such a kind needs ``--why``, as the
#: ``retired`` role always has (``retired-requires-reason``). Checked before the settle
#: clears the record's open gates, so a refused settle has cleared nothing.
CONTRACT_CARRIED_SETTLE_REQUIRES_REASON = "carried-settle-requires-reason"

#: KYO-137: the vocabulary a controller settles an obligation with. Two
#: verdicts, because there are two truthful answers and collapsing them would make the
#: honest one unsayable:
#:
#: - ``updated``: the document was changed to match the work.
#: - ``not-affected``: the document was READ and needs no change. The join is a
#:   file-level intersection and will sometimes point at a section whose prose the change
#:   does not reach; a mechanism with no way to say "I looked, it is fine" teaches its
#:   users to say "updated" when they mean that, which corrupts the only signal it has.
#:
#: Both are assertions carrying a method and an author (law 9), and that is the point:
#: what separates them from a timestamp is not which was chosen but that a person chose.
#: There is no ``stale`` verdict — an obligation nobody has answered is already recorded
#: as exactly that, by ``verdict IS NULL``.
OBLIGATION_UPDATED = "updated"
OBLIGATION_NOT_AFFECTED = "not-affected"
OBLIGATION_VERDICTS: tuple[str, ...] = (OBLIGATION_UPDATED, OBLIGATION_NOT_AFFECTED)

#: A verdict is provenance (law 9), and provenance is never overwritten. `kyo docs
#: settle` refuses a second verdict on an obligation that already carries one rather
#: than replacing it — the same reason a criterion keeps a history of verdicts instead
#: of a current answer. If the code has moved again, that is a new observation about a
#: new state of the world and the dispatch that moved it raises its own obligation.
CONTRACT_DOCS_OBLIGATION_ALREADY_SETTLED = "docs-obligation-already-settled"

CONTRACT_ACTIVE_REQUIRES_CRITERIA = "active-requires-criteria"
#: KYO-413.c1: `active-requires-criteria` binds to the active role
#: only, and a transition may skip forward (charter), so `open -> built -> verified`
#: carried a record with nothing to verify into `kyo next`'s awaiting-verification
#: queue without ever entering an active status. This is the same fact checked at the
#: two rungs a verdict is about: a record that has no criteria has nothing a verdict
#: could be recorded against. A separate name rather than a widened
#: `active-requires-criteria` because the refusal names the role that was entered, and
#: a move to `built` refused as "active requires criteria" would point at a status the
#: caller never asked for.
CONTRACT_VERIFICATION_REQUIRES_CRITERIA = "verification-requires-criteria"
#: KYO-546 (a consumer report): the verified role means "a verdict is
#: recorded; only the decision to complete remains" (ROLE_DESCRIPTIONS), and nothing held
#: a move to it to that: APP-58 entered verified with every criterion still at its
#: filing state, after the `kyo accept` that would have recorded them had been refused.
#: Only the verified role -- entering needs-verification is how verdicts get asked for.
CONTRACT_VERIFIED_REQUIRES_A_VERDICT = "verified-requires-a-verdict"
CONTRACT_RETIRED_REQUIRES_REASON = "retired-requires-reason"
CONTRACT_TERMINAL_IS_TERMINAL = "terminal-status-is-terminal"
CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW = "knowledge-kind-has-no-status-flow"
#: KYO-44.c1: `kyo void` is the knowledge archetype's disposal path and only that.
#: A work record retires through `kyo wont-do`, and the refusal says so — the two
#: verbs are symmetric, and neither error is a dead end.
CONTRACT_VOID_IS_KNOWLEDGE_ONLY = "void-is-knowledge-archetype-only"
#: KYO-43: `kyo new` files a record directly, which is what the registry's ``intake``
#: pseudo-source means. Named so the refusal is a contract, not a bare string.
CONTRACT_KIND_NOT_DIRECTLY_FILEABLE = "kind-does-not-accept-intake"
#: KYO-521: `kyo new` on a kind whose policy sets ``requires_evidence_links`` (the
#: default ``decision``) is refused unless ``--evidence-file`` attaches the evidence in
#: the same act. The registry opened ``intake`` on ``decision`` so a decision made in
#: conversation is filed where it was made; this is what keeps such a record from being
#: an unsupported assertion -- the operator's words are stored with it, as evidence, the
#: way `kyo attach` and `kyo confirm` store them.
CONTRACT_FILING_REQUIRES_EVIDENCE = "filing-requires-evidence"
#: KYO-68.c3: `kyo promote` is the ``idea`` edge, and this is the same class of
#: rejection as the two contracts either side of it — a creation verb turning a kind
#: away because the registry's flow rules do not admit it. It was a bare
#: :class:`~kyosei.errors.KyoseiError` (exit 1) while its two siblings were contracts
#: (exit 2), so a consumer scripting against "a creation verb refused my kind" had to
#: handle two exit codes for one situation, with nothing visible to the caller
#: deciding which. Exit codes are a documented contract here (``errors.py``:
#: 0/1/2/3/64), so the three now agree. Named on the same principle as the others,
#: and named after the *pseudo-source* it enforces, so the three read as one family:
#: ``intake`` -> `kyo new`, ``idea`` -> here, another kind -> `kyo spawn`.
CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA = "kind-does-not-accept-idea"
#: KYO-50: `kyo spawn` is the kind-to-kind edge — the third and last source an
#: ``accepts_from`` entry can name (``intake`` -> `kyo new`, ``idea`` -> `kyo promote`,
#: another kind -> here) — and this is its refusal, named on the same principle.
CONTRACT_KIND_NOT_SPAWNABLE_FROM = "kind-does-not-accept-source-kind"
#: KYO-50: a retired record (``wont-do``, or ``void`` for a knowledge kind) produces
#: nothing further. A separate name from the refusal above because nothing is wrong
#: with the registry edge in this case — it is the *source* that is settled — and the
#: two refusals point the caller somewhere different.
CONTRACT_SPAWN_SOURCE_IS_RETIRED = "spawn-source-is-retired"
#: KYO-22: what a redacted evidence blob's text becomes. A fixed marker rather
#: than an empty string, so a reader sees "something was here and was removed"
#: instead of an artifact that looks like it never had content. Named here rather
#: than in `commands` because `kyo redact` writes it and `kyo doctor` checks it,
#: and a marker those two disagreed about would make a half-applied redaction
#: look complete.
REDACTION_MARKER = "[redacted]"

#: KYO-25: every verbatim-prose store `kyo redact` can reach, and where the text
#: lives in each. `kyo redact` shipped covering `evidence` alone; the same argument
#: covers a claim's statement and a shift note's text — a shift note is where a
#: controller quotes a log line while thinking out loud — and the original request's
#: criterion could not be met while three of the four were out of the command's
#: vocabulary. Adding a prose store to the schema without
#: adding it here is the mistake this table exists to make visible.
REDACTABLE: dict[str, tuple[str, str, str | None]] = {
    # kind -> (table, text column, record-id column or None)
    "evidence": ("evidence_artifacts", "text", "record_id"),
    "claim": ("claims", "statement", "record_id"),
    "note": ("notes", "text", "record_id"),
    "shift-note": ("shift_notes", "text", None),
}

#: KYO-29: the surfaces that hold a COPY of prose authored elsewhere, as opposed to
#: :data:`REDACTABLE`'s stores, which hold prose *as* the thing you name by id.
#:
#: This list existed as a bare tuple inside a function body until the brief case was
#: reported: the REDACTABLE table's property — a missing surface shows up as a kind
#: with no entry rather than as silence — is one this list did not itself have. Declared
#: here, next to the table it belongs beside, with
#: ``tests/test_prose_surfaces.py`` asserting every prose-bearing column on these
#: tables is either listed or explicitly exempted — so the next surface is a
#: failing test rather than a silent gap.
#:
#: The surfaces found so far: evidence artifacts, the event ledger, claims and shift
#: notes, source-code comments, frozen dispatch briefs. Every surface holding
#: controller-written prose is a new place an identifier can land, and the list grows
#: as the tool grows.
EMBEDDED_PROSE: tuple[tuple[str, str], ...] = (
    # A frozen brief renders the record's description, criteria and claim
    # statements into one string (brief.generate_brief).
    ("dispatches", "brief"),
    # A worker's structured report, ingested verbatim (report_ingest.cmd_report).
    ("reports", "body_json"),
)

#: Columns on the tables above that are NOT prose, listed so that adding one forces
#: a decision rather than defaulting to "nobody scrubs it". Keep the reason short
#: and true; a wrong exemption here is exactly the silence this pair exists to end.
EMBEDDED_PROSE_EXEMPT: dict[tuple[str, str], str] = {
    ("dispatches", "id"): "id",
    ("dispatches", "record_id"): "id",
    ("dispatches", "seq"): "integer",
    ("dispatches", "state"): "enum (contracts.DISPATCH_STATES)",
    ("dispatches", "worked_from_sha"): "a git sha",
    ("dispatches", "worktree"): (
        "a filesystem path, declared not quoted — and stored relative to the project "
        "root (KYO-52, contracts.store_path), so there is no absolute path in it for "
        "a redaction to have to reach"
    ),
    ("dispatches", "branch"): "a git branch name, declared not quoted",
    ("dispatches", "worker_model"): (
        "a model name, declared not quoted — resolved at dispatch time from "
        "kyosei/config.yml, the kind's policy, or `kyo dispatch --model` (KYO-61), "
        "none of which is a place a person's prose lands"
    ),
    ("dispatches", "touch_override_json"): (
        "override records: ids and the overlapping paths — with (KYO-65) the "
        "also-covered records on either side that put those paths in the claim — "
        "or (KYO-48) ids and the record that declared no touches, when --force "
        "dispatched past a disjointness question the tool could not answer"
    ),
    ("dispatches", "covers_json"): "a list of record ids",
    ("dispatches", "report_id"): "id",
    ("dispatches", "terminated_why"): (
        "an orchestrator-written reason — prose, but authored at closure rather than "
        "copied from anywhere, so it is redactable only as the event it lives in; "
        "flagged here rather than silently skipped"
    ),
    ("dispatches", "tick_audit_json"): (
        "KYO-375's tick counter: a count and two timestamps (first_seen, last_seen) the "
        "stop guard increments in place for a not-a-worker tick -- three numbers and "
        "nothing anybody wrote"
    ),
    ("dispatches", "closed_at"): "timestamp",
    ("dispatches", "created_at"): "timestamp",
    ("dispatches", "updated_at"): "timestamp",
    ("reports", "id"): "id",
    ("reports", "record_id"): "id",
    ("reports", "dispatch_id"): "id",
    ("reports", "seq"): "integer",
    ("reports", "worked_from_sha"): "a git sha",
    ("reports", "author"): "an actor name",
    ("reports", "created_at"): "timestamp",
}

CONTRACT_REDACTION_INCOMPLETE = "redaction-incomplete"

CONTRACT_DISPATCH_TOUCH_DISJOINTNESS = "dispatch-touch-disjointness"
#: KYO-22: touch-disjointness silently *implied* an isolation the tool never
#: arranged and could not observe. Two correctly-disjoint dispatches into one
#: checkout produced three distinct failures in a single consumer session: a
#: commit landed on the other dispatch's branch, a test failure was attributed to
#: the wrong tree, and the controller told its operator the workers were not
#: colliding when they were. This is the symmetric
#: refusal — the tool already knows how to say "not while that one is running".
CONTRACT_DISPATCH_WORKTREE_COLLISION = "dispatch-worktree-collision"
#: KYO-48: an UNDECLARED touch list is not an empty one. Disjointness is set
#: intersection and the empty set intersects nothing, so "I have not said what I
#: touch" and "I have said, and it is independent" used to produce the same
#: favourable answer — law 6 inverted, with absence borrowing the permissive
#: value. This is the refusal that says so instead, and `kyo next` withholds the
#: same records from its ready list for the same reason, off the same computation
#: (``commands.touch_standing``), so the two surfaces cannot disagree about what
#: is safe.
CONTRACT_DISPATCH_SCOPE_UNKNOWN = "dispatch-scope-unknown"
#: KYO-295, and the same asymmetry one class along. `kyo next` has withheld a record
#: whose criteria are present and all settled since KYO-87 ("finished, not moved"),
#: KYO-289 widened the class to records nobody dispatched, and `kyo dispatch` read
#: none of it — so one surface said a record had nothing left to build while the
#: other froze a brief for it. Refused off the listing's own function
#: (``cmd_next.settled_not_moved_entry``) for exactly the reason
#: :data:`CONTRACT_DISPATCH_SCOPE_UNKNOWN` is refused off ``touch_standing``: two
#: implementations of "is this dispatchable?" get to disagree, and one does not.
CONTRACT_DISPATCH_RECORD_FINISHED = "dispatch-record-finished"
#: KYO-570. ``kyo link A blocks B`` echoed "B cannot finish until A does" and nothing
#: enforced it: `kyo next`, the brief and `kyo dispatch` read no link, so a consumer's
#: orchestrator saw all 16 of its records listed ready and ordered them by hand. `kyo
#: dispatch` now refuses a record with an unsettled ``blocks`` source, off the same
#: function `kyo next` withholds it with (``cmd_next.unsettled_blockers``).
CONTRACT_DISPATCH_RECORD_BLOCKED = "dispatch-record-blocked"
CONTRACT_DISPATCH_MUST_BE_DISPATCHABLE = "dispatch-kind-must-be-dispatchable"
CONTRACT_DISPATCH_ALREADY_TERMINAL = "dispatch-already-terminal"
CONTRACT_DISPATCH_CLOSE_REQUIRES_REPORT = "dispatch-close-requires-report"
CONTRACT_DISPATCH_ALREADY_CLOSED = "dispatch-already-closed"
CONTRACT_REPORT_ID_MISMATCH = "report-id-mismatch"

#: KYO-198: ``kyo verify --from-report <dispatch-id>`` on a dispatch that carries no
#: ingested report. There is nothing to apply, and the remedy is the command that
#: creates something to apply — ``kyo report <dispatch-id> --file <report-path>``.
#: Refused rather than answered "applied 0 of 0", which is the shape of a success.
CONTRACT_VERIFY_FROM_REPORT_NEEDS_A_REPORT = "verify-from-report-needs-a-report"

#: KYO-198: ``--only`` named a criterion the report does not carry a verdict for.
#: A typo in a criterion id is the whole reason ``--only`` exists — the controller is
#: naming the subset they have checked — so an unrecognised one is refused with the
#: offender named, never silently applied-as-nothing.
CONTRACT_VERIFY_ONLY_NAMES_AN_ABSENT_CRITERION = "verify-only-names-an-absent-criterion"
#: KYO-32: a report's commits must be reachable from the branch its dispatch
#: declared. A consumer's worker created its own branch instead of the harness's;
#: the controller's merge targeted the (empty) declared branch, git said "Already
#: up to date" and exited 0, and nothing shipped — the success signal itself lied.
#: This turns that into a loud failure at report time, *before* the merge.
CONTRACT_REPORT_COMMIT_OFF_BRANCH = "report-commit-off-branch"

#: KYO-32, law 6: the three outcomes of the reachability check above, named once so
#: `kyo report`'s text output and its `--json` payload cannot drift apart (law 3).
#: ``CANNOT_TELL`` is a first-class third outcome, never folded into either of the
#: other two: git may be absent, the branch may not exist in the repo `kyo report`
#: runs in, or the sha may be an object this clone has never seen. "Unknown" must
#: never render as "reachable".
BRANCH_REACH_REACHABLE = "reachable"
BRANCH_REACH_OFF_BRANCH = "not-reachable"
BRANCH_REACH_CANNOT_TELL = "cannot-tell"
BRANCH_REACH_OUTCOMES: tuple[str, ...] = (
    BRANCH_REACH_REACHABLE,
    BRANCH_REACH_OFF_BRANCH,
    BRANCH_REACH_CANNOT_TELL,
)
#: KYO-48, law 6: the three outcomes of the touch-disjointness question, named
#: once so `kyo next`, `kyo brief` and `kyo dispatch` cannot drift apart (law 3).
#: ``SCOPE_UNKNOWN`` is a first-class third outcome for exactly the reason
#: :data:`BRANCH_REACH_CANNOT_TELL` is: the check could not run. "Unknown" must
#: never render as "disjoint" — which is what it did until KYO-48, in the one
#: place a controller reads precisely because they want something to dispatch.
TOUCH_DISJOINT = "disjoint"
TOUCH_COLLIDES = "collides"
TOUCH_SCOPE_UNKNOWN = "scope-unknown"


def scope_unknown_detail(entry: dict) -> str:
    """The one sentence both surfaces say about one unanswerable comparison
    (law 3): `kyo dispatch`'s refusal detail and `kyo next`'s reason for holding a
    record back are the same words, so a controller who has seen one recognises
    the other instead of reading them as two different problems.

    ``entry`` is one item from :func:`commands.touch_standing`'s unknown list.
    """
    who = " and ".join(entry["undeclared"])
    return (
        f"{entry['dispatch_id']} (dispatching {entry['record_id']}) is running and "
        f"{who} declares no touches, so disjointness cannot be computed — an "
        "undeclared scope is unknown, not empty"
    )


def collision_detail(entry: dict) -> str:
    """The one sentence a known overlap says (law 3), the sibling of
    :func:`scope_unknown_detail`.

    KYO-65.c2: when the overlap comes from a record one side merely *covers*, the
    sentence names that record. A controller who reads "KYO-43.d1 (dispatching
    KYO-43) shares src/kyosei/render.py with this dispatch" will open KYO-43,
    find no `render.py` in its touches, and conclude the tool is wrong — because
    the path is KYO-44's, and KYO-44 is only reachable through the dispatch's
    `--also-covers`. Naming it is what makes the refusal checkable by the person
    who has to decide whether to `--force` past it.

    ``entry`` is one item from :func:`commands.touch_standing`'s collision list.
    The ``--force`` remedy is deliberately NOT part of this: the same sentence is
    printed back after a forced dispatch to say what was overridden, and offering
    the remedy again there would name a step the controller has already taken.
    """
    text = (
        f"{entry['dispatch_id']} (dispatching {entry['record_id']}) shares "
        f"{', '.join(entry['paths'])} with this dispatch"
    )
    via = []
    if entry.get("via_running"):
        via.append(f"{', '.join(entry['via_running'])} (also covered by {entry['dispatch_id']})")
    if entry.get("via_candidate"):
        via.append(f"{', '.join(entry['via_candidate'])} (also covered by this dispatch)")
    if via:
        text += (
            " — the shared path(s) are declared by "
            + " and ".join(via)
            + ", so reading the primary record's own touches will not show the overlap"
        )
    return text


#: KYO-48. Named once and printed by both surfaces, and the *first* remedy adds
#: information rather than suppressing the question — `--force` is offered second
#: and on the record, because a controller who genuinely knows the work is
#: independent is the case this class exists to keep dispatchable.
SCOPE_UNKNOWN_REMEDY = (
    "declare the scope with `kyo edit <id> --touches-file FILE` (every record in the "
    "comparison needs one), or pass `kyo dispatch --force` to dispatch anyway and have "
    "the unchecked comparison recorded on the dispatch"
)
#: KYO-570: what clears the blocked class, printed by `kyo next`, the brief and the
#: `kyo dispatch` refusal alike.
BLOCKED_REMEDY = (
    "settle each blocker (move it to a complete or retired status), retract an edge that "
    "is wrong with `kyo unlink <link-id> --why TEXT`, or pass --force to `kyo dispatch` "
    "to dispatch anyway"
)
CONTRACT_SPLIT_REQUIRES_UNMET_CRITERIA = "split-requires-unmet-criteria"
#: KYO-58.c1. Criterion text is immutable **from its first verdict**, not from
#: creation. The hazard immutability exists for is a verdict attesting to text that
#: later changed — evidence silently re-attaching to a different claim — and a
#: criterion nobody has verified has no attestation to protect. This is the refusal
#: on the other side of that line, and it names `kyo restate` rather than ending at
#: "no" (law 6: every refusal names a remedy that works).
CONTRACT_CRITERION_TEXT_IS_ATTESTED = "criterion-text-is-attested"
#: KYO-58.c2. `kyo restate` refuses a criterion that is already void: its ``reason``
#: column holds the judgment that voided it, and a restatement would overwrite that
#: with its own backlink — destroying the only live record of why it was dropped.
#: A settled criterion needs no replacement anyway; a new claim is a new criterion.
CONTRACT_RESTATE_REQUIRES_A_LIVE_CRITERION = "restate-requires-a-live-criterion"
CONTRACT_FLAG_ALREADY_RESOLVED = "flag-already-resolved"
#: KYO-13.c5: the merge guard `kyo check-branch` makes mechanical — a branch must
#: never diff anything under `kyosei/` or `ideas/` (procedures.md, "never write to
#: the structured store directly": a worker's worktree holds no kyosei.db, so the
#: only way it could touch these paths is the export/kind-registry/procedures
#: files the store itself owns).
CONTRACT_BRANCH_TOUCHES_STORE = "branch-touches-store"

#: KYO-13.c5: the two directories a worker branch must never diff. Named once so
#: `kyo check-branch` and the `SubagentStop` hook template that invokes it agree
#: on exactly what "the store" means (law 3).
GUARDED_BRANCH_DIRS: tuple[str, ...] = ("kyosei/", "ideas/")

#: KYO-403: the exact paths, beside the directories above, a worker branch must never
#: add, delete or change. ``.kyosei`` is the split layout's store link — the one file
#: that tells every `kyo` in the code repository where the store is. A branch that
#: deleted or re-pointed it passed `check-branch` (only the two prefixes above were
#: guarded), and after it merged every guard in the code repository answered "nothing
#: to guard" with exit 0: the layout silently changed by a worker. Matched as an exact
#: path, never a prefix, so ``.kyosei-report.json`` (:data:`WORKER_REPORT_PATH`) and
#: anything else merely beginning with the name stays the worker's.
#:
#: Deliberately NOT excluded by `kyo check-clean`, unlike the directories: those are
#: excluded because the controller's own writes dirty them in a shared checkout, and
#: nothing the controller runs rewrites the link — so an uncommitted change to it is
#: the worker's, and a stop over it is one the worker should not take.
#:
#: The link's name is declared HERE and re-exported by :mod:`kyosei.config` (which
#: every reader of the link already imports it from), because the import runs that
#: way round: config reaches this module through schema and sweep, so a name this
#: tuple read back out of config would close a cycle.
STORE_LINK_NAME = ".kyosei"
GUARDED_BRANCH_FILES: tuple[str, ...] = (STORE_LINK_NAME,)

#: KYO-206: the one path a dispatched worker writes its structured report to,
#: relative to the root of the code repository it is working in.
#:
#: The brief used to say "write your structured report ... commit your work before you
#: stop" and name no path at all, and `kyo check-clean` counts any untracked file — so
#: the only way a worker could satisfy both sentences was to commit its report into the
#: tree. A consumer's first two merges carried `report.json` into main, the third
#: conflicted on it add/add, the fourth modify/delete, and the controller ended up
#: running `git rm --cached report.json` at every merge. `kyo touch-drift` then proposed
#: the same file as a touch, and the `kyo edit --touches-file` line it printed failed
#: validation once the controller had dropped the file.
#:
#: One name, so the fix can land on both sides at once: the brief states it, `kyo init`
#: and `kyo install-hooks` write it into the code repository's `.gitignore`,
#: :func:`kyosei.cmd_check_clean.cmd_check_clean` excludes it, touch drift never names it, and
#: `kyo report --file` still reads it because that flag reads whatever path it is given.
#: A dotfile deliberately: it sorts out of the way, it reads as tool state rather than
#: project content, and the `kyosei-` stem says which tool left it there.
WORKER_REPORT_PATH = ".kyosei-report.json"

#: KYO-17: the `SubagentStop` hook's *other* half. `kyo check-clean` reads the same
#: :data:`GUARDED_BRANCH_DIRS` rather than carrying its own copy of "the store",
#: because the two halves contradicted each other when they did not agree: in a
#: checkout the controller also runs `kyo` in, the controller's writes to
#: `kyosei/export/` dirty the tree, the dirty check's only available remedy is to
#: commit them, and committing them fails `check-branch` — while the worker is
#: separately forbidden to write there at all. One consumer's worker fired ten
#: times against that loop.
#: Deriving one check's exclusions from the other's prohibitions makes the pair
#: consistent by construction rather than by two people remembering (law 3).
CONTRACT_WORKTREE_HAS_UNCOMMITTED_WORK = "worktree-has-uncommitted-work"

#: KYO-60: the third thing a hook can refuse, and the only one aimed at an *agent's*
#: standing rather than its worktree hygiene. A push from a repository that carries
#: the store publishes every idea, description, shift note, evidence blob and frozen
#: brief the project has ever held — a decision that belongs to whoever owns that
#: prose, and never to an agent that happened to reach for `git push`. Blocking is
#: the whole mechanism: `kyo redact` explicitly cannot reach a commit that has
#: already been pushed, so there is no after-the-fact remedy to fall back on. The
#: human half of the same design *warns* instead (a git `pre-push` hook, which
#: `--no-verify` bypasses), because a human pushing their own repository owns that
#: call. Fires once per project: see ``publication_acknowledged`` in
#: :data:`kyosei.schema.CONFIG_CONSUMERS`.
CONTRACT_PUSH_PUBLISHES_THE_STORE = "push-publishes-the-store"

#: KYO-298: the fourth thing a hook can refuse, and the second aimed at an agent's
#: standing rather than its worktree hygiene. The whole suite is the CONTROLLER's to
#: run, once, after a wave merges — a worker that runs it inside its own worktree
#: spends ten to seventeen minutes producing a green that is about its branch before
#: every other worker's merge (which is nobody's question), contends with the
#: controller's own run for whatever exclusive resource the suite holds, and then
#: re-writes its entire context because the harness's prompt cache expired while it
#: waited. Measured on one wave in this project's own store: two of five workers did exactly
#: that, for 172,733 and 325,752 tokens of cache creation, 28% of everything that
#: wave's workers wrote, and both of the day's desktop failures were that contention.
#:
#: Which commands those are is the PROJECT's to declare (``controller_only_commands``
#: in :data:`kyosei.schema.CONFIG_CONSUMERS`); a project that declares none never
#: sees this refusal. The override is deliberately cheap and deliberately visible —
#: :data:`SUITE_OVERRIDE_ENV` in front of the command — because the standing stance
#: is that contradicting a rule must cost less than complying with one that is wrong,
#: and a refusal with no way past it buys silence rather than agreement.
CONTRACT_SUITE_IS_THE_CONTROLLERS = "suite-is-the-orchestrators"  # KYO-519

#: KYO-298.c3: the environment variable a worker prefixes a controller-only command
#: with to run it anyway. Named here rather than inside the guard because three
#: surfaces have to agree about it — the refusal that offers it, the matcher that
#: honours it, and the generated `config.yml` comment that describes it — and a
#: string three places spell separately is a string that will eventually be spelled
#: two ways (law 3).
#:
#: A PREFIX rather than an exported variable, and that is the mechanism rather than a
#: style choice: a `PreToolUse` hook is a fresh process that sees the harness's
#: environment, not the shell's, so an `export` in an earlier Bash call is invisible
#: to it — and, more to the point, invisible to whoever reads the transcript
#: afterwards. Prefixed, the override is in the command line the transcript records,
#: which is what makes "visible" true rather than claimed.
SUITE_OVERRIDE_ENV = "KYOSEI_SUITE_OK"

#: KYO-312.c2: the fifth thing a hook can refuse, and the first aimed at how LONG a
#: worker intends to wait rather than at what it intends to run. KYO-298 refuses the
#: whole suite because it is the controller's; this refuses any Bash call whose own
#: declared timeout is longer than the ceiling the project states, because a dispatched
#: worker has no way to be idle except a foreground command — so "waiting once" for a
#: long run IS a long foreground command, the harness's prompt cache expires in it, and
#: the worker's next turn re-writes its whole context at the cache-write price.
#:
#: Measured on this store's own KYO-306.d1, which had the prose rule in its brief and
#: walked straight through it: two waits of 10.1 and 6.1 minutes in single
#: ``until ...; do sleep 30; done`` loops, 250,466 and 255,899 tokens re-written between
#: them — more than every other re-write of that day combined. The arithmetic the rule
#: now carries, at a 250k context: a look costs a cache READ (about a tenth of the input
#: price, ~25k token-equivalents) and keeps the cache warm; one expiry costs a full cache
#: WRITE (1.25x, ~313k). Slices under the ceiling are about ten times cheaper than one
#: wait, for any run up to roughly fifty minutes.
#:
#: The ceiling is the PROJECT's to state and there is no default
#: (``worker_command_timeout_ceiling_seconds`` in :data:`kyosei.schema.CONFIG_CONSUMERS`),
#: for a reason this contract cannot decide for itself: the number is a property of the
#: consumer's harness, not of Kyosei. A consumer whose workers hold a one-hour prompt
#: cache does not want this refusal at all, and guessing five minutes at them would be
#: refusing commands that cost them nothing.
CONTRACT_LONG_COMMAND_OUTLIVES_THE_CACHE = "long-command-outlives-the-cache"

#: KYO-312.c2: the environment prefix a worker puts in front of a command line to make
#: an over-ceiling wait anyway. :data:`SUITE_OVERRIDE_ENV`'s design, for
#: :data:`SUITE_OVERRIDE_ENV`'s reasons — one word, in the command line the transcript
#: records, named in the refusal that offers it, and asked to be reported as a
#: disagreement.
#:
#: A SECOND name rather than a reuse of that one, deliberately. The two refusals are
#: different claims — "this run is not yours at all" and "this wait costs more than it
#: buys" — and a worker that overrode one has said nothing about the other, which is
#: exactly what a controller reading ``disagreements[]`` needs to be able to tell apart.
#: ``KYOSEI_SUITE_OK=1 sleep 900`` would also read as a lie about what is being asked
#: for.
LONG_COMMAND_OVERRIDE_ENV = "KYOSEI_LONG_OK"

#: KYO-299.c4: `kyo docs assemble` found a fragment it cannot assemble, and therefore
#: assembled NOTHING — not the fragment, and not the documents that had no problem.
#: All-or-nothing is the contract rather than a nicety: assembling half a wave consumes
#: numbers, deletes the fragments that carried them and leaves a controller
#: reconstructing which half happened. So the refusal is raised before the first write,
#: over the whole plan, and names every fragment that failed with what clears it.
CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED = "fragment-cannot-be-assembled"

#: KYO-299.c5: a worker's branch added a numbered section straight to a declared
#: append-only document instead of writing it as a fragment. Two workers who each write
#: "## 205." collide on the number, on the tail of the file, and on every index
#: generated from it — and they collide however their touches were declared, because a
#: touch narrows a CLAIM and does not move a byte. The refusal names the fragment path
#: the section belongs in, which is the whole remedy: move the section there, head it
#: with the placeholder, and the controller numbers it on the merged tree.
#:
#: Deliberately narrow: only an ADDED numbered heading. Editing an existing section is
#: an ordinary edit, serialized the ordinary way by a `path#section heading` touch, and
#: a guard that refused those would refuse the normal maintenance of the document it is
#: trying to protect.
CONTRACT_NUMBERED_SECTION_BELONGS_IN_A_FRAGMENT = "numbered-section-belongs-in-a-fragment"

#: KYO-499.c4: a worker's branch changed a document whose section status and links
#: disagree, or a `src/` file pointing at a superseded section without naming its
#: successor. The same rules `kyo docs check` runs (docs_check.SECTION_STATUS_REFUSALS),
#: run where the worker who made the mistake still holds the branch, rather than at the
#: controller's suite one merge later -- KYO-480's reasoning for fragment headers.
CONTRACT_SECTION_STATUS_DISAGREES = "section-status-disagrees"


@dataclass(frozen=True)
class CriterionView:
    """The slice of a criterion that transition contracts read.

    ``has_verdict`` (KYO-546) is whether any verification row was ever recorded against
    the criterion. The state alone cannot say: ``unmet-unbuilt`` is both the state a
    criterion is filed in and a verdict ``kyo verify`` records. ``False`` by default, and
    read only for an ``unmet-unbuilt`` criterion -- every other live state is reachable
    only through a verdict, so a caller that does not fill it in loses nothing for them.
    """

    id: str
    text: str
    state: str
    has_verdict: bool = False


@dataclass(frozen=True)
class DocsObligationView:
    """The slice of an open docs obligation that the transition contract reads.

    The same shape :class:`CriterionView` has and for the same reason: this module reads
    rows it does not open a database for, so what it needs is stated as a value type the
    caller fills in rather than as a ``sqlite3.Row`` it would have to know the schema of.
    """

    id: str
    doc_path: str
    section: str


@dataclass(frozen=True)
class GateView:
    """The slice of an open gate the settling contract reads (KYO-490).

    A value type for the reason :class:`DocsObligationView` is one: this module opens
    no database. ``detail`` is the gate's payload, which may span several lines; the
    refusal names only its first.
    """

    id: str
    gate_type: str
    detail: str | None


def gate_payload_first_line(detail: str | None) -> str:
    """The first non-blank line of a gate's payload, or a stated absence."""
    for line in (detail or "").splitlines():
        if line.strip():
            return line.strip()
    return "(no payload)"


def knowledge_disposal_remedy(record_id: str) -> str:
    """The one command that disposes of a knowledge record (KYO-44.c1).

    Named once, here, so the `kyo move` refusal, the `kyo wont-do` refusal and the
    `/kyosei` skill cannot each remember a different answer (law 3).
    """
    return (
        f"to retire it, run `kyo void {record_id} --why <reason>` (or `--why-file`) — that is "
        "this archetype's disposal path, and it is exactly what `kyo wont-do` is for a work "
        "record: the record reads `void` from then on and its id is never reused"
    )


# ------------------------------------ KYO-182: the clauses a stale build's refusals share
#
# Two refusals tell one reader the same three things about a build that is behind the
# artifact in front of it. ``store.open_store``'s ahead-direction refusal (KYO-168) is
# raised when a DATABASE was written by a later schema; ``rebuild``'s (KYO-179, extended by
# KYO-181) when an EXPORT was.
#
# **They stay two messages.** KYO-179's worker argued against collapsing them and the
# argument holds: one names a database and is raised at open, the other names an export
# directory and specific files and says that nothing was written — and which artifact is
# meant is the thing a reader in the desktop app most needs to know. One parameterised
# message for both sites would produce a sentence vague about exactly that. What moves here
# is only the three clauses they already stated verbatim in two files, with no shared
# source.
#
# The third is why this was worth doing rather than tidy. It was written for KYO-175's
# desktop reader, who is not at a shell and may not be the person who can upgrade anything;
# it is the most consequential sentence in either message, and the one most likely to be
# improved in one file and left stale in the other. ``knowledge_disposal_remedy`` above is
# the precedent: text two surfaces must agree on is named once, here (law 3).
#
# ``tests/test_kyo5_error_remedies.py`` holds both halves down — that each message still
# contains all three clauses AS THIS MODULE RENDERS THEM, and that neither file states them
# literally, so a site cannot quietly stop using the shared source while still reading
# correctly.


def stale_build_clause(*, artifact: str, cannot: str) -> str:
    """"This build is the stale side of the pair", for one artifact class.

    ``artifact`` is the noun the reader is holding (``"store"``, ``"export"``) and
    ``cannot`` is what this build therefore cannot do with it (``"open a store"``,
    ``"read an export"``). Both are parameters rather than one generic word for the reason
    the block above gives: the refusals differ in which artifact they are about, and that
    difference is the useful part.
    """
    return (
        f"The {artifact} is intact and there is nothing in it to repair: this build is "
        f"the stale side of the pair, and it cannot {cannot} written by a later schema "
        "because a build has no way to know what a later schema added."
    )


def upgrade_the_build_clause(version: int) -> str:
    """The only remedy either refusal names, and deliberately not a command.

    Every command that would make the later-schema artifact readable by this build writes
    the older schema over the newer one, which is the harm rather than the fix (KYO-168,
    KYO-179). So the remedy is an installation, and both sites name the same one.
    """
    return (
        "Bring the software up to the store, not the store down to the software: install "
        f"a kyosei that speaks schema_version {version} or later."
    )


def installed_build_clause() -> str:
    """The sentence written for a reader who did not install this build (KYO-168, KYO-175).

    It used to say the build was "bundled with the application you are running" and that
    upgrading it meant updating that application. The app's bundled runtime was retired,
    and KYO-450 retired the install kind: the desktop app runs the machine's
    one `kyo`, the same copy every project's hooks run, so a reader in the app is looking
    at an install somebody made rather than a part of the app (KYO-466). Which route
    moves that install is :func:`kyosei.buildinfo.upgrade_route`'s answer for the copy
    raising the refusal -- asked, not restated, so this sentence cannot name a route the
    copy does not take. No trailing full stop: both sites follow it with an em dash and
    the record id.
    """
    from .buildinfo import upgrade_route

    return (
        "If you did not install this kyosei yourself, it is the one on this machine that "
        "the desktop app and every project's hooks run -- the app carries no copy of its "
        "own -- and upgrading it is for whoever installed it: " + upgrade_route()
    )


def check_archetype_allows_status_flow(
    record_id: str,
    kind: str,
    archetype: str,
    ladder: StatusLadder,
    *,
    also: str | None = None,
) -> None:
    """Refuse ``kyo move``/``wont-do`` on a ``knowledge``-archetype record.

    ``docs/design/charter.md``, "The kind registry": ``archetype: knowledge`` "has
    claims with verdict histories and ``amended_by`` machinery ... no status flow".

    KYO-44.c1: the refusal used to end there, and the consequence was that a knowledge
    record filed in error had no disposal path at all — it could not be retired, the
    charter forbids deleting it, and the workflow's own standing instruction ("if a
    record's premise died entirely, use `kyo wont-do <id> --why` — never leave a
    dead-premise record open") was unfollowable for half the registry. Every error
    naming a remedy is supposed to name one that works, and this one named none. It
    now redirects to `kyo void`, which is a disposal, not a flow step: the ladder
    (``ladder.flow`` — this project's own, KYO-101) stays work-only, which is what
    "no status flow" was protecting.
    """
    if archetype != "work":
        raise ContractViolation(
            CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW,
            [
                f"{record_id} is kind {kind!r} (archetype: {archetype}); {archetype}-archetype "
                f"records have no status flow — {ladder.flow} is the work "
                "archetype's ladder, and a knowledge record's lifecycle is amendment "
                f"(`kyo edit {record_id} --description-file <f>`) or retirement instead",
                also or knowledge_disposal_remedy(record_id),
            ],
        )


def check_void_is_knowledge_only(record_id: str, kind: str, archetype: str) -> None:
    """The symmetric half of the above: `kyo void` refuses a ``work`` record.

    A work record's terminal is ``wont-do`` and it carries the whole status flow's
    meaning ("we decided not to do this work"); ``void`` says something else ("this
    is not to be relied on"). Two words for two things, and neither refusal is a
    dead end (law 6).
    """
    if archetype != "knowledge":
        raise ContractViolation(
            CONTRACT_VOID_IS_KNOWLEDGE_ONLY,
            [
                f"{record_id} is kind {kind!r} (archetype: {archetype}); `kyo void` is the "
                "knowledge archetype's disposal path",
                f"to retire it, run `kyo wont-do {record_id} --why <reason>` — a work record "
                "has a status flow, and `wont-do` is its terminal",
            ],
        )


def check_transition(
    *,
    record_id: str,
    kind: str,
    from_status: str,
    to_status: str,
    criteria: list[CriterionView],
    reason: str | None,
    requires_criteria: bool,
    ladder: StatusLadder,
    open_docs_obligations: list[DocsObligationView],
    unapplied_met: dict[str, list[str]] | None = None,
    open_gates: list[GateView] | None = None,
    waiting_reports: list[str] | None = None,
) -> None:
    """Raise :class:`ContractViolation` if this transition is not permitted.

    ``waiting_reports`` (KYO-546.c3) are the dispatches whose ingested report holds a
    verdict, never applied, for a criterion that has none recorded -- found by the walk
    KYO-478's ``unapplied_met`` is (``cmd_records._unapplied_report_verdicts``). Read
    only for a move into the verified role, where a criterion with no verdict names them
    as the remedy (``kyo accept``); ``None`` or empty names ``kyo verify`` alone.

    ``open_gates`` (KYO-490) are the record's uncleared gates. The caller reads them
    only for a move into a settling status and passes ``None`` when ``--force`` was
    given, so a forced settle and a non-settling move both reach this with nothing to
    check — the same shape ``unapplied_met`` has.

    ``unapplied_met`` (KYO-478) maps a criterion id to the dispatches whose ingested,
    current report holds a ``met`` verdict for it that was never applied. This module
    sees only criteria, so the caller works that out (``cmd_records`` does, through
    the same derivation ``kyo verify --from-report`` skips an applied verdict by) and
    the settled-criteria refusal names those reports as the remedy instead of
    ``kyo split``, which is the wrong one for a criterion a worker already met.

    Called from inside the write transaction, never from a validator run later.
    ``requires_criteria`` comes from the kind's registry entry (``policy.requires_criteria``
    for ``work`` kinds, always ``False`` for ``knowledge`` kinds) — milestone 2 replaces the
    old hardcoded ``KINDS_WITHOUT_CRITERIA`` set with this per-kind policy lookup.

    KYO-101: every test below is against ``ladder``'s **role** for a status, never
    against a status name written into this module. That is the whole of what "contracts
    key off roles" buys — rename the completion status to ``shipped`` and the settled-
    criteria and met-criterion contracts still refuse an unqualified move into it, under
    the new name, with the new name in the message.
    """
    to_role = ladder.role(to_status)
    if to_role is None:
        raise ContractViolation(
            "status-must-be-known",
            [
                f"{to_status!r} is not a status; {ladder.registry.path} declares: "
                f"{ladder.listing()}"
            ],
        )

    if ladder.is_terminal(from_status):
        raise ContractViolation(
            CONTRACT_TERMINAL_IS_TERMINAL,
            [
                f"{record_id} is {from_status}, which is terminal; "
                f"its id is retired and cannot move to {to_status}"
            ],
        )

    if to_role == ROLE_RETIRED and not (reason or "").strip():
        raise ContractViolation(
            CONTRACT_RETIRED_REQUIRES_REASON,
            [f"{record_id} needs --why (or --why-file) to enter {to_status}"],
        )

    if to_role == ROLE_ACTIVE and requires_criteria and not criteria:
        raise ContractViolation(
            CONTRACT_ACTIVE_REQUIRES_CRITERIA,
            [
                f"{record_id} (kind {kind}) has no criteria; kind {kind!r}'s "
                "policy.requires_criteria is true in kyosei/kinds.yml"
            ],
        )

    if to_role == ROLE_COMPLETE and requires_criteria:
        unsettled = [c for c in criteria if c.state not in SETTLED_CRITERION_STATES]
        if unsettled:
            # KYO-26: this is the exact moment `kyo split`
            # exists for, and the message did not mention it. A consumer
            # hand-rolled the split twice in one day — void-withdraw plus a
            # replacement criterion — without reaching for the command built to do
            # it. `split` appears in the quick-start list framed as a dispatch-loop
            # command, and nothing at the moment of friction suggested it.
            #
            # KYO-478: a criterion an ingested report already met, unapplied, is not
            # one to carry to a successor — the verdict exists and only needs
            # applying. Those are named by report, with the command that applies
            # them; `kyo split` stays the remedy for the criteria no report covers,
            # and is not offered at all when there are none.
            covering: dict[str, list[str]] = {}
            for c in unsettled:
                for dispatch_id in (unapplied_met or {}).get(c.id, []):
                    covering.setdefault(dispatch_id, []).append(c.id)
            uncovered = [
                c for c in unsettled if not (unapplied_met or {}).get(c.id)
            ]
            # The message stays inline in the raise: the remedy surface walk
            # (tests/test_kyo5_error_remedies.py) reads what a site names from the
            # raise expression itself.
            raise ContractViolation(
                CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA,
                [
                    *(f"{c.id} is {c.state}: {c.text}" for c in unsettled),
                    *(
                        f"dispatch {dispatch_id}'s ingested report holds an unapplied "
                        f"`met` for {', '.join(ids)}: `kyo verify --from-report "
                        f"{dispatch_id} --only {' '.join(ids)}` applies "
                        f"{'them' if len(ids) > 1 else 'it'} (or kyo accept "
                        f"{dispatch_id}, which applies the whole report)"
                        for dispatch_id, ids in covering.items()
                    ),
                    *(
                        [
                            f"`kyo split {record_id} --to-successor "
                            f"{' '.join(c.id for c in uncovered)}` carries "
                            f"{'them' if len(uncovered) > 1 else 'it'} to a successor "
                            "record, back-linked both ways, and leaves this one settled "
                            "— that is what it is for. If the work really is finished, "
                            f"verify or withdraw {'each' if len(uncovered) > 1 else 'it'} "
                            "instead."
                        ]
                        if uncovered
                        else []
                    ),
                ],
            )
        met = [c for c in criteria if c.state == "met"]
        if not met:
            raise ContractViolation(
                CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION,
                [
                    f"{record_id} (kind {kind}) has no criterion in state 'met' "
                    f"({len(criteria)} total, all void or absent) — {to_status!r} would "
                    "mean 'all criteria met', which is vacuously true at zero met "
                    "criteria; if nothing was actually established here, "
                    f"{ladder.retired!r} (`kyo wont-do`) is the honest state (kind's "
                    "policy.requires_criteria is true)"
                ],
            )

    # KYO-137.c5, and it is checked LAST of the completion contracts on purpose. The two
    # above ask whether the work is finished; this one asks whether what the work changed
    # is still described correctly, which is only a question worth putting once the
    # answer to the first is yes. Unlike them it does not consult `requires_criteria`:
    # whether a kind carries criteria says nothing about whether its work moved a file
    # some document speaks for.
    if to_role == ROLE_COMPLETE and open_docs_obligations:
        raise ContractViolation(
            CONTRACT_COMPLETE_REQUIRES_DOCS_OBLIGATIONS_SETTLED,
            [
                *(
                    f"{o.id} is open: {o.doc_path} [{o.section}]"
                    for o in open_docs_obligations
                ),
                f"`kyo docs settle <id> --verdict {OBLIGATION_UPDATED} --method "
                f"<how>` records the verdict that clears "
                f"{'each' if len(open_docs_obligations) > 1 else 'it'} — or --verdict "
                f"{OBLIGATION_NOT_AFFECTED}, if the section was read and needs no "
                "change. A document counts as updated when you say it is, with a "
                "method, not when its timestamp moves: editing "
                "the file is not by itself what settles this.",
            ],
        )

    # KYO-413.c1. Last in the function only so the raises above keep their ordinals
    # (tests/test_kyo5_error_remedies.py keys its remedy table on them); the roles are
    # disjoint from every check above, so the order changes no outcome.
    if (
        to_role in (ROLE_NEEDS_VERIFICATION, ROLE_VERIFIED)
        and requires_criteria
        and not criteria
    ):
        raise ContractViolation(
            CONTRACT_VERIFICATION_REQUIRES_CRITERIA,
            [
                f"{record_id} (kind {kind}) has no criteria, so entering {to_status} "
                f"(role {to_role}) would put a record with nothing to verify in "
                "the awaiting-verification queue; kind "
                f"{kind!r}'s policy.requires_criteria is true in kyosei/kinds.yml",
                f"add at least one criterion first — `kyo edit {record_id} "
                "--add-criteria-file <file>` (one criterion per line, or '-' for "
                "stdin) — then repeat the move",
            ],
        )

    # KYO-490. Last in the function for the same reason KYO-413.c1's check is: the
    # remedy table in tests/test_kyo5_error_remedies.py keys on raise ordinals, so a new
    # raise goes after every existing one. Checked after the other settling contracts
    # on purpose too — whether the work is finished is the first question, and an open
    # gate is only worth naming once nothing else stands in the way.
    if to_role in SETTLED_ROLES and open_gates:
        # The message stays inline in the raise: the remedy surface walk reads what a
        # site names from the raise expression itself.
        raise ContractViolation(
            CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES,
            [
                *(
                    f"{g.id} ({g.gate_type}) is open: "
                    f"{gate_payload_first_line(g.detail)} — `kyo ungate {g.id} --why "
                    "<reason>` clears it once what it waits on has happened"
                    for g in open_gates
                ),
                f"a record settled with an open gate is not finished (KYO-490): clear "
                f"{'each gate' if len(open_gates) > 1 else 'the gate'} first, or repeat "
                f"this command with --force to enter {to_status} and leave "
                f"{'them' if len(open_gates) > 1 else 'it'} open",
            ],
        )

    # KYO-546. Last in the function for KYO-413.c1's and KYO-490's reason: the remedy
    # table in tests/test_kyo5_error_remedies.py keys on raise ordinals. The verified
    # role is disjoint from the settling roles above and KYO-413.c1's check has already
    # refused a record with no criteria at all, so the order changes no outcome. A void
    # criterion is out of the question; every live state but the filing one is reachable
    # only through a verdict, so only an `unmet-unbuilt` criterion is asked for one.
    if to_role == ROLE_VERIFIED and requires_criteria:
        unverdicted = [
            c
            for c in criteria
            if c.state == DEFAULT_CRITERION_STATE and not c.has_verdict
        ]
        if unverdicted:
            # The message stays inline in the raise: the remedy surface walk reads what a
            # site names from the raise expression itself.
            raise ContractViolation(
                CONTRACT_VERIFIED_REQUIRES_A_VERDICT,
                [
                    f"{record_id} cannot enter {to_status} (role {to_role}: "
                    f"{ROLE_MEANINGS[ROLE_VERIFIED]}) while "
                    f"{'these criteria have' if len(unverdicted) > 1 else 'this criterion has'}"
                    " no recorded verdict:",
                    *(f"{c.id} is {c.state}, never verified: {c.text}" for c in unverdicted),
                    *(
                        f"dispatch {dispatch_id}'s ingested report holds an unapplied "
                        f"verdict for it: `kyo accept {dispatch_id}` records the report's "
                        "verdicts"
                        for dispatch_id in (waiting_reports or [])
                    ),
                    *(
                        f"`kyo verify {c.id} --verdict <verdict> --method <how>` records "
                        "one by hand"
                        for c in unverdicted
                    ),
                    "then repeat the move",
                ],
            )


def check_carried_settle_why(
    *, record_id: str, kind: str, to_status: str, reason: str | None, ladder: StatusLadder
) -> None:
    """KYO-497: a carried record settles with a reason (``carried-settle-requires-reason``).

    The caller asks only for a kind whose ``policy.carried`` is true. The ``retired`` role
    is left to :func:`check_transition`'s ``retired-requires-reason``, which already says
    the same thing for every kind; this binds the ``complete`` role, which no other kind
    asks a reason for.
    """
    if ladder.role(to_status) == ROLE_COMPLETE and not (reason or "").strip():
        raise ContractViolation(
            CONTRACT_CARRIED_SETTLE_REQUIRES_REASON,
            [
                f"{record_id} is kind {kind!r}, a carried kind (policy.carried in "
                f"kyosei/kinds.yml): entering {to_status} needs --why (or --why-file), "
                "the record of how the item settled",
            ],
        )


def check_plan_close_over_open_members(
    record_id: str,
    members: Iterable[Mapping[str, str]],
    ladder: StatusLadder,
    *,
    force: bool,
) -> list[Mapping[str, str]]:
    """KYO-308.c1: refuse `kyo move <plan> <a completion status>` over unsettled
    members unless ``force`` says otherwise; return the unsettled subset either way,
    so the caller can print the distribution and — when ``force`` let it through —
    record which members it closed over.

    "Unsettled" is read off the ladder's own roles (:meth:`StatusLadder.is_settled`),
    the same way every other completion contract reads a status fact — never a
    literal status name, so a project that renames its ladder keeps this working
    under the new names.

    A knowledge-archetype member gets no special case, and none is needed: `kyo void`
    is its only way out of the ladder's ``initial`` role
    (:func:`check_archetype_allows_status_flow` refuses every other move on it), so
    its stored status is always either ``initial`` (unsettled — nothing disposed of
    it) or ``retired`` (settled — it was voided). ``is_settled`` already reads that
    correctly with no archetype lookup at all; a work member's ``complete`` status is
    simply a second way to reach "settled" that a knowledge member's ladder has no
    rung for.
    """
    unsettled = [m for m in members if not ladder.is_settled(m["status"])]
    if unsettled and not force:
        plural = len(unsettled) != 1
        raise ContractViolation(
            CONTRACT_COMPLETE_REQUIRES_SETTLED_PLAN_MEMBERS,
            [
                f"{record_id} has {len(unsettled)} unsettled member(s): "
                + ", ".join(f"{m['id']} ({m['status']})" for m in unsettled),
                f"settle {'them' if plural else 'it'} first, or detach "
                f"{'them' if plural else 'it'} from the plan with `kyo edit <MEMBER-ID> "
                "--no-plan` — or pass --force to close the plan anyway, which records "
                "that it was closed over open members",
            ],
        )
    return unsettled


def check_criterion_state(criterion_id: str, state: str, reason: str | None) -> None:
    """Raise if a criterion state is unknown, or lacks a required reason (law 10)."""
    if state not in CRITERION_STATES:
        raise ContractViolation(
            "criterion-state-must-be-known",
            [f"{state!r} is not a criterion state; known: {', '.join(CRITERION_STATES)}"],
        )
    if state in CRITERION_STATES_REQUIRING_REASON and not (reason or "").strip():
        raise ContractViolation(
            "criterion-state-requires-reason",
            [f"{criterion_id} entering {state} needs --why (or --why-file)"],
        )


# --------------------------------------------------------------- KYO-333: length

#: `kyo dispatch` already warns, never refuses, when a frozen brief's task prose
#: carries a bare count or duration copied from a doc instead of measured
#: (`cmd_dispatch._measurement_warnings`) — same instrument, same law ("flags, never
#: enforces"), reused here for the two places a record's own authored prose runs
#: long: its title, and a criterion that bundles more than one separately-checkable
#: claim into one sentence a worker can answer with only one verdict.
#:
#: Measured on one day's filings in this project's own store: titles filed that day
#: averaged 115 characters (read in a list, a title that long needs the view expanded or
#: a tooltip read); criteria filed that day averaged 452 characters against 217
#: store-wide. KYO-329's own records — this plan's own, written to be short, the
#: target shape the brief asks for — measure well under both, by hand from
#: `kyo list --plan KYO-329` (names KYO-329..KYO-333) and `kyo show <id> --json` on
#: each: titles up to 51 characters (avg 43), criteria up to 213 characters (avg
#: 136). TITLE_LENGTH_WARN sits above that ceiling and well short of the day's bad
#: average; CRITERION_LENGTH_WARN is the store-wide baseline itself — already
#: established, and already clearing every one of KYO-329's own criteria (max 213).
TITLE_LENGTH_WARN = 80
CRITERION_LENGTH_WARN = 217


def title_length_warning(title: str) -> str | None:
    """``None`` when ``title`` is within :data:`TITLE_LENGTH_WARN`; otherwise the
    warning line a caller prints and carries in ``--json`` (KYO-333.c1).

    Warns, never refuses: the title is stored exactly as given either way (charter:
    "flags, never enforces"), on the same terms as `kyo dispatch`'s bare-measurement
    warning this reuses the shape of.
    """
    length = len(title)
    if length <= TITLE_LENGTH_WARN:
        return None
    return (
        f"title is {length} characters (over {TITLE_LENGTH_WARN}) — long enough that "
        "a list view has to be expanded, or a tooltip read, to see it whole; consider "
        "a shorter one"
    )


#: A code span may legitimately hold a semicolon of its own (a shell command, an
#: already-punctuated phrase quoted verbatim); it is stripped before clauses are
#: counted so a backticked example never manufactures a bundling warning.
_BACKTICK_SPAN_RE = re.compile(r"`[^`]*`")


def _clause_count(text: str) -> int:
    """How many semicolon-separated clauses ``text`` reads as, ignoring any
    semicolon inside a backtick-quoted span."""
    return _BACKTICK_SPAN_RE.sub("", text).count(";") + 1


def criterion_length_warning(text: str) -> str | None:
    """``None`` when ``text`` is short and reads as one claim; otherwise the warning
    line a caller prints and carries in ``--json`` (KYO-333.c2).

    A long criterion and a multi-clause one are two symptoms of the same defect —
    several separately-checkable claims collapsed into one verdict — so both are
    named when both are true, and either alone is enough to warn. Warns, never
    refuses: the text is stored exactly as given either way.
    """
    length = len(text)
    clauses = _clause_count(text)
    reasons = []
    if length > CRITERION_LENGTH_WARN:
        reasons.append(f"{length} characters (over {CRITERION_LENGTH_WARN})")
    if clauses > 1:
        reasons.append(f"{clauses} semicolon-separated clauses")
    if not reasons:
        return None
    return (
        f"criterion is {' and '.join(reasons)} — likely bundles more than one "
        "separately-checkable claim into a single verdict; consider one claim per "
        "criterion"
    )

#: KYO-555.c5: a worker's branch newly introduced a block-grade history phrase ("used
#: to", "previously") into a document of an ``ssot:`` set declaring ``history_lint:
#: block``, and no commit on the branch carries a ``Kyosei-History-OK`` trailer naming
#: that file and pattern. An ssot document states what is true now; how it came to be
#: belongs in a decision record or the commit message (kyosei.docs_history).
CONTRACT_HISTORY_PROSE_INTRODUCED = "history-prose-introduced"
