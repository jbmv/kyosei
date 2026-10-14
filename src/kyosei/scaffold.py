"""``kyo init`` — scaffold a consumer project.

The layout is table-driven on purpose. Milestone 2 ships default ``kinds.yml`` and
``procedures.md`` from ``src/kyosei/defaults/`` (the single packaged copy — law 3;
``pyproject.toml``'s package-data ships them with the installed wheel) by appending
to :data:`DEFAULT_ASSETS`, with no change to ``init``'s logic. That seam was
exercised by ``tests/test_init.py`` before these files existed, so it did not rot.

Also the packaged-asset and git helpers the three install commands share. The installers
themselves live beside it: :mod:`kyosei.install_hooks`, :mod:`kyosei.install_skill`,
:mod:`kyosei.install_procedures`, :mod:`kyosei.install_gitignore` (the store's ignore
lines) and :mod:`kyosei.install_prose` (what `kyo init` discloses). The incidents behind
their rules are in docs/design/scaffold.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import ids
from .config import (
    DB_NAME,
    DOCTOR_REPORT_NAME,
    KYOSEI_DIR,
    LAYOUT_IN_REPO,
    LAYOUT_SPLIT,
    SESSIONS_NAME,
    config_path_for,
    default_store_dir,
    read_raw_config,
    store_link_path,
    write_config,
    write_store_link,
)
from .contracts import WORKER_REPORT_PATH
from .errors import KyoseiError, UsageError
from .export import ATOMIC_TEMP_SUFFIX
from .store import create_database

#: The ideas layer's directory name, and the one file name in it that is never swept,
#: counted, or listed by `kyo ideas review` (charter, Layer 1). Named here once so
#: `kyo promote` and `kyo ideas review` (kyosei.ideas) share it rather than each
#: hardcoding the string (law 3).
#:
#: KYO-566: `kyo init` no longer writes a file by this name. The scratchpad moved to the
#: per-project home in KYO-114 (``serve_files.scratchpad_target``, what the desktop app's
#: Scratchpad opens), and an ``ideas/scratchpad.md`` is skipped by every reader -- so a
#: scaffolded one was a place to write that nothing ever showed again. The name stays
#: because the skip stays (a store scaffolded before this still holds one), and
#: ``kyo doctor``'s ``ideas_scratchpad`` notice names where such a file's content belongs.
IDEAS_DIR = "ideas"
SCRATCHPAD_BASENAME = "scratchpad.md"

#: The text `kyo init` wrote to ``ideas/scratchpad.md`` before KYO-566. Kept only so
#: ``kyo doctor``'s ``ideas_scratchpad`` notice can tell an untouched scaffold (nothing
#: to move, delete it) from a file somebody wrote in. Nothing writes it any more.
RETIRED_IDEAS_SCRATCHPAD_TEMPLATE = """# Scratchpad

Zero ceremony. Write the thought, keep going. Nothing here is swept, counted, or
required to have any field. Structure appears at promotion, not at intake.
"""

EXPORT_README = """This directory is generated. Do not edit these files by hand.

Every file is a deterministic rendering of one record in `kyosei.db`, rewritten
after every mutation so that changes stay reviewable as git diffs. `kyo doctor`
re-derives all of them and fails on any mismatch. `_events/` (the append-only audit
ledger, written as bounded shards so that a commit re-stores one shard rather than the
whole history — KYO-268) and `_allocations.json` are the other two export artifacts `kyo
rebuild` needs to reconstruct `kyosei.db` from this directory alone (charter, Layer 2).
"""

#: `kyo init` writes this so consumer repos commit the export, never the database
#: it derives from (charter, Layer 2: "`kyosei.db` is gitignored in consumer
#: projects; `kyosei/export/` is the git-canonical serialization").
#:
#: Every line is local state beside the database that must never be committed: the
#: database itself, its SQLite WAL sidecars (open whenever anything holds the database,
#: which with the desktop app is always), the session hooks' ``sessions.json`` (it carries
#: a claude.ai Remote Control session id and is rebuildable), the doctor report, and
#: interrupted writers' temporary files. An untracked file that appears and vanishes
#: between stops is a failed ``git add`` and a dirty tree at somebody's stop. The sidecar
#: names are DERIVED from :data:`DB_NAME` rather than spelled beside it. There is no
#: ``html/`` line: that output is retired. Why each line is here: docs/design/scaffold.md#kyo-186,
#: #kyo-215, #kyo-322.
DB_SIDECAR_SUFFIXES: tuple[str, ...] = ("-shm", "-wal")


def store_gitignore_text(
    db_name: str = DB_NAME,
    sessions_name: str = SESSIONS_NAME,
    report_name: str = DOCTOR_REPORT_NAME,
) -> str:
    """:data:`KYOSEI_GITIGNORE`'s content, as a function of the names it covers.

    A function rather than one f-string so the derivation is something a test can
    *exercise* — hand it another database name and every sidecar pattern has to move
    with it — instead of something a test can only re-spell (KYO-322.c1's "derived
    from ``DB_NAME`` rather than spelled twice").

    ``report_name`` is the file `kyo doctor --hook` writes beside the database while a
    check fails; an existing project gets it, like every later line, from
    :func:`kyosei.install_gitignore.install_store_gitignore` (docs/design/scaffold.md#kyo-362).
    The two temporary-file patterns are derived too -- from ``sessions_name`` and from
    :data:`kyosei.export.ATOMIC_TEMP_SUFFIX` -- so a writer killed between creating its
    temporary file and renaming it leaves nothing git would commit. A pattern without a
    slash matches at any depth (docs/design/scaffold.md#kyo-407).
    """
    names = (
        db_name,
        *(f"{db_name}{suffix}" for suffix in DB_SIDECAR_SUFFIXES),
        sessions_name,
        report_name,
        f"{sessions_name}.*.tmp",
        f"*{ATOMIC_TEMP_SUFFIX}",
    )
    return "".join(f"{name}\n" for name in names)


KYOSEI_GITIGNORE = store_gitignore_text()

#: Directories every consumer project gets.
SCAFFOLD_DIRS: tuple[str, ...] = (IDEAS_DIR, KYOSEI_DIR, f"{KYOSEI_DIR}/export")

#: Files written from templates held in this module.
#: KYO-14.c3. Opening `ideas/` as an Obsidian vault is the intended way to use the
#: ideas layer, and Obsidian immediately writes UI state into it: `workspace.json`
#: is one person's open panes and `graph.json` one machine's graph layout, both
#: churning every session. The vault's *shared* config (app/appearance/core-plugins)
#: stays tracked, because that is project content. Written by `kyo init` so a
#: consumer does not discover this by reviewing a diff full of pane positions.
IDEAS_GITIGNORE = """\
# Obsidian UI state: one person's open panes, one machine's graph layout.
# The vault's shared config (app.json, appearance.json, core-plugins.json) is
# deliberately NOT ignored -- that is project content.
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/graph.json
"""


SCAFFOLD_FILES: tuple[tuple[str, str], ...] = (
    (f"{IDEAS_DIR}/.gitignore", IDEAS_GITIGNORE),
    (f"{KYOSEI_DIR}/export/README.md", EXPORT_README),
    (f"{KYOSEI_DIR}/.gitignore", KYOSEI_GITIGNORE),
)


@dataclass(frozen=True)
class Asset:
    """A file copied from a packaged directory, ``defaults/`` unless it says otherwise."""

    source: str  # relative to ``package_dir``
    target: str  # relative to the project root
    #: KYO-593: the directory under the package that holds ``source``. Every asset but
    #: the `/kyosei` skill's two files lives in ``defaults/``; those moved into the Claude
    #: Code plugin's tree (``harness/claude-code/plugin/skills/kyosei/``) so that the
    #: plugin and `kyo install-skill` read one copy.
    package_dir: str = "defaults"

    @property
    def source_path(self) -> Path:
        """Where the checkout that builds this package keeps ``source``, relative to
        the code root (:data:`PACKAGED_ASSET_SOURCE_DIR` for a ``defaults/`` asset)."""
        return PACKAGE_SOURCE_DIR / self.package_dir / self.source

    def packaged_path(self) -> Path:
        """Where the running build ships ``source``."""
        return Path(__file__).resolve().parent / self.package_dir / self.source


#: KYO-212: the standing-rules ledger, named on its own because it is the one asset
#: `kyo init` copies that a later release must be able to reach again — every other
#: default here either ships empty or is read from the code when a project has none,
#: while a rule added to this file after a project's `kyo init` reached that project
#: through nothing at all. `kyo install-procedures` and `kyo doctor`'s
#: ``procedures_rules_missing`` / ``procedures_rules_differ`` checks are the two
#: halves of that reach.
PROCEDURES_ASSET = Asset("procedures.md", f"{KYOSEI_DIR}/procedures.md")

#: The default kind registry and standing-rules ledger, copied verbatim into every
#: new project. ``kyosei/config.yml`` is not here: it is generated per project by
#: :func:`kyosei.config.write_config`, not copied from a template.
DEFAULT_ASSETS: tuple[Asset, ...] = (
    Asset("kinds.yml", f"{KYOSEI_DIR}/kinds.yml"),
    # KYO-100: the scope registry, shipped declaring no scopes. A scope name is a claim
    # about how THIS project is divided, so a seeded set would be names to delete before
    # the first true one is written; what the file ships is the shape, the rules and a
    # commented example. (Seeding scopes from a project type is left for later.) A
    # project that predates this asset simply has no scopes.yml, which the loader reads
    # as an empty registry rather than an error — see kyosei/registry.py.
    Asset("scopes.yml", f"{KYOSEI_DIR}/scopes.yml"),
    # KYO-101: the status registry, shipped declaring v1's ladder exactly. Unlike
    # scopes.yml this one is seeded rather than empty — a project with no statuses could
    # not move a record at all — and the seed is the same file `contracts.default_ladder`
    # reads for a project that has none, so "the default reproduces v1's ladder" is true
    # by construction rather than by two lists agreeing today (law 3). A store written
    # before this asset simply has no statuses.yml and gets the same ladder.
    Asset("statuses.yml", f"{KYOSEI_DIR}/statuses.yml"),
    # KYO-102: the gate-tag registry, shipped declaring no tags — scopes.yml's shape
    # rather than statuses.yml's, and for scopes.yml's reason: what a project waits on
    # is a claim about that project, and the tool works unchanged with none declared
    # (a gate's --do payload is untouched). A store written before this asset has no
    # gate-tags.yml, which the loader reads as an empty registry.
    Asset("gate-tags.yml", f"{KYOSEI_DIR}/gate-tags.yml"),
    # KYO-108: saved Base definitions (the store's query primitive), shipped declaring no
    # bases — scopes.yml's shape, for a sharper version of scopes.yml's reason. A seeded
    # base would have to name a status or a kind out of the project's own registries,
    # and a project that renamed its ladder would then hold a bases.yml that fails to
    # load, taking every base it wrote itself down with it. What the file ships is the
    # shape, the field set and two commented examples. A store written before this asset
    # has no bases.yml, which the loader reads as an empty registry rather than an error.
    Asset("bases.yml", f"{KYOSEI_DIR}/bases.yml"),
    # KYO-129: the Layer 3 document registry, shipped declaring none. Its real default
    # (root-level *.md plus docs/**/*.md) lives in the CODE, so a store that predates this
    # file gets it too; declaring anything replaces that default (docs/design/scaffold.md#kyo-129).
    Asset("documents.yml", f"{KYOSEI_DIR}/documents.yml"),
    PROCEDURES_ASSET,
)

#: KYO-309: where the checkout that BUILDS this package keeps the *source* of every
#: packaged asset, relative to the code root — the other side of :func:`defaults_dir`,
#: which finds the copy the running build ships.
#:
#: Its presence is the whole detector, and it is a **file test, not a name test**: a
#: project is the package's source repository when it contains the file the package is
#: built from, whatever the project is called and whatever id prefix it allocates.
#: Nothing here can be fooled by a consumer named "kyosei", and nothing here stops
#: working when this repository is renamed or cloned somewhere else.
#:
#: Derived from ``__package__`` rather than spelled, so a package rename cannot leave a
#: literal behind. ``tests/test_doctor.py`` pins it against
#: :data:`procedures.PACKAGED_LEDGER_SOURCE_PATH`, which states the same path for the
#: ledger's half and predates this (law 3: two spellings, one checked against the other).
PACKAGE_SOURCE_DIR = Path("src") / (__package__ or "kyosei")
PACKAGED_ASSET_SOURCE_DIR = PACKAGE_SOURCE_DIR / "defaults"


def repo_asset_source(root: Path, asset: Asset) -> Path | None:
    """``asset``'s source file under ``root``, when ``root`` builds this package.

    ``None`` for every consumer project, which is what keeps KYO-309's reversal from
    reaching one: a project that does not carry
    :data:`PACKAGED_ASSET_SOURCE_DIR` is checked exactly as it was before. KYO-593: an
    asset outside ``defaults/`` is looked for under its own :attr:`Asset.package_dir`.
    """
    path = root / asset.source_path
    return path if path.is_file() else None


#: KYO-318: the packaged hook fragments' subdirectory, under both
#: :func:`defaults_dir` (the running build's copy) and
#: :data:`PACKAGED_ASSET_SOURCE_DIR` (the source those are built from). Named once
#: because those two spellings have to agree or the comparison below is meaningless.
HOOKS_SOURCE_SUBDIR = "hooks"


def defaults_dir() -> Path | None:
    """Locate the repo's ``defaults/`` directory, if this checkout has one."""
    packaged = Path(__file__).resolve().parent / "defaults"
    if packaged.is_dir():
        return packaged
    repo = Path(__file__).resolve().parents[2] / "defaults"
    return repo if repo.is_dir() else None


def repo_hooks_source(root: Path) -> Path | None:
    """``root``'s own hook-fragment directory, when ``root`` builds this package.

    :func:`repo_asset_source`'s shape for the one packaged thing that is a *directory*
    rather than an :class:`Asset`: the hook fragments have no single target file (they
    are merged into ``.claude/settings.json``), so there is nothing for the Asset table
    to hold, but the question "which copy of these is the source" is the same question
    and has the same answer. ``None`` for every consumer project.
    """
    path = root / PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR
    return path if path.is_dir() else None


#: KYO-318: how the running build stands to a checkout, in words a sentence can take.
#: Three answers because there are three honest ones, and the third is not a failure:
#: content alone cannot say which of two differing texts came first, so the direction
#: is asked of git, and git cannot answer about a build made from a different clone.
BUILD_OLDER = "older than"
BUILD_NEWER = "newer than"
BUILD_AGE_UNKNOWN = "a different build from"


def build_age_against(root: Path) -> str:
    """Whether the running build's own commit is behind or ahead of ``root``'s HEAD.

    Answered by ancestry rather than by dates or by the files themselves: a commit
    reachable from ``HEAD`` is one this checkout already contains, which is exactly
    what "the build is older" means, and the reverse reading is equally exact. Any
    other relation — a build with no stamp, a checkout with no git, a commit from a
    clone this one has never seen — is :data:`BUILD_AGE_UNKNOWN`, which is a true
    statement rather than a guess at one of the other two.
    """
    from . import buildinfo

    commit = buildinfo.build_identity().commit
    if not commit:
        return BUILD_AGE_UNKNOWN
    # KYO-569 made `_git` refuse a missing directory instead of reading it as "no git";
    # here a missing checkout is one more case whose answer is unknown, not an error.
    if not Path(root).is_dir():
        return BUILD_AGE_UNKNOWN
    behind = _git(root, "merge-base", "--is-ancestor", commit, "HEAD")
    if behind is not None and behind.returncode == 0:
        return BUILD_OLDER
    ahead = _git(root, "merge-base", "--is-ancestor", "HEAD", commit)
    if ahead is not None and ahead.returncode == 0:
        return BUILD_NEWER
    return BUILD_AGE_UNKNOWN


def copy_assets(root: Path, assets: tuple[Asset, ...] = DEFAULT_ASSETS) -> list[Path]:
    """Copy each default asset into the project. Missing sources fail loudly."""
    if not assets:
        return []
    source_dir = defaults_dir()
    if source_dir is None:
        raise KyoseiError(
            "this build declares default assets but ships no defaults/ directory; "
            "reinstall kyosei"
        )
    written: list[Path] = []
    for asset in assets:
        source = source_dir / asset.source
        if not source.is_file():
            raise KyoseiError(f"default asset {asset.source} is missing from {source_dir}")
        target = root / asset.target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        written.append(target)
    return written


def existing_store_refusal(root: Path, *, asked_for_split: bool) -> str:
    """Why `kyo init` will not scaffold over the store already at ``root`` (KYO-73.c2).

    One message for both callers below, composed from three facts read off the disk
    at the moment of the refusal rather than assumed:

    * ``kyosei/config.yml`` is there (the caller has already established that);
    * whether ``kyosei/kyosei.db`` is there;
    * what ``layout_at_init`` (KYO-60) records — ``split`` means this directory *is*
      a store, ``in-repo`` means it is a code repository carrying one, and absent
      means the store predates the key and nothing recorded which it was.

    Each fact is read, never inferred: a fresh clone of a split store has no database by
    design, and must be told to `kyo rebuild` rather than pointed at moving an in-repo
    store it does not have (docs/design/scaffold.md#kyo-73).

    The README pointer stays exactly where it is true — a live in-repo store, database
    and all, being asked to become a split one — and appears nowhere else.
    """
    config_path = config_path_for(root)
    database = root / KYOSEI_DIR / DB_NAME
    layout_at_init = read_raw_config(root).get("layout_at_init")
    head = f"{config_path} already exists; {root} already holds a Kyosei store"

    if not database.is_file():
        # Exactly one command is named here, and it is the one that clears this state.
        # `init` is referred to by description rather than in the backticked form the
        # rest of the tool uses for "run this": a message whose subject is a command
        # that does not apply must not also read as an offer to run it, which is the
        # whole of KYO-73.
        parts = [
            f"{head}, and its database ({database}) is not there. This command scaffolds "
            "a new store; it does not build a database from an existing kyosei/export/, "
            "so it is not what clears this state.",
            f"Run `kyo rebuild` in {root} to reconstruct {DB_NAME} from "
            f"{root / KYOSEI_DIR / 'export'}. `{DB_NAME}` is gitignored, so a fresh clone "
            "of a store repository has exactly this shape and this is its first command.",
        ]
        if layout_at_init == LAYOUT_SPLIT:
            parts.append(
                f"({config_path} records `layout_at_init: {LAYOUT_SPLIT}`, so this "
                "directory is itself a store rather than a code repository carrying one; "
                "a code repository is pointed at it from that repository, not from here.)"
            )
        return " ".join(parts)

    if asked_for_split and layout_at_init == LAYOUT_SPLIT:
        return (
            f"{head}, and {config_path} records `layout_at_init: {LAYOUT_SPLIT}` — this "
            "directory is a store, not a code repository with a store inside it, so there "
            f"is nothing here to split out. To point a code repository at this store, run "
            f"`kyo init --store {root}` from that repository."
        )
    if asked_for_split:
        known = (
            f"{root} already carries an in-repo store"
            if layout_at_init == LAYOUT_IN_REPO
            else (
                f"{config_path} records no `layout_at_init` (it predates KYO-60), so "
                "nothing here says whether this store was created inside a code "
                "repository or as a store of its own; if it is an in-repo store"
            )
        )
        return (
            f"{config_path} already exists; {known}. Moving an existing store out of a "
            "code repository is not something `kyo init` does — see "
            "docs/reference/topics/store.md (in Kyosei's own repository), \"Moving an "
            'existing store out of the code repository"'
        )
    return f"{head}; this is already a Kyosei project"


def init_project(
    root: Path, *, prefix: str | None = None, layout: str | None = None
) -> tuple[list[Path], str]:
    """Create the project skeleton. Returns the paths written and the chosen prefix.

    ``layout`` is recorded in ``config.yml`` as ``layout_at_init`` (KYO-60): a
    historical fact about which layout this project was created under, which is
    the only thing that separates a project that *chose* the in-repo layout from
    one that predates the choice existing at all.
    """
    root = root.resolve()
    if config_path_for(root).exists():
        raise KyoseiError(existing_store_refusal(root, asked_for_split=False))

    chosen = ids.normalise_prefix(prefix) if prefix else ids.normalise_prefix(root.name)
    if prefix and chosen != prefix.upper():
        raise UsageError(
            f"--prefix {prefix!r} is not a legal project prefix "
            f"(2-4 characters, letters and digits, starting with a letter)"
        )

    written: list[Path] = []
    root.mkdir(parents=True, exist_ok=True)
    for relative in SCAFFOLD_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)
    for relative, content in SCAFFOLD_FILES:
        path = root / relative
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            written.append(path)

    written.append(write_config(root, project_prefix=chosen, layout_at_init=layout))
    from .config import load_config

    create_database(load_config(root))
    written.append(root / KYOSEI_DIR / "kyosei.db")
    written.extend(copy_assets(root))
    return written, chosen


# ------------------------------------------------------- KYO-54: the two layouts

#: KYO-416.c8: no git call on the init path may wait forever. The one known to block
#: is a signed commit whose pinentry waits for a human who is not there; the
#: bound is generous because a slow disk is not a failure, and a timed-out call is
#: reported as a failed one (return code 124, the reason in stderr), never as "git is
#: not installed".
GIT_TIMEOUT_SECONDS = 120

#: KYO-559.c3: what every message about a repository with no commit yet names as the
#: way its identity gets recorded -- the automatic fill-in (:func:`fill_in_code_repo_id`),
#: never `kyo init --adopt`, which stays the override for a recorded identity that differs.
IDENTITY_FILL_IN_PATH = (
    "the identity is recorded automatically by the next `kyo doctor` or session-start "
    "brief (or a re-run of `kyo init` there), with no --adopt"
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    """Run ``git ARGS`` in ``cwd``: the repository there and no other, bounded in time.

    ``None`` means git itself could not be run; a call that timed out is a
    ``CompletedProcess`` with return code 124 and the reason in ``stderr``.
    """
    # KYO-416.c8 scrubbed GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE here -- an inherited
    # GIT_DIR landed a scaffold commit in the caller's repository instead of the new
    # store's. KYO-417 moved that scrub into `run_git`, for every git call in the
    # package; the init path keeps its own, longer bound (a signing pinentry).
    from .git_queries import GitTimeout, run_git

    if not Path(cwd).is_dir():
        # KYO-569.c1: subprocess raises the same FileNotFoundError for a missing cwd as
        # for a missing git, and every caller reads ``None`` as "git is not on PATH".
        raise KyoseiError(
            f"{cwd} does not exist (or is not a directory), so git cannot be run there; "
            "create it first, or name a directory that exists"
        )
    try:
        return run_git(cwd, *args, timeout=GIT_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return None
    except GitTimeout:
        return subprocess.CompletedProcess(
            ["git", *args],
            124,
            "",
            f"`git {' '.join(args)}` in {cwd} did not finish within "
            f"{GIT_TIMEOUT_SECONDS}s and was stopped",
        )


def git_toplevel(path: Path) -> Path | None:
    """The git work tree that would contain ``path``, or ``None``.

    Asked of the nearest ancestor that exists, because the caller's interest is a
    directory it is about to *create*: "would this new store land inside somebody
    else's repository?" Returns ``None`` when git is unavailable — an
    unanswerable question is not a refusal.
    """
    probe = path
    while not probe.is_dir():
        if probe.parent == probe:
            return None
        probe = probe.parent
    result = _git(probe, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def check_store_location(store_root: Path) -> None:
    """Refuse a split store that would land inside an existing git repository.

    One git repository per project (KYO-56): a new store nested inside any existing work
    tree -- ``~/kyosei`` itself made a repo, a dotfiles repo that owns the home directory,
    another project's store -- would make that repository carry two projects' records, so
    it is refused here rather than discovered later in a diff (docs/design/scaffold.md#kyo-56).
    """
    if store_root.is_dir() and any(store_root.iterdir()):
        raise KyoseiError(
            f"{store_root} already exists and is not empty; refusing to make it a store "
            "repository. Pass --store DIR to choose a different location, or clear it "
            "yourself first"
        )
    enclosing = git_toplevel(store_root)
    if enclosing is not None:
        raise KyoseiError(
            f"{store_root} is inside the git repository at {enclosing}, so this project's "
            f"store would be committed there alongside whatever else that repository "
            "holds. Each project's store is its own git repository (KYO-56), so no store "
            "repository ever carries two projects' records. Pass --store DIR pointing "
            f"outside {enclosing}, or set KYOSEI_HOME to a directory that is not inside a "
            "git repository"
        )


def init_store_repository(store_root: Path) -> list[str]:
    """``git init`` the split store and commit the scaffold. Returns note lines.

    Never fatal. A store that could not be committed is a real problem — an
    uncommitted store is the durability loss this whole record refused — but the
    fix is a git command the consumer can run, so it is reported rather than made
    into a failed `kyo init` that leaves a half-made project behind.
    """
    if _git(store_root, "init", "-q", "-b", "main") is None:
        return [
            f"git is not on PATH, so {store_root} is not a git repository yet — run "
            "`git init` there and commit it: an untracked store survives only as "
            "working-tree files, which a fresh clone, a `git clean`, or a lost laptop "
            "destroys with no recovery path"
        ]
    if not (store_root / ".git").exists():
        # Older git without `-b`; the default branch name is then whatever the
        # user's git configures, which is theirs to decide.
        _git(store_root, "init", "-q")
    notes = [f"initialised {store_root} as its own git repository"]
    _git(store_root, "add", "-A")
    # KYO-416.c7: the scaffold commit is Kyosei's, so what the consumer's global git config
    # would add to it is pinned off -- signing (a pinentry can block forever) and hooks (a
    # global pre-commit can refuse it). ``core.hooksPath`` at the null device runs no hook,
    # and ``--no-verify`` says the same for the two it covers (docs/design/scaffold.md#kyo-416).
    committed = _git(
        store_root,
        "-c",
        "user.name=Kyosei",
        "-c",
        "user.email=kyosei@localhost",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "commit",
        "--no-verify",
        "-q",
        "-m",
        f"Kyosei store for {store_root.name}: initial scaffold",
    )
    if committed is None or committed.returncode != 0:
        notes.append(
            f"could not make the first commit in {store_root} "
            f"({(committed.stderr.strip() if committed else 'git unavailable')}) — commit "
            "it yourself; an uncommitted store is not durable"
        )
    else:
        notes.append(f"committed the scaffold in {store_root}")
    return notes


@dataclass(frozen=True)
class InitResult:
    """What `kyo init` did, in enough detail for the command to report it."""

    layout: str
    code_root: Path
    store_root: Path
    prefix: str
    written: list[Path] = field(default_factory=list)
    link_path: Path | None = None
    git_notes: list[str] = field(default_factory=list)
    #: True when the store already existed and was linked to rather than created.
    adopted: bool = False
    #: KYO-416: what `kyo init` could establish about whether an adopted store is this
    #: code repository's — ``"verified"``, ``"unverified"`` or ``"overridden"``
    #: (``--adopt`` past a recorded identity that differs) — and ``None`` when nothing
    #: was adopted.
    adoption_check: str | None = None
    #: KYO-416: the root-commit identity of the code repository, as recorded in (or
    #: compared against) the store's ``code_repo_id``; ``None`` when it has none.
    code_repo_id: str | None = None
    #: KYO-558.c4: the CLAUDE.md `kyo init` gave its setup-incomplete section, or
    #: ``None`` when it was not asked to or the file already had a Kyosei section.
    claude_md: Path | None = None
    #: KYO-559.c4: `kyo init` re-run where this code repository already links to a store
    #: -- nothing was scaffolded or linked, and :attr:`git_notes` is what the run changed.
    rerun: bool = False


def code_repo_identity(code_root: Path) -> str | None:
    """KYO-416: the code repository's history identity — its root-commit sha(s),
    sorted and comma-joined — or ``None`` when none can be established honestly.

    The same value, by the same rule, as ``git_queries._git_history_identity`` (the
    ``code_repo_id`` a sweep records; a test holds the two equal), asked through
    :func:`_git` so an inherited ``GIT_DIR`` cannot make `kyo init` record some other
    repository's history. ``None`` for no git, no repository, no commits yet, or a
    shallow clone, whose oldest commit is a graft boundary rather than a root.
    """
    if not code_root.is_dir():
        return None
    shallow = _git(code_root, "rev-parse", "--is-shallow-repository")
    if shallow is None or shallow.returncode != 0 or shallow.stdout.strip() == "true":
        return None
    roots = _git(code_root, "rev-list", "--max-parents=0", "HEAD")
    if roots is None or roots.returncode != 0:
        return None
    shas = sorted(line.strip() for line in roots.stdout.splitlines() if line.strip())
    return ",".join(shas) or None


def same_code_history(recorded: str, here: str) -> bool:
    """Whether two ``code_repo_id`` values name the same history: they share a root
    commit. Not string equality, because merging an unrelated history into a
    repository adds a root without making it a different project."""
    return bool(set(recorded.split(",")) & set(here.split(",")))


def planned_store_root(
    code_root: Path,
    *,
    store_dir: str | Path | None = None,
    in_repo: bool = False,
    environ: dict[str, str] | None = None,
) -> Path:
    """Where `kyo init` will put (or find) this project's store — the one derivation
    both :func:`init_layout` and the line `kyo init` prints about it use (KYO-416.c10)."""
    code_root = code_root.resolve()
    if in_repo:
        return code_root
    if store_dir is not None:
        store_root = Path(store_dir).expanduser()
        return (store_root if store_root.is_absolute() else code_root / store_root).resolve()
    # KYO-569.c2: resolved like ``code_root`` and like every path init_project writes, so
    # a ``$KYOSEI_HOME`` reached through a symlink (macOS's /var -> /private/var) never
    # pairs a resolved path with an unresolved one in a ``relative_to``.
    return default_store_dir(code_root.name, environ).resolve()


def _adoption_check(
    code_root: Path,
    store_root: Path,
    *,
    recorded: str | None,
    here: str | None,
    explicit_store: bool,
    adopt: bool,
) -> tuple[str, str]:
    """KYO-416: decide whether ``code_root`` may adopt the store at ``store_root``.

    Returns ``(verdict, note)`` or raises. A recorded identity that shares no root
    commit with this repository is refused unless ``--adopt``. An adoption that
    cannot be verified — the store records no identity (every store before
    KYO-416), or this repository has no commits yet — is refused on the implicit
    default path, where the only evidence of belonging is a shared directory
    basename, and allowed on an explicit ``--store DIR``, where naming the store IS
    the evidence (docs/reference/topics/store.md's "Moving an existing store out of the
    code repository" ends that way); either way it is never silent.
    """
    remedies = (
        "Pass --adopt to link this store anyway, if it really is this repository's "
        "(a fork, or a re-created repository); or pass --store DIR to give this "
        "repository a store of its own somewhere else"
    )
    if recorded is not None and here is not None:
        if same_code_history(recorded, here):
            return "verified", (
                f"the store records this repository's history (root commit {here}), so "
                "it is this project's own store"
            )
        if not adopt:
            raise KyoseiError(
                f"refusing to link {code_root} to the store at {store_root}: that store "
                f"was scaffolded for a different code repository (root commit "
                f"{recorded}), and {code_root} has root commit {here}. Linking it would "
                "put this repository's records, sweeps, dispatch worktrees and guards on "
                f"another project's store. {remedies}"
            )
        return "overridden", (
            f"--adopt: linked although the store was scaffolded for a different code "
            f"repository (root commit {recorded}; this one is {here})"
        )
    reason = (
        f"{code_root} has no commits yet, so it has no identity to compare"
        if here is None
        else f"the store records no code repository (it predates KYO-416's code_repo_id), "
        f"so it cannot be compared with {code_root} (root commit {here})"
    )
    if not explicit_store and not adopt:
        raise KyoseiError(
            f"refusing to link {code_root} to the store at {store_root} unverified: the "
            f"only evidence it belongs to this repository is the shared directory name "
            f"{store_root.name!r}, and {reason}. {remedies}"
        )
    how = "--adopt" if adopt else "--store names it"
    return "unverified", (
        f"UNVERIFIED adoption ({how}): {reason}. Check that {store_root} is this "
        "project's store before relying on it"
    )


def init_layout(
    code_root: Path,
    *,
    prefix: str | None = None,
    store_dir: str | Path | None = None,
    in_repo: bool = False,
    adopt: bool = False,
    git_init: bool = True,
    environ: dict[str, str] | None = None,
    claude_md: bool = False,
) -> InitResult:
    """Scaffold a project under whichever of the two layouts was chosen.

    Split is the default (KYO-56). ``in_repo=True`` is the alternative, and
    ``store_dir`` names the split store explicitly instead of taking
    ``~/kyosei/<project>/``. ``adopt`` (KYO-416) links a store whose recorded code
    repository differs from this one, or whose ownership cannot be verified on the
    default path — see :func:`_adoption_check`.

    ``claude_md`` (KYO-558.c4, what `kyo init` passes) gives the code repository's
    CLAUDE.md the setup-incomplete Kyosei section when it has none -- written before
    the first commit when this call makes the repository, so that commit still holds
    every file `kyo init` wrote there.
    """
    code_root = code_root.resolve()
    if in_repo:
        if store_dir is not None:
            raise UsageError(
                "--in-repo and --store contradict each other: --in-repo puts the store "
                "inside the code repository, --store puts it at a path you name"
            )
        if adopt:
            raise UsageError(
                "--in-repo and --adopt contradict each other: --adopt links an existing "
                "split store, --in-repo scaffolds a new store inside the code repository"
            )
        written, chosen = init_project(code_root, prefix=prefix, layout=LAYOUT_IN_REPO)
        # KYO-206.c1: the code repository ignores the worker report path from the
        # moment the project exists, under both layouts and by the same call.
        ignore_changes, _ = install_report_gitignore(code_root)
        return InitResult(
            layout=LAYOUT_IN_REPO,
            code_root=code_root,
            store_root=code_root,
            prefix=chosen,
            written=written,
            git_notes=ignore_changes,
            claude_md=_claude_md_for_init(code_root) if claude_md else None,
        )

    store_root = planned_store_root(code_root, store_dir=store_dir, environ=environ)
    if config_path_for(code_root).is_file():
        raise KyoseiError(existing_store_refusal(code_root, asked_for_split=True))

    existing_link = store_link_path(code_root)
    if existing_link.is_file():
        # KYO-559: recording a missing identity is a fill-in, not an adoption, so a
        # re-run needs no --adopt (which stays the override for a mismatch).
        recorded_result = _record_identity_into_linked_store(code_root, store_root)
        if recorded_result is not None:
            return recorded_result
    if existing_link.is_file():
        raise KyoseiError(
            f"{existing_link} already exists; {code_root} already links to a store. "
            "Read that file to see where, or delete it if the store it names is gone"
        )

    # Adoption. A store that already exists at the named path is *linked to*, not
    # refused and not scaffolded over. This is the second half of moving a store
    # out of a code repository (docs/reference/topics/store.md, "Moving an existing store
    # out of the code repository"), and it is also what a second checkout of the same code needs
    # when it did not inherit the link. Which of the two happened is stated in the
    # output rather than left for the consumer to infer from an empty file list
    # (law 6: "success" must say what succeeded).
    #
    # KYO-416: and it is checked. The default path is `$KYOSEI_HOME/<basename>`, so
    # `~/oss/api` and `~/work/api` name the same store; a store existing there is
    # evidence of a shared basename and nothing more.
    adopting = config_path_for(store_root).is_file()
    bootstrap_notes: list[str] = []
    claude_md_path: Path | None = None
    here = code_repo_identity(code_root)
    adoption_check: str | None = None
    if adopting:
        from .config import load_config

        stored = load_config(store_root)
        chosen = stored.project_prefix
        if prefix is not None and ids.normalise_prefix(prefix) != chosen:
            raise KyoseiError(
                f"--prefix {prefix} contradicts the store at {store_root}, which already "
                f"exists and whose records use the prefix {chosen}; adopting it cannot "
                f"change that. Re-run without --prefix (or with --prefix {chosen}) to "
                "link that store, or pass --store DIR to scaffold a new store with "
                f"prefix {prefix} somewhere else"
            )
        adoption_check, verdict_note = _adoption_check(
            code_root,
            store_root,
            recorded=stored.code_repo_id,
            here=here,
            explicit_store=store_dir is not None,
            adopt=adopt,
        )
        written: list[Path] = []
        git_notes = [
            f"adopted the existing store at {store_root} (nothing was scaffolded); its "
            f"records already use the prefix {chosen}",
            verdict_note,
        ]
        if not (store_root / ".git").exists():
            git_notes.extend(init_store_repository(store_root))
    else:
        check_store_location(store_root)
        # KYO-569.c1: `kyo init --defaults DIR` creates DIR, as `git init DIR` does --
        # after every refusal above, so a refused init still writes nothing.
        code_root.mkdir(parents=True, exist_ok=True)
        # KYO-507: after the refusals above (nothing may be written for a refused init),
        # before the identity is read.
        if git_init and git_toplevel(code_root) is None:
            bootstrap_notes, claude_md_path = _bootstrap_code_repository(
                code_root, store_root, claude_md=claude_md
            )
            here = code_repo_identity(code_root)
            claude_md = False
        store_root.mkdir(parents=True, exist_ok=True)
        written, chosen = init_project(
            store_root,
            prefix=prefix or ids.normalise_prefix(code_root.name),
            layout=LAYOUT_SPLIT,
        )
        # KYO-416.c4: recorded before the first commit, so the store's own history
        # carries which code repository it was made for from its first line.
        if here is not None:
            write_config(store_root, project_prefix=chosen, code_repo_id=here)
            identity_note = (
                f"recorded this code repository's identity (root commit {here}) in the "
                "store's config, so a later `kyo init` elsewhere cannot adopt it by name"
            )
        else:
            identity_note = (
                f"{code_root} has no commits yet, so no code-repository identity was "
                "recorded in the store; a later adoption of it will be reported "
                "unverified until it has a first commit. After that, "
                + IDENTITY_FILL_IN_PATH
                + (
                    ""
                    if git_init or (code_root / ".git").exists()
                    else " (--no-git-init: this directory is not a git repository; "
                    "`git init` and commit first)"
                )
            )
        git_notes = [*bootstrap_notes, identity_note, *init_store_repository(store_root)]
    link_path = write_store_link(code_root, store_root)
    # KYO-206.c1, and it belongs to the CODE repository under both layouts: the report
    # is written in the worktree, which is a checkout of the code repository, and the
    # store's own `.gitignore` is a different repository's and would never be consulted.
    git_notes.extend(install_report_gitignore(code_root)[0])
    if claude_md:
        claude_md_path = _claude_md_for_init(code_root)
    return InitResult(
        layout=LAYOUT_SPLIT,
        code_root=code_root,
        store_root=store_root,
        prefix=chosen,
        written=written,
        link_path=link_path,
        git_notes=git_notes,
        adopted=adopting,
        adoption_check=adoption_check,
        code_repo_id=here,
        claude_md=claude_md_path,
    )


def _pinned_commit(
    cwd: Path, message: str, *, paths: list[str] | None = None
) -> subprocess.CompletedProcess | None:
    """A commit by Kyosei with the host's signing and hooks pinned off (KYO-416.c7).

    ``paths`` limits the commit to those paths, leaving anything else staged there
    staged and uncommitted (KYO-559: the identity fill-in commits in a live store).
    """
    return _git(
        cwd,
        "-c",
        "user.name=Kyosei",
        "-c",
        "user.email=kyosei@localhost",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "commit",
        "--no-verify",
        "-q",
        "-m",
        message,
        *(["--", *paths] if paths else []),
    )


def _claude_md_for_init(code_root: Path) -> Path | None:
    from .install_skill import claude_md_for_init

    return claude_md_for_init(code_root)


def _bootstrap_code_repository(
    code_root: Path, store_root: Path, *, claude_md: bool = False
) -> tuple[list[str], Path | None]:
    """KYO-507: ``code_root`` is in no git repository, so make it one and commit exactly
    the files `kyo init` writes there (``.gitignore``, the ``.kyosei`` store link and,
    with ``claude_md``, KYO-558's CLAUDE.md), so the code-repository identity exists to
    be recorded. Never fatal; returns notes, and the CLAUDE.md written or ``None``.
    """
    if _git(code_root, "init", "-q", "-b", "main") is None:
        return [
            f"git is not on PATH, so {code_root} is not a git repository: no "
            "code-repository identity can be recorded yet. Install git, run `git init` "
            "and commit there; then " + IDENTITY_FILL_IN_PATH
        ], (_claude_md_for_init(code_root) if claude_md else None)
    if not (code_root / ".git").exists():
        _git(code_root, "init", "-q")
    write_store_link(code_root, store_root)
    install_report_gitignore(code_root)
    claude_md_path = _claude_md_for_init(code_root) if claude_md else None
    notes = [f"{code_root} was not a git repository: ran `git init` there"]
    names = [".gitignore", store_link_path(code_root).name]
    if claude_md_path is not None:
        names.append(claude_md_path.name)
    _git(code_root, "add", "--", *names)
    committed = _pinned_commit(code_root, "kyo init: the files kyo init wrote here")
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    if committed is None or committed.returncode != 0:
        notes.append(
            "could not make the first commit "
            f"({(committed.stderr.strip() if committed else 'git unavailable')}); commit "
            f"{listed} yourself; then " + IDENTITY_FILL_IN_PATH
        )
    else:
        notes.append(f"committed {listed} (the only files kyo init wrote here)")
    return notes, claude_md_path


def fill_in_code_repo_id(store_root: Path, code_root: Path) -> list[str]:
    """KYO-559: record the code repository's identity in a split store that has none.

    A fill-in, never an adoption: it writes ``code_repo_id`` only where the store's
    config records NONE (KYO-559.c7 -- a recorded identity, matching or not, is never
    overwritten by any automatic path; a mismatch stays ``kyo init --adopt``'s, through
    :func:`_adoption_check`) and only where ``code_root`` now has a root commit. Shared
    by `kyo init` re-run, `kyo doctor` and the session-start brief, so the three cannot
    disagree about when it happens.

    Returns the lines saying what was done -- the first one names the identity -- or
    ``[]`` when nothing was: the in-repo layout (no adoption check reads the key there),
    a code root that is a linked git worktree (a dispatch worktree never writes the
    store), no commit yet, or an identity already recorded.
    """
    from .config import git_worktree_facts, load_config

    store_root, code_root = Path(store_root), Path(code_root)
    if not config_path_for(store_root).is_file() or not code_root.is_dir():
        return []
    if store_root.resolve() == code_root.resolve():
        return []
    stored = load_config(store_root)
    if stored.code_repo_id is not None:
        return []
    here = code_repo_identity(code_root)
    if here is None:
        return []
    if git_worktree_facts(code_root).main_checkout is not None:
        return []
    write_config(store_root, project_prefix=stored.project_prefix, code_repo_id=here)
    notes = [
        f"recorded this code repository's identity (root commit {here}) in the config of "
        f"the store at {store_root}, which recorded none"
    ]
    if (store_root / ".git").exists():
        config_rel = config_path_for(store_root).relative_to(store_root).as_posix()
        _git(store_root, "add", "--", config_rel)
        committed = _pinned_commit(
            store_root, "Kyosei: record the code repository's identity", paths=[config_rel]
        )
        notes.append(
            f"committed it in {store_root}"
            if committed is not None and committed.returncode == 0
            else f"could not commit it in {store_root}; commit the config change yourself"
        )
    return notes


def _record_identity_into_linked_store(code_root: Path, store_root: Path) -> InitResult | None:
    """KYO-507/KYO-559: `kyo init` re-run where this repository already links to a store
    that records no identity, now that a first commit exists: the identity is filled in
    (:func:`fill_in_code_repo_id`) with or without ``--adopt``. ``None`` when there is
    nothing to fill in (the caller refuses, as before)."""
    from .config import load_config, read_store_link

    linked = read_store_link(code_root)
    if linked is None or not config_path_for(linked).is_file():
        return None
    linked = linked.resolve()
    notes = fill_in_code_repo_id(linked, code_root)
    if not notes:
        return None
    stored = load_config(linked)
    return InitResult(
        layout=LAYOUT_SPLIT,
        code_root=code_root,
        store_root=linked,
        prefix=stored.project_prefix,
        git_notes=notes,
        adopted=True,
        adoption_check="verified",
        code_repo_id=stored.code_repo_id,
        rerun=True,
    )


def _reinstall_route() -> str:
    """How to move *this* copy to a build made from the checkout it is standing in.

    Read off :func:`buildinfo.upgrade_route` rather than written out, so a refusal
    below names the route for the install that is actually running, rather than
    assuming every copy moves with the same command (KYO-317).
    """
    from . import buildinfo

    return buildinfo.upgrade_route()


# ------------------ KYO-318: the WRITE side of KYO-309, for the three install commands
#
# In the repository this package is built from, the three install commands would write
# an older installed build's copy over the newer source (docs/design/scaffold.md#kyo-318).
#
# So the refusal is at the point of the write, and it is one shape for all three:
#
#   * it fires only where BOTH halves are true -- this checkout carries the source AND
#     the running build's packaged copy differs from it. Identical copies mean there is
#     nothing to protect and the command behaves exactly as it always has, which is also
#     every run from a build made out of this checkout (an editable install resolves
#     both paths to the same directory);
#   * a consumer project has no source here at all, so `repo_asset_source` /
#     `repo_hooks_source` answer None and not one line of this is reachable for them;
#   * `--print` is exempt, on `install_procedures_refusal`'s precedent: a preview that
#     touches nothing is what lets a reader see the situation rather than only be
#     refused it;
#   * every refusal names two remedies and each is proved by a test to clear the
#     refusal, or to reach what the command was asked for, without losing the newer
#     text -- the bar KYO-309 set for its findings, applied to these.


def _source_repo_refusal(
    root: Path, *, command: str, what: str, source_path: Path, instead: list[str]
) -> str:
    """The common wording. ``instead`` is the remedy list, in the reader's order."""
    lines = [
        f"`{command}` writes this build's packaged {what}, and {root} is the repository "
        f"that build is made FROM: it carries {source_path}, this repository's own "
        f"source for it, and the two differ — the running build is "
        f"{build_age_against(root)} this checkout. Writing here would put the build's "
        "copy over the source's, which is the wrong direction (KYO-309, KYO-318). "
        "Nothing was written. What to do instead:",
    ]
    lines += [f"    * {remedy}" for remedy in instead]
    return "\n".join(lines)


# ------------------------------- KYO-206: the report path, ignored by the code repo

#: The comment line that identifies the block below as this tool's, so a second run
#: recognises its own work and a maintainer reading their `.gitignore` learns who wrote
#: it and why. Presence of the PATH is what makes the write idempotent; the marker is
#: for the human.
REPORT_GITIGNORE_MARKER = (
    "# a dispatched worker's structured report — written here, ingested by "
    "`kyo report`, never committed (KYO-206)"
)


def report_gitignore_block() -> str:
    """The two lines `kyo init` and `kyo install-hooks` add to the code repository's
    root ``.gitignore``."""
    return f"{REPORT_GITIGNORE_MARKER}\n{WORKER_REPORT_PATH}\n"


def install_report_gitignore(
    code_root: Path, *, print_only: bool = False
) -> tuple[list[str], list[str]]:
    """Make the code repository ignore :data:`WORKER_REPORT_PATH`. Returns
    ``(changes, notes)``.

    A path that is only *named* in the brief still lands in `git status` as an untracked
    file, so the entry is written by the two commands a consumer already runs -- ``kyo
    init`` for a new project, ``kyo install-hooks`` for one that predates this -- rather
    than left to a README instruction (docs/design/scaffold.md#kyo-206).

    **The root ``.gitignore``, not ``kyosei/.gitignore``.** The file appears in the CODE
    repository (a worker's worktree is a checkout of it), and under the split layout the
    store is a different repository entirely, whose ``.gitignore`` git would never
    consult for this path.

    Idempotent, additive, and never destructive: an existing file is appended to, never
    rewritten, and a run that finds the path already listed reports no change. Matching
    is on the path line alone, so a maintainer who added the entry themselves — with or
    without our comment, anywhere in the file — is left alone.
    """
    target = code_root / ".gitignore"
    existing = (
        target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    )
    for line in existing.splitlines():
        stripped = line.strip()
        if stripped in (WORKER_REPORT_PATH, f"/{WORKER_REPORT_PATH}"):
            return [], []
    if not print_only:
        # A newline first when the file exists and does not end in one, so an entry
        # never lands glued to somebody else's last pattern.
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        separator = "\n" if existing else ""
        target.write_text(
            existing + prefix + separator + report_gitignore_block(), encoding="utf-8"
        )
    verb = "would add" if print_only else "added"
    return [
        f"gitignore: {verb} {WORKER_REPORT_PATH} to {target} — a dispatched worker "
        "writes its report there and leaves it uncommitted; `kyo check-clean` does not "
        "count it and `kyo touch-drift` does not name it"
    ], []
