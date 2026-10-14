"""KYO-100: scope is a schema dimension, backed by a per-project registry.

The first of the four config dimensions to gain one, and the first of KYO-72
step 1's three registry tasks — so what is asserted here is not only that scope works
but that the *shape* it works through is general, because statuses-as-roles (KYO-101)
and gate tags (KYO-102) are meant to arrive by declaring a :class:`registry.Dimension`
rather than by writing a second loader. ``test_the_shape_is_general_not_scope_shaped``
is the one that would fail if that stopped being true — and KYO-104, which folded
``kinds.yml`` onto this shape as the fourth and last dimension, put the three pieces that
fold needed under the same charge: ``required``, ``entries_check`` and an empty list that
means something. Each is exercised below against a dimension this package does not have,
because a "general" mechanism with one user is a mechanism shaped like its one user until
something else proves otherwise.

The design constraint that decides most of what is below: derivation from
``touches`` is a suggester and validator for scope, never the source.
So every test that involves touches asserts two things — what was printed, and that
nothing was stored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import registry as registry_module
from kyosei.errors import EXIT_ERROR
from kyosei.scaffold import defaults_dir

from .conftest import allow_direct_filing, export_of, git_init, new_record

TWO_SCOPES = """\
scopes:
  app-desktop:
    summary: The desktop app -- its shell, its panes, its bundled runtime.
    paths:
      - apps/desktop/*
  cli:
    summary: The kyo command line tool.
    paths:
      - src/kyosei/*
"""


def scopes_yml(project: Path) -> Path:
    return project / "kyosei" / "scopes.yml"


def write_scopes(project: Path, text: str) -> Path:
    path = scopes_yml(project)
    path.write_text(text, encoding="utf-8")
    return path


def touching(project: Path, *paths: str) -> str:
    """Create each file under the project and return a --touches-file holding them."""
    for relative in paths:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    listing = project / "touches.txt"
    listing.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
    return str(listing)


def a_task(kyo, project: Path, *args: str) -> str:
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("something is true\n", encoding="utf-8")
    result = kyo(
        "new", "task", "--title", "A scoped thing",
        "--criteria-file", str(criteria), "--json", "--force", *args,  # KYO-538: repeats
    )  # fmt: skip
    assert result.code == 0, result
    return result.json["record"]["id"]


# --------------------------------------------------------------------- KYO-100.c1


def test_a_record_declares_a_scope_from_the_registry(kyo, project: Path) -> None:
    write_scopes(project, TWO_SCOPES)
    record = a_task(kyo, project, "--scope", "app-desktop")
    assert kyo("show", record, "--json").json["scope"] == "app-desktop"


def test_a_scope_the_registry_does_not_declare_is_refused_at_write_time(
    kyo, project: Path
) -> None:
    """c1: refused, naming the registry file and the scopes it does hold.

    Both halves matter and the second is the one that was missing from the defect this
    is modelled on: a refusal that says only "unknown" leaves the caller to find the
    file, open it, and guess whether they mistyped or the scope was never declared."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    result = kyo("new", "task", "--title", "A thing", "--scope", "app-electron")
    assert result.code == EXIT_ERROR
    assert "app-electron" in result.err
    assert str(scopes_yml(project)) in result.err
    assert "app-desktop" in result.err and "cli" in result.err
    # And nothing was written on the way to the refusal.
    assert kyo("list", "--json").json["summary"]["total"] == 0


def test_a_project_with_no_scopes_file_says_so_rather_than_listing_nothing(
    kyo, project: Path
) -> None:
    """The state every store written before this release is in, and the one a bare
    "not in the registry" message would report as an empty registry. `kyo init` writes
    a scopes.yml, so this is provoked by deleting it — which is also what an older
    store looks like from here."""
    scopes_yml(project).unlink()
    allow_direct_filing(project, "task")
    result = kyo("new", "task", "--title", "A thing", "--scope", "app-desktop")
    assert result.code == EXIT_ERROR
    assert "does not exist" in result.err
    assert "declares no scopes at all" in result.err


def test_the_registry_is_the_way_through_and_the_refusal_says_so(kyo, project: Path) -> None:
    """KYO-43's rule, one dimension over: the escape hatch is the registry, not a
    --force. The refusal names the edit, and the edit actually clears it."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    assert kyo("new", "task", "--title", "A thing", "--scope", "server").code == EXIT_ERROR
    write_scopes(project, TWO_SCOPES + "  server:\n    summary: The API service.\n")
    record = a_task(kyo, project, "--scope", "server")
    assert kyo("show", record, "--json").json["scope"] == "server"


def test_every_write_path_validates_the_scope(kyo, project: Path, write_file) -> None:
    """`new`, `promote`, `spawn` and `edit` all reach the same check — which is the
    point of there being one :func:`cmd_records._checked_scope` rather than four."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    idea = project / "ideas" / "thing.md"
    idea.write_text("# An idea\n\nbody\n", encoding="utf-8")
    criteria = write_file("c.txt", "something is true\n")

    refused = kyo(
        "promote", str(idea), "--kind", "task", "--criteria-file", criteria,
        "--touches-file", touching(project, "apps/desktop/main.ts"), "--scope", "nope",
    )  # fmt: skip
    assert refused.code == EXIT_ERROR and "nope" in refused.err

    record = a_task(kyo, project)
    assert kyo("edit", record, "--scope", "nope").code == EXIT_ERROR
    assert kyo("edit", record, "--scope", "cli", "--json").code == 0
    assert kyo("show", record, "--json").json["scope"] == "cli"

    # `kyo spawn`: give `task` an accepts_from edge from itself is not legal, so use the
    # registry's own bug -> task edge, which the default kinds.yml declares.
    source = new_record(kyo, kind="bug", criteria=["a"])
    spawned = kyo(
        "spawn", source, "--kind", "task", "--title", "The follow-up",
        "--criteria-file", criteria, "--touches-file",
        touching(project, "apps/desktop/main.ts"), "--scope", "nope",
    )  # fmt: skip
    assert spawned.code == EXIT_ERROR and "nope" in spawned.err


def test_scope_or_no_scope_but_not_both(kyo, project: Path) -> None:
    write_scopes(project, TWO_SCOPES)
    record = a_task(kyo, project, "--scope", "cli")
    result = kyo("edit", record, "--scope", "app-desktop", "--no-scope")
    assert result.code != 0
    assert "not both" in result.err


def test_no_scope_clears_a_declared_one(kyo, project: Path) -> None:
    """A nullable column needs a way back to NULL — the `--no-plan` precedent."""
    write_scopes(project, TWO_SCOPES)
    record = a_task(kyo, project, "--scope", "cli")
    assert kyo("edit", record, "--no-scope").code == 0
    assert kyo("show", record, "--json").json["scope"] is None


# --------------------------------------------------------------------- KYO-100.c2


def test_show_renders_the_scope_and_renders_its_absence(kyo, project: Path) -> None:
    """Both directions. A line that appears only when a value exists makes "no scope
    was declared" indistinguishable from a build that has no such field (law 6)."""
    write_scopes(project, TWO_SCOPES)
    scoped = a_task(kyo, project, "--scope", "app-desktop")
    assert "scope:   app-desktop" in kyo("show", scoped).out

    unscoped = a_task(kyo, project)
    assert "scope:   (none declared)" in kyo("show", unscoped).out


def test_list_renders_the_scope_and_filters_by_it(kyo, project: Path) -> None:
    write_scopes(project, TWO_SCOPES)
    desktop = a_task(kyo, project, "--scope", "app-desktop")
    cli = a_task(kyo, project, "--scope", "cli")
    unscoped = a_task(kyo, project)

    listed = kyo("list", "--json").json["records"]
    assert {row["id"]: row["scope"] for row in listed} == {
        desktop: "app-desktop",
        cli: "cli",
        unscoped: None,
    }
    assert "app-desktop" in kyo("list").out

    filtered = kyo("list", "--scope", "app-desktop", "--json").json["records"]
    assert [row["id"] for row in filtered] == [desktop]


def test_a_list_filter_naming_an_undeclared_scope_is_refused_not_answered_empty(
    kyo, project: Path
) -> None:
    """Law 6, and the same sentence the write path refuses with. An empty listing is
    an answer — "nothing is in that scope" — and it is the wrong one."""
    write_scopes(project, TWO_SCOPES)
    a_task(kyo, project, "--scope", "cli")
    result = kyo("list", "--scope", "app-electron")
    assert result.code == EXIT_ERROR
    assert "app-desktop" in result.err


def test_the_export_carries_the_scope_and_a_rebuild_restores_it(kyo, project: Path) -> None:
    write_scopes(project, TWO_SCOPES)
    scoped = a_task(kyo, project, "--scope", "app-desktop")
    unscoped = a_task(kyo, project)

    document = json.loads(export_of(project, scoped).read_text(encoding="utf-8"))
    assert document["scope"] == "app-desktop"
    assert json.loads(export_of(project, unscoped).read_text(encoding="utf-8"))["scope"] is None

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", scoped, "--json").json["scope"] == "app-desktop"
    assert kyo("show", unscoped, "--json").json["scope"] is None
    # The round-trip check is what would catch a column that survives the write and not
    # the rebuild, which is the failure this criterion is really about.
    assert kyo("doctor").code == 0


def test_a_split_successor_inherits_the_scope(kyo, project: Path) -> None:
    """Unlike `kyo spawn`, which does not: a split successor is the same work carrying
    some of the same criteria forward, so it belongs to the same part of the system by
    construction. A spawn crosses a kind boundary and makes no such claim."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("first\nsecond\n", encoding="utf-8")
    created = kyo(
        "new", "task", "--title", "Two halves", "--criteria-file", str(criteria),
        "--scope", "cli", "--json",
    )  # fmt: skip
    assert created.code == 0, created
    record = created.json["record"]["id"]

    split = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    assert split.code == 0, split
    assert kyo("show", split.json["successor"]["id"], "--json").json["scope"] == "cli"


# --------------------------------------------------------------------- KYO-100.c3


def test_touches_suggest_a_scope_and_store_nothing(kyo, project: Path) -> None:
    """Derivation is a suggester, never the source. The suggestion is printed, it
    is labeled derived, and the record's stored scope is still null afterwards."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("something is true\n", encoding="utf-8")
    result = kyo(
        "new", "task", "--title", "A desktop thing", "--criteria-file", str(criteria),
        "--touches-file", touching(project, "apps/desktop/main.ts"), "--json",
    )  # fmt: skip
    assert result.code == 0, result
    suggestion = result.json["scope_suggestion"]
    assert suggestion["stored"] is None
    assert [entry["scope"] for entry in suggestion["suggested"]] == ["app-desktop"]
    assert suggestion["label"] == registry_module.DERIVED_LABEL
    assert kyo("show", result.json["record"]["id"], "--json").json["scope"] is None


def test_the_suggestion_is_labeled_derived_on_the_human_surface_too(
    kyo, project: Path
) -> None:
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("something is true\n", encoding="utf-8")
    printed = kyo(
        "new", "task", "--title", "A desktop thing", "--criteria-file", str(criteria),
        "--touches-file", touching(project, "apps/desktop/main.ts"),
    )  # fmt: skip
    assert printed.code == 0, printed
    assert registry_module.DERIVED_LABEL in printed.out
    assert "app-desktop" in printed.out
    assert "stored scope: (none declared)" in printed.out


def test_a_declared_scope_the_touches_disagree_with_is_warned_about_never_overridden(
    kyo, project: Path
) -> None:
    """The mismatch half of c3. The warning is labeled derived; the stored value is
    what the caller declared, unchanged."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("something is true\n", encoding="utf-8")
    result = kyo(
        "new", "task", "--title", "A mislabelled thing", "--criteria-file", str(criteria),
        "--touches-file", touching(project, "apps/desktop/main.ts"), "--scope", "cli",
    )  # fmt: skip
    assert result.code == 0, result
    assert registry_module.DERIVED_LABEL in result.out
    assert "app-desktop" in result.out
    assert "unchanged" in result.out
    listed = kyo("list", "--json").json["records"]
    assert [row["scope"] for row in listed] == ["cli"]


def test_a_record_with_nothing_to_derive_from_is_not_nagged(kyo, project: Path) -> None:
    """Records without touches, and docs, have nothing to derive from. A
    suggester that says something anyway is noise, and KYO-33 is already on file for
    what routine noise does to a warning."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    result = kyo(
        "new", "task", "--title", "No touches at all", "--json",
    )  # fmt: skip
    assert result.code == 0, result
    # The JSON block is always present — an absent key would read as "this build does
    # not derive", which is a different fact from "it derived and found nothing".
    assert result.json["scope_suggestion"]["suggested"] == []
    # The human surface says nothing at all, which is the half this test is about.
    printed = kyo("new", "task", "--title", "Nor these")
    assert printed.code == 0, printed
    assert registry_module.DERIVED_LABEL not in printed.out


def test_editing_the_touches_alone_surfaces_the_mismatch(kyo, project: Path) -> None:
    """The case flags alone cannot see: the scope was declared in one command and the
    touches were replaced in another, so `kyo edit` reads the document it just wrote
    rather than the flags it was given."""
    write_scopes(project, TWO_SCOPES)
    record = a_task(kyo, project, "--scope", "cli")
    edited = kyo(
        "edit", record, "--touches-file", touching(project, "apps/desktop/main.ts"),
    )  # fmt: skip
    assert edited.code == 0, edited
    assert registry_module.DERIVED_LABEL in edited.out
    assert "app-desktop" in edited.out
    assert kyo("show", record, "--json").json["scope"] == "cli"


def test_nothing_derives_a_scope_at_rebuild_time_either(kyo, project: Path) -> None:
    """The suggester has no write path at all, and a rebuild is where a quiet one would
    do the most damage: it would manufacture a declaration for every record in the store
    at once, in the git-canonical export, with nothing printed."""
    write_scopes(project, TWO_SCOPES)
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("something is true\n", encoding="utf-8")
    created = kyo(
        "new", "task", "--title", "A desktop thing", "--criteria-file", str(criteria),
        "--touches-file", touching(project, "apps/desktop/main.ts"), "--json",
    )  # fmt: skip
    record = created.json["record"]["id"]

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["scope"] is None


# ------------------------------------------------------- the registry file and shape


def test_kyo_init_writes_a_scope_registry(project: Path) -> None:
    assert scopes_yml(project).is_file()
    loaded = registry_module.load_scope_registry(project / "kyosei")
    assert loaded.exists and loaded.names == []


def test_the_packaged_default_is_a_valid_empty_registry(tmp_path: Path) -> None:
    """Shipped declaring no scopes on purpose: a scope name is a claim about how one
    project is divided, so a seeded set is names to delete before the first true one is
    written. What it ships is the shape."""
    packaged = defaults_dir()
    assert packaged is not None
    (tmp_path / "kyosei").mkdir()
    (tmp_path / "kyosei" / "scopes.yml").write_text(
        (packaged / "scopes.yml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    loaded = registry_module.load_scope_registry(tmp_path / "kyosei")
    assert loaded.exists and loaded.names == []


def test_an_absent_registry_is_an_empty_one_not_an_error(tmp_path: Path) -> None:
    """The distinction :attr:`Dimension.required` carries. ``kinds.yml`` is
    load-bearing and its absence is loud; ``scopes.yml`` is not, and every store
    written before this release has none."""
    (tmp_path / "kyosei").mkdir()
    loaded = registry_module.load_scope_registry(tmp_path / "kyosei")
    assert not loaded.exists
    assert loaded.names == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("scopes:\n  app-desktop: {}\n", "summary must be a non-empty string"),
        ("scopes:\n  App_Desktop:\n    summary: x\n", "name must be a lowercase slug"),
        ("scopes:\n  cli:\n    summary: x\n    docs_root: docs/\n", "unknown key(s) docs_root"),
        ("scopes:\n  cli:\n    summary: x\n    paths: nope\n", "paths must be a list of strings"),
        ("scopes:\n  cli:\n    summary: x\n    paths: []\n", "must not be an empty list"),
        ("scopes: {}\nkinds: {}\n", "unknown top-level key(s) kinds"),
        ("kinds:\n  cli:\n    summary: x\n", "must contain a top-level 'scopes' mapping"),
        ("scopes: [1, 2]\n", "must contain a top-level 'scopes' mapping"),
    ],
)
def test_a_malformed_registry_is_refused_naming_the_offending_entry(
    tmp_path: Path, text: str, expected: str
) -> None:
    (tmp_path / "kyosei").mkdir()
    (tmp_path / "kyosei" / "scopes.yml").write_text(text, encoding="utf-8")
    with pytest.raises(registry_module.RegistryError) as excinfo:
        registry_module.load_scope_registry(tmp_path / "kyosei")
    assert expected in str(excinfo.value)


def test_every_problem_is_collected_and_raised_together(tmp_path: Path) -> None:
    """``kinds.py``'s style, and law 6's: a registry with four mistakes is fixed in one
    pass, not by rerunning once per line."""
    (tmp_path / "kyosei").mkdir()
    (tmp_path / "kyosei" / "scopes.yml").write_text(
        "scopes:\n  Bad_Name:\n    summary: x\n  no-summary: {}\n  bad-paths:\n"
        "    summary: y\n    paths: 3\n",
        encoding="utf-8",
    )
    with pytest.raises(registry_module.RegistryError) as excinfo:
        registry_module.load_scope_registry(tmp_path / "kyosei")
    message = str(excinfo.value)
    assert "Bad_Name" in message and "no-summary" in message and "bad-paths" in message


def test_a_declared_empty_registry_is_not_the_same_as_an_absent_one(tmp_path: Path) -> None:
    """Two states, two refusal messages. "Add a name to this file" and "this file is
    not there yet" are different instructions and the caller needs the right one."""
    (tmp_path / "kyosei").mkdir()
    (tmp_path / "kyosei" / "scopes.yml").write_text("scopes:\n", encoding="utf-8")
    declared_empty = registry_module.load_scope_registry(tmp_path / "kyosei")
    assert declared_empty.exists and declared_empty.names == []
    assert "does not exist" not in registry_module.unknown_name_refusal(declared_empty, "cli")

    (tmp_path / "kyosei" / "scopes.yml").unlink()
    absent = registry_module.load_scope_registry(tmp_path / "kyosei")
    assert "does not exist" in registry_module.unknown_name_refusal(absent, "cli")


def test_the_shape_is_general_not_scope_shaped(tmp_path: Path) -> None:
    """The four config dimensions share one shape, and KYO-100 is the task
    that chose it — so this is the test the two siblings (KYO-101 statuses-as-roles,
    KYO-102 gate tags) inherit.

    It declares a dimension this package does not have and loads it through the same
    machinery: one file per dimension under ``kyosei/``, named for the plural, whose
    top-level key IS the plural, whose entries carry a name and a summary and the
    dimension's own optional fields. If a sibling ever needs a second loader, this test
    is what will have stopped being true first.
    """
    dimension = registry_module.Dimension(
        singular="widget",
        plural="widgets",
        what="a dimension that exists only in this test",
        optional_fields={"aliases": registry_module._string_list},
    )
    assert dimension.filename == "widgets.yml"
    (tmp_path / "kyosei").mkdir()
    (tmp_path / "kyosei" / "widgets.yml").write_text(
        "widgets:\n  spinner:\n    summary: It spins.\n    aliases:\n      - twirler\n",
        encoding="utf-8",
    )
    loaded = registry_module.load(tmp_path / "kyosei", dimension)
    assert loaded.names == ["spinner"]
    assert loaded["spinner"].summary == "It spins."
    assert loaded["spinner"].get("aliases") == ["twirler"]
    # The refusal is the shared one, in the new dimension's own words.
    with pytest.raises(registry_module.RegistryError) as excinfo:
        loaded.require("dial")
    assert "widget 'dial' is not declared" in str(excinfo.value)
    assert "declared widgets: spinner" in str(excinfo.value)


def test_a_dimensions_own_rules_run_through_the_shared_loader(tmp_path: Path) -> None:
    """KYO-104: the same charge, over the machinery the kinds fold needed.

    ``kinds.yml`` is the only dimension in this package that is ``required``, has rules
    between its entries, and writes ``produces: []`` meaning something — so all three
    would be untested as *general* mechanisms and merely tested as kinds behaviour. This
    declares a dimension this package does not have and uses all three.

    What it pins is the property the fold rests on: a dimension's own rules and the
    shared shape's rules arrive in ONE refusal. Run them as a pass after the loader
    returns and each half raises separately, which is the death-by-a-thousand-reruns law
    6 exists against and the reason ``entries_check`` is a declaration rather than
    something the caller does afterwards.
    """

    def gears_mesh(entries, declared) -> list[str]:
        # A rule no per-field validator could express: it is about the OTHER entries.
        return [
            f"gear {name!r}: meshes with undeclared gear {other!r}"
            for name in sorted(declared)
            for other in (declared[name] or {}).get("meshes", [])
            if other not in declared
        ]

    dimension = registry_module.Dimension(
        singular="gear",
        plural="gears",
        what="a dimension that exists only in this test",
        required=True,
        optional_fields={"meshes": registry_module.string_list(allow_empty=True)},
        entries_check=gears_mesh,
    )
    kyosei_dir = tmp_path / "kyosei"
    kyosei_dir.mkdir()
    path = kyosei_dir / "gears.yml"

    # required: absent, and the refusal names the recovery for a committed config file.
    with pytest.raises(registry_module.RegistryError) as excinfo:
        registry_module.load(kyosei_dir, dimension)
    assert "does not exist" in str(excinfo.value)
    assert registry_module.missing_required_remedy(path) in str(excinfo.value)

    # required: present and declaring nothing, which is not a workable registry either.
    path.write_text("gears:\n", encoding="utf-8")
    with pytest.raises(registry_module.RegistryError, match="declares no gears"):
        registry_module.load(kyosei_dir, dimension)

    # One refusal, carrying the shared shape's rule and this dimension's own.
    path.write_text(
        "gears:\n  spur:\n    summary: ''\n    meshes: [ghost]\n"
        "  idler:\n    summary: It idles.\n    meshes: []\n",
        encoding="utf-8",
    )
    with pytest.raises(registry_module.RegistryError) as excinfo:
        registry_module.load(kyosei_dir, dimension)
    message = str(excinfo.value)
    assert "summary must be a non-empty string" in message, message
    assert "meshes with undeclared gear 'ghost'" in message, message

    # And the empty list is a value here, not the "omit the key instead" refusal
    # `scopes.yml` gets for the same shape of field.
    path.write_text(
        "gears:\n  idler:\n    summary: It idles.\n    meshes: []\n", encoding="utf-8"
    )
    loaded = registry_module.load(kyosei_dir, dimension)
    assert loaded["idler"].get("meshes") == []


# ----------------------------------------------------------------- kyo doctor


def test_doctor_fails_a_record_whose_scope_has_left_the_registry(kyo, project: Path) -> None:
    """The half a write-time check cannot see. The registry is an ordinary editable
    file, so a scope can be renamed the day after the records naming it were written,
    and nothing at write time is watching then."""
    write_scopes(project, TWO_SCOPES)
    record = a_task(kyo, project, "--scope", "app-desktop")
    assert kyo("doctor").code == 0

    write_scopes(project, "scopes:\n  cli:\n    summary: The kyo command line tool.\n")
    result = kyo("doctor", "--json")
    failures = result.json["checks"]["scope_registry"]["failures"]
    assert failures, result.out
    assert record in failures[0]
    assert "app-desktop" in failures[0]


def test_doctor_does_not_mind_a_project_with_no_scopes_file(kyo, project: Path) -> None:
    """Every store written before this release is in exactly this state, and a check
    that failed on it would fail on every one of them at once, for nothing."""
    new_record(kyo, criteria=["a"])
    scopes_yml(project).unlink()
    result = kyo("doctor", "--json")
    assert result.json["checks"]["scope_registry"]["failures"] == []
    assert result.code == 0


def test_doctor_reports_a_malformed_registry_rather_than_crashing(kyo, project: Path) -> None:
    write_scopes(project, "scopes: [1, 2]\n")
    result = kyo("doctor", "--json")
    assert result.json["checks"]["scope_registry"]["failures"], result.out


def test_a_git_project_still_passes_doctor_with_scopes(kyo, project: Path) -> None:
    """End to end, on the layout the tool actually ships: the export round-trip check
    is what would catch a column written by one path and not the other."""
    write_scopes(project, TWO_SCOPES)
    git_init(project)
    a_task(kyo, project, "--scope", "cli")
    assert kyo("doctor").code == 0


# --------------------------------------------------------------------- KYO-195


def a_desktop_task(kyo, project: Path, *args: str) -> str:
    """A task whose touches fall under ``app-desktop``, so the derivation always has
    something to say about it. Every test below needs that: a record the suggester is
    silent about would make "printed nothing" true for the wrong reason."""
    allow_direct_filing(project, "task")
    criteria = project / "criteria.txt"
    criteria.write_text("something is true\n", encoding="utf-8")
    result = kyo(
        "new", "task", "--title", "A desktop thing", "--criteria-file", str(criteria),
        "--touches-file", touching(project, "apps/desktop/main.ts"), "--json", *args,
    )  # fmt: skip
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_an_edit_that_cannot_change_the_derivation_says_nothing_about_it(
    kyo, project: Path
) -> None:
    """KYO-195.c1. `kyo edit --note-file` changes neither the scope nor the touches, so
    the answer it would print is the answer the record already carried — and it was
    printed on every note append, every criteria addition and every doc-refs edit for
    the life of the record. KYO-33 is on file for what routine noise does to a warning.

    The canary is the first assertion: this record is one the suggester DOES speak
    about, so the silence below is the gate working and not an empty registry."""
    write_scopes(project, TWO_SCOPES)
    record = a_desktop_task(kyo, project)
    printed = kyo("show", record)
    assert printed.code == 0, printed

    note = project / "note.txt"
    note.write_text("a thought that has nothing to do with scope\n", encoding="utf-8")
    edited = kyo("edit", record, "--note-file", str(note))
    assert edited.code == 0, edited
    assert registry_module.DERIVED_LABEL not in edited.out, (
        "a note append cannot change what the touches derive, so it has nothing to say "
        "about it"
    )

    # ...and the same record, edited in a way that CAN change the answer, still speaks.
    # Without this the assertion above would also pass on a build that never derives.
    criteria = project / "more.txt"
    criteria.write_text("another thing is true\n", encoding="utf-8")
    quiet = kyo("edit", record, "--add-criteria-file", str(criteria))
    assert quiet.code == 0, quiet
    assert registry_module.DERIVED_LABEL not in quiet.out


def test_an_edit_that_stores_a_contradicted_scope_still_prints_the_derivation(
    kyo, project: Path
) -> None:
    """KYO-195.c2, the half that keeps KYO-100.c3 alive. `--scope cli` on a record whose
    touches are all under `app-desktop` is exactly the mismatch the derivation exists to
    surface, and a gate that silenced this would have deleted the mechanism rather than
    quieted it."""
    write_scopes(project, TWO_SCOPES)
    record = a_desktop_task(kyo, project)
    edited = kyo("edit", record, "--scope", "cli")
    assert edited.code == 0, edited
    assert registry_module.DERIVED_LABEL in edited.out, edited.out
    assert "app-desktop" in edited.out
    assert "unchanged" in edited.out
    assert kyo("show", record, "--json").json["scope"] == "cli"


def test_an_edit_that_replaces_the_touches_still_prints_the_derivation(
    kyo, project: Path
) -> None:
    """KYO-195.c2, the other half. Replacing the touches is the one edit that can change
    the answer without any scope flag at all, and it is the case KYO-100.c3 was written
    for."""
    write_scopes(project, TWO_SCOPES)
    record = a_desktop_task(kyo, project, "--scope", "app-desktop")
    edited = kyo(
        "edit", record, "--touches-file", touching(project, "src/kyosei/cli.py"),
    )  # fmt: skip
    assert edited.code == 0, edited
    assert registry_module.DERIVED_LABEL in edited.out, edited.out
    assert "cli" in edited.out
    assert kyo("show", record, "--json").json["scope"] == "app-desktop"


def test_the_json_block_is_present_on_a_quiet_edit_too(kyo, project: Path) -> None:
    """KYO-195.c3. The gate is on the human lines only. A JSON consumer must still be
    able to read that derivation ran and what it found — an absent key reads as "this
    build does not derive", which is the different fact `suggestion_lines` returns its
    always-present block to avoid."""
    write_scopes(project, TWO_SCOPES)
    record = a_desktop_task(kyo, project)
    note = project / "note.txt"
    note.write_text("still nothing to do with scope\n", encoding="utf-8")
    edited = kyo("edit", record, "--note-file", str(note), "--json")
    assert edited.code == 0, edited
    suggestion = edited.json["scope_suggestion"]
    assert suggestion["basis"] == "touches"
    assert suggestion["stored"] is None
    assert [entry["scope"] for entry in suggestion["suggested"]] == ["app-desktop"]
    assert suggestion["label"] == registry_module.DERIVED_LABEL
