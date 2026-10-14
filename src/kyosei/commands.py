"""The shared command runtime, and the three verbs that bootstrap a project.

Every ``kyo`` subcommand is a ``(Context, Namespace) -> Output`` function. The
implementations used to live here as well — all thirty-six of them, 3,846
lines grouped by "is a CLI command", which is the one axis along which every
record that touches any of them touches all of them. KYO-57 split them by
subject into ``cmd_*.py``; what stays here is what they all stand on:

* :class:`Context` and :class:`Output` — one invocation's environment, and what
  a command hands back to :mod:`kyosei.cli`;
* the mutation plumbing every write shares (see the shape below);
* ``init`` / ``install-hooks`` / ``install-skill``, the three verbs that set a
  project up rather than operating on anything inside it.

Shape shared by every mutating command:

1. open one transaction;
2. snapshot the affected record(s) *before* the change;
3. mutate, enforcing contracts inside the transaction (law 7);
4. append an ``events`` row with before/after snapshots;
5. commit, then regenerate the deterministic export for every touched record;
6. echo back exactly what was stored (law 5).

Steps 4-6 are not optional anywhere: ``tests/test_events.py`` asserts that every
mutating subcommand writes an event and rewrites its export.

Subject modules call :func:`_commit_and_export` and :func:`_finish_mutation`
through this module (``commands._commit_and_export(...)``) rather than importing
them by name. That pair is the transaction seam, and a test substitutes it to
prove a half-written mutation rolls back (``tests/test_promote.py``); a
by-name import in each subject module would put the seam beyond the reach of a
single patch.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from argparse import Namespace
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import contracts, events_ledger, export, ids, install_prose, kinds, ledgers
from . import install_procedures as install_procedures_module
from . import install_skill as install_skill_module
from . import procedures as procedures_module
from . import scaffold as scaffold_module
from .clock import now
from .config import (
    KYOSEI_DIR,
    LAYOUT_UNCONFIGURED,
    Layout,
    resolve_layout,
    resolve_worktree_path,
    store_worktree_path,
    worktree_home,
)
from .config import project_home_name as config_project_home_name
from .contracts import (
    CRITERION_STATES,
    DEFAULT_CRITERION_STATE,
    TOUCH_COLLIDES,
    TOUCH_DISJOINT,
    TOUCH_SCOPE_UNKNOWN,
    CriterionView,
    DocsObligationView,
)
from .errors import ContractViolation, KyoseiError, NotFound, UsageError
from .install_hooks import install_hooks
from .prose import (
    check_not_shell_mangled,
    read_prose,
    single_line,
)
from .render import echo_value
from .store import Store, open_store

KIND_RE = kinds.KIND_NAME_RE


#: KYO-519: what a new write records as its actor when neither ``--actor``
#: nor ``$KYOSEI_ACTOR`` names one. It was ``"controller"`` before the rename, and stored
#: rows keep that value forever -- they record what the actor was called when it acted,
#: and nothing compares actor values -- so a query that means "the
#: orchestrator" has to match both.
DEFAULT_ACTOR = "orchestrator"

#: A command's relationship to a project, declared once per command in
#: :data:`kyosei.cli.COMMAND_TABLE` and consumed by :func:`build_context`. This
#: was a bool ("needs a project") until KYO-54, which added a third honest
#: answer: the worktree guards want the project *if there is one* and want to
#: say so plainly if there is not, rather than either failing obscurely in a
#: repository that never configured a store or passing as though they had
#: checked something.
PROJECT_REQUIRED = "required"
PROJECT_NONE = "none"
PROJECT_OPTIONAL = "optional"


@dataclass
class Output:
    """What a command produces: human lines and the ``--json`` document."""

    lines: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class Context:
    """One invocation's environment: where the project is, and who is acting.

    ``root`` is the *store* root — where records live — and stays the answer to
    every question about the store. KYO-54 adds :attr:`layout`, which also knows
    where the *code* is: the two are the same directory under the in-repo layout
    and different directories under the split one, so a call site that means
    "the git repository the work happens in" must say :attr:`code_root` rather
    than ``root``, or it will read git in the wrong place with total confidence.
    """

    root: Path
    actor: str
    argv: list[str]
    layout: Layout | None = None
    environ: dict[str, str] = field(default_factory=dict, repr=False)
    #: KYO-453: open the store read-only (`kyo serve`, through ``cli.run``).
    read_only: bool = False
    _store: Store | None = field(default=None, repr=False)

    @property
    def code_root(self) -> Path:
        """The code repository: git queries, ``touches``, ``.claude/`` settings."""
        return self.layout.code_root if self.layout is not None else self.root

    @property
    def worktree_project_name(self) -> str:
        """This project's name under ``$KYOSEI_HOME`` (KYO-70).

        A third "where" alongside :attr:`root` and :attr:`code_root`: not where
        records live and not where code lives, but where *this tool's own working
        directories* live. Kept on the Context for the same reason the other two
        are — a call site that computed it itself would answer confidently for the
        wrong layout.
        """
        if self.layout is None:
            return self.root.name
        return config_project_home_name(self.layout)

    def store_worktree(self, path: Path | str) -> str:
        """The value to store for a worktree path, under this project's rules."""
        return store_worktree_path(
            self.code_root, self.worktree_project_name, path, self.environ
        )

    def resolve_worktree(self, stored: str) -> Path:
        """Where a stored worktree value points on this machine."""
        return resolve_worktree_path(
            self.code_root, self.worktree_project_name, stored, self.environ
        )

    def store(self) -> Store:
        if self._store is None:
            # KYO-342: whether this store was reached through a main checkout is the
            # layout's answer, carried to the one thing that acts on it —
            # `Store.transaction`, which refuses a write to another working tree's
            # store. A Context with no layout is the pre-KYO-54 shape and has nothing
            # to say about it.
            self._store = open_store(
                self.root,
                code_root=self.code_root,
                store_via_main_checkout=(
                    None if self.layout is None else self.layout.store_via_main_checkout
                ),
                read_only=self.read_only,
            )
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None


# -------------------------------------------------------------- shared helpers


def resolve_actor(cli_actor: str | None, environ: dict[str, str]) -> str:
    """``--actor`` beats ``KYOSEI_ACTOR`` beats ``orchestrator`` (KYO-519)."""
    for candidate in (cli_actor, environ.get("KYOSEI_ACTOR")):
        if candidate and candidate.strip():
            return candidate.strip()
    return DEFAULT_ACTOR


def _reason(args: Namespace) -> str | None:
    """Resolve ``--why`` / ``--why-file``.

    Whether a reason is *required* is a contract question, answered at the
    transition (law 7), never here.

    KYO-91: the shell-residue check below is now also run for *every* prose-argument
    flag by :func:`kyosei.cli.check_prose_arguments`, before the store is even opened
    — which is what reaches ``--do``, ``--title`` and ``--method``, none of which pass
    through this function. The call is kept because it is not reached only from
    ``cli.main``: anything embedding these handlers with a hand-built
    :class:`argparse.Namespace` skips the parser walk entirely, and a ``--why`` that
    lost a word is exactly the case that must not depend on how it was invoked.
    """
    why = getattr(args, "why", None)
    why_file = getattr(args, "why_file", None)
    if why and why_file:
        raise UsageError("pass --why or --why-file, not both")
    if why_file:
        return read_prose(why_file, what="--why-file")
    if why:
        check_not_shell_mangled(why, what="--why")
        return single_line(why, what="--why")
    return None


def _require_record(store: Store, record_id: str) -> None:
    store.record(record_id)


#: KYO-237: `kyo new --plan` and `kyo edit --plan` reached the `plan_id` FOREIGN
#: KEY with whatever the caller typed, unchecked in the empty-string case (`if
#: args.plan:` is false for `''`, so `_require_record` was never called) and
#: unchecked for kind in every case (a record that exists but is not a `plan`
#: never failed at all). Named on the same principle as
#: `CONTRACT_KIND_NOT_DIRECTLY_FILEABLE` beside it and given `ContractViolation`'s
#: exit 2 rather than a bare `KyoseiError`'s exit 1, on KYO-68.c3's reasoning: this
#: is a creation/edit verb declining a reference the caller gave it, the same
#: shape of refusal as the kind-registry ones, and a scripted caller should not
#: need to branch on which of two exit codes one shape of problem comes back as.
CONTRACT_PLAN_ID_INVALID = "plan-id-invalid"


def _require_plan(store: Store, plan_id: str) -> None:
    """Validate a ``--plan`` id before any write, with a distinct sentence for each
    of the three ways it can be wrong (law 6):

    1. empty (``kyo new --plan ''`` used to reach the FOREIGN KEY unchecked and
       come back as a raw ``sqlite3.IntegrityError`` — a trace naming no id and no
       remedy, for a transaction that had already rolled back by the time anyone
       read it, KYO-237);
    2. an id no record has;
    3. an id whose record exists but is not a ``plan`` — nothing downstream
       (``kyo show``, ``kyo list --plan``) is wrong about that record existing,
       only about treating it as a plan.

    Called before the first write inside the transaction that would otherwise
    write around it (``kyo new``'s INSERT) or into it directly (``kyo edit``'s
    UPDATE).
    """
    if not plan_id:
        raise ContractViolation(
            CONTRACT_PLAN_ID_INVALID,
            [f"--plan {plan_id!r} is empty; a plan id is required"],
        )
    row = store.conn.execute(
        "SELECT kind FROM records WHERE id = ?", (plan_id,)
    ).fetchone()
    if row is None:
        raise ContractViolation(
            CONTRACT_PLAN_ID_INVALID,
            [f"no record {plan_id!r} exists to be a plan"],
        )
    if row["kind"] != "plan":
        raise ContractViolation(
            CONTRACT_PLAN_ID_INVALID,
            [f"{plan_id!r} is a {row['kind']!r} record, not a plan"],
        )


def _criterion_views(store: Store, record_id: str) -> list[CriterionView]:
    # KYO-546: whether a verdict was ever recorded is the verifications table's to say;
    # the state cannot, since `unmet-unbuilt` is both the filing state and a verdict.
    verdicted = {
        row[0]
        for row in store.conn.execute(
            "SELECT DISTINCT criterion_id FROM verifications WHERE record_id = ?",
            (record_id,),
        )
    }
    return [
        CriterionView(
            id=row["id"],
            text=row["text"],
            state=row["state"],
            has_verdict=row["id"] in verdicted,
        )
        for row in store.criteria(record_id)
    ]


def _open_docs_obligation_views(store: Store, record_id: str) -> list[DocsObligationView]:
    """KYO-137.c5: the open docs obligations ``check_transition`` refuses `done` on.

    Sibling of :func:`_criterion_views`, deliberately — both turn store rows into the
    value types ``contracts`` reads, so that module goes on knowing no schema.
    """
    return [
        DocsObligationView(id=row["id"], doc_path=row["doc_path"], section=row["section"])
        for row in store.open_docs_obligations(record_id)
    ]


def _add_criteria(store: Store, record_id: str, texts: list[str]) -> list[tuple[str, str]]:
    stamp = now()
    added: list[tuple[str, str]] = []
    for text in texts:
        criterion_id, seq = store.new_child_id(record_id, "criterion")
        store.conn.execute(
            "INSERT INTO criteria (id, record_id, seq, text, state, reason,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (criterion_id, record_id, seq, text, DEFAULT_CRITERION_STATE, stamp, stamp),
        )
        added.append((criterion_id, text))
    return added


def _add_evidence(
    store: Store, record_id: str, text: str, *, supports_id: str | None, author: str
) -> tuple[str, str]:
    """Store one evidence artifact verbatim; returns ``(evidence_id, sha256)``.

    Shared by ``kyo attach`` and ``kyo verify --evidence-file`` (KYO-4.c4): both
    create the same kind of row, the only difference is what triggers it.
    """
    evidence_id, seq = store.new_child_id(record_id, "evidence")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    stamp = now()
    store.conn.execute(
        "INSERT INTO evidence_artifacts (id, record_id, seq, text, sha256, supports_id,"
        " author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (evidence_id, record_id, seq, text, digest, supports_id, author, stamp),
    )
    return evidence_id, digest


def _require_supports_target(store: Store, target_id: str) -> None:
    """``--supports``/``--supersedes`` name a record, criterion, or claim id.

    Law 6: naming a nonexistent id errors, here as everywhere else.
    """
    if store.record_exists(target_id):
        return
    kind = ids.child_kind(target_id)
    if kind == "criterion":
        store.criterion(target_id)
        return
    if kind == "claim":
        store.claim(target_id)
        return
    raise KyoseiError(f"{target_id!r} is not a record, criterion, or claim id")


def _touch_record(store: Store, record_id: str) -> None:
    store.conn.execute("UPDATE records SET updated_at = ? WHERE id = ?", (now(), record_id))


def _touch_files(store: Store, record_id: str) -> set[str]:
    """File-level touch set for the disjointness check — ``path#symbol`` counts as
    its file (KYO-7.c2: "file-level overlap")."""
    return {row["path"] for row in store.touches(record_id)}


def _touch_symbols(store: Store, record_id: str) -> dict[str, set[str | None]]:
    """``path -> the symbols this record named on it``, with ``None`` standing for a
    plain-path touch (KYO-147.c5).

    ``None`` is a *value* here rather than an absence, and that is the whole point:
    "I named ``doctor.py#check_orphans``" and "I named ``doctor.py``" are different
    claims about the same file, and the second one claims all of it. Collapsing them
    (an empty symbol set for the plain form) would make "no symbols" ambiguous
    between "the whole file" and "nothing", which is KYO-48's mistake in miniature —
    absence borrowing whichever reading is convenient.
    """
    symbols: dict[str, set[str | None]] = {}
    for row in store.touches(record_id):
        symbols.setdefault(row["path"], set()).add(row["symbol"] or None)
    return symbols


def dispatch_covers(row) -> list[str]:
    """The records a dispatch row formally covers (``--also-covers``), read from
    ``covers_json`` exactly as :func:`export.record_document` reads it.

    KYO-65: this column is a *declaration of work in flight*, not decoration. The
    disjointness computation used to read only the primary record, so the covered
    records' touches were claimed by nobody as far as the check was concerned.
    """
    return json.loads(row["covers_json"]) if row["covers_json"] else []


@dataclass(frozen=True)
class _Claim:
    """What one dispatch actually claims: the primary record's touches unioned
    with every ``--also-covers`` record's (KYO-65).

    ``per`` is kept alongside ``files`` rather than thrown away after the union,
    because a refusal has to be able to say *which* record put a path in the
    claim — see :func:`contracts.collision_detail`.
    """

    per: dict[str, set[str]]
    files: set[str]
    undeclared: list[str]
    #: ``path -> {symbol | None}`` unioned over every record in the claim
    #: (KYO-147.c5). Merged rather than kept per-record because the question the
    #: symbol refinement answers is about the *claim*: if any record in it named the
    #: whole file, the whole file is claimed, whoever named it.
    symbols: dict[str, set[str | None]]


def _claim_of(store: Store, record_id: str, covers: list[str]) -> _Claim:
    """The touch claim of a dispatch on ``record_id`` that also covers ``covers``.

    KYO-65 + KYO-48 together: the union is over *records*, and a record that
    declares nothing contributes an unknown, not an empty set. Taking the union
    first and asking "is it empty?" afterwards would reintroduce KYO-48's bug in a
    new place — a scoped primary would paper over an unscoped covered record.
    """
    per = {rid: _touch_files(store, rid) for rid in (record_id, *covers)}
    symbols: dict[str, set[str | None]] = {}
    for rid in (record_id, *covers):
        for path, named in _touch_symbols(store, rid).items():
            symbols.setdefault(path, set()).update(named)
    return _Claim(
        per=per,
        files=set().union(*per.values()),
        undeclared=[rid for rid, files in per.items() if not files],
        symbols=symbols,
    )


#: The two reasons an overlap :func:`touch_standing` found is let through rather than
#: refused, carried as ``why`` on every :func:`touch_narrowings` entry so each renderer
#: branches on one vocabulary. KYO-147.c5: both sides named disjoint symbols in the
#: same file. KYO-395: this side creates a file that does not exist yet, under a
#: directory the other side claims — a WARNING, not a clearance.
NARROWED_DISJOINT_SYMBOLS = "disjoint-symbols"
NARROWED_NEW_FILE_UNDER_DIRECTORY = "new-file-under-directory"


def _narrow_by_symbol(
    mine: _Claim, theirs: _Claim, shared: set[str], repo_root: Path | None = None
) -> tuple[set[str], dict[str, tuple[list[str], list[str]]], dict[str, list[str]]]:
    """Split :func:`contracts.shared_touch_paths`' answer into the overlaps that are
    real and the ones that are shared only in the FILE NAME (KYO-147.c5).

    The file-level question stays where it has always been answered: this is handed
    ``shared`` and never recomputes it, so a directory touch keeps claiming its tree
    through exactly one implementation. What is new is a refinement *inside* an
    already-established overlap — never a widening, so nothing this returns can make
    a collision disappear that ``shared_touch_paths`` did not already find.

    An overlap narrows away only when every condition holds at once:

    * both sides name the **same** path (a directory touch overlaps a file under it
      at two different strings, and there is no symbol on either to compare);
    * neither side's path is a directory touch (it takes no symbol, and it claims
      files that do not exist yet — there is nothing to be precise about);
    * **neither side is a plain path**. A touch with no ``#symbol`` claims the whole
      file, so it collides with every symbol in it. This is the asymmetry the record
      asks for: the check narrows only where *both* sides were precise, and a
      controller who declared a bare path gets exactly the guard they had before.
    * the symbol sets are disjoint, where the additive claim ``#+`` (KYO-300.c3) is
      disjoint from itself: "I only add to this file" said by two records is two
      statements that cannot contradict each other, so it is the one symbol whose
      equality is not a collision. It still collides with a plain path, by the
      condition above it.

    KYO-395 adds the one other overlap that is let through rather than refused: a
    new-file touch of MINE (``+dir/x.py``) lying under a directory touch of THEIRS
    (``dir/``), while ``dir/x.py`` does not exist under ``repo_root``. A file that
    does not exist yet cannot be in the other dispatch's frozen plan — that is the
    reasoning of a consumer report, and it holds for the plan. It does NOT
    hold for the other worker's freedom: a directory touch claims "a tree this work
    creates or owns", so that worker may create files there, this same name
    included. So the overlap becomes a warning, never silence — see
    :func:`narrowing_detail`. Two things keep it a refusal: the file already
    existing (the ``+`` claim is then stale and the other worker may already be
    editing a real file), and the other side naming that same path (``x.py`` or
    ``+x.py`` in their claim is a file-level collision, decided by the ``hard``
    branch below and never overridden by this one). Only one direction: a
    candidate that claims ``dir/`` while a running dispatch declared ``+dir/x.py``
    owns the tree that worker is already writing into, and that stays a collision.

    Returns ``(hard, narrowed, under_directory)``: the paths that still collide,
    ``path -> (my symbols, their symbols)`` for the symbol narrowings, and
    ``my new-file touch -> [their directory touches over it]`` for the KYO-395 ones.
    A path that collides for any reason is never also reported as let through — one
    claim can name both ``x.py#f`` and ``x.py``, and the second reading is the one
    that decides.
    """
    narrowed: dict[str, tuple[list[str], list[str]]] = {}
    under_directory: dict[str, set[str]] = {}
    hard: set[str] = set()
    for a in (path for path in mine.files if path in shared):
        for b in (path for path in theirs.files if path in shared):
            if not (contracts.touch_covers(a, b) or contracts.touch_covers(b, a)):
                continue
            # KYO-395: my `+path` under their `dir/`, the file not yet on disk. The
            # other side naming the same path arrives here as a different `b` (their
            # file touch, not their directory) and lands in `hard` below.
            if (
                contracts.is_new_file_touch(a)
                and contracts.is_directory_touch(b)
                and not contracts.is_directory_touch(contracts.touch_target(a))
                and contracts.touch_covers(b, a)
                and not (
                    repo_root is not None
                    and (repo_root / contracts.touch_target(a)).exists()
                )
            ):
                under_directory.setdefault(a, set()).add(b)
                continue
            mine_named = mine.symbols.get(a) or {None}
            their_named = theirs.symbols.get(b) or {None}
            precise = (
                a == b
                and not contracts.is_directory_touch(a)
                and None not in mine_named
                and None not in their_named
                # KYO-300.c3: the additive claim (`path#+`) is subtracted from the
                # intersection rather than compared by name. Two records that both say
                # "I only add to this file" are not in each other's way — that is the
                # whole content of the claim, and it is the one symbol where an equal
                # string means agreement instead of collision. Every OTHER equality
                # still collides, and `+` against a plain path still collides, because
                # `None in ...` above has already decided that case.
                and not ((mine_named & their_named) - {contracts.ADDITIVE_SYMBOL})
            )
            if precise:
                narrowed[a] = (
                    sorted(str(s) for s in mine_named),
                    sorted(str(s) for s in their_named),
                )
            else:
                hard.add(a)
                hard.add(b)
    for path in hard:
        narrowed.pop(path, None)
        under_directory.pop(path, None)
    return hard, narrowed, {path: sorted(dirs) for path, dirs in under_directory.items()}


def _standing_entries(
    store: Store,
    record_id: str,
    running: list | None,
    also_covers: list[str] | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """The three things one pass over the running dispatches finds: collisions,
    unanswerable comparisons, and overlaps that narrowed away on symbols.

    Split out of :func:`touch_standing` rather than bolted onto its return value
    because that function has a third caller (``cmd_next.cmd_next``) that asks only
    the verdict question, and widening the tuple would make every caller learn about
    an answer only ``kyo dispatch`` renders. Both public readings below run this, so
    the narrowing `kyo dispatch` states and the collision it declines to raise are one
    computation seen twice, not two that agree until one is edited (law 3).
    """
    my_claim = _claim_of(store, record_id, list(also_covers or []))
    collisions: list[dict] = []
    unknown: list[dict] = []
    narrowings: list[dict] = []
    for row in store.running_dispatches() if running is None else running:
        if row["record_id"] == record_id:
            continue
        their_covers = dispatch_covers(row)
        their_claim = _claim_of(store, row["record_id"], their_covers)
        undeclared = my_claim.undeclared + their_claim.undeclared
        entry = {
            "dispatch_id": row["id"],
            "record_id": row["record_id"],
            "covers": their_covers,
        }
        if undeclared:
            unknown.append({**entry, "why": TOUCH_SCOPE_UNKNOWN, "undeclared": undeclared})
            continue
        # KYO-209: not `&`. A directory touch claims everything under it, so
        # `apps/desktop/` and `apps/desktop/main.js` name the same work while
        # intersecting in nothing — the one shape where set intersection answers
        # "disjoint" about two dispatches that are anything but.
        shared = contracts.shared_touch_paths(my_claim.files, their_claim.files)
        if not shared:
            continue
        # KYO-147.c5: the file-level overlap above is the same one KYO-7 established;
        # this only asks whether both sides were precise enough inside it to be sure
        # they are not in each other's way.
        hard, narrowed, under_directory = _narrow_by_symbol(
            my_claim, their_claim, shared, store.config.repo_root
        )
        narrowings += [
            {
                **entry,
                "why": NARROWED_DISJOINT_SYMBOLS,
                "path": path,
                "candidate_symbols": mine_named,
                "running_symbols": their_named,
            }
            for path, (mine_named, their_named) in sorted(narrowed.items())
        ]
        # KYO-395: let through, and stated as a WARNING — the same list, so the one
        # computation that decided "not refused" is the one that says why (law 3).
        narrowings += [
            {
                **entry,
                "why": NARROWED_NEW_FILE_UNDER_DIRECTORY,
                "warning": True,
                "path": path,
                "candidate_record_id": record_id,
                "running_directories": directories,
            }
            for path, directories in sorted(under_directory.items())
        ]
        if hard:
            # KYO-65.c2: which records put the shared paths in each claim, with
            # the two primaries left out — what remains is exactly the set a
            # controller cannot see by opening the primary record's touches.
            collisions.append(
                {
                    **entry,
                    "why": TOUCH_COLLIDES,
                    "paths": sorted(hard),
                    "via_running": sorted(
                        rid
                        for rid, files in their_claim.per.items()
                        if rid != row["record_id"] and files & hard
                    ),
                    "via_candidate": sorted(
                        rid
                        for rid, files in my_claim.per.items()
                        if rid != record_id and files & hard
                    ),
                }
            )
    return collisions, unknown, narrowings


def touch_narrowings(
    store: Store,
    record_id: str,
    running: list | None = None,
    also_covers: list[str] | None = None,
) -> list[dict]:
    """The file-level overlaps that did NOT become a refusal because both sides named
    disjoint ``#symbol``\\ s in the same file (KYO-147.c5).

    Never a verdict of its own — every entry here is a place the check would have
    said "collides" before and now says something more precise. It is stated rather
    than swallowed for the reason the whole record exists: a guard that quietly
    narrows is a guard whose guarantee nobody can audit. Measured need:
    KYO-214.d1 was refused against KYO-212.d1 over ``doctor.py`` and ``README.md``
    while the two edited different functions and different paragraphs, and ``--force``
    — which records an override of a collision that was never there — was the only
    way through.
    """
    return _standing_entries(store, record_id, running, also_covers)[2]


def narrowing_warnings(narrowings: list[dict]) -> list[dict]:
    """The let-through overlaps that are WARNINGS (KYO-395), in the order found."""
    return [n for n in narrowings if n.get("why") == NARROWED_NEW_FILE_UNDER_DIRECTORY]


def narrowing_detail(entry: dict) -> str:
    """The one sentence a narrowed overlap says (KYO-147.c5), sibling of
    :func:`contracts.collision_detail` and :func:`contracts.scope_unknown_detail`.

    It states the file and BOTH sides' symbols, because the fact a controller has to
    audit is not "we narrowed" but "we narrowed on this evidence" — and the evidence
    is two symbol lists that a reader can open the two records and check. Its natural
    home is beside its two siblings in ``contracts``; it was written here while that
    module was claimed by another running dispatch.

    KYO-395: a ``new-file-under-directory`` entry says something different, and says
    it as a warning — both dispatches, the file, the directory, and the risk the
    warning exists for: the other worker owns that tree and may create this name.
    ``candidate_dispatch_id`` is filled in by `kyo dispatch` once the id is minted;
    before that (and in any other reader) the candidate is named by its record.
    """
    if entry.get("why") == NARROWED_NEW_FILE_UNDER_DIRECTORY:
        me = (
            f"this dispatch {entry['candidate_dispatch_id']} "
            f"(dispatching {entry['candidate_record_id']})"
            if entry.get("candidate_dispatch_id")
            else f"this dispatch (dispatching {entry['candidate_record_id']})"
        )
        target = contracts.touch_target(entry["path"])
        directories = ", ".join(entry["running_directories"])
        return (
            f"WARNING: {me} creates {target} (declared {entry['path']}), inside "
            f"{directories}, which {entry['dispatch_id']} (dispatching "
            f"{entry['record_id']}) claims as a directory touch. {target} does not exist "
            "yet, so it cannot be in that dispatch's frozen plan, and this was NOT "
            "refused — but a directory touch claims a tree its work creates or owns, "
            f"so {entry['dispatch_id']}'s worker is free to create files there, "
            f"{target} included. Tell both workers, or narrow {entry['dispatch_id']}'s "
            "claim to the files it will actually create"
        )
    return (
        f"{entry['dispatch_id']} (dispatching {entry['record_id']}) also declares "
        f"{entry['path']}, at {', '.join('#' + s for s in entry['running_symbols'])} — "
        f"this dispatch declares {', '.join('#' + s for s in entry['candidate_symbols'])} "
        "in it. Both sides named symbols and the sets are disjoint, so this is a shared "
        "file rather than a collision. Either side declaring the plain path instead "
        "would claim the whole file and collide with every symbol in it"
    )


# --------------------------------------------------- what a declared file drags in
#
# KYO-147.c2/c3. The store has held this since the first dispatch and nothing ever
# read it: every ingested report carries ``touched_files[]``, which is what the work
# ACTUALLY touched, and a hundred of those rows are a co-occurrence table.
#
# Why this and not "write better touches tables". KYO-147.m2 is the finding, and it
# is about a class of file rather than about carelessness: `docs/reference/cli.md` is
# generated from the parser, `tests/test_kyo5_error_remedies.py` indexes error sites
# positionally per function, `templates/hooks/*.json` were byte-identical twins of
# `src/kyosei/defaults/hooks/*.json`. Each is dragged in by a property of the
# DEPENDENT file, invisible from the record and invisible to the grep a controller
# authors touches with. No prediction improves; the coupling has to be observed.
#
# It fires at generation time on purpose. KYO-147's own description proposed feeding
# drift back at report time instead, and the reporter then ran that experiment on
# their own project and refuted it: feeding drift back corrects the record that just
# FINISHED, and the guard consumes the table of the record NOT YET DISPATCHED.
#
# Measured on this repository's store, over the 128 ingested reports that
# carry `touched_files[]` (all 128 of them do). Two things fell out that set the
# thresholds below:
#
#   1. Mining DRIFT — "touched but not declared" — reads as almost nothing here: only
#      18 of 128 reports still show any undeclared file against today's touches
#      tables, because this repository's orchestrator applies the `kyo edit --touches-file`
#      correction after every merge. So the query is over `touched_files[]` PLAIN,
#      with "and this one does not declare it" asked of the candidate at generation
#      time. That version survives a store whose past has been reconciled; the drift
#      version is erased by the very habit KYO-147.c4 asks for.
#   2. The rate distribution is long-tailed and mostly noise: of the 4445 ordered
#      pairs with support >= 5, the median rate is 0.14 and the 90th percentile 0.42.
#      A threshold anywhere below half is a machine for printing coincidences.
#
#: A store with fewer ingested reports than this says nothing at all (KYO-147.c3).
#: Not tuned — twenty is where a rate of 0.6 needs at least 5 supporting reports to
#: be expressible at all, so it is the point below which the other three thresholds
#: cannot be met anyway, stated rather than left implicit.
COOCCURRENCE_MIN_REPORTS = 20
#: How many past dispatches must have touched a declared file before this will say
#: anything about it. Below 5 the rate is a fraction of a handful.
COOCCURRENCE_MIN_SUPPORT = 5
#: And how many of those must also have touched the co-occurring file. Both are
#: required: 3-of-5 is a rate of 0.6 and is still three dispatches, which on this
#: store surfaced `query.py => tests/test_bases.py` — a pair with no mechanism behind
#: it. Requiring an absolute count as well as a rate dropped it and kept every pair
#: that had a reason.
COOCCURRENCE_MIN_HITS = 5
#: The rate at which a co-occurrence is worth a controller's attention. 0.6 sits
#: above the 90th percentile of this store's pair distribution (0.42), so what it
#: admits is the tail rather than the body.
COOCCURRENCE_MIN_RATE = 0.6


def _reported_touch_sets(store: Store) -> list[set[str]]:
    """One set of actually-touched files per dispatch that has reported.

    Keyed by dispatch rather than by report: an amended report (KYO-198) supersedes
    its predecessor, and counting both would let one dispatch vote twice on every
    pair it named. The highest ``seq`` for a dispatch is the current one, which is
    the same rule ``kyo show`` renders by.

    A report that carried no ``touched_files[]``, or carried it empty, contributes
    nothing rather than an empty set — the same distinction :func:`
    touch_drift._reported_touched_files` makes for drift, and for the same reason:
    an empty list is a statement, but it is not evidence about which files were
    edited.
    """
    latest: dict[str, set[str]] = {}
    rows = store.conn.execute(
        "SELECT dispatch_id, body_json FROM reports ORDER BY seq"
    )
    for row in rows:
        try:
            body = json.loads(row["body_json"])
        except (TypeError, ValueError):  # pragma: no cover - validated at ingest
            continue
        if not isinstance(body, dict):  # pragma: no cover - validated at ingest
            continue
        files = body.get("touched_files")
        if not isinstance(files, list):
            continue
        named = {str(path).strip() for path in files if str(path).strip()}
        if named:
            latest[row["dispatch_id"]] = named
    return list(latest.values())


def touch_cooccurrence(store: Store, declared: Iterable[str]) -> dict:
    """Which files dispatches touching ``declared`` have historically ALSO touched
    and ``declared`` does not name (KYO-147.c2).

    Refuses nothing, and stays silent rather than guessing (KYO-147.c3): a store
    under :data:`COOCCURRENCE_MIN_REPORTS` reports has no history to speak from, and
    a pair under any of the three thresholds is a coincidence with a percentage
    attached. Both cases return ``findings: []``, and every rendering of this prints
    literally nothing for that — a weak signal dressed as a finding is worse than
    silence, because it is the same shape as a real one.

    A declared *directory* touch aggregates its whole tree, through the same
    :func:`contracts.touch_covers` every other reader uses, so `apps/desktop/` speaks
    for the files under it instead of for a string nobody ever touched.

    The returned dict carries the thresholds it applied and the report count it had,
    so `--json` can say why it was silent without the text output saying anything.
    """
    claim = list(declared)
    sets = _reported_touch_sets(store)
    result = {
        "reports": len(sets),
        "min_reports": COOCCURRENCE_MIN_REPORTS,
        "min_support": COOCCURRENCE_MIN_SUPPORT,
        "min_hits": COOCCURRENCE_MIN_HITS,
        "min_rate": COOCCURRENCE_MIN_RATE,
        "findings": [],
    }
    if not claim or len(sets) < COOCCURRENCE_MIN_REPORTS:
        return result
    findings = []
    for path in sorted(set(claim)):
        matching = [
            touched
            for touched in sets
            if any(contracts.touch_covers(path, other) for other in touched)
        ]
        support = len(matching)
        if support < COOCCURRENCE_MIN_SUPPORT:
            continue
        counts: dict[str, int] = {}
        for touched in matching:
            for other in touched:
                if contracts.claim_covers(claim, other):
                    continue
                counts[other] = counts.get(other, 0) + 1
        for other, hits in counts.items():
            rate = hits / support
            if hits >= COOCCURRENCE_MIN_HITS and rate >= COOCCURRENCE_MIN_RATE:
                findings.append(
                    {
                        "declared": path,
                        "also_touched": other,
                        "hits": hits,
                        "support": support,
                        "rate": round(rate, 2),
                    }
                )
    findings.sort(key=lambda f: (-f["rate"], -f["hits"], f["declared"], f["also_touched"]))
    result["findings"] = findings
    return result


#: KYO-327.c2: what counts as a test file, without asking the project. Two shapes,
#: both language-agnostic on purpose: a path with a ``test``/``tests``/``__tests__``
#: directory anywhere above it, and a basename that names itself a test
#: (``test_x.py``, ``x_test.go``, ``x.test.ts``, ``x.spec.tsx``). This is a *label on
#: history*, never a gate — a file it misses is one row missing from an advisory
#: table, and a file it wrongly includes is one row a worker discards on sight. That
#: asymmetry is why it is a heuristic here and a declaration nowhere.
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__", "spec", "e2e"})
_TEST_BASENAME = re.compile(r"(^test[_.-]|[_.-]test\.|[_.-]spec\.|^conftest\.)", re.IGNORECASE)


def is_test_path(path: str) -> bool:
    """Does ``path`` name a test file, by its own spelling? (KYO-327.c2)"""
    parts = [part for part in str(path).replace("\\", "/").split("/") if part]
    if not parts:
        return False
    if any(part.lower() in _TEST_DIRECTORIES for part in parts[:-1]):
        return True
    return bool(_TEST_BASENAME.search(parts[-1]))


def touch_test_history(store: Store, declared: Iterable[str]) -> dict:
    """Which test files dispatches touching ``declared`` have historically also
    changed (KYO-327.c2).

    THE SAME QUERY AS :func:`touch_cooccurrence`, not a second one: the same
    :func:`_reported_touch_sets` rows, matched by the same
    :func:`contracts.touch_covers`, so a directory touch aggregates its tree here
    exactly as it does there and the two blocks of one brief cannot describe two
    different histories (law 3). What differs is only which co-touched files are kept
    and how they are judged — :func:`is_test_path`, and no thresholds at all.

    NO THRESHOLDS, deliberately, where the co-occurrence query has four. Those exist
    because that block makes a claim about *your* work ("this file is likely to come
    with yours"), and a weak signal dressed as a finding is worse than silence. This
    block makes no claim about your work: it answers "where has this project put the
    tests for this file", where one dispatch having put them in one place is a real
    answer to a question whose alternative is the worker grepping for it. It is
    labelled as history and not as coverage for exactly that reason, and a path with
    no such history contributes nothing — no row, no "none".
    """
    claim = list(declared)
    sets = _reported_touch_sets(store)
    result: dict = {"reports": len(sets), "findings": []}
    if not claim or not sets:
        return result
    findings = []
    for path in sorted(set(claim)):
        matching = [
            touched
            for touched in sets
            if any(contracts.touch_covers(path, other) for other in touched)
        ]
        if not matching:
            continue
        counts: dict[str, int] = {}
        for touched in matching:
            for other in touched:
                # Already in the claim means already in the table above this block.
                if contracts.claim_covers(claim, other) or not is_test_path(other):
                    continue
                counts[other] = counts.get(other, 0) + 1
        if not counts:
            continue
        findings.append(
            {
                "declared": path,
                "support": len(matching),
                "tests": [
                    {"path": test, "hits": hits}
                    for test, hits in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
            }
        )
    result["findings"] = findings
    return result


def touch_standing(
    store: Store,
    record_id: str,
    running: list | None = None,
    also_covers: list[str] | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """Where ``record_id`` stands against every *running* dispatch: one of
    :data:`contracts.TOUCH_DISJOINT`, :data:`contracts.TOUCH_COLLIDES` or
    :data:`contracts.TOUCH_SCOPE_UNKNOWN`, with the entries behind the verdict.

    KYO-48. This lives here, called by both `kyo dispatch`'s refusal and `kyo
    next`/`kyo brief`'s ready list, because the two surfaces disagreeing about
    what is safe is worse than either being wrong alone: a controller gets a
    recommendation from one and a refusal from the other, or — as happened live —
    agreement in the permissive direction, where a zero-touch record
    was offered as ready and then dispatched silently into a file another worker
    was editing.

    An undeclared touch list yields ``scope-unknown``, never ``disjoint``. Set
    intersection cannot tell the two apart on its own (the empty set intersects
    nothing), which is precisely why the distinction has to be made before the
    intersection rather than read out of it. It is relative to work in flight:
    with nothing running there is nothing to be disjoint from, so an unscoped
    record's standing is knowable and favourable, and zero-ceremony intake (laws
    12/13) is untaxed in the quiet case.

    A collision outranks an unknown — it is a fact, and the stronger one.

    KYO-65: both sides of every comparison are *claims*, not single records. A
    dispatch's ``--also-covers`` records are work it formally declared it is
    doing, so their touches are part of what it claims; reading only the primary
    record's touches let a dispatch edit files nothing knew were claimed, and let
    a second dispatch into exactly those files through with no refusal. Live on
    this board: KYO-43.d1 covered KYO-44, and `render.py`, `html_site.py` and
    `brief.py` were being edited for the whole run while the tool believed they
    were unclaimed. Nothing collided, which was luck.

    ``also_covers`` is the *candidate's* covered records: at `kyo dispatch` time
    the dispatch row does not exist yet, so what it will cover has to be passed
    in. Running dispatches carry theirs in ``covers_json`` and it is re-read on
    every comparison, never frozen at dispatch time — an orchestrator once corrected a
    covered record's touches four separate times in one session, each after the
    dispatch covering it had started, and a stored union would have been wrong
    from the moment of each correction while still reading as an answer.
    KYO-147.c5: a file-level overlap where both sides named disjoint ``#symbol``\\ s
    is no longer a collision — see :func:`_narrow_by_symbol` for the four conditions
    and :func:`touch_narrowings` for the list `kyo dispatch` states. The narrowing is
    a refinement inside an overlap this function already found; it can turn a
    ``collides`` into a ``disjoint``, never the other way round. KYO-395 is the same
    shape: a candidate's ``+path`` under a running ``dir/``, the file not yet on
    disk, reads ``disjoint`` here and is stated as a WARNING by `kyo dispatch`.
    """
    collisions, unknown, _narrowed = _standing_entries(store, record_id, running, also_covers)
    if collisions:
        return TOUCH_COLLIDES, collisions, unknown
    if unknown:
        return TOUCH_SCOPE_UNKNOWN, collisions, unknown
    return TOUCH_DISJOINT, collisions, unknown


def _load_registry(store: Store) -> kinds.KindRegistry:
    return kinds.load_kind_registry(store.config.kyosei_dir)


def _load_status_ladder(store: Store) -> contracts.StatusLadder:
    """This project's declared statuses (KYO-101), from the same place the kind
    registry comes from. A sibling of :func:`_load_registry` on purpose: both are
    per-project config read fresh per command rather than cached on the store, so an
    edit to either file takes effect on the next invocation and never mid-command."""
    return contracts.load_status_ladder(store.config.kyosei_dir)


def _active_statuses(store: Store) -> tuple[str, ...]:
    """Neither complete nor retired — the "still in flight" reading of KYO-8's
    "open records" that `kyo sweep`, `kyo next`'s readiness check, and the
    creation-time duplicate check all share (law 3: named once here, not three
    independent guesses at what "open" means across this milestone).

    KYO-101: read off the project's own ladder by role, so a project that renamed
    ``done`` to ``shipped`` does not silently start sweeping its finished records.
    The v1 form asked whether a status was one of two literal names, which is
    exactly the class of comparison the registry breaks."""
    return _load_status_ladder(store).active_names()


_UNMET_CRITERION_STATES = ("unmet-unbuilt", "unmet-unobserved")


def criterion_has_a_verdict(store: Store, criterion_id: str, state: str) -> bool:
    """KYO-58.c1/c4: has anything yet attested to this criterion's text?

    Two ways a verdict reaches a criterion, and this answers for both:

    * a row in ``verifications`` — `kyo verify`'s record of "met, by this method,
      on this date, with this evidence". This is the case the immutability rule
      was written for.
    * a void state set through `kyo edit --criterion --state` — ``void-premise-gone``
      / ``void-withdrawn``, which write no verification row (they are premise
      judgments, not verified facts: :data:`contracts.EDIT_DIRECT_CRITERION_STATES`)
      but *are* a recorded judgment about this exact wording, and carry a required
      reason that reads as one.

    So the correctable window is "no verification row **and** still
    ``unmet-unbuilt``". Every path out of ``unmet-unbuilt`` writes one or the
    other, which is what makes this a mechanical test rather than a heuristic.

    Deliberately NOT part of the test: whether the criterion has been quoted into a
    frozen dispatch brief. See ``cmd_records.cmd_edit`` for that decision and why —
    the short form is that a brief is an instruction, not an attestation, and it
    stores its own verbatim copy of what the worker read.
    """
    if state != DEFAULT_CRITERION_STATE:
        return True
    return (
        store.conn.execute(
            "SELECT 1 FROM verifications WHERE criterion_id = ? LIMIT 1", (criterion_id,)
        ).fetchone()
        is not None
    )


def briefs_quoting(store: Store, record_id: str) -> list[str]:
    """Dispatch ids whose frozen brief was generated from ``record_id``.

    KYO-58.c4: correcting a criterion's text does not consult this — the boundary
    is the first verdict — but the correction *says* it, because a worker somewhere
    read the old wording and law 6 does not allow that to be silent.
    """
    return [
        row["id"]
        for row in store.conn.execute(
            "SELECT id FROM dispatches WHERE record_id = ? ORDER BY seq", (record_id,)
        )
    ]


def insert_flag(
    store: Store,
    record_id: str,
    flag_type: str,
    detail: str,
    *,
    other_record_id: str | None = None,
    other_criterion_id: str | None = None,
    sweep_run_id: str | None = None,
) -> str:
    """Write one flag row and return its id, inside the caller's transaction.

    Shared runtime rather than `kyo sweep`'s private helper since KYO-59: sweep is
    no longer the only producer of flags (`kyo ungate` raises
    :data:`contracts.FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA`), and two writers of one
    table with two copies of the insert is how the columns drift apart.
    """
    flag_id, seq = store.new_child_id(record_id, "flag")
    store.conn.execute(
        "INSERT INTO flags (id, record_id, seq, flag_type, detail, other_record_id,"
        " other_criterion_id, sweep_run_id, created_at, resolved_at, resolved_by,"
        " resolved_why) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
        (
            flag_id,
            record_id,
            seq,
            flag_type,
            detail,
            other_record_id,
            other_criterion_id,
            sweep_run_id,
            now(),
        ),
    )
    return flag_id


def _read_worker_procedures(store: Store) -> str:
    """Read the project's live ``kyosei/procedures.md`` — read-only; this build
    never writes to it (out of file scope)."""
    path = store.config.kyosei_dir / "procedures.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


# -------------------------------------------------------- the transaction seam


def _commit_and_export(
    store: Store,
    *,
    command: str,
    actor: str,
    record_ids: list[str],
    before: dict[str, dict | None],
) -> tuple[dict[str, dict], list[dict]]:
    """Write events for each touched record, inside the caller's transaction.

    Returns the post-mutation documents *and* the event rows just written, so the
    caller can append them to ``kyosei/export/_events.jsonl`` after commit (milestone 2
    export completeness — the events ledger is a second, append-only export artifact,
    not just an internal audit table).
    """
    after: dict[str, dict] = {}
    events: list[dict] = []
    for rid in record_ids:
        after[rid] = export.record_document(store, rid)
        events.append(
            store.record_event(
                actor=actor,
                command=command,
                record_id=rid,
                before=before.get(rid),
                after=after[rid],
            )
        )
    return after, events


def _finish_mutation(store: Store, record_ids: list[str], events: list[dict]) -> None:
    """Everything a mutating command does after its transaction commits.

    Appends this mutation's events to the append-only ledger, regenerates the
    per-record export, and refreshes the allocation-counter snapshot — the three
    artifacts that together make ``kyosei/export/`` a complete, rebuildable
    serialization of the store (charter, Layer 2).

    **The ledger goes first** (KYO-404). All three are written after COMMIT, so
    anything that stops the process in between leaves the database ahead of the
    export — and the three are not equally recoverable from that. A record document
    and the counter snapshot are functions of the database's current state, which
    ``kyo export --refresh`` re-derives. An event is history: until this ordering it
    was written second, so an interruption during the document writes left it in
    ``kyosei.db`` only, and the next ``kyo rebuild`` dropped it without a trace. The
    append-only artifact is therefore the first thing written, and the ones a refresh
    can regenerate follow it; ``doctor``'s ``ledger_gaps`` check and the repair in
    ``kyo export --refresh`` cover the interruption that still lands before it.
    """
    events_ledger.append_events(store, events)
    export.write_exports(store, record_ids)
    ledgers.write_allocations(store)


# --------------------------------------------------------------------- lookups


def build_context(
    root: str | None,
    actor: str | None,
    environ: dict[str, str],
    argv: list[str],
    *,
    project: str,
    read_only: bool = False,
) -> Context:
    """Resolve this invocation's store root and code root.

    ``project`` is the command's relationship to a project, from
    :data:`kyosei.cli.COMMAND_TABLE`:

    ``PROJECT_REQUIRED``
        Search upward (or ask ``--root`` directly) and fail loudly if there is
        no project — every command that reads or writes the store.

    ``PROJECT_NONE``
        Never search: ``init`` runs *before* a project exists, and a search here
        would silently retarget it at whatever project happens to be above the
        working directory.

    ``PROJECT_OPTIONAL``
        Search, but return a :data:`LAYOUT_UNCONFIGURED` layout rather than
        raising — the worktree guards (KYO-54.c2), which have something true and
        useful to say in a repository that has configured no store at all, and
        whose whole job is to say what they are and are not guarding.
    """
    if project == PROJECT_NONE:
        resolved = Path(root).expanduser().resolve() if root else Path.cwd().resolve()
        layout = Layout(LAYOUT_UNCONFIGURED, store_root=resolved, code_root=resolved)
    else:
        layout = resolve_layout(
            explicit_root=root, required=project == PROJECT_REQUIRED
        )
    return Context(
        root=layout.store_root,
        actor=resolve_actor(actor, environ),
        argv=argv,
        layout=layout,
        environ=dict(environ),
        read_only=read_only,
    )


# ----------------------------------------------------------- project bootstrap


def _init_store_location_line(target: Path, args: Namespace, environ) -> str:
    """KYO-416.c10: the line `kyo init` leads with — the store's path, and what put it
    there, including that ``$KYOSEI_HOME`` moves the default."""
    from .config import STORE_HOME_ENV, store_home

    if args.in_repo:
        return (
            f"store: inside the code repository at {target.resolve()} (--in-repo); "
            f"no store is created under ${STORE_HOME_ENV} ({store_home(environ)}), whose "
            "projects.json registry only gains this project's path (KYO-455)"
        )
    planned = scaffold_module.planned_store_root(
        target, store_dir=args.store, environ=environ
    )
    if args.store is not None:
        return f"store: {planned} (named by --store)"
    home_is_set = bool((environ or {}).get(STORE_HOME_ENV, "").strip())
    home = (
        f"${STORE_HOME_ENV}"
        if home_is_set
        else f"~/kyosei, because ${STORE_HOME_ENV} is unset"
    )
    return (
        f"store: {planned} — the default location, this directory's name under {home}; "
        f"set ${STORE_HOME_ENV} to move it, or pass --store DIR"
    )


def _init_layout_unchosen(target: Path, args: Namespace) -> bool:
    """KYO-558.c1: no store here yet, and no flag that chooses one. Re-running `kyo init`
    where a store link or an in-repo store already exists is not this case; it keeps
    its own answers (KYO-559 covers that run's output)."""
    from .config import config_path_for, store_link_path

    if args.defaults or args.in_repo or args.store is not None or args.adopt:
        return False
    return not (store_link_path(target).is_file() or config_path_for(target).is_file())


def _init_routes_text(target: Path, args: Namespace, environ) -> str:
    """KYO-558.c7: the text bare `kyo init` prints in a project with no store -- the start
    of setup rather than an error about the reader. It names `kyo onboard`, `kyo init
    --defaults` with the store path and prefix that would get, and `kyo init --in-repo`,
    each runnable as printed: the directory, ``--prefix`` and ``--no-git-init`` given
    to this run are carried into the commands. Identical on and off a terminal
    (KYO-558.c8): nothing here asks whether one is attached."""
    from .config import config_path_for

    carried = []
    if args.prefix:
        carried += ["--prefix", args.prefix]
    if getattr(args, "no_git_init", False):
        carried.append("--no-git-init")
    if args.directory:
        carried.append(str(target))
    tail = "".join(f" {shlex.quote(word)}" for word in carried)
    here = f" {shlex.quote(str(target))}" if args.directory else ""
    explain = f"kyo init --explain{here}"
    onboard = f"cd{here} && kyo onboard" if args.directory else "kyo onboard"
    prefix = ids.normalise_prefix(args.prefix or target.name)
    planned = scaffold_module.planned_store_root(target, environ=environ)
    if config_path_for(planned).is_file():
        defaults_what = (
            f"link the store that already exists at {planned} (a second checkout's case; "
            "kyo init checks it was made for this repository)"
        )
    else:
        defaults_what = (
            f"take every default: the store in its own git repository at {planned}, "
            f"record ids {ids.record_id(prefix, 1)}, {ids.record_id(prefix, 2)}, ..."
        )
    return "\n".join(
        [
            f"{target} has no Kyosei store yet, so `kyo init` wrote nothing. Where the "
            "store lives is the one setup choice that is costly to undo (setup decision "
            "B1), so it is yours to make. Start setup with one of these:",
            f"  `{onboard}`",
            "      guided setup (recommended): prints the steps for your agent to follow. "
            "They ask where the store lives, the record id prefix and whether to install "
            "hooks, then run `kyo init` and the install commands",
            f"  `kyo init --defaults{tail}`",
            f"      {defaults_what}",
            f"  `kyo init --in-repo{tail}`",
            "      put the store inside this repository instead: kyosei/ and ideas/ are "
            "committed beside the code, so pushing this repository publishes them",
            f"`{explain}` describes both layouts and writes nothing.",
        ]
    )


def _init_explain(target: Path, args: Namespace, environ) -> Output:
    """``kyo init --explain`` (KYO-558.c6): the store-layout explanation `kyo init` used
    to print after scaffolding -- KYO-54.c3's committed-prose list and KYO-70.c4's
    worktree home -- for ``target``, writing nothing. The project's own layout when it
    has a store; otherwise the layout the other flags choose, or both when none does."""
    from .config import LAYOUT_IN_REPO, LAYOUT_SPLIT, config_path_for, store_link_path

    existing = store_link_path(target).is_file() or config_path_for(target).is_file()
    if existing:
        found = resolve_layout(explicit_root=target, required=False)
        layouts = [(found.kind, found.code_root, found.store_root)]
        head = f"{target} already has a store: {found.kind}, at {found.store_root}."
    else:
        split = (
            LAYOUT_SPLIT,
            target,
            scaffold_module.planned_store_root(target, store_dir=args.store, environ=environ),
        )
        in_repo = (LAYOUT_IN_REPO, target, target)
        if args.in_repo:
            layouts = [in_repo]
        elif args.store is not None or args.defaults or args.adopt:
            layouts = [split]
        else:
            layouts = [split, in_repo]
        head = f"{target} has no Kyosei store yet; nothing was written."
    lines = [f"kyo init --explain: {head}"]
    for kind, code_root, store_root in layouts:
        if existing:
            title = f"{kind}: this project's layout"
        elif kind == LAYOUT_IN_REPO:
            title = f"{kind}: what `kyo init --in-repo` makes"
        else:
            title = f"{kind}: what `kyo init --defaults` (or --store DIR) makes"
        lines += ["", title]
        lines += install_prose.prose_disclosure(kind, code_root=code_root, store_root=store_root)
        lines += install_prose.worktree_disclosure(
            kind, code_root=code_root, store_root=store_root, environ=environ
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "init",
            "explain": True,
            "written": [],
            "layouts": [
                {"layout": kind, "code_root": str(code_root), "store_root": str(store_root)}
                for kind, code_root, store_root in layouts
            ],
            "committed_prose_surfaces": [
                surface.path for surface in install_prose.COMMITTED_PROSE_SURFACES
            ],
        },
    )


def cmd_init(ctx: Context, args: Namespace) -> Output:
    """``kyo init [DIR] [--prefix P] [--in-repo | --store DIR]``.

    KYO-54: the default is the **split** layout — the store at
    ``~/kyosei/<project>/``, its own git repository, and one committed pointer
    file in the code repository. ``--in-repo`` is the alternative, named in
    ``--help``, named in this command's own output under both layouts, and
    documented in the reference (docs/reference/topics/store.md, "Where the store
    lives") rather than hidden behind a flag a consumer would have to already know
    about (KYO-54.c5).

    Whichever is chosen, this prints what does or does not become git-committed
    prose *at the moment it scaffolds* (KYO-54.c3). That timing is the point: a
    consumer used to discover the answer by reading README carefully, or by
    reviewing a diff that already contained their intake layer.
    """
    target = Path(args.directory).expanduser().resolve() if args.directory else ctx.code_root
    # KYO-558: --explain writes nothing, so it is answered before every refusal below.
    if getattr(args, "explain", False):
        return _init_explain(target, args, ctx.environ)
    # KYO-535.c2: from inside a running dispatch's worktree, against the real home, this
    # is refused before anything below creates a store or writes the registry.
    from . import config as config_module

    config_module.refuse_real_home_from_a_dispatch_worktree(
        ctx.environ,
        command="kyo init",
        what=(
            "register this project in projects.json"
            if args.in_repo or args.store is not None
            else "create this project's store and register it in projects.json"
        ),
        given=[("the directory named", target)],
    )
    # KYO-558.c1/c7: a project with no store, and no layout chosen, gets the routes and
    # nothing written. Never a prompt, and never a terminal check: an agent calls
    # `kyo` without a terminal, so that path is the one that has to explain itself.
    if _init_layout_unchosen(target, args):
        raise UsageError(_init_routes_text(target, args, ctx.environ))
    # KYO-416.c10: where the store goes, and what decided it, is the FIRST thing said
    # — before "initialised" or "linked" — from the same derivation init_layout uses.
    # A verifier's hand-run `kyo init` once left a store under the real ~/kyosei that
    # nothing in the output had said it would create, or how to have put it elsewhere.
    lead = _init_store_location_line(target, args, ctx.environ)
    result = scaffold_module.init_layout(
        target,
        prefix=args.prefix,
        store_dir=args.store,
        in_repo=bool(args.in_repo),
        adopt=bool(getattr(args, "adopt", False)),
        git_init=not getattr(args, "no_git_init", False),
        environ=ctx.environ,
        claude_md=True,
    )
    if result.rerun:
        # KYO-559.c4: a re-run on a store that already exists says what it changed and
        # nothing else -- not the store's location, not the layout pointer.
        return Output(
            [
                f"kyo init: {result.code_root} already links to the store at "
                f"{result.store_root}; this run changed:",
                *[f"  {note}" for note in result.git_notes],
            ],
            {
                "ok": True,
                "command": "init",
                "rerun": True,
                "root": str(result.store_root),
                "layout": result.layout,
                "adopted": result.adopted,
                "adoption_check": result.adoption_check,
                "code_repo_id": result.code_repo_id,
                "code_root": str(result.code_root),
                "store_root": str(result.store_root),
                "project_prefix": result.prefix,
                "written": [],
                "git": result.git_notes,
            },
        )
    verb = "linked" if result.adopted else "initialised"
    lines = [
        lead,
        f"{verb} Kyosei project for {result.code_root}",
        echo_value("store layout", result.layout),
        echo_value("store root", str(result.store_root)),
        echo_value("project prefix", result.prefix),
    ]
    # KYO-558.c6: one line for the store's files rather than one per file -- `--json`'s
    # `written` still lists every path -- so what was done and what is next lead.
    if result.written:
        names = ", ".join(str(path.relative_to(result.store_root)) for path in result.written)
        lines.append(f"  + {len(result.written)} files in the store: {names}")
    if result.link_path is not None:
        lines.append(f"  + {result.link_path.relative_to(result.code_root)} (store link)")
    # KYO-558.c4: a session opened here later is told this project uses Kyosei and where
    # setup continues, even when nothing else gets installed.
    claude_md = result.claude_md
    if claude_md is not None:
        lines.append(
            f"  + {claude_md.relative_to(result.code_root)} (a marked section saying setup "
            "is incomplete and naming `kyo onboard --check`; `kyo install-skill` replaces it)"
        )
    lines += [f"  {note}" for note in result.git_notes]
    lines.append("record ids will look like " + ids.record_id(result.prefix, 1))
    # KYO-455: the project init made is one this machine serves. Never
    # fails the init -- a registry that refuses it says so, with its remedy.
    from . import cmd_projects

    registry_lines, registry_data = cmd_projects.register_on_init(result.code_root, ctx.environ)
    lines += registry_lines
    # KYO-558.c6: the layout explanation (KYO-54.c3's committed-prose list, KYO-70.c4's
    # worktree home) moved behind this one line. It was ~15 lines AFTER a choice already
    # made; the choice is now made at the refusal or in `kyo onboard`, and the full text
    # is one command away for this project at any time, not only at the moment it was
    # scaffolded.
    explain = "kyo init --explain" + (
        f" {shlex.quote(str(result.code_root))}" if args.directory else ""
    )
    lines.append(
        f"What this layout keeps out of the code repository (or puts in it), and where "
        f"dispatch worktrees go: `{explain}` (writes nothing)."
    )
    # KYO-335.c1: the other command that writes `kyosei/config.yml` and stamps this
    # build's `schema_version` into a store. Structurally silent in the fresh case —
    # this command does not install hooks (`kyo install-hooks` does), so a repository
    # that has none is not yet running the PATH build for anything. It speaks where
    # the situation is real: a code repository that ALREADY carries `kyo` hooks and
    # is being given a store, which is `--adopt`'s case and a second checkout's.
    # Asked after the scaffold rather than before it, because the config that build
    # would refuse does not exist until now.
    if config_module.project_runs_kyo_from_path(result.code_root):
        path_build = config_module.probe_path_build(result.store_root, environ=ctx.environ)
        if path_build is not None and path_build.blind:
            lines.append("")
            lines += config_module.path_build_lag_lines(
                path_build, subject="the store this just created"
            )
    # KYO-558.c3/c10: the next steps, last, from `kyo onboard --check`'s own readback so
    # the two cannot disagree about what is left; nothing when nothing is.
    from . import cmd_onboard

    incomplete_lines, incomplete_items = cmd_onboard.setup_incomplete(
        resolve_layout(explicit_root=result.code_root, required=False)
    )
    if incomplete_lines:
        lines.append("")
        lines += incomplete_lines
    return Output(
        lines,
        {
            "ok": True,
            "command": "init",
            # KYO-70.c4: the same directory the disclosure lines above name,
            # resolved through the same pair of functions every other command uses.
            "worktree_home": str(
                worktree_home(
                    config_project_home_name(
                        Layout(
                            result.layout,
                            store_root=result.store_root,
                            code_root=result.code_root,
                        )
                    ),
                    ctx.environ,
                )
            ),
            # The store root, under both layouts: it is what `--root` names and
            # what every other command's "root" means.
            "root": str(result.store_root),
            "layout": result.layout,
            "adopted": result.adopted,
            # KYO-416: "verified", "unverified" or "overridden" when a store was
            # adopted, null when one was created.
            "adoption_check": result.adoption_check,
            "code_repo_id": result.code_repo_id,
            "code_root": str(result.code_root),
            # KYO-455: whether this machine's project registry now lists it, and as what.
            "registry": registry_data,
            "store_root": str(result.store_root),
            "store_link": str(result.link_path) if result.link_path else None,
            "project_prefix": result.prefix,
            "written": [str(p.relative_to(result.store_root)) for p in result.written],
            "git": result.git_notes,
            "committed_prose_surfaces": [
                surface.path for surface in install_prose.COMMITTED_PROSE_SURFACES
            ],
            # KYO-558: the CLAUDE.md init wrote its setup-incomplete section into (null
            # when the file already had a Kyosei section), and the readback items still
            # missing or behind -- the same objects `kyo onboard --check --json` prints.
            "claude_md": str(claude_md) if claude_md is not None else None,
            "setup_incomplete": [item.to_json() for item in incomplete_items],
        },
    )


def _refuse_copies_unasked(ctx: Context, args: Namespace, command: str) -> None:
    """KYO-595.c3: the copies are written without ``--copies`` only in a project already
    on them; elsewhere the plugin is the default and this names it
    (:func:`kyosei.cmd_harness_migrate.copies_refusal`). ``--print`` writes nothing and
    is not refused."""
    if getattr(args, "copies", False) or getattr(args, "print_only", False):
        return
    from .cmd_harness_migrate import copies_refusal

    refusal = copies_refusal(ctx.code_root, command)
    if refusal is not None:
        raise KyoseiError(refusal)


def cmd_install_hooks(ctx: Context, args: Namespace) -> Output:
    """``kyo install-hooks [--print]`` — KYO-13.c1: merge the packaged Claude Code
    hook templates into this project's ``.claude/settings.json`` (SessionStart ->
    `kyo brief --compact --session-start`; Stop and SessionEnd -> `kyo sweep --hook &&
    kyo doctor --hook`; SubagentStop -> `kyo check-clean --subagent-stop`,
    then `kyo check-branch --subagent-stop --against main`, per KYO-13.c5 and
    KYO-128; PreToolUse -> `kyo check-push --pre-tool-use`, per KYO-60).
    Merges into (never overwrites) an existing settings file, and is idempotent: a
    second run with nothing new to add reports zero changes.

    KYO-60 also writes the advisory git ``pre-push`` hook, which is the same
    warning aimed at the human rather than the agent. It lives in ``.git/hooks``
    because that is the only place a `git push` typed by hand passes through — and
    ``.git/hooks`` is not versioned, so a fresh clone will not have it until this
    command is run there.

    KYO-36: those three recipes are not restated from memory —
    ``tests/test_hook_prose_currency.py`` compares every prose statement of a
    packaged hook command against what the packaged fragment installs, so this
    docstring cannot fall behind a fragment change again (it did, twice).
    """
    _refuse_copies_unasked(ctx, args, "kyo install-hooks")
    # KYO-318.c1. This command installs two things, and the skill's refusal has to be
    # raised BEFORE the hooks are merged or a source repository whose skill alone is
    # behind would get a written settings.json and then an error — a half-done run,
    # which is the one outcome a refusal exists to prevent (law 6).
    skill_refusal = install_skill_module.install_skill_refusal(ctx.code_root)
    if skill_refusal is not None and not args.print_only:
        raise KyoseiError(skill_refusal)
    settings, changes, path, notes = install_hooks(
        ctx.code_root, print_only=bool(args.print_only)
    )
    # KYO-15: a consumer that installs the enforcement should get the workflow in the
    # same act. Otherwise `kyo init` hands them rules with no instructions.
    # KYO-328: the skill is two files, and each that changed is named on its own line.
    # A consumer told only that "the skill" was written would not learn that a second
    # file appeared beside it.
    for skill_path, skill_changed in install_skill_module.install_skill(
        ctx.code_root, print_only=bool(args.print_only)
    ):
        if skill_changed:
            changes.append(f"skill: wrote {skill_path.relative_to(ctx.code_root)}")
    if args.print_only:
        lines = [f"would write {path} (--print: nothing written):"]
    else:
        lines = [f"wrote {path}:"]
    # KYO-361: `--print` is the preview, so it still renders the settings.json it
    # would write -- but a real run no longer dumps the file it just wrote at all
    # (it is already on disk; `--json` still carries `settings`), and in BOTH modes
    # the change list goes last. Those are the only lines that say what happened,
    # and `| tail` is how every controller reads a long command's output: a JSON
    # dump after them buried the one line ("+ skill: wrote ...") a tailed run needed.
    if args.print_only:
        rendered = json.dumps(settings, indent=2, ensure_ascii=False)
        lines += ["", rendered]
    lines += ([f"  + {c}" for c in changes] if changes else ["  (no changes: already installed)"])
    # KYO-60: a standing condition this run could not change — no git repository
    # here, or a `pre-push` hook that is somebody else's — is stated every time and
    # never counted as a change, so it can neither break idempotence nor go unsaid.
    lines += [f"  ! {note}" for note in notes]
    return Output(
        lines,
        {
            "ok": True,
            "command": "install-hooks",
            "path": str(path),
            "printed_only": bool(args.print_only),
            "changes": changes,
            "notes": notes,
            "settings": settings,
        },
    )


def cmd_install_skill(ctx: Context, args: Namespace) -> Output:
    """``kyo install-skill [--print]`` (KYO-15, KYO-328) — write the packaged
    `/kyosei` controller workflow, and the reference beside it, into this project's
    ``.claude/skills/``.

    The skill describes the CLI you are running, so it ships with the package
    rather than being copied by hand: the one consumer that hand-copied it was 34
    lines behind within a day, documenting commands and flags that had since
    changed. `kyo doctor`'s ``skill`` check reports that drift; this is what clears
    it. Also runs as part of `kyo install-hooks`.

    KYO-328 made it two files — ``SKILL.md``, the workflow, and ``REFERENCE.md``, the
    incident histories it cites by name in every section. They are written together
    and checked together, because a workflow whose pointers resolve to a missing file
    is worse than one that never had them.

    ``path`` in the JSON document stays the workflow's, which is what every existing
    reader of this command's output means by "the skill"; ``paths`` carries both, and
    ``changed`` is true when either file was written.
    """
    _refuse_copies_unasked(ctx, args, "kyo install-skill")
    written = install_skill_module.install_skill(
        ctx.code_root, print_only=bool(args.print_only)
    )
    verb = "would write" if args.print_only else "wrote"
    # KYO-361.c3: a reader who just ran `kyo install-hooks` and now sees "no changes"
    # here needs the no-changes line itself to say the two commands write the same
    # files -- otherwise a truthful "already current" reads as contradicting the
    # `+ skill: wrote ...` line install-hooks printed moments earlier.
    lines = [
        f"{verb} {path}"
        if changed
        else f"{path} is already current (no changes; `kyo install-hooks` writes this too)"
        for path, changed in written
    ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "install-skill",
            "path": str(written[0][0]),
            "paths": [str(path) for path, _ in written],
            "changed": any(changed for _, changed in written),
            "printed_only": bool(args.print_only),
        },
    )


def _indented(text: str, prefix: str = "    ") -> str:
    """``text`` with every line prefixed, for quoting a rule's before/after under a
    one-line label without it running together with the line above it."""
    return "\n".join(prefix + line for line in text.splitlines())


def cmd_install_procedures(ctx: Context, args: Namespace) -> Output:
    """``kyo install-procedures [--print] [--adopt TITLE ...] [--adopt-preamble]``
    (KYO-212, KYO-151.c2) — add the standing rules this build ships that this
    project's ``kyosei/procedures.md`` does not have, then rewrite anything named by
    ``--adopt``/``--adopt-preamble`` from the packaged default.

    The base step is a *merge*, not an overwrite, and that distinction is the record:
    the ledger is project-editable by design (this repository's own copy carries two
    rules the packaged default does not), so a packaged rule the project lacks is
    inserted where the packaged file puts it, a rule the project edited is left
    exactly as written, and a rule only the project has is not touched. Without
    ``--adopt``/``--adopt-preamble`` this command decides nothing between two versions
    of the same rule — `kyo doctor`'s ``procedures_rules_differ`` check reports those,
    as a notice rather than a failure — so KYO-212.c3 holds unconditionally.

    ``--adopt``/``--adopt-preamble`` are the opt-in override: naming a rule (or the
    preamble) replaces its text with the packaged wording, on the record that a person
    decided so deliberately rather than the tool deciding on their behalf. Both refuse,
    naming why, when there is nothing sound to adopt — a rule the packaged default
    does not ship, or one that already reads the same in both copies.

    Takes the *store* root rather than ``code_root``: the ledger lives with the
    records, which under the split layout is a different repository from the code.
    That is also why KYO-322's top-up of ``<store>/kyosei/.gitignore`` runs from here
    rather than from `kyo install-hooks` — see :func:`install_gitignore.install_store_gitignore`.
    """
    result = install_procedures_module.install_procedures(
        # KYO-342: THIS checkout's copy of the store, never the one a worktree's read
        # of the database resolved through. `procedures.md` is a tracked file, so the
        # worktree has its own at whatever this branch holds, and it is the copy this
        # branch would be diffed on — see `Layout.store_root_in_this_checkout`. Under
        # every other layout and in the main checkout this is `ctx.root` exactly.
        ctx.root if ctx.layout is None else ctx.layout.store_root_in_this_checkout,
        # KYO-318.c1: the CODE root beside the store's, on
        # `procedures_ledger_drift`'s precedent — under the split layout the source of
        # the packaged default is in the checkout and the ledger is in another
        # repository, so a refusal that read one root would answer for the wrong one.
        repo_root=ctx.code_root,
        print_only=bool(args.print_only),
        adopt=tuple(args.adopt),
        adopt_preamble=bool(args.adopt_preamble),
    )
    verb = "would add" if args.print_only else "added"
    if result.added:
        lines = [f"{verb} {len(result.added)} rule(s) to {result.path}:"]
        lines += [f"  + {title}" for title in result.added]
    else:
        lines = [
            f"{result.path} already has every rule this build ships"
            + ("" if result.refreshed else " (no changes)")
        ]
    # KYO-322: stated beside the ledger's own changes rather than folded into them.
    # A reader who ran this for the rules still has to be told that a second file in
    # their store changed, and a reader who had nothing to add to the ledger would
    # otherwise see "no changes" over a run that wrote one.
    lines += [f"  + {change}" for change in result.gitignore_changes]
    refresh_verb = "would update" if args.print_only else "updated"
    if result.refreshed:
        # KYO-515: said out loud, with both texts, because this is the one rewrite the
        # plain run makes — of a rule still on a wording an earlier build installed.
        lines.append(
            f"{refresh_verb} {len(result.refreshed)} rule(s) still on a wording an earlier "
            f"build installed and never edited here, to this build's wording:"
        )
        for refreshed in result.refreshed:
            lines.append(f"  ^ {refreshed.title}")
            lines.append("  before:")
            lines.append(_indented(refreshed.before))
            lines.append("  after:")
            lines.append(_indented(refreshed.after))
    if result.differing:
        # Named here as well as by `kyo doctor`, because this is the command a
        # controller runs *expecting* the ledger to be current afterwards, and a rule
        # this deliberately declined to touch would otherwise be invisible at exactly
        # the moment it matters.
        lines.append(
            f"{len(result.differing)} rule(s) differ between the two copies and were "
            f"left alone — `kyo doctor` quotes both sides; `--adopt \"<title>\"` "
            f"rewrites one from the packaged default:"
        )
        lines += [f"  ~ {title}" for title in result.differing]
    adopt_verb = "would rewrite" if args.print_only else "rewrote"
    for adopted in result.adopted:
        lines.append(f"{adopt_verb} the rule {adopted.title!r} from the packaged default:")
        lines.append("  before:")
        lines.append(_indented(adopted.before))
        lines.append("  after:")
        lines.append(_indented(adopted.after))
    if result.preamble_adopted is not None:
        before, after = result.preamble_adopted
        lines.append(f"{adopt_verb} the preamble from the packaged default:")
        lines.append("  before:")
        lines.append(_indented(before))
        lines.append("  after:")
        lines.append(_indented(after))
    return Output(
        lines,
        {
            "ok": True,
            "command": "install-procedures",
            "path": str(result.path),
            "added": result.added,
            "differing": result.differing,
            "refreshed": [
                {"title": r.title, "before": r.before, "after": r.after}
                for r in result.refreshed
            ],
            "adopted": [
                {"title": a.title, "before": a.before, "after": a.after}
                for a in result.adopted
            ],
            "preamble_adopted": (
                None
                if result.preamble_adopted is None
                else {"before": result.preamble_adopted[0], "after": result.preamble_adopted[1]}
            ),
            "gitignore_changes": result.gitignore_changes,
            "changed": bool(result.added)
            or bool(result.refreshed)
            or bool(result.adopted)
            or bool(result.gitignore_changes)
            or result.preamble_adopted is not None,
            "printed_only": bool(args.print_only),
        },
    )


#: KYO-327.c1. What `kyo rules` says above a rule it read from the packaged default
#: rather than from this project's own ledger. The ledger is project-editable by
#: design — this repository's own copy carries rules the packaged default does not —
#: so "which copy is this" is not a detail: a worker comparing the text it was
#: dispatched under against the text it just read has to know whether it read the
#: same file.
RULES_SOURCE_PACKAGED = (
    "(from the rules this build of Kyosei ships — no project store is readable from "
    "here, so this may differ from the ledger your brief was generated from; run it "
    "with --root <project> for that one)"
)


def _rules_ledger(ctx: Context) -> tuple[str, str]:
    """``(text, source line)`` for `kyo rules`: this project's ledger when one is
    readable, the packaged default otherwise."""
    if ctx.layout is not None and ctx.layout.configured:
        path = ctx.root / KYOSEI_DIR / "procedures.md"
        if path.is_file():
            return path.read_text(encoding="utf-8"), f"(from {path})"
    source_dir = scaffold_module.defaults_dir()
    packaged = (source_dir / "procedures.md") if source_dir else None
    if packaged is not None and packaged.is_file():
        return packaged.read_text(encoding="utf-8"), RULES_SOURCE_PACKAGED
    return "", RULES_SOURCE_PACKAGED


#: KYO-565: the positional spelling that makes `kyo rules` write instead of read. Exact
#: and case-sensitive, so a rule whose title contains "add" is still printable as
#: `kyo rules Add` (the title match is case-insensitive) -- the help says so.
RULES_ADD_WORD = "add"

#: The add form's flags, ``(spelling, argparse dest)``: required by it, refused without it.
RULES_ADD_FLAGS: tuple[tuple[str, str], ...] = (
    ("--title", "add_title"),
    ("--audience", "audience"),
    ("--enforced-by", "enforced_by"),
    ("--reason-file", "reason_file"),
)


def _rules_add(ctx: Context, args: Namespace) -> Output:
    """``kyo rules add --title T --audience A[,A...] --enforced-by E --reason-file F``
    (KYO-565) — append one well-formed rule under ``## Project rules`` in this
    project's ``kyosei/procedures.md``.

    Onboarding tells an agent to record a project's own restriction as a rule; this
    is the shape, the audience tokens and the placement as a command rather than as
    prose to be reverse-engineered. The rule is built and read
    back through :func:`procedures.add_project_rule`, which refuses anything the
    ledger's own parser would not read back with exactly these values, so every
    refusal writes nothing.

    An orchestrator's write. It writes the file the way `kyo install-procedures` does
    — the copy of the store in THIS checkout, one ``write_text`` — and adds no guard
    of its own against a worker: `kyo check-branch` already fails a dispatch branch
    that diffs ``kyosei/``. It writes no record, no event and no export, which is why
    `rules` stays out of ``MUTATING_COMMANDS``.
    """
    missing = [flag for flag, dest in RULES_ADD_FLAGS if getattr(args, dest, None) is None]
    if missing:
        raise UsageError(
            f"`kyo rules add` needs {', '.join(missing)}: kyo rules add --title "
            f'"<imperative title>" --audience <token>[,<token>...] --enforced-by '
            f'"<mechanism>|aspirational" --reason-file <file|->. To print a rule whose '
            f"title contains 'add' instead, run `kyo rules Add`"
        )
    if ctx.layout is None or not ctx.layout.configured:
        raise UsageError(
            "`kyo rules add` writes this project's kyosei/procedures.md, and no project "
            "store is readable from here; run it inside the project or with --root <project>"
        )
    path = ctx.layout.store_root_in_this_checkout / KYOSEI_DIR / "procedures.md"
    if not path.is_file():
        raise KyoseiError(
            f"{path} does not exist, so there is no ledger to add a rule to; "
            "`kyo install-procedures` writes this build's ledger there, then run this again"
        )
    audiences: list[str] = []
    for token in args.audience.split(","):
        token = token.strip()
        if token and token not in audiences:
            audiences.append(token)
    reason = read_prose(args.reason_file, what="the rule's reason")
    current = path.read_text(encoding="utf-8")
    updated = procedures_module.add_project_rule(
        current,
        title=args.add_title,
        audiences=audiences,
        # Through the shared one-line check, so the refusal is the one the prose rule
        # names for every one-line text flag (exit 64, "single line").
        enforced_by=single_line(args.enforced_by, what="--enforced-by"),
        reason=reason,
    )
    path.write_text(updated, encoding="utf-8")
    title = args.add_title.strip()
    added = next(r for r in procedures_module.parse_procedures(updated) if r.title == title)
    return Output(
        [
            f"added the rule {title!r} under `## {procedures_module.PROJECT_RULES_SECTION}` "
            f"in {path}",
            f"  audience: {added.audience}",
            f"  enforced-by: {added.enforced_by}",
            f"  first sentence (what a brief carries): {added.first_line}",
            "commit kyosei/procedures.md to keep it",
        ],
        {
            "ok": True,
            "command": "rules add",
            "path": str(path),
            "section": procedures_module.PROJECT_RULES_SECTION,
            "rule": {
                "title": added.title,
                "audience": added.audience,
                "enforced_by": added.enforced_by,
                "first_line": added.first_line,
            },
        },
    )


def cmd_rules(ctx: Context, args: Namespace) -> Output:
    """``kyo rules [TITLE]`` — the standing rules, in full (KYO-327.c1).

    THE READER THIS EXISTS FOR is a dispatched worker. Until KYO-327 a frozen brief
    carried every worker-audience rule *entire* — each ``enforced-by`` bullet's whole
    argument, which is the bulk of the brief and which a worker acts on rarely. The
    brief now carries each rule's title, its one-line enforced-by and the first
    sentence of its reasoning, and names this command for the rest. That trade is only
    honest if the rest is genuinely one command away, so this is that command, and it
    is the reason the shortening is not a deletion.

    ``PROJECT_OPTIONAL``, and that is load-bearing rather than convenient. Under the
    split layout the store is a different repository from the code, so a worker
    standing in a `git worktree add` of the code tree has no ``kyosei/procedures.md``
    to open and no project above it to find — the case where naming a path instead of
    a command would have failed. With no project readable this falls back to the rules
    this build ships and *says so*: the ledger is project-editable, so a copy that
    might differ is offered as what it is rather than as the project's own.

    Read-only in the strongest sense available: it opens no database, writes no event,
    regenerates no export, and reads one markdown file. Nothing about it can change
    what a worker was dispatched under.

    ``kyo rules add`` (KYO-565) is the one exception, and it is a different act: see
    :func:`_rules_add`. It is selected by the positional spelled exactly ``add``, and
    its flags are refused without it, so the read above stays exactly what it was.
    """
    if getattr(args, "title", None) == RULES_ADD_WORD:
        return _rules_add(ctx, args)
    stray = [flag for flag, dest in RULES_ADD_FLAGS if getattr(args, dest, None) is not None]
    if stray:
        raise UsageError(
            f"{', '.join(stray)} belong(s) to `kyo rules add`, which writes a rule; "
            f'`kyo rules "<title>"` and `kyo rules` only read. Run `kyo rules add '
            f'--title ... --audience ... --enforced-by ... --reason-file ...`'
        )
    text, source = _rules_ledger(ctx)
    rules = procedures_module.parse_procedures(text)
    sections = procedures_module.rule_sections(text)
    wanted = (getattr(args, "title", None) or "").strip()
    if wanted:
        matched = [r for r in rules if wanted.lower() in r.title.lower()]
        if not matched:
            raise NotFound(
                f"no standing rule's title contains {wanted!r} {source}. "
                "`kyo rules` with no argument lists every rule this project carries"
            )
        lines = []
        for rule in matched:
            lines += [f"### {rule.title}  {source}", "", sections.get(rule.title, ""), ""]
        return Output(
            lines,
            {
                "ok": True,
                "command": "rules",
                "source": source,
                "rules": [
                    {
                        "title": r.title,
                        "audience": r.audience,
                        "enforced_by": r.enforced_by,
                        "first_line": r.first_line,
                        "body": sections.get(r.title, ""),
                    }
                    for r in matched
                ],
            },
        )
    lines = [f"{len(rules)} standing rule(s) {source}:"]
    lines += [f"  - {r.title}  [audience: {r.audience}]" for r in rules]
    if rules:
        lines.append('`kyo rules "<title>"` prints one of them in full.')
    return Output(
        lines,
        {
            "ok": True,
            "command": "rules",
            "source": source,
            "rules": [
                {"title": r.title, "audience": r.audience, "enforced_by": r.enforced_by}
                for r in rules
            ],
        },
    )


__all__ = [
    "CRITERION_STATES",
    "Context",
    "NotFound",
    "Output",
    "build_context",
    "cmd_init",
    "cmd_install_hooks",
    "cmd_install_procedures",
    "cmd_install_skill",
    "cmd_rules",
    "is_test_path",
    "resolve_actor",
    "touch_test_history",
]
