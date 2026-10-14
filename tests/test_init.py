"""``kyo init`` scaffolding, and the seam milestone 2 extends."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kyosei import install_gitignore, scaffold
from kyosei.errors import KyoseiError
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

from .conftest import invoke


def _patterns(gitignore: str) -> list[str]:
    """The ignore patterns in a ``.gitignore``'s text — comments and blanks dropped."""
    return [
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_init_creates_the_charter_layout(project: Path) -> None:
    assert (project / "ideas").is_dir()
    # KYO-566.c4: the scratchpad lives in the per-project home, so init writes none here.
    assert not (project / "ideas" / "scratchpad.md").exists()
    assert (project / "kyosei" / "kyosei.db").is_file()
    assert (project / "kyosei" / "export").is_dir()
    assert (project / "kyosei" / "config.yml").is_file()


def test_init_ships_the_default_kind_registry_and_procedures(project: Path) -> None:
    """Milestone 2: DEFAULT_ASSETS is no longer empty (law 3, single packaged copy)."""
    assert (project / "kyosei" / "kinds.yml").is_file()
    assert (project / "kyosei" / "procedures.md").is_file()
    kinds_text = (project / "kyosei" / "kinds.yml").read_text(encoding="utf-8")
    assert "bug:" in kinds_text and "decision:" in kinds_text


def test_init_scaffolds_a_gitignore_for_the_database(project: Path) -> None:
    """Charter, Layer 2: consumer repos commit the export, never the db it derives from."""
    gitignore = (project / "kyosei" / ".gitignore").read_text(encoding="utf-8")
    assert "kyosei.db" in gitignore


def test_the_scaffolded_gitignore_covers_the_wal_sidecars(project: Path) -> None:
    """KYO-322.c1. The store opens its database in WAL mode, so SQLite keeps
    ``kyosei.db-shm`` and ``kyosei.db-wal`` beside it for as long as any connection is
    open. Ignoring only ``kyosei.db`` produced a `git add kyosei` that failed with
    "unable to stat 'kyosei/kyosei.db-shm'" — the file vanished between the status and
    the add — plus a dirty tree at a worker's stop and the risk of committing a WAL
    file carrying unexported rows."""
    gitignore = (project / "kyosei" / ".gitignore").read_text(encoding="utf-8")
    patterns = _patterns(gitignore)
    assert "kyosei.db-shm" in patterns
    assert "kyosei.db-wal" in patterns


def test_the_sidecar_patterns_are_derived_from_the_database_name() -> None:
    """KYO-322.c1's "derived from DB_NAME rather than spelled twice", stated as the
    mechanism rather than as the output: rename the database and every pattern moves
    with it. Three literals that have to agree about one filename is two chances for
    them not to, and this test fails the moment the derivation becomes a literal."""
    from kyosei.config import DB_NAME

    renamed = _patterns(scaffold.store_gitignore_text("records.sqlite3", "s.json", "r.txt"))
    assert renamed == [
        "records.sqlite3",
        "records.sqlite3-shm",
        "records.sqlite3-wal",
        "s.json",
        "r.txt",
        # KYO-407.c8: the sessions temporary follows the sessions name it is written
        # beside; the atomic-write temporary follows no name, only its suffix.
        "s.json.*.tmp",
        "*.kyosei-tmp",
    ]
    assert not any(pattern.startswith("kyosei.db") for pattern in renamed)

    # ...and the shipped constant is that same function applied to the real names.
    assert scaffold.store_gitignore_text() == scaffold.KYOSEI_GITIGNORE
    for suffix in scaffold.DB_SIDECAR_SUFFIXES:
        assert f"{DB_NAME}{suffix}" in _patterns(scaffold.KYOSEI_GITIGNORE)


def test_a_project_that_predates_the_sidecar_lines_gets_them_from_install_procedures(
    kyo, project: Path
) -> None:
    """KYO-322.c1's "an existing project gets the lines by a command it already reruns
    after an upgrade". Every project scaffolded before this release has the gap and
    nothing would ever have told it: `kyo init` writes the file once and no command
    read it again."""
    gitignore = project / "kyosei" / ".gitignore"
    gitignore.write_text("kyosei.db\nsessions.json\n", encoding="utf-8")

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["changed"] is True
    assert len(result.json["gitignore_changes"]) == 1

    patterns = _patterns(gitignore.read_text(encoding="utf-8"))
    assert "kyosei.db-shm" in patterns
    assert "kyosei.db-wal" in patterns
    # Additive, never a rewrite: what the project already had is still there.
    assert "sessions.json" in patterns


def test_the_top_up_is_idempotent_and_leaves_a_projects_own_lines_alone(kyo, project: Path) -> None:
    gitignore = project / "kyosei" / ".gitignore"
    gitignore.write_text("kyosei.db\nsessions.json\nscratch/\n", encoding="utf-8")
    assert kyo("install-procedures").code == 0
    after = gitignore.read_text(encoding="utf-8")

    again = kyo("install-procedures", "--json")
    assert again.code == 0
    assert again.json["gitignore_changes"] == []
    assert gitignore.read_text(encoding="utf-8") == after
    assert "scratch/" in _patterns(after)


def test_print_mode_names_the_top_up_without_writing_it(kyo, project: Path) -> None:
    gitignore = project / "kyosei" / ".gitignore"
    gitignore.write_text("kyosei.db\nsessions.json\n", encoding="utf-8")

    result = kyo("install-procedures", "--print", "--json")
    assert result.code == 0, result
    assert len(result.json["gitignore_changes"]) == 1
    assert "would add" in result.json["gitignore_changes"][0]
    assert _patterns(gitignore.read_text(encoding="utf-8")) == ["kyosei.db", "sessions.json"]


def test_a_project_whose_anchored_patterns_already_cover_it_is_left_alone(
    project: Path,
) -> None:
    """A maintainer who added the lines themselves — with git's leading slash, which
    means the same thing in this directory — is not given a duplicate pair."""
    gitignore = project / "kyosei" / ".gitignore"
    gitignore.write_text(
        "/kyosei.db\n/kyosei.db-shm\n/kyosei.db-wal\n/sessions.json\n/doctor-report.txt\n"
        "/sessions.json.*.tmp\n/*.kyosei-tmp\n",
        encoding="utf-8",
    )
    assert install_gitignore.missing_store_gitignore_lines(project) == []
    changes, _ = install_gitignore.install_store_gitignore(project)
    assert changes == []


def test_the_split_layout_tops_up_the_stores_own_gitignore(
    split_project: tuple[Path, Path],
) -> None:
    """KYO-322.c1's "under both the in-repo and the split store layout". Under the
    split layout the store is a different git repository from the code, and this file
    is in the store's — which is why the top-up hangs off the one upgrade command that
    takes the store root."""
    code, store = split_project
    gitignore = store / "kyosei" / ".gitignore"
    gitignore.write_text("kyosei.db\nsessions.json\n", encoding="utf-8")

    result = invoke(["--root", str(code), "install-procedures", "--json"])
    assert result.code == 0, result
    patterns = _patterns(gitignore.read_text(encoding="utf-8"))
    assert "kyosei.db-shm" in patterns
    assert "kyosei.db-wal" in patterns
    # The code repository is a different repository here and git would never consult
    # its .gitignore for a path in the store, so nothing was written there.
    code_ignore = code / ".gitignore"
    if code_ignore.is_file():
        assert "kyosei.db-wal" not in code_ignore.read_text(encoding="utf-8")


def test_the_scaffolded_gitignore_has_no_line_for_the_retired_static_viewer(
    project: Path,
) -> None:
    """KYO-215. This asserted `html/` in the ignore file for KYO-11's generated site.

    `kyo html` is retired, nothing writes that directory any more, and an ignore rule
    for output no build produces is a line every later reader has to work out is dead.
    A project that still HAS the directory keeps its own line, which is theirs;
    `kyo doctor`'s `retired_html_directory` notice is what speaks to them.
    """
    gitignore = (project / "kyosei" / ".gitignore").read_text(encoding="utf-8")
    assert "html/" not in gitignore


def test_config_carries_exactly_the_keys_that_have_consumers(project: Path) -> None:
    """Law 2, at the config layer: no key without a reader, no reader without a key.

    KYO-60 adds the one exception and bounds it: a key in ``OPTIONAL_CONFIG_KEYS``
    may be absent, because its absence is meaningful (nobody has acknowledged
    publication) and because requiring it would make every config file written
    before it existed unreadable. Nothing else may be missing, and nothing outside
    the registry may be present, in either direction.
    """
    raw = yaml.safe_load((project / "kyosei" / "config.yml").read_text(encoding="utf-8"))
    assert set(raw) <= set(CONFIG_CONSUMERS)
    assert set(CONFIG_CONSUMERS) - set(raw) <= OPTIONAL_CONFIG_KEYS
    assert raw["project_prefix"] == "TST"


def test_config_comments_document_every_field_from_the_consumer_registry(project: Path) -> None:
    """KYO-2: the comments are generated from ``schema.CONFIG_CONSUMERS`` itself, so
    they cannot say something the code no longer does (law 3)."""
    text = (project / "kyosei" / "config.yml").read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    for key, consumers in CONFIG_CONSUMERS.items():
        if key not in raw:  # an optional key this project has no value for
            continue
        assert f"# {key}:" in text, f"no generated comment for {key!r}"
        for consumer in consumers:
            assert consumer in text, f"{key!r}'s consumer {consumer!r} is not named in the comments"


def test_config_comments_track_a_changed_registry(monkeypatch, tmp_path: Path) -> None:
    """The mechanism this test would fail if removed: comments come from
    ``CONFIG_CONSUMERS``, not from a hand-typed string that could drift from it."""
    from kyosei import config as config_module

    fake_consumers = {
        "schema_version": ("store.open_store",),
        "project_prefix": ("store.allocate",),
        "export_dir": ("export.export_dir",),
    }
    monkeypatch.setattr(config_module, "CONFIG_CONSUMERS", fake_consumers)
    text = config_module.render_config(
        {"schema_version": 1, "project_prefix": "TST", "export_dir": "export"}
    )
    assert "read by store.open_store" in text
    assert "read by store.allocate" in text


def test_render_config_refuses_a_values_mapping_out_of_step_with_the_registry(
    tmp_path: Path,
) -> None:
    from kyosei import config as config_module

    with pytest.raises(KyoseiError, match="out of step"):
        config_module.render_config({"schema_version": 1})


def test_prefix_defaults_to_the_directory_name(tmp_path: Path) -> None:
    root = tmp_path / "my-app"
    assert invoke(["init", str(root), "--in-repo"]).code == 0
    raw = yaml.safe_load((root / "kyosei" / "config.yml").read_text(encoding="utf-8"))
    assert raw["project_prefix"] == "MYAP"


def test_prefix_defaults_to_the_code_directory_name_under_the_split_layout(
    tmp_path: Path,
) -> None:
    """KYO-54: the store directory is named after the project, but `--store` can
    point anywhere, so the prefix must come from the *code* directory's name — not
    from whatever the store path happens to be called."""
    root = tmp_path / "my-app"
    root.mkdir()
    store = tmp_path / "elsewhere" / "a-store-named-something-else"
    result = invoke(["init", str(root), "--store", str(store), "--json"])
    assert result.code == 0, result
    raw = yaml.safe_load((store / "kyosei" / "config.yml").read_text(encoding="utf-8"))
    assert raw["project_prefix"] == "MYAP"


def test_init_refuses_to_clobber_an_existing_project(project: Path) -> None:
    """Law 6: "success" must mean the thing happened; re-init would silently not."""
    result = invoke(["init", str(project)])
    assert result.code == 1
    assert "already" in result.err


def test_init_rejects_an_illegal_prefix(tmp_path: Path) -> None:
    result = invoke(["init", "--defaults", str(tmp_path / "p"), "--prefix", "a-b-c"])
    assert result.code == 64
    assert "prefix" in result.err


def test_commands_outside_a_project_fail_loudly(tmp_path: Path) -> None:
    result = invoke(["--root", str(tmp_path / "nowhere"), "show", "TST-1"])
    assert result.code == 1
    assert "no Kyosei project" in result.err or "config.yml" in result.err


def test_default_asset_seam_copies_files_for_milestone_two(tmp_path: Path, monkeypatch) -> None:
    """``init`` grows kinds.yml/procedures.md by appending to DEFAULT_ASSETS only.

    This test stands in for milestone 2: it declares a synthetic asset and proves
    ``init``'s copy step already works, so adding the real defaults needs no rework.
    """
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    monkeypatch.setattr(scaffold, "defaults_dir", lambda: defaults)

    root = tmp_path / "proj"
    root.mkdir()
    written = scaffold.copy_assets(root, (scaffold.Asset("kinds.yml", "kyosei/kinds.yml"),))
    assert written == [root / "kyosei" / "kinds.yml"]
    assert (root / "kyosei" / "kinds.yml").read_text(encoding="utf-8") == "kinds: {}\n"


def test_missing_default_asset_fails_loudly(tmp_path: Path, monkeypatch) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    monkeypatch.setattr(scaffold, "defaults_dir", lambda: defaults)
    with pytest.raises(KyoseiError, match="missing"):
        scaffold.copy_assets(tmp_path, (scaffold.Asset("kinds.yml", "kyosei/kinds.yml"),))
