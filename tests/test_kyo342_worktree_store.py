"""KYO-342: a `kyo` read inside a linked git worktree resolves through to the main
checkout, and a `kyo` write from there is still refused.

The state is the ordinary one, not an edge case. ``kyosei.db`` is gitignored — the
charter's Layer 2 makes ``kyosei/export/`` the git-canonical serialization and the
database a rebuild of it — and ``git worktree add`` checks out tracked files and
nothing else. So every dispatch worktree has a complete ``kyosei/`` with a
``config.yml``, an ``export/`` and no database, and every read run there refused,
naming `kyo rebuild`: a write under ``kyosei/``, which is the one act a dispatched
worker is forbidden and which would rewrite ``config.yml`` onto the worker's branch.
Two consumer projects reported every worker of every wave hitting it, and both
worked around it by pasting the text of anything a worker needed to read into its
spawn prompt.

What this file pins is the pair, because either half alone is a defect:

* the read resolves (``test_a_read_in_a_linked_worktree_answers_out_of_the_main_checkout``
  and its dispatch-id twin), and says which store answered (`kyo where`);
* the write does not (:func:`test_every_store_writer_is_refused_from_a_linked_worktree`),
  and the refusal names the checkout the write would have landed in.

The second is what makes the first safe to do at all. A redirected store belongs to
another working tree: a write from here lands under whatever branch that tree has
checked out, outside this branch's diff entirely, where `kyo check-branch` — which
judges the commits a worker's branch added — could never see it.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from kyosei import buildinfo, changelog, cmd_publish
from kyosei import config as config_module

from .conftest import Run, allow_direct_filing, git_init, git_worktree, invoke, new_record

# ------------------------------------------------------------------- fixtures


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _db(root: Path) -> Path:
    return root / "kyosei" / "kyosei.db"


#: The database's WAL sidecars. Excluded from a file snapshot and nothing else is:
#: SQLite creates and updates ``-shm``/``-wal`` for a *reader* of a WAL database, so
#: their presence is not evidence of a write and their absence would not be evidence
#: of none. ``kyosei.db`` itself is compared byte for byte, which is the claim that
#: matters — see :func:`_store_fingerprint`.
_WAL_SIDECARS = ("kyosei.db-wal", "kyosei.db-shm")


def _store_fingerprint(root: Path) -> dict[str, str]:
    """Every file under ``root/kyosei/``, by path, hashed. WAL sidecars excluded."""
    directory = root / "kyosei"
    if not directory.is_dir():
        return {}
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name not in _WAL_SIDECARS
    }


@pytest.fixture
def worktree_project(kyo, tmp_path: Path) -> tuple[Path, Path, str, str]:
    """``(main checkout, linked worktree, record id, dispatch id)``.

    A REAL ``git worktree add`` off the project's own repository — the arrangement
    `kyo dispatch --worktree` produces, and the only one that reproduces this record:
    the worktree shares the main checkout's git common dir, carries the committed
    ``kyosei/export/`` and does not carry the gitignored ``kyosei.db``.
    """
    project: Path = kyo.project
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    # git first: `kyo dispatch` stamps a base sha and needs a repository to read one
    # from. The store writes below are committed after it, which is also the real
    # order — a controller commits the dispatch record it has just created (KYO-10).
    git_init(project)
    record = new_record(kyo, criteria=["a"], title="a record to read from a worktree")
    touches = tmp_path / "touches.txt"
    touches.write_text("src/widget.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(touches)).code == 0
    declared = tmp_path / "declared-worktree"
    git_worktree(project, declared)  # KYO-402: `--worktree` must be a real worktree
    dispatched = kyo("dispatch", record, "--json", "--worktree", str(declared))
    assert dispatched.code == 0, dispatched
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "store: a record and the dispatch of it")

    tree = tmp_path / "linked"
    _git(project, "worktree", "add", "-q", "-b", "kyo-342-branch", str(tree))
    assert not _db(tree).exists(), (
        "the fixture did not reproduce the record: a linked worktree must NOT carry "
        "kyosei.db, which is gitignored"
    )
    assert (tree / "kyosei" / "export").is_dir(), "the export must be checked out"
    return project, tree, record, dispatched.json["dispatch_id"]


def _from(root: Path, *args: str) -> Run:
    return invoke(["--root", str(root), *args])


# ------------------------------------------------------- KYO-342.c1: the read


def test_a_read_in_a_linked_worktree_answers_out_of_the_main_checkout(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """`kyo show <record>` prints what it prints in the main checkout (c1)."""
    project, tree, record, _ = worktree_project
    before = _store_fingerprint(tree)

    here = _from(tree, "show", record)
    there = _from(project, "show", record)

    assert here.code == 0, here
    assert here.out == there.out, (
        "a read from the worktree must print what the same read prints in the main "
        "checkout, not a rebuild of this branch's export"
    )
    assert _store_fingerprint(tree) == before, "the read wrote under the worktree's kyosei/"


def test_a_dispatch_id_resolves_in_the_worktree_it_was_dispatched_for(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """The exact command the record was filed about: a worker reading its own brief.

    `kyo show <dispatch-id>` is what a dispatch's spawn prompt tells a worker to run
    and what both consumer projects reported refusing. It is tested separately from
    the record read above because it is the *dispatch* row — written by the
    controller after the branch point in the general case — that a worktree's own
    export is least likely to carry.
    """
    project, tree, _, dispatch_id = worktree_project
    before = _store_fingerprint(tree)

    here = _from(tree, "show", dispatch_id)

    assert here.code == 0, here
    assert here.out == _from(project, "show", dispatch_id).out
    assert dispatch_id in here.out
    assert _store_fingerprint(tree) == before


def test_the_store_the_read_reports_is_the_main_checkouts_live_one(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """Which of the two stores answered, asserted rather than assumed.

    A worktree carries a ``kyosei/export/`` of its own — a checkout of whatever this
    branch holds — so "it answered" does not by itself say which store was read. This
    moves the main checkout's store ON and the worktree's export not at all, and
    reads the newer answer back out of the worktree.
    """
    project, tree, record, _ = worktree_project
    title = tree / "title.txt"
    title.write_text("renamed after the branch was cut", encoding="utf-8")
    moved = invoke(["--root", str(project), "edit", record, "--title", title.read_text().strip()])
    assert moved.code == 0, moved

    assert "renamed after the branch was cut" in _from(tree, "show", record).out


# ------------------------------------------------------ KYO-342.c2: the write


#: One invocation per store-writing route, named by what it would have written.
#:
#: The four are not variations on one path: `kyo new` writes through
#: ``Store.transaction()``, `kyo export --refresh` rewrites ``kyosei/export/`` with no
#: transaction at all, `kyo rebuild` replaces ``kyosei.db`` without opening a store,
#: and `kyo check-push --acknowledge` writes ``publication_acknowledged`` into
#: ``config.yml`` through neither (KYO-357 — found after the first three were pinned,
#: because `grep refuse_store_write` never reached ``cmd_publish.py``). Each therefore
#: needs its own guard, and a table with one entry would have proved only that
#: whichever one was chosen holds.
_WRITERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("a record, through a transaction", ("new", "bug", "--title", "written?")),
    ("kyosei/export/, with no transaction", ("export", "--refresh")),
    ("kyosei.db, with no store open", ("rebuild", "--force")),
    ("kyosei/config.yml, through check-push --acknowledge", ("check-push", "--acknowledge")),
)


@pytest.mark.parametrize("what,command", _WRITERS, ids=[name for name, _ in _WRITERS])
def test_every_store_writer_is_refused_from_a_linked_worktree(
    worktree_project: tuple[Path, Path, str, str], what: str, command: tuple[str, ...]
) -> None:
    """c2: refused, the refusal names the main checkout, and nothing anywhere moved."""
    project, tree, _, _ = worktree_project
    before_here, before_there = _store_fingerprint(tree), _store_fingerprint(project)

    refused = _from(tree, *command)

    assert refused.code != 0, f"{what} was not refused: {refused}"
    assert str(project) in refused.err, (
        f"the refusal must name the main checkout the write would have landed in: {refused.err}"
    )
    assert _store_fingerprint(tree) == before_here, f"{what}: the worktree's kyosei/ changed"
    assert _store_fingerprint(project) == before_there, (
        f"{what}: the MAIN CHECKOUT's kyosei/ changed — the write landed in the store "
        "this worktree may not touch, which is the damage the refusal exists to prevent"
    )


def test_the_redirected_connection_cannot_write_even_without_a_transaction(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """The mechanism under the sentence (KYO-342, belt and braces).

    ``Store.transaction()`` refuses in words a person can act on. This asserts the
    half that holds when a future write path does not open a transaction at all: the
    connection itself is opened ``query_only``, so the database cannot be changed
    through it whatever SQL is issued.
    """
    from kyosei.store import open_store

    project, tree, _, _ = worktree_project
    layout = config_module.resolve_layout(explicit_root=tree)
    assert layout.store_via_main_checkout == project.resolve()

    store = open_store(
        layout.store_root,
        code_root=layout.code_root,
        store_via_main_checkout=layout.store_via_main_checkout,
    )
    try:
        with pytest.raises(Exception) as raised:
            store.conn.execute("INSERT INTO meta (key, value) VALUES ('kyo342', 'x')")
        assert "readonly" in str(raised.value).lower(), raised.value
    finally:
        store.close()


def test_the_write_refusal_survives_a_store_the_worktree_could_reach_by_walking_up(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """The guard is on the resolution, not on ``--root``.

    Every other test here passes ``--root``; this one resolves the layout the way a
    command run *inside* the worktree does, by walking up from a directory in it, and
    asserts the same answer. The two paths through ``resolve_layout`` are separate
    branches and a fix applied to one of them would pass this file otherwise.
    """
    project, tree, _, _ = worktree_project
    inner = tree / "src" / "deep"
    inner.mkdir(parents=True, exist_ok=True)

    walked = config_module.resolve_layout(inner)

    assert walked.store_root == project.resolve()
    assert walked.code_root == tree.resolve()
    assert walked.store_via_main_checkout == project.resolve()


# --------------------------------- KYO-342.c3: when there is nothing to reach


def test_a_worktree_with_no_reachable_database_is_not_told_to_rebuild_here(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """c3, first half: the refusal names the main checkout, never a rebuild here.

    The state is a main checkout whose own database is gone — a fresh clone of the
    code repository, or a checkout nobody has rebuilt yet — read from a worktree of
    it. Before this record the message said "run `kyo rebuild`" with no location at
    all, which in a worktree is an instruction to write ``kyosei/`` on the worker's
    own branch.
    """
    project, tree, record, _ = worktree_project
    _db(project).unlink()

    refused = _from(tree, "show", record)

    assert refused.code != 0
    assert str(project) in refused.err, refused.err
    assert "linked git worktree" in refused.err
    # Every mention of the rebuild is bound to the main checkout's path. The bare
    # "run `kyo rebuild`" this message used to end on is what a worker acted on.
    assert "`kyo rebuild`" not in refused.err.replace(f"`kyo rebuild` in {project}", "")


def test_the_remedy_the_worktree_is_named_clears_the_error(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """c3, second half: run what it names, where it names it, and the read works.

    The KYO-5 convention, applied to a message whose remedy is deliberately somewhere
    else: the rebuild runs in the main checkout, nothing at all runs in the worktree,
    and the *worktree's* read is what has to succeed afterwards.
    """
    project, tree, record, _ = worktree_project
    _db(project).unlink()
    assert _from(tree, "show", record).code != 0

    rebuilt = _from(project, "rebuild")
    assert rebuilt.code == 0, rebuilt

    cleared = _from(tree, "show", record)
    assert cleared.code == 0, cleared
    assert cleared.out == _from(project, "show", record).out


# -------------------------------------------------------- KYO-342.c4: saying so


def test_where_says_which_store_it_resolved_and_how_it_got_there(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """c4. Both surfaces: the sentence a person reads and the key a program reads."""
    project, tree, _, _ = worktree_project

    human = _from(tree, "where")
    payload = _from(tree, "where", "--json").json

    assert human.code == 0, human
    assert payload["store_via_main_checkout"] == str(project.resolve())
    assert payload["store_root"] == str(project.resolve())
    assert payload["code_root"] == str(tree.resolve())
    assert payload["can_open"] is True
    assert str(project) in human.out
    assert "linked git worktree" in human.out


def test_where_in_the_main_checkout_says_nothing_about_a_main_checkout(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """The other direction of c4: the ordinary answer is unchanged and unadorned."""
    project, _, _, _ = worktree_project

    payload = _from(project, "where", "--json").json

    assert payload["store_via_main_checkout"] is None
    assert payload["store_root"] == payload["code_root"] == str(project.resolve())
    assert "linked git worktree" not in _from(project, "where").out


# -------------------------------------------- what this must NOT have changed


def test_a_worktree_that_has_its_own_database_is_left_alone(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """The redirect fires only where there is nothing here to read.

    A worktree somebody rebuilt in deliberately has a database of its own, and it is
    the one that answers — untouched, writable, and costing no `git rev-parse` at
    all, since the lookup is gated on the database being absent.
    """
    project, tree, _, _ = worktree_project
    # Put one there the way a second checkout acquires one. Not with `kyo rebuild`,
    # which is refused from here by design — the state under test is "a database is
    # present", however it arrived.
    _db(tree).write_bytes(_db(project).read_bytes())

    layout = config_module.resolve_layout(explicit_root=tree)

    assert layout.store_root == tree.resolve()
    assert layout.store_via_main_checkout is None
    written = _from(tree, "new", "bug", "--title", "writable here")
    assert written.code == 0, written


def test_a_split_layout_worktree_is_untouched(
    split_project: tuple[Path, Path], tmp_path: Path
) -> None:
    """A split store is outside the repository already (the record says so).

    Its database is not gitignored *out of a worktree* — it is not in the repository
    at all — so a worktree of the code repository reaches the one store by the
    ordinary route, and writing to it from there is as legitimate as writing to it
    from the main checkout.
    """
    code, store = split_project
    allow_direct_filing(store, "bug")
    tree = tmp_path / "split-linked"
    _git(code, "worktree", "add", "-q", "-b", "split-branch", str(tree))

    layout = config_module.resolve_layout(explicit_root=tree)

    assert layout.kind == config_module.LAYOUT_SPLIT
    assert layout.store_root == store
    assert layout.store_via_main_checkout is None
    written = _from(tree, "new", "bug", "--title", "a split store is writable")
    assert written.code == 0, written


def test_a_stored_worktree_token_resolves_to_the_same_place_from_inside_a_worktree(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """KYO-70's ``<worktrees>`` token is a rule, and the rule needs the project's name.

    Under the in-repo layout that name is the code repository's directory name, and
    inside a linked worktree the code root is the worktree — whose directory is named
    after a *branch*. Left alone, a read run in a worktree would resolve every stored
    ``<worktrees>/...`` to a home this project has never used, and `kyo worktrees`
    would call live directories gone.
    """
    project, tree, _, dispatch_id = worktree_project
    from_worktree = config_module.resolve_layout(explicit_root=tree)
    from_main = config_module.resolve_layout(explicit_root=project)

    assert config_module.project_home_name(from_worktree) == config_module.project_home_name(
        from_main
    )
    assert dispatch_id in _from(tree, "show", dispatch_id).out


def test_a_tracked_store_file_is_read_from_this_checkout_not_through_the_redirect(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """The redirect is for the database, and it must not drag tracked files with it.

    Found by `tests/test_procedures.py` when this record's first draft moved the whole
    store root: `kyo install-procedures` compares the packaged rule ledger against
    ``kyosei/procedures.md``, which is *tracked* — so a worktree has its own copy at
    whatever this branch holds, and it is the copy this branch would be diffed on.
    Following the redirect made `--print` describe a hand-off to a file in another
    working tree, and made KYO-151's write refusal ask git about the main checkout,
    where there is no worktree to refuse.
    """
    project, tree, _, _ = worktree_project
    ledger = tree / "kyosei" / "procedures.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + "\n## A rule only this branch has\n\nProse.\n",
        encoding="utf-8",
    )
    marked = ledger.read_text(encoding="utf-8")

    preview = _from(tree, "install-procedures", "--print", "--json")

    assert preview.code == 0, preview
    assert preview.json["path"] == str(ledger), (
        "the preview described another working tree's ledger, not this branch's"
    )
    assert ledger.read_text(encoding="utf-8") == marked
    assert (project / "kyosei" / "procedures.md").read_text(encoding="utf-8") != marked

    written = _from(tree, "install-procedures")
    assert written.code != 0, written
    assert "linked git worktree" in written.err


# ------------------------------- KYO-355: the export is tracked too, and the changelog
#
# The record above pinned ONE tracked file, ``procedures.md``, at the site that found
# it. ``kyosei/export/`` is the other, and the larger one: it is the git-canonical
# serialization of the whole store (charter, Layer 2), `git worktree add` checks every
# document of it out, and :mod:`kyosei.changelog` renders from it and is compared byte
# for byte against this branch's committed ``docs/reference/changelog.md``. Following
# the database's redirect there made that comparison ask two working trees one
# question: the rendering came from the main checkout and the document it was checked
# against came from this branch, so the check failed the instant the controller
# completed anything mid-wave — on records the worker had never seen and could not act
# on. Measured on this store (KYO-351.d1's worktree, on a missing
# ``kyosei/export/KYO-354.json``).


def _completed(kyo, title: str) -> str:
    """File a record and walk it all the way to ``done``; return its id.

    The whole ladder rather than a status written into the store by hand, because what
    the changelog reads is the *events* ledger's completion transition
    (:func:`changelog._completion_dates`) — a record that arrived at ``done`` without
    moving there would be dated by the fallback and would not exercise this at all.
    """
    record = new_record(kyo, criteria=["a criterion to settle"], title=title)
    for status in ("working", "built"):
        assert kyo("move", record, status).code == 0
    verified = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    assert verified.code == 0, verified
    for status in ("verified", "done"):
        assert kyo("move", record, status).code == 0
    return record


#: Where the fixture below commits its generated changelog — this suite's stand-in for
#: ``docs/reference/changelog.md``, which is what ``test_kyo226``'s staleness check
#: compares a rendering against. Any tracked path does; the point is that it is
#: committed BEFORE the worktree is cut, so the worktree carries the copy its own
#: branch holds.
COMMITTED_CHANGELOG = Path("docs") / "reference" / "changelog.md"


def _render(root: Path) -> str:
    """The markdown ``root``'s store yields right now, by the generator's own route."""
    entries = changelog.entries_from_store(root)
    return changelog.markdown(
        changelog.Changelog(entries, changelog.SOURCE_STORE), entries, None
    )


@pytest.fixture
def diverged_worktree(kyo, project: Path, tmp_path: Path) -> tuple[Path, Path, str, str]:
    """``(main checkout, linked worktree, completed before the cut, completed after)``.

    The state a dispatch worktree is in for most of a wave, built rather than described:
    a record was completed, the changelog was regenerated and committed, the worktree
    was cut from that commit, and the controller then completed another record. The main
    checkout's ``kyosei/export/`` now holds a document the worktree's does not — the only
    arrangement that can tell the two copies apart, and the one every mid-wave worker is
    in.

    ``src/kyosei/`` is real here because :func:`buildinfo.source_tree_root` is anchored
    on the package's own location: a dirtiness check needs a package directory whose
    parent is ``src`` to be asked about at all.
    """
    package = project / "src" / "kyosei"
    package.mkdir(parents=True, exist_ok=True)
    (package / "cli.py").write_text("# code\n", encoding="utf-8")

    before = _completed(kyo, "completed before the worktree was cut")
    committed = project / COMMITTED_CHANGELOG
    committed.parent.mkdir(parents=True, exist_ok=True)
    committed.write_text(_render(project), encoding="utf-8")
    git_init(project)

    tree = tmp_path / "linked-changelog"
    _git(project, "worktree", "add", "-q", "-b", "kyo-355-branch", str(tree))
    assert not _db(tree).exists(), "the fixture did not reproduce the record"

    after = _completed(kyo, "completed after the worktree was cut")
    assert (project / "kyosei" / "export" / f"{after}.json").is_file()
    assert not (tree / "kyosei" / "export" / f"{after}.json").exists(), (
        "the fixture did not diverge the two exports: the worktree already carries the "
        "record the main checkout completed after it was cut"
    )
    assert (tree / "kyosei" / "export" / f"{before}.json").is_file()
    return project, tree, before, after


def test_the_changelog_in_a_worktree_renders_this_branchs_export(
    diverged_worktree: tuple[Path, Path, str, str],
) -> None:
    """KYO-355.c1. The rendering is this branch's export, main-checkout record and all.

    Both directions are asserted and neither is enough alone: ``after`` absent says the
    redirect was not followed, and ``before`` present says the rendering did not simply
    come up empty — which it would if ``store_export_dir`` had started answering
    ``None`` and every caller had swallowed it.
    """
    project, tree, before, after = diverged_worktree

    layout = config_module.resolve_layout(tree)
    assert layout.store_via_main_checkout == project, (
        "the database redirect is not in play, so this test would pass without "
        "distinguishing the two copies at all"
    )
    assert changelog.store_export_dir(tree) == (tree / "kyosei" / "export").resolve()
    assert changelog._kyosei_dir(tree) == (tree / "kyosei").resolve()

    ids = {entry.id for entry in changelog.entries_from_store(tree)}
    assert before in ids, "this branch's own completed record is missing from its changelog"
    assert after not in ids, (
        f"the changelog rendered in {tree} carries {after}, which only the main "
        f"checkout's export holds — `store_export_dir` followed the database's "
        "redirect through to another working tree"
    )


def test_the_committed_changelog_check_passes_inside_a_linked_worktree(
    diverged_worktree: tuple[Path, Path, str, str],
) -> None:
    """KYO-355.c2 — ``test_kyo226``'s staleness check, run where it used to fail.

    That check is one equality: the ``docs/reference/changelog.md`` **committed on this
    branch** against a rendering of the store *right now*. What it needs is not "the
    rendering is correct" but "both sides come out of one working tree", and the
    assertion is reproduced here rather than described, because a worker cannot commit
    a record the controller completed after its branch was cut and has nothing it could
    do about the failure.

    The same equality, byte for byte, in the main checkout — where the redirect never
    happens and the extra record IS committed — so the test cannot pass by having
    quietly stopped reading anything.
    """
    project, tree, _before, after = diverged_worktree

    assert (tree / COMMITTED_CHANGELOG).read_text(encoding="utf-8") == _render(tree), (
        "the committed changelog and the rendering it is compared against came from "
        "two different working trees: this branch's document does not carry the record "
        f"{after}, completed in the main checkout after the branch was cut, and the "
        "rendering did"
    )
    assert after not in (tree / COMMITTED_CHANGELOG).read_text(encoding="utf-8")

    regenerated = _render(project)
    assert after in regenerated
    (project / COMMITTED_CHANGELOG).write_text(regenerated, encoding="utf-8")
    assert (project / COMMITTED_CHANGELOG).read_text(encoding="utf-8") == _render(project)


def test_the_build_dirtiness_exclusion_finds_this_checkouts_export(
    diverged_worktree: tuple[Path, Path, str, str],
) -> None:
    """The third reader of the same tracked directory, and the quietest (KYO-355.c3).

    KYO-338 leaves ``kyosei/export/`` out of the ``git status`` that decides whether a
    build is stamped dirty. That answer is a ``:(top)``-anchored pathspec for a status
    run **in this tree**, so the only export it can mean is this tree's — but it was
    computed from the redirected ``store_root``, whose path is not under this tree at
    all, and :func:`buildinfo.store_export_relpath` turned the resulting ``ValueError``
    into ``None``. ``None`` there means *exclude nothing*, which is the same value a
    checkout with no store at all returns, so the exclusion did not fail loudly: a
    worktree with any churn in its own export was simply stamped dirty again.
    """
    _project, tree, _before, _after = diverged_worktree

    assert buildinfo.store_export_relpath(tree) == "kyosei/export"
    assert buildinfo._status_pathspec(tree)[-1] == ":(exclude,top)kyosei/export"

    export = tree / "kyosei" / "export"
    (export / "_sweeps.jsonl").write_text('{"swept": 1}\n', encoding="utf-8")
    identity = buildinfo.build_identity(tree / "src" / "kyosei")
    assert identity.dirty is False, (
        "the worktree's own store churn was counted as code: the KYO-338 exclusion "
        "resolved to a directory outside this tree and silently excluded nothing"
    )


def test_the_worktree_guards_still_run_in_a_worktree(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """`kyo check-branch` and `kyo check-clean` keep working (the record's own ask).

    Both are ``PROJECT_OPTIONAL`` guards that run from inside a worker's worktree,
    and both opened a store there — which until this record could not be opened at
    all. They must still run, and they must not be the thing that trips the write
    refusal: neither writes the store.
    """
    _, tree, _, _ = worktree_project

    branch = _from(tree, "check-branch", "--json")
    clean = _from(tree, "check-clean", "--json")

    for guard, name in ((branch, "check-branch"), (clean, "check-clean")):
        assert guard.json["ok"] in (True, False), f"{name} did not answer: {guard}"
        assert "refusing to write" not in guard.err, f"{name} tripped the write guard: {guard}"


def test_an_ordinary_checkout_asks_git_nothing(monkeypatch, kyo, project: Path) -> None:
    """The cost of this feature in the case that is not it: none.

    The lookup is gated on the database being missing, so a project whose store is
    right there never runs ``git rev-parse``. Asserted because the alternative — two
    or three subprocesses on the front of every `kyo` invocation, including the ones
    session hooks run on every turn — is the kind of cost that is only ever measured
    after it has been paid for a month.
    """
    calls: list[tuple[str, ...]] = []
    real = subprocess.run

    def recording(args, *rest, **kwargs):  # noqa: ANN001 - a passthrough spy
        if args and args[0] == "git":
            calls.append(tuple(args))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(config_module.subprocess, "run", recording)
    assert config_module.resolve_layout(explicit_root=project).store_via_main_checkout is None
    assert calls == [], f"an ordinary in-repo store asked git {len(calls)} question(s): {calls}"


# ------------------------------------------------- KYO-357: the read half of the same bug
#
# ``check-push --acknowledge``'s write is pinned above, in ``_WRITERS``. Its read,
# ``publication_state``, has the shape KYO-355 already fixed once in ``changelog.py``:
# it asked whether pushing THIS tree would publish the store, then measured and read
# ``publication_acknowledged`` out of ``layout.store_root`` — redirected to the MAIN
# checkout under KYO-342 for ``kyosei.db``, and wrong here, because ``config.yml`` and
# ``kyosei/export/`` are tracked files this branch has its own checked-out copy of.


def test_publication_state_measures_this_checkouts_own_tracked_copy(
    worktree_project: tuple[Path, Path, str, str],
) -> None:
    """KYO-357.c2.

    Gives the two checkouts different answers on both axes ``publication_state``
    reports — the acknowledgement and the counted surfaces — and asserts the
    worktree's own answer is the one it gives, not the main checkout's.
    """
    project, tree, _record, _dispatch_id = worktree_project
    ideas_dir = project / "ideas"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    (ideas_dir / "only-the-main-checkout-has-this.md").write_text(
        "an idea filed in the main checkout after the worktree was cut\n", encoding="utf-8"
    )

    acknowledged = _from(project, "check-push", "--acknowledge")
    assert acknowledged.code == 0, acknowledged

    from_project = cmd_publish.publication_state(
        config_module.resolve_layout(explicit_root=project)
    )
    from_tree = cmd_publish.publication_state(config_module.resolve_layout(explicit_root=tree))

    assert from_project.acknowledged is not None, "the fixture's own setup did not take"
    assert from_tree.acknowledged is None, (
        "the worktree reported the MAIN checkout's acknowledgement — its own "
        "kyosei/config.yml, tracked and committed on this branch, was never "
        "acknowledged"
    )
    assert from_project.counts["ideas"] != from_tree.counts["ideas"], (
        "both checkouts counted the same directory — the idea filed in the main "
        "checkout after the branch was cut should not appear in the worktree's count"
    )
    assert from_tree.store_root == tree.resolve()
    assert from_tree.measured_in == tree.resolve()
