"""KYO-226: which build is this copy, and the hook that stamps the answer into it.

The defect this exists for, measured. A consumer's report carried an item that
KYO-173 had already fixed, against a build installed after the fix that
*contained* the fix — their own shift note shows the remedy working two days before they
reported it broken. Nothing on either side could have told them, because nothing on
either side knew which build was installed: `pyproject.toml`'s version string is pinned
at ``0.1.0`` and says nothing about the snapshot, and an installed wheel has no ``.git``
to ask.

**The identity is the commit, not the version string.** Every wheel, a release or a
snapshot installed with ``uv tool install .`` from a checkout, is built from
the working tree at that instant — so there is exactly one moment at which the tool's
identity is knowable, and it is build time. :func:`regenerate` is what runs then (the
in-tree PEP 517 backend in ``build_backend/kyosei_build.py`` calls it before delegating
to setuptools), and it writes :data:`STAMP_FILENAME` into the package directory. The
installed copy reads that file and needs no git, no ``.git`` and no network.

**Two answer paths, and the answer says which one it came from.** A run out of a source
tree (this repository, an editable install, a dispatch worktree) has a real git
repository above it and reads the live HEAD, which is more current than any stamp left
by an earlier build. An installed copy has only the stamp. ``kyo version --json``
carries ``source`` so a reader can tell those apart rather than guess — the difference
matters, because the live reading tracks the checkout as it moves and the stamp does
not.

**Dirty is a fact about the build, not a warning.** ``uv tool install .`` from a tree
with uncommitted edits produces a wheel whose contents match no commit that exists. That
is a legitimate thing to do and a dangerous thing not to know, so it is stated in the
same line rather than hidden behind a flag.

**And a fact about the code, which is why the store is excluded from it** (KYO-338). A
project that keeps its store in the same repository as its code has a directory there
that its own session hooks rewrite between commits: every build made in this repository
was stamped "built from a dirty working tree" with nothing modified but
``kyosei/export/``'s ledgers. A marker that is on every time says nothing on the day a
source file really was edited, which is the only day it exists for — so
:func:`store_export_relpath` finds that directory from the project's own layout and
:func:`git_identity` asks git to skip it. Nothing else is skipped, and a project whose
store is elsewhere (the split layout) or absent skips nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .clock import now

#: The build stamp, written into the package directory by :func:`regenerate` and read
#: back by :func:`build_identity`. Gitignored: it is a build product, and a committed
#: copy would be a second, stale answer to the one question this module exists to
#: answer correctly.
STAMP_FILENAME = "_build_stamp.json"

#: The distribution's name as ``kyo version`` prints it. The two console scripts are one
#: program (``kyosei`` and ``kyo``), and this is the package, so it is spelled in full.
DISTRIBUTION = "kyosei"

#: Where the identity came from, carried in ``--json`` so the two are never conflated.
SOURCE_GIT = "git"
SOURCE_STAMP = "stamp"
SOURCE_UNKNOWN = "unknown"

#: Short-sha width. Eight, matching what this repository's own commit listings use, so
#: a sha printed here can be pasted into `git show` beside one read off `git log`.
SHORT_SHA = 8

#: What says "there is a git repository rooted here" without running git.
GIT_MARKER = ".git"

#: KYO-445: a release is the commit tagged ``v<version>``. The release steps run in
#: the order tag, build, verify, publish, so the release build sees its own tag and
#: :func:`release_facts` can record
#: it; the tag is the durable record of what was published.
RELEASE_TAG_PREFIX = "v"

#: The ``git describe --match`` glob for "a release tag": the prefix and then a digit,
#: so a branch-ish tag that merely starts with ``v`` (``very-old``) is not read as one.
RELEASE_TAG_GLOB = f"{RELEASE_TAG_PREFIX}[0-9]*"

# --------------------------------------------------------------------------- KYO-317
#
# Which kind of install this copy is, derived from where its package directory sits --
# never from a stored setting or an environment variable, both of which are facts
# somebody wrote down once and this project has already shipped one upgrade line that
# went stale the moment a second install route existed beside it: `kyo version`'s "to
# move to a newer build" line, printed by the desktop app's former bundled build, sent a
# consumer project toward `uv tool install --force` for a CLI its session was not
# running (KYO-317).

#: The three honest answers. A working tree and a uv tool are each a real, structural
#: fact about the path; ``cannot-tell`` is what any other kind (a plain ``pip install``,
#: a ``pipx`` install, a location this function was never taught) gets instead of a
#: guess.
#:
#: KYO-450 retired a fourth, ``bundled`` -- "the desktop app's bundled runtime" -- with
#: the runtime itself: the app ships no Python, so no build can be one, and
#: the answer is retired rather than left unreachable. An OLDER build's ``kyo version
#: --json`` can still say ``bundled`` (the PATH build KYO-335 probes, during the
#: migration); :func:`upgrade_route` reads any kind it does not know as ``cannot-tell``,
#: whose route is the install that replaces it.
INSTALL_KIND_UV_TOOL = "uv-tool"
INSTALL_KIND_WORKING_TREE = "working-tree"
INSTALL_KIND_UNKNOWN = "cannot-tell"

#: The two path segments a ``uv tool install`` lays a tool under, immediately above
#: the per-tool directory (``.../uv/tools/kyosei/...``) -- stable across every place
#: uv's own tools directory can live (``XDG_DATA_HOME``, its platform default, a
#: ``UV_TOOL_DIR`` override), because uv does not let a consumer configure these two
#: segments away. The prefix ahead of them is deliberately ignored.
UV_TOOLS_DIR_SEGMENTS = ("uv", "tools")

#: What ``kyo version``'s "installed as" line prints for each kind.
INSTALL_KIND_LABELS = {
    INSTALL_KIND_UV_TOOL: "a uv tool install",
    INSTALL_KIND_WORKING_TREE: "a working tree",
    INSTALL_KIND_UNKNOWN: "an install this copy cannot place from its own path",
}


def install_kind(directory: Path | None = None) -> str:
    """Which of the three kinds this running copy is installed as (KYO-317.c1).

    ``directory`` is the package directory (:func:`package_dir` by default), so a test
    can hand this a made-up path and exercise every kind without three different real
    installs. Order: a working tree is checked first, because :func:`source_tree_root`
    is the stronger, pre-existing signal -- an editable install genuinely runs the
    checkout live, and nothing about *that* fact needed inventing here. The uv-tools
    marker is then a structural path check. Anything left is the third, honest answer
    rather than a guess.
    """
    directory = directory if directory is not None else package_dir()
    if source_tree_root(directory) is not None:
        return INSTALL_KIND_WORKING_TREE
    parts = [part.lower() for part in directory.parts]
    for left, right in zip(parts, parts[1:], strict=False):
        if (left, right) == UV_TOOLS_DIR_SEGMENTS:
            return INSTALL_KIND_UV_TOOL
    return INSTALL_KIND_UNKNOWN


#: KYO-446: how a machine that runs Kyosei from source re-snapshots its one `kyo`
#: from a checkout -- the Makefile target that runs ``uv tool install --force .`` with
#: ``VIRTUAL_ENV`` unset (KYO-297). Named, not paraphrased, so the remedy a reader is
#: handed is the one command ``tests/test_kyo335_path_build_lag.py`` proves the Makefile
#: carries.
RESNAPSHOT_TARGET = "make install-tool"

#: KYO-446.c3: what moves a uv-tool copy that IS a published release -- the PyPI route.
UV_TOOL_UPGRADE_COMMAND = "uv tool upgrade kyosei"


def upgrade_route(kind: str | None = None, release: bool | None = None) -> str:
    """The remedy clause for "how do I move this copy to a newer build" (KYO-317.c1).

    One function for the three sentences that used to each hardcode
    ``uv tool install --force .`` regardless of which install printed them: `kyo
    version`'s own "to move to a newer build" line (``cmd_views.cmd_version``),
    :func:`version_line`'s "build unknown" sentence below, and
    ``changelog.load``'s "no packaged changelog" sentence. All three ask the same
    question -- which route moves *this* copy -- and answering it once here is the
    difference between three routes that can agree and three that can quietly drift
    apart the way the uv-only wording did.

    KYO-446.c3: a uv tool splits by ``release``. A release build (the stamp says its
    commit carried ``v<version>``, KYO-445) came from PyPI's route and moves with
    ``uv tool upgrade kyosei``. Anything else is a snapshot of a checkout -- the case of
    a machine that runs Kyosei from source -- and ``uv tool upgrade`` would carry it to the
    latest *release*, which is behind the source it was snapshotted from; its route is
    the re-snapshot. ``None`` reads as unreleased: a build whose stamp or ``version
    --json`` predates the ``release`` fact predates every release too.

    With no ``kind``, both facts are this running copy's own. A caller that passes a
    ``kind`` describes some OTHER build (the PATH build, KYO-335) and passes that
    build's ``release`` with it -- never this copy's.
    """
    if kind is None:
        kind = install_kind()
        if release is None and kind == INSTALL_KIND_UV_TOOL:
            release = build_identity().release
    # KYO-318.n1: the per-project step (`kyo install-hooks`...) is its own sentence; see
    # :func:`per_project_upgrade_step`. KYO-450 retired the desktop app's bundled kind
    # and its "re-pack the app" route; an older build that still reports it lands in
    # the last branch below, whose route is the install that replaces it.
    if kind == INSTALL_KIND_WORKING_TREE:
        return (
            "pull or check out a newer commit in this checkout -- it runs the "
            "checkout live, so there is nothing to reinstall"
        )
    if kind == INSTALL_KIND_UV_TOOL:
        if release is True:
            return f"`{UV_TOOL_UPGRADE_COMMAND}` -- this copy is a published release"
        return (
            f"re-snapshot it from a Kyosei checkout: `{RESNAPSHOT_TARGET}` there (it runs "
            "`uv tool install --force .` with VIRTUAL_ENV unset) -- this copy is an "
            f"unreleased snapshot, so `{UV_TOOL_UPGRADE_COMMAND}` would move it to the "
            "latest release rather than newer source"
        )
    return (
        "install it as the one supported kind, since this copy's install cannot be told "
        "apart from its own path: `uv tool install kyosei` for the published release, or "
        f"`{RESNAPSHOT_TARGET}` (`uv tool install --force .` with VIRTUAL_ENV unset) from "
        "a Kyosei checkout for an unreleased snapshot, and remove whatever put this copy "
        "on PATH so the one you install is the one that answers"
    )


#: The three copies `kyo init` leaves in a project that a newer build can top up, and
#: the commands that do it — the per-project half of an upgrade, which is a different
#: question from :func:`upgrade_route`'s "how do I move this copy".
PER_PROJECT_UPGRADE_COMMANDS = (
    "`kyo install-hooks`, `kyo install-skill`, `kyo install-procedures`"
)


def per_project_upgrade_step(root: Path) -> str:
    """What to run in ``root`` after moving this copy to a newer build (KYO-318.n1).

    KYO-595.c4: for a consumer it depends on the project's harness mode
    (:func:`kyosei.cmd_harness_migrate.harness_mode`): a project on the copies runs
    :data:`PER_PROJECT_UPGRADE_COMMANDS`; one on the plugin runs nothing, because the
    plugin is read in place from the build the stable link names; one on neither is
    pointed at `kyo harness install claude-code`.

    :data:`PER_PROJECT_UPGRADE_COMMANDS` was the answer everywhere except the repository
    this package is BUILT from, where two of those three write the running build's copies over the
    checkout's own source and refuse to do it (KYO-318.c1,
    :func:`install_skill.install_skill_refusal` and friends). A line that named them there
    would be the destructive advice this record is about, printed by the command a
    reader consults precisely because they are unsure which build they have.

    Asked of the directory rather than of a name, on :func:`scaffold.repo_asset_source`'s
    reasoning: a project is the package's source repository when it carries the file the
    package is built from, and nothing here can be fooled by a consumer called "kyosei".
    """
    from . import cmd_harness_migrate, install_skill, scaffold

    if scaffold.repo_asset_source(root, install_skill.SKILL_ASSET) is None:
        # KYO-595.c4: which commands depends on the project's harness mode.
        mode = cmd_harness_migrate.harness_mode(root)
        if mode == cmd_harness_migrate.MODE_PLUGIN:
            return (
                "`kyo install-procedures` only: this project is on the Kyosei plugin, "
                "which loads the skill and hooks of whichever build the stable link "
                "points at, and procedures are a copy on every route"
            )
        if mode == cmd_harness_migrate.MODE_NONE:
            return (
                "`kyo install-procedures`; nothing for the harness, since this project "
                "carries neither the copies nor the plugin (`kyo harness install "
                "claude-code` enables the plugin, the default)"
            )
        return (
            f"{PER_PROJECT_UPGRADE_COMMANDS}, since this project is on the copies "
            "(`kyo harness migrate` moves it to the plugin, after which only "
            "`kyo install-procedures` remains)"
        )
    return (
        f"{PER_PROJECT_UPGRADE_COMMANDS} -- except in this checkout, which is the "
        f"SOURCE of what those commands install ({scaffold.PACKAGED_ASSET_SOURCE_DIR} "
        "is here). Two of the three would write an older build's copies over the newer "
        "text in this repository, and refuse to; `kyo doctor` says which files have "
        "moved apart and how to bring them together (KYO-309, KYO-318)"
    )


@dataclass(frozen=True)
class BuildIdentity:
    """One installed copy's identity: the package version and the snapshot behind it."""

    version: str
    commit: str | None
    commit_date: str | None
    dirty: bool | None
    source: str
    kind: str
    #: KYO-398.c1: the commit's instant as a full UTC timestamp in the store's own
    #: format (:data:`kyosei.clock.TIMESTAMP_FORMAT`), and the value ``commit_date`` is
    #: the date OF. ``None`` for an unknown build and for a stamp written before this
    #: field existed, which carries only the date it was given.
    commit_time: str | None = None
    #: KYO-445.c2: whether this build IS a release -- its commit carried the tag
    #: ``v<version>`` and its tree was clean when the stamp was written. Only a stamp
    #: can say ``True``: a copy running a checkout live runs whatever the tree holds
    #: now, which is never a published artifact (and an old stamp, written before this
    #: field existed, is read as ``False``, since nothing recorded that it was one).
    release: bool = False
    #: KYO-445.c3: the most recent release tag reachable from the build commit, as a
    #: version (``0.2.0``, not ``v0.2.0``), or ``None`` when there is none or it was
    #: not recorded -- what an unreleased build's line says it came "after".
    last_release: str | None = None

    @property
    def short_commit(self) -> str | None:
        return self.commit[:SHORT_SHA] if self.commit else None

    @property
    def known(self) -> bool:
        return self.commit is not None

    def as_data(self) -> dict:
        """The ``--json`` document, and the same fields the human line is built from."""
        return {
            "distribution": DISTRIBUTION,
            "version": self.version,
            "commit": self.commit,
            "commit_short": self.short_commit,
            "commit_date": self.commit_date,
            "commit_time": self.commit_time,
            "dirty": self.dirty,
            "source": self.source,
            "kind": self.kind,
            # KYO-445.c1: the two facts a reader of a release -- and the desktop app's
            # compatibility check -- needs without opening a store: whether this
            # build is a published release, and the one schema it speaks. The schema
            # is the constant this build was written against, not any store's, so it
            # is read from the code; `open_store` refuses every other one.
            "release": self.release,
            "last_release": self.last_release,
            "schema_version": _schema_version(),
            "line": version_line(self),
        }


def _schema_version() -> int:
    """The one ``schema_version`` this build speaks (``kyosei.schema.SCHEMA_VERSION``)."""
    from .schema import SCHEMA_VERSION

    return SCHEMA_VERSION


def package_dir() -> Path:
    """The directory this package is installed or imported from."""
    return Path(__file__).resolve().parent


def source_tree_root(directory: Path | None = None) -> Path | None:
    """The checkout this package is being imported *out of*, or ``None``.

    Anchored on the package's own location and never on the working directory, and that
    is the whole point: ``kyo version`` run inside a consumer's project must report the
    build of the tool, not the consumer's repository. An installed copy lives in
    ``.../site-packages/kyosei`` — whose parent is not ``src`` — and so answers ``None``
    here whatever repository the caller happens to be standing in.

    The second condition is a git repository at the root, rather than a build file
    beside it. Both of this function's callers want something only a repository has —
    ``build_identity`` wants HEAD, ``changelog.load`` wants the store that repository
    carries — so "is there a repository here" is the question that actually matters, and
    an unpacked sdist answering ``None`` is right rather than unfortunate: it has no
    HEAD to read and its packaged products are what the build left it. Naming a build
    file would also have put a path only Kyosei's own repository has inside the package,
    which ``tests/test_kyo149_consumer_paths.py`` is right to refuse.
    """
    directory = directory if directory is not None else package_dir()
    if directory.parent.name != "src":
        return None
    root = directory.parent.parent
    # A directory in a clone, a file in a worktree or a submodule — so existence, not
    # `is_dir`. A dispatch worktree is the case that makes the difference concrete.
    return root if (root / GIT_MARKER).exists() else None


def _git(root: Path, *args: str) -> str | None:
    """``git`` in ``root``, or ``None`` if it could not answer.

    ``None`` and not ``""``: a clean ``git status --porcelain`` is an empty *answer*,
    and "git is not installed" is no answer at all. Collapsing the two would report a
    tree with unknown state as clean.
    """
    # KYO-417: routed, and this is the call the GIT_DIR scrub matters most to -- it
    # probes the installed package's OWN source tree, so an inherited GIT_DIR would
    # have answered for whichever repository the caller happened to be in.
    from .git_queries import run_git

    try:
        result = run_git(root, *args, timeout=10)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def store_export_relpath(root: Path) -> str | None:
    """The project's store export directory inside ``root``, or ``None`` (KYO-338).

    Resolved through the same discovery every other command uses --
    :func:`config.resolve_layout` from ``root`` upward, then the store's own
    ``export_dir`` key -- rather than spelled here, because ``kyosei/export`` is this
    project's answer and not the package's: a consumer may have named the directory
    something else, and ``tests/test_kyo149_consumer_paths.py`` is right to refuse a
    path only one repository has from inside the package.

    ``None`` is the common case and means *exclude nothing*. It covers a checkout with
    no project at or above it, a store whose config cannot be read, and — the case worth
    naming — the split layout, where the store is its own git repository somewhere else
    and cannot dirty this one at all. The path is returned relative to ``root``, which
    :func:`source_tree_root` has already established is a repository root, so it is what
    git's ``:(top)``-anchored pathspecs want.

    KYO-355: ``store_root_in_this_checkout`` and not ``store_root``. The answer here is
    a pathspec handed to ``git status`` **in ``root``**, so the only export it can ever
    mean is the one inside ``root`` — and ``kyosei/export/`` is tracked, so a linked
    worktree has one. Reading the redirected ``store_root`` (KYO-342) named the main
    checkout's export, ``relative_to`` raised ``ValueError`` on a path outside this
    tree, and the ``except`` below turned that into ``None`` — *exclude nothing*, which
    is indistinguishable from "this project has no store" and reported a worktree as a
    dirty build the moment its own export had any churn. Measured in this repository's
    own KYO-355 worktree: ``None`` before this line changed, ``kyosei/export`` after.
    """
    from . import config as config_module
    from .errors import KyoseiError

    try:
        layout = config_module.resolve_layout(root, required=False)
        if not layout.configured:
            return None
        export = config_module.load_config(
            layout.store_root_in_this_checkout, code_root=layout.code_root
        ).export_path
        relative = export.resolve().relative_to(root.resolve())
    except (KyoseiError, OSError, ValueError):
        return None
    text = relative.as_posix()
    return text if text not in ("", ".") else None


def _status_pathspec(root: Path) -> tuple[str, ...]:
    """What ``git status`` is asked about: everything but the store's export directory.

    ``:(exclude,top)`` is git's own exclusion pathspec, anchored at the repository root
    — the same form and the same anchoring ``cmd_check_clean.cmd_check_clean`` uses to leave
    the store out of a worker's cleanliness check, and for the neighbouring reason: the
    store's churn is the controller's, not this tree's. The positive ``:(top)`` beside
    it is what an all-exclusions pathspec list needs to still mean "everything else".
    """
    relative = store_export_relpath(root)
    if relative is None:
        return ()
    return ("--", ":(top)", f":(exclude,top){relative}")


def utc_timestamp(iso: str) -> str | None:
    """``iso`` — any ISO-8601 instant with an offset, as git's ``%cI`` prints one — as a
    UTC timestamp in the store's own format, or ``None`` if it is not one (KYO-398.c1).

    **Why UTC, measured.** ``%cI`` is the committer's LOCAL time, and the date used to
    be its first ten characters. A commit made at 22:34 -0400 on 2026-09-24 is
    02:34Z on 2026-09-25, and every event the store records is stamped in UTC
    (:func:`kyosei.clock.now`), so a build dated by the local reading printed as
    2026-09-24 while ``kyo changelog`` dated the records it carried 2026-09-25 — a
    build's own fixes, listed as completed after the build. A consumer's session read
    exactly that as "neither fix is in the version you have". One clock
    on both sides is the fix, and the store's clock is the one that cannot move.

    The store's own format (milliseconds and a ``Z``) rather than ``isoformat()``'s,
    because the value is compared by string against event times: ``...:00Z`` sorts
    AFTER ``...:00.123Z`` byte for byte, so a mixed format orders two instants in the
    same second backwards.
    """
    try:
        moment = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if moment.tzinfo is None:
        # No offset is no statement of which clock it was read on; guessing would be
        # the mixed-clock defect again, one layer down.
        return None
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def git_identity(root: Path, *, with_dirty: bool = True) -> dict | None:
    """HEAD's sha, its commit time and date (UTC) and whether the tree is dirty — or
    ``None``.

    "Dirty" here means the *code* differs from HEAD: the project's own store export
    directory is excluded (KYO-338, :func:`_status_pathspec`). ``with_dirty=False``
    skips that question and reports ``None`` for it — the per-prompt build check
    (KYO-398.c6) compares commits only, and ``git status`` plus the project discovery
    behind its pathspec are most of this function's cost.
    """
    described = _git(root, "show", "-s", "--format=%H%n%cI", "HEAD")
    if not described:
        return None
    lines = described.splitlines()
    if len(lines) < 2 or not lines[0]:
        return None
    commit_time = utc_timestamp(lines[1])
    status = (
        _git(root, "status", "--porcelain", *_status_pathspec(root)) if with_dirty else None
    )
    return {
        "commit": lines[0],
        "commit_time": commit_time,
        # KYO-398.c1: the UTC date, never `%cI[:10]` (the committer's local date).
        "commit_date": commit_time[:10] if commit_time else None,
        "dirty": None if status is None else bool(status.strip()),
    }


def release_tag(version: str) -> str:
    """The tag that marks ``version``'s release commit: ``v<version>``."""
    return f"{RELEASE_TAG_PREFIX}{version}"


def last_release(root: Path) -> str | None:
    """The newest release tag reachable from ``root``'s HEAD, as a version, or ``None``.

    ``git describe`` walks back from HEAD, so the answer is the release this commit is
    *after* -- a release on a branch this commit does not descend from is not one it
    came after. ``--tags`` because a local release tag need not be annotated (the
    release sequence uses a plain ``git tag v<v>``). ``None`` both when no release tag exists and
    when git could not answer; the line omits the clause in either case rather than
    claiming there was no release.
    """
    described = _git(root, "describe", "--tags", "--abbrev=0", "--match", RELEASE_TAG_GLOB, "HEAD")
    if not described:
        return None
    return described[len(RELEASE_TAG_PREFIX):]


def release_facts(root: Path, version: str, *, dirty: bool | None) -> dict:
    """KYO-445.c2: whether a build of ``root``'s HEAD, stamped now, is a release.

    True only when HEAD carries :func:`release_tag` of ``version`` AND ``dirty`` is
    ``False`` -- a tagged commit built from a tree with edits in it is a wheel whose
    contents match no commit, published or not, and "unknown" (``None``) is not clean.
    """
    tags = _git(root, "tag", "--points-at", "HEAD")
    tagged = tags is not None and release_tag(version) in tags.split()
    return {"release": bool(tagged and dirty is False), "last_release": last_release(root)}


def read_stamp(directory: Path | None = None) -> dict | None:
    """The build stamp this package carries, or ``None`` if it carries none."""
    path = (directory if directory is not None else package_dir()) / STAMP_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_identity(directory: Path | None = None, *, with_dirty: bool = True) -> BuildIdentity:
    """This copy's identity: live git where there is a checkout, else the stamp.

    ``with_dirty`` is :func:`git_identity`'s, and matters only on the live path: a
    stamp carries its dirty bit for free.
    """
    directory = directory if directory is not None else package_dir()
    root = source_tree_root(directory)
    if root is not None:
        live = git_identity(root, with_dirty=with_dirty)
        if live is not None:
            return BuildIdentity(
                version=__version__,
                commit=live["commit"],
                commit_date=live["commit_date"],
                dirty=live["dirty"],
                source=SOURCE_GIT,
                kind=install_kind(directory),
                commit_time=live["commit_time"],
                # KYO-445: a live checkout is never a release (what runs is whatever the
                # tree holds now), but it still says which release it came after.
                # Skipped with the dirty check: the per-prompt build check wants
                # commits only, and prints no version line.
                release=False,
                last_release=last_release(root) if with_dirty else None,
            )
    stamp = read_stamp(directory)
    if stamp and stamp.get("commit"):
        # KYO-398.c1: a stamp written before `commit_time` existed carries only
        # `commit_date`, and that date is the committer's LOCAL one. It is reported as
        # it was stamped, with no time, rather than re-derived: there is no instant in
        # it to convert, and a date presented as UTC that is not would be the defect
        # this field exists to remove. Every stamp written from here on carries both.
        commit_time = stamp.get("commit_time") or None
        return BuildIdentity(
            version=str(stamp.get("version") or __version__),
            commit=str(stamp["commit"]),
            commit_date=commit_time[:10] if commit_time else stamp.get("commit_date"),
            dirty=stamp.get("dirty"),
            source=SOURCE_STAMP,
            kind=install_kind(directory),
            commit_time=commit_time,
            release=stamp.get("release") is True,
            last_release=str(stamp["last_release"]) if stamp.get("last_release") else None,
        )
    return BuildIdentity(
        version=__version__,
        commit=None,
        commit_date=None,
        dirty=None,
        source=SOURCE_UNKNOWN,
        kind=install_kind(directory),
    )


#: What each source is called in the human line. A reader has to be able to tell "this
#: is the commit the wheel was built from" from "this is whatever the checkout is at
#: right now", because only the first one is a property of what they installed.
SOURCE_WORDING = {
    SOURCE_GIT: "read live from the checkout this copy runs from",
    SOURCE_STAMP: "stamped into the package when this copy was built",
}


def version_line(identity: BuildIdentity | None = None) -> str:
    """The one line ``kyo version`` prints and ``kyo brief`` repeats in its header.

    One function because c1 and c2 ask for the *same* line in two places: a session
    opening on a different sentence than the version command prints would be two facts
    a reader has to reconcile, which is the shape of the problem this record is about.
    """
    identity = identity if identity is not None else build_identity()
    if not identity.known:
        return (
            f"{DISTRIBUTION} {identity.version} — build unknown: this copy carries no "
            f"build stamp and is not inside a git checkout, so it cannot say which "
            f"snapshot it is. To fix that: {upgrade_route(identity.kind, identity.release)}"
        )
    dirty = ", built from a dirty working tree" if identity.dirty else ""
    where = SOURCE_WORDING.get(identity.source, identity.source)
    # KYO-398.c1: the date printed is the UTC date of the commit's instant, and says
    # so, because the store dates everything else it prints in UTC and a reader
    # comparing this date with a changelog's is comparing across that line.
    date = f"{identity.commit_date} UTC" if identity.commit_time else identity.commit_date
    # KYO-445.c3, the two forms. A release names itself; anything else says it is not
    # one and which release it came after, so a reader comparing against a published
    # version is never left to infer it from the commit.
    if identity.release:
        return (
            f"{DISTRIBUTION} {identity.version} (release) — build "
            f"{identity.short_commit} ({date}){dirty}; {where}"
        )
    after = f", after release {identity.last_release}" if identity.last_release else ""
    return (
        f"{DISTRIBUTION} {identity.version} — unreleased build {identity.short_commit} "
        f"({date}){after}{dirty}; {where}"
    )


def write_stamp(directory: Path, code_root: Path) -> dict:
    """Write :data:`STAMP_FILENAME` into ``directory`` from ``code_root``'s git."""
    live = git_identity(code_root) or {}
    # KYO-445.c2: decided here, at build time, against the tree as it is being built --
    # the only moment "was it clean and tagged" is observable.
    released = (
        release_facts(code_root, __version__, dirty=live.get("dirty"))
        if live
        else {"release": False, "last_release": None}
    )
    payload = {
        "version": __version__,
        "commit": live.get("commit"),
        # KYO-398.c1: the instant, in UTC, and the date of that instant.
        "commit_time": live.get("commit_time"),
        "commit_date": live.get("commit_date"),
        "dirty": live.get("dirty"),
        "release": released["release"],
        "last_release": released["last_release"],
        # Law 16: the tool stamps time. Nothing here accepts one.
        "stamped_at": now(),
    }
    (directory / STAMP_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def regenerate(code_root: Path, directory: Path | None = None) -> dict:
    """Write both build-time products into the package. **Called by the build hook.**

    The stamp and the packaged changelog are written by one function, on purpose. They
    answer the same reader's two halves of one question — "which build is this" and
    "what changed since the one I had" — and a changelog regenerated on a different
    schedule from the stamp would be a changelog that is silently older than the build
    carrying it. One hook, one moment, both files.
    """
    from . import changelog as changelog_module

    directory = directory if directory is not None else package_dir()
    stamp = write_stamp(directory, code_root)
    packaged = changelog_module.write_packaged_data(directory, code_root)
    return {"stamp": stamp, "changelog": packaged}
