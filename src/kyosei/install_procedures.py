"""``kyo install-procedures``: the standing-rules ledger, updated rather than rewritten
(KYO-151, KYO-212, KYO-318).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import procedures
from .config import (
    KYOSEI_DIR,
    git_worktree_facts,
)
from .errors import KyoseiError
from .install_gitignore import install_store_gitignore
from .scaffold import (
    PACKAGED_ASSET_SOURCE_DIR,
    PROCEDURES_ASSET,
    _reinstall_route,
    _source_repo_refusal,
    defaults_dir,
    repo_asset_source,
)


def install_procedures_source_refusal(repo_root: Path | None) -> str | None:
    """Why `kyo install-procedures` must not write here, or ``None`` (KYO-318.c1).

    Takes the **code** root rather than the store's, for
    :func:`procedures_ledger_drift`'s reason: under the split layout the source of the
    packaged default is in the checkout and the ledger it would be merged into is in a
    different repository. ``None`` when the caller has no code root to offer.
    """
    if repo_root is None:
        return None
    source = repo_asset_source(repo_root, PROCEDURES_ASSET)
    if source is None:
        return None
    try:
        packaged = packaged_procedures_text()
    except KyoseiError:  # pragma: no cover - a broken install; reported by doctor
        return None
    if source.read_text(encoding="utf-8") == packaged:
        return None
    source_path = PACKAGED_ASSET_SOURCE_DIR / PROCEDURES_ASSET.source
    return _source_repo_refusal(
        repo_root,
        command="kyo install-procedures",
        what="standing-rules ledger",
        source_path=source_path,
        instead=[
            f"move this copy to a build made from this checkout ({_reinstall_route()}) "
            "and run it again",
            f"or copy the rule you want across from {source_path} by hand — `kyo "
            f"doctor` names each rule {PROCEDURES_ASSET.target} is missing and quotes "
            "both wordings of any rule the two copies word differently. `--adopt` in "
            "particular takes its text out of the installed build, which here is a "
            "third copy and the oldest of the three",
        ],
    )


# ------------------- KYO-212: the standing-rules ledger, updated rather than rewritten


def packaged_procedures_text() -> str:
    """The packaged standing-rules ledger's text, or raise if this build ships none."""
    source_dir = defaults_dir()
    if source_dir is None:
        raise KyoseiError("this build ships no defaults/ directory; reinstall kyosei")
    path = source_dir / PROCEDURES_ASSET.source
    if not path.is_file():
        raise KyoseiError(f"the packaged procedures ledger is missing from {path}")
    return path.read_text(encoding="utf-8")


def procedures_path(store_root: Path) -> Path:
    """Where a project's own copy of the ledger lives (``<store>/kyosei/procedures.md``)."""
    return store_root / PROCEDURES_ASSET.target


def procedures_ledger_drift(
    store_root: Path,
    repo_root: Path | None = None,
) -> tuple[list[procedures.LedgerDifference], list[str]]:
    """``(differences, errors)`` between ``store_root``'s ledger and the default it is
    a copy of (KYO-212, read for KYO-213's brief block; KYO-309 for *which* default).

    The one reader of these two files that every consumer-facing caller shares —
    `kyo doctor`'s ``procedures_rules_missing``/``procedures_rules_differ`` checks and
    `kyo brief`'s asset-drift block both call this rather than re-opening either file,
    so the three cannot disagree about what the ledger's drift is. Mirrors
    :func:`skill_drift` and :func:`hook_drift` in shape — a ``root``-in, drift-out
    reader living beside the asset it reads — and in posture: ``([], [])`` when the
    project carries no ledger at all, the same silence those two give a project that
    never installed the asset in question.

    ``errors`` is separate from ``differences`` because it answers a different
    question — not "how has the ledger drifted" but "can this build even compare" —
    and it is non-empty only for a broken installation that ships no packaged default,
    which belongs in the failing tier alone (KYO-214) and never among the notices a
    reworded rule produces. A caller that wants one flat list of problems (`kyo
    doctor`'s ``procedures_rules_missing``) folds ``errors`` in; one that needs to tell
    a missing rule from a reworded one (`kyo brief`: the first gets `kyo
    install-procedures`, the second gets none) reads ``differences`` itself and filters
    on :data:`procedures.LEDGER_MISSING` / :data:`procedures.LEDGER_CHANGED`.

    ``repo_root`` is KYO-309's half, and it is the **code** root rather than the store's
    (under a split store the two are different directories, and the source of the
    packaged default is in the checkout, not the store). Passed and carrying
    :data:`PACKAGED_ASSET_SOURCE_DIR`, the ledger is compared against *that* file
    instead of the running build's packaged copy, for :func:`skill_drift`'s reason: in
    the repository the package is built from, the packaged copy is the OUTPUT and an
    installed build can only be behind. Two findings turn on it, and both were wrong
    there before: a rule deleted from the source is reported as one the
    ledger is "missing" (red, and `kyo install-procedures` would put it back), and a
    rule reworded in the source and the ledger together is reported as
    a wording the project drifted into (with `--adopt`, which would take the older
    text). ``None``, or a checkout without that file, is every consumer — compared
    against the packaged copy exactly as before.
    """
    target = procedures_path(store_root)
    if not target.is_file():
        return [], []
    source = None if repo_root is None else repo_asset_source(repo_root, PROCEDURES_ASSET)
    if source is not None:
        default = source.read_text(encoding="utf-8")
    else:
        try:
            default = packaged_procedures_text()
        except KyoseiError as exc:  # pragma: no cover - only on a broken install
            return [], [str(exc)]
    return procedures.compare_ledgers(default, target.read_text(encoding="utf-8")), []


#: The branch `kyo check-branch` measures a dispatch branch against, and the one this
#: hand-off is performed on. Spelled here rather than imported from ``cmd_guard`` (whose
#: import would pull the whole command layer into this module) — the same literal that
#: command defaults ``--against`` to, named so a reader can see it is a choice.
MAIN_BRANCH = "main"


def _git_worktree_facts(root: Path) -> tuple[bool, str, str | None, bool]:
    """``(is a git checkout, current branch, the main checkout's path, `main` exists)``.

    Every value read from git at the moment of the call, never assumed (law 16). A
    directory that is not a git checkout at all yields ``(False, "", None, False)``:
    stores are scaffolded outside git and in throwaway trees, and a command that refused
    to run there would be refusing on the strength of a question it could not ask.

    "Is this a linked worktree" is git's own fact rather than an inference from the
    path. ``--git-common-dir`` is the *shared* administrative directory, and it differs
    from ``--git-dir`` in exactly the trees ``git worktree add`` creates — which is what
    a dispatch worktree is, whatever it or its branch happens to be named.

    The ``git rev-parse`` calls live in :func:`kyosei.config.git_worktree_facts`, which
    store resolution shares (law 3); this is only the tuple shape this module's refusals
    interpolate (docs/design/scaffold.md#kyo-342).
    """
    facts = git_worktree_facts(root, branch=MAIN_BRANCH)
    if not facts.is_repo:
        return False, "", None, False
    return (
        True,
        facts.branch,
        None if facts.main_checkout is None else str(facts.main_checkout),
        facts.has_named_branch,
    )


def install_procedures_refusal(store_root: Path) -> str | None:
    """Why `kyo install-procedures` must not write here, or ``None`` (KYO-151.c3).

    Applying the packaged ledger to the store is a **controller** act. `kyo check-branch`
    fails any dispatch branch that diffs ``kyosei/``, but only at stop time, after the
    write; refusing the write makes the ban hold at the moment it would be broken
    (docs/design/scaffold.md#kyo-151).

    Two signals, both read from git (:func:`_git_worktree_facts`), because a dispatch is
    two things at once and either alone leaves a hole:

    * a **linked worktree** — what ``git worktree add`` produces and therefore what
      every `kyo dispatch --worktree` is, whatever it is named or branched to;
    * a **branch other than** :data:`MAIN_BRANCH`, which catches the controller who
      forgot to come back to the main checkout, and a worker sent to a branch in the
      main tree rather than to a worktree of its own.

    The branch signal is conditional on the repository actually having a ``main``, so a
    project on ``master`` is not refused for a naming convention Kyosei happens to
    prefer. The message always names where to run it instead — the main checkout's path
    when git knows it, the branch by name always — because a refusal without a
    destination is this record's own defect moved one step later.
    """
    is_repo, branch, main_checkout, has_main = _git_worktree_facts(store_root)
    if not is_repo:
        return None
    if main_checkout is not None:
        return (
            f"`kyo install-procedures` writes {PROCEDURES_ASSET.target}, and {store_root} "
            f"is a linked git worktree (`git rev-parse --git-common-dir` points "
            f"elsewhere) — what `git worktree add`, and therefore every `kyo dispatch "
            f"--worktree`, creates. Applying the packaged ledger to the store copy is a "
            f"orchestrator act: `kyo check-branch` fails any dispatch branch that diffs "
            f"{KYOSEI_DIR}/, so this write would be reverted at best and would smuggle a "
            f"store edit past that ban at worst. Run it in {main_checkout}, on "
            f"`{MAIN_BRANCH}`, once this branch has merged. `kyo install-procedures "
            f"--print` writes nothing and is not refused, so you can still see exactly "
            f"what the hand-off would add."
        )
    if has_main and branch and branch != MAIN_BRANCH:
        return (
            f"`kyo install-procedures` writes {PROCEDURES_ASSET.target}, and this "
            f"checkout is on `{branch}` rather than `{MAIN_BRANCH}`. Applying the "
            f"packaged ledger to the store copy is an orchestrator act performed on the "
            f"branch the project merges into — `kyo check-branch` fails any dispatch "
            f"branch that diffs {KYOSEI_DIR}/, and a store edit made on a feature branch "
            f"either loses the guard or loses the edit. Switch to `{MAIN_BRANCH}` in this "
            f"checkout and run it again. `kyo install-procedures --print` writes nothing "
            f"and is not refused."
        )
    return None


@dataclass(frozen=True)
class AdoptedRule:
    """One ``--adopt``ed rule (KYO-151.c2): its title, and its text before and after."""

    title: str
    before: str
    after: str


@dataclass(frozen=True)
class InstallProceduresResult:
    """Everything ``kyo install-procedures`` did or would do, for the command layer
    to render and for tests to assert on directly rather than through stdout."""

    path: Path
    added: list[str]
    differing: list[str]
    adopted: list[AdoptedRule]
    preamble_adopted: tuple[str, str] | None
    #: KYO-322: what the store's own ``.gitignore`` gained on this run, if anything.
    #: Carried here rather than printed at the call site so a test can assert on the
    #: top-up directly, the way every other field of this result already is.
    gitignore_changes: list[str] = field(default_factory=list)
    #: KYO-515: rules whose text was an earlier packaged wording nobody in the project
    #: edited, moved to this build's wording by the plain run (title, before, after —
    #: :class:`AdoptedRule`'s shape, because the rewrite is the same one `--adopt` makes).
    refreshed: list[AdoptedRule] = field(default_factory=list)


def install_procedures(
    store_root: Path,
    *,
    repo_root: Path | None = None,
    print_only: bool = False,
    adopt: tuple[str, ...] = (),
    adopt_preamble: bool = False,
) -> InstallProceduresResult:
    """``kyo install-procedures`` (KYO-212.c2, KYO-151.c2): add the packaged rules
    this project's ledger does not have, then apply any ``--adopt``s named.

    Refuses to *write* from a dispatch worktree or off :data:`MAIN_BRANCH` (KYO-151.c3;
    :func:`install_procedures_refusal` carries the reasoning) — for the whole call,
    ``--adopt``/``--adopt-preamble`` included: the hand-off this command performs is a
    controller act however it is invoked, and a worktree that could preview it but not
    write it is exactly the balance :func:`install_procedures_refusal` already strikes.
    ``print_only=True`` is exempt, deliberately: a preview that touches nothing is
    precisely what a worker needs in order to report the hand-off accurately.

    NOT an overwrite, and that is the whole design of the plain merge. ``kyosei/
    procedures.md`` is project-editable by intent — `kyo init` copies the packaged
    default in precisely so the project can then own it, and this repository's own
    copy carries two rules the default does not ship. So the base merge is BY RULE: a
    packaged rule the ledger lacks is inserted where the packaged file puts it, a rule
    whose text differs is left exactly as the project wrote it (``differing``; `kyo
    doctor` reports those, and only a person decides which copy is right), and a rule
    only the project has is not touched at all. The one exception is KYO-515's, and it
    is not a judgement: a rule whose text is byte-for-byte (after normalization) a
    wording an earlier build shipped for it — :data:`procedures.SUPERSEDED_RULE_WORDINGS`
    — is moved to the packaged wording (``refreshed``), because nobody in the project
    wrote it. A reworded rule matches no earlier wording and is left alone as before.

    ``adopt``/``adopt_preamble`` are the opt-in override KYO-212.c3 deliberately has
    none of otherwise: naming a rule (or the preamble) replaces its text with the
    packaged wording, on top of the base merge, and :func:`procedures.adopt_rule` /
    :func:`procedures.adopt_preamble` raise rather than silently doing nothing when
    there is nothing sound to adopt (a store-only title, or the two already agreeing).
    Without either flag nothing here differs from the plain merge, so KYO-212.c3 still
    holds unconditionally: a rule is rewritten only when a caller names it.

    Unlike :func:`install_skill` this takes the *store* root, not the code root: the
    ledger lives with the records, which under the split layout is a different
    repository from the code (KYO-54).
    """
    if not print_only:
        refusal = install_procedures_refusal(store_root)
        if refusal is not None:
            raise KyoseiError(refusal)
        # KYO-318.c1, checked before the store-location refusal's sibling has written
        # anything: in the repository this package is built from, the packaged default
        # is this checkout's OUTPUT, so merging it in is the wrong direction.
        source_refusal = install_procedures_source_refusal(repo_root)
        if source_refusal is not None:
            raise KyoseiError(source_refusal)
    # KYO-322. Before the ledger, and unconditionally: the two are independent
    # top-ups of the same store, and a project whose ledger is already current would
    # otherwise never reach the line below.
    gitignore_changes, _ = install_store_gitignore(store_root, print_only=print_only)
    target = procedures_path(store_root)
    packaged = packaged_procedures_text()
    if not target.is_file():
        # A project that predates the asset, or one whose copy was deleted: there is
        # no merge to do and nothing of the project's to preserve, so the whole
        # packaged file already answers every `--adopt` before it is asked.
        if not print_only:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(packaged, encoding="utf-8")
        return InstallProceduresResult(
            path=target,
            added=[rule.title for rule in procedures.parse_procedures(packaged)],
            differing=[],
            adopted=[],
            preamble_adopted=None,
            gitignore_changes=gitignore_changes,
        )

    current = target.read_text(encoding="utf-8")
    merged, added = procedures.merge_ledger(packaged, current)

    adopted: list[AdoptedRule] = []
    for title in adopt:
        merged, before, after = procedures.adopt_rule(packaged, merged, title)
        adopted.append(AdoptedRule(title=title, before=before, after=after))

    # KYO-515: a rule whose text is exactly a wording an earlier build shipped was
    # written by nobody in this project, so moving it to this build's wording is not
    # the overwrite KYO-212.c3 forbids. Never in the repository the package is built
    # from (KYO-309/318): there the other copy is the repository's own source, and an
    # old wording is two copies of the same text disagreeing, not an inheritance.
    # After the `--adopt`s, so a rule named there is reported as adopted, as asked,
    # rather than refreshed first and then refused as already identical.
    refreshed: list[AdoptedRule] = []
    if repo_root is None or repo_asset_source(repo_root, PROCEDURES_ASSET) is None:
        merged, moved = procedures.refresh_superseded_rules(packaged, merged)
        refreshed = [AdoptedRule(title=t, before=b, after=a) for t, b, a in moved]
        # KYO-524: the preamble too, when it is a wording an earlier build shipped.
        # Reported among ``refreshed`` under :data:`procedures.PREAMBLE_TITLE`. Not when
        # `--adopt-preamble` was asked for, so that request is reported as the adopt it
        # is rather than refused afterwards as already identical.
        if not adopt_preamble:
            merged, moved_preamble = procedures.refresh_superseded_preamble(packaged, merged)
            if moved_preamble is not None:
                refreshed.append(
                    AdoptedRule(
                        title=procedures.PREAMBLE_TITLE,
                        before=moved_preamble[0],
                        after=moved_preamble[1],
                    )
                )

    preamble_adopted: tuple[str, str] | None = None
    if adopt_preamble:
        merged, before, after = procedures.adopt_preamble(packaged, merged)
        preamble_adopted = (before, after)

    differing = [
        difference.title
        for difference in procedures.compare_ledgers(packaged, merged)
        if difference.kind == procedures.LEDGER_CHANGED
    ]
    changed = bool(added) or bool(refreshed) or bool(adopted) or preamble_adopted is not None
    if changed and not print_only:
        target.write_text(merged, encoding="utf-8")
    return InstallProceduresResult(
        path=target,
        added=added,
        differing=differing,
        adopted=adopted,
        preamble_adopted=preamble_adopted,
        gitignore_changes=gitignore_changes,
        refreshed=refreshed,
    )
