"""KYO-158: `kyo rebuild` deleted config keys ``write_config`` was never taught.

``kyo rebuild`` rewrites ``kyosei/config.yml`` on every run so its ``schema_version``
matches the database it just built (KYO-5.c1). What survived that rewrite used to be a
hand-written parameter list on :func:`kyosei.config.write_config`. ``worktree_setup``
(KYO-143) was added to ``CONFIG_CONSUMERS``, to the parser and to :class:`Config`, and
not to that list — so every rebuild silently deleted a project's declared worktree
setup, and the comment block explaining it, from a git-tracked file.

Two things are pinned here, and the second is the one that matters. The first is that
the key survives. The second is that *nothing was added to a list to make it survive*:
the carried set is derived from :data:`kyosei.schema.OPTIONAL_CONFIG_KEYS`, so a future
optional key is carried the moment it is registered. The class of bug — a
hand-maintained list that must agree with a set the code already knows — is the reason
this was filed as a bug rather than a papercut, and the derivation is the fix that ends
it rather than fixing one instance of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kyosei import config as config_module
from kyosei.config import load_config, write_config
from kyosei.errors import KyoseiError
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

from .conftest import invoke

# A project's config, with a comment block and inline notes of the shape this
# repository's own config.yml carries — the file the bug was found against.
DECLARED_SETUP = """\
# worktree_setup: read by brief.generate_brief, cmd_dispatch._cmd_dispatch_create (KYO-143).
# The commands a fresh worktree needs before this project's tests can run there.
# `make test` self-provisions the venv; the desktop half is listed separately
# because `npm ci` leaves electron/dist absent.
worktree_setup:
  - make test          # provisions .venv, then runs the suite
  - cd apps/desktop && npm ci   # desktop records only
"""


def _declare(project: Path, block: str = DECLARED_SETUP) -> Path:
    path = project / "kyosei" / "config.yml"
    path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
    return path


# --------------------------------------------------------------------- c1


def test_rebuild_preserves_a_declared_worktree_setup(project: Path) -> None:
    """KYO-158.c1, against the named instance: the key the list had fallen behind.

    Before the fix this assertion failed on this repository's own config: `kyo
    rebuild` returned 0, reported a healthy store, and left a config.yml with the
    key gone.
    """
    path = _declare(project)
    result = invoke(["--root", str(project), "rebuild", "--force"])
    assert result.code == 0, result
    assert load_config(project).worktree_setup == (
        "make test",
        "cd apps/desktop && npm ci",
    )
    assert "worktree_setup" in yaml.safe_load(path.read_text(encoding="utf-8"))


def test_rebuild_preserves_every_optional_key_at_once(project: Path) -> None:
    """KYO-158.c1, generalised: no optional key is dropped, and the set of keys
    tried is read from the registry rather than typed out here — a test that listed
    them would be a fourth copy of the list this record exists to delete."""
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    # A value per optional key, derived: the carry-forward never inspects the value,
    # so a marker string exercises it for any key the registry may grow.
    for key in sorted(OPTIONAL_CONFIG_KEYS):
        raw.setdefault(key, f"carried-{key}")
    path.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")

    write_config(project, project_prefix="TST", export_dir="export")

    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in sorted(OPTIONAL_CONFIG_KEYS):
        assert key in after, f"{key!r} was dropped by a rewrite (KYO-158)"
        assert after[key] == raw[key], f"{key!r} was rewritten, not carried"


def test_an_explicit_argument_still_wins_over_the_stored_value(project: Path) -> None:
    """The carry-forward must not have become an override: `kyo check-push
    --acknowledge` overwriting an older acknowledgement is the behaviour KYO-60
    relies on."""
    write_config(project, project_prefix="TST", worker_model="old-model")
    write_config(project, project_prefix="TST", worker_model="new-model")
    assert load_config(project).worker_model == "new-model"


# --------------------------------------------------------------------- c2, c3


def test_the_carried_set_is_the_registry_itself(monkeypatch, project: Path) -> None:
    """KYO-158.c2 and c3, and the test that fails if the mechanism is removed.

    A key nothing in this codebase has ever heard of is declared optional at runtime
    and must survive a rewrite. A hand-written parameter list cannot pass this: the
    name does not appear anywhere in ``config.py``, so the only way the value is
    still on disk afterwards is that the carried set was read from the registry.
    """
    future = "pretend_future_key"
    monkeypatch.setattr(
        config_module,
        "CONFIG_CONSUMERS",
        {**CONFIG_CONSUMERS, future: ("some_module.some_reader",)},
    )
    monkeypatch.setattr(
        config_module, "OPTIONAL_CONFIG_KEYS", OPTIONAL_CONFIG_KEYS | {future}
    )
    path = project / "kyosei" / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8") + f"{future}: declared-by-a-later-release\n",
        encoding="utf-8",
    )

    write_config(project, project_prefix="TST", export_dir="export")

    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after[future] == "declared-by-a-later-release", (
        "an optional key registered after write_config was last edited was dropped — "
        "the carry-forward is a hand-maintained list again (KYO-158.c2)"
    )


def test_write_config_takes_no_optional_key_the_registry_does_not_declare() -> None:
    """The other direction of the same derivation: the accepted keywords are the
    registry's, so a typo is refused by name instead of being written as a key no
    reader claims (law 2)."""
    with pytest.raises(KyoseiError, match="worker_modle"):
        write_config(Path("/nonexistent"), project_prefix="TST", worker_modle="x")


def test_no_optional_key_is_named_in_write_configs_signature() -> None:
    """KYO-158.c2, stated structurally rather than behaviourally.

    The defect was a *parameter list*. This fails if one grows back, which is the
    form the regression would take — someone adding the missing name rather than
    keeping the derivation.
    """
    import inspect

    parameters = set(inspect.signature(write_config).parameters)
    restated = parameters & set(CONFIG_CONSUMERS)
    assert restated <= (set(CONFIG_CONSUMERS) - OPTIONAL_CONFIG_KEYS), (
        f"write_config names optional config key(s) {sorted(restated)} in its "
        "signature; the carried set must stay derived from OPTIONAL_CONFIG_KEYS"
    )


# --------------------------------------------------------------------- c4


def test_a_projects_own_comments_survive_a_rebuild(project: Path) -> None:
    """KYO-158.c4, in its stronger form: the comments survive, so there is nothing
    to announce. The rebuild changes the version number on its own line and leaves
    every other byte of the file alone."""
    path = _declare(project)
    before = path.read_text(encoding="utf-8")
    result = invoke(["--root", str(project), "rebuild", "--force"])
    assert result.code == 0, result
    after = path.read_text(encoding="utf-8")
    for line in before.splitlines():
        if line.lstrip().startswith("#") or "#" in line:
            assert line in after, f"comment line lost by a rebuild: {line!r}"
    assert "config.yml" not in result.out, (
        "nothing was dropped, so the rebuild should say nothing about config.yml"
    )


def test_a_version_bump_rewrites_one_line_and_nothing_else(
    monkeypatch, project: Path
) -> None:
    """The mechanism behind the test above: an in-place substitution, not a lucky
    no-op. Without the version change the file is already correct and any
    implementation passes."""
    path = _declare(project)
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(config_module, "SCHEMA_VERSION", 9999)
    write_config(project, project_prefix="TST", export_dir="export")
    after = path.read_text(encoding="utf-8")

    old_lines, new_lines = before.splitlines(), after.splitlines()
    assert len(old_lines) == len(new_lines), "a version bump changed the file's shape"
    changed = [
        (old, new)
        for old, new in zip(old_lines, new_lines, strict=True)
        if old != new
    ]
    assert len(changed) == 1, f"a version bump touched more than one line: {changed}"
    assert changed[0][1] == "schema_version: 9999"


def test_a_rewrite_that_cannot_keep_comments_says_what_it_dropped(
    project: Path,
) -> None:
    """KYO-158.c4's fallback, for the case in-place editing cannot cover: adding a
    key needs its generated comment placed, which only a full re-render can do. The
    project's comments do not survive that, and silence about it is the defect —
    a lossy rewrite that announces itself is defensible."""
    _declare(project)
    dropped: list[str] = []
    write_config(
        project,
        project_prefix="TST",
        export_dir="export",
        worker_model="some-model",  # a key the file does not have yet
        rewritten_wholesale=dropped,
    )
    assert dropped, "a full re-render dropped comment lines and reported none"
    assert any("make test" in line for line in dropped)
    # The values themselves are never the casualty — only the prose around them.
    assert load_config(project).worktree_setup == (
        "make test",
        "cd apps/desktop && npm ci",
    )
    assert load_config(project).worker_model == "some-model"


def test_the_generated_comment_is_still_refreshed_from_the_registry(
    monkeypatch, project: Path
) -> None:
    """Preserving a project's comments must not preserve a stale copy of Kyosei's.

    KYO-2's whole point is that the per-key comment naming the readers is generated,
    so it cannot claim a consumer the code no longer has. An in-place edit that left
    the whole file alone would quietly turn that guarantee off.
    """
    path = project / "kyosei" / "config.yml"
    monkeypatch.setattr(
        config_module,
        "CONFIG_CONSUMERS",
        {**CONFIG_CONSUMERS, "export_dir": ("export.export_dir_renamed",)},
    )
    write_config(project, project_prefix="TST", export_dir="export")
    text = path.read_text(encoding="utf-8")
    assert "# export_dir: read by export.export_dir_renamed" in text
    assert "read by export.export_dir\n" not in text


def test_a_project_suffix_on_the_generated_comment_survives_the_refresh(
    monkeypatch, project: Path
) -> None:
    """This repository's own config appends ``(KYO-143).`` to the generated line and
    continues the thought below it. Refreshing the derived half must not eat the
    half a person wrote."""
    path = project / "kyosei" / "config.yml"
    text = path.read_text(encoding="utf-8").replace(
        "# export_dir: read by export.export_dir",
        "# export_dir: read by export.export_dir (and see the note in README).",
    )
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config_module, "SCHEMA_VERSION", 9999)
    write_config(project, project_prefix="TST", export_dir="export")
    assert "(and see the note in README)." in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["export_dir", "controller_only_commands"])
def test_the_full_stop_before_a_projects_own_words_survives_the_refresh(key: str) -> None:
    """KYO-359, a consumer's config after a schema upgrade: ``read by
    doctor.check_controller_only_setup Operator: declared.`` The stop after
    the consumer list is the seam between the tool's half and the person's, and a
    pattern that took it as the last identifier's last character regenerated the line
    without it. One single-consumer key and one comma-joined, since the report guessed
    the second was the only form that lost it and both did."""
    consumers = ", ".join(CONFIG_CONSUMERS[key])
    assert ("," in consumers) == (key == "controller_only_commands")
    line = f"# {key}: read by {consumers}. Operator: declared.\n"
    assert config_module._refreshed_generated_comment(line, key) == line


# --------------------------------------------------------------------- kyo init


def test_init_is_unaffected_by_the_carry_forward(tmp_path: Path) -> None:
    """`kyo init` and `kyo rebuild` go through the same function, so a fix aimed at
    the rebuild path has to be checked against the fresh-project one: there is no
    existing file to carry anything forward from, and the generated config must
    still be exactly what the registry declares."""
    root = tmp_path / "fresh"
    assert invoke(["init", str(root), "--in-repo", "--prefix", "TST"]).code == 0
    raw = yaml.safe_load((root / "kyosei" / "config.yml").read_text(encoding="utf-8"))
    assert set(raw) <= set(CONFIG_CONSUMERS)
    assert set(CONFIG_CONSUMERS) - set(raw) <= OPTIONAL_CONFIG_KEYS
    assert raw["project_prefix"] == "TST"
    assert load_config(root).worktree_setup == ()
