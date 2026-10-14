"""KYO-317: `kyo version` says which kind of install is running, and the "to move to
a newer build" line (plus its siblings in buildinfo.py and changelog.py) names the
route for THAT kind instead of the uv-only wording that used to answer every reader.

The measured defect: `kyo version`'s upgrade line, printed by the app's bundled build,
sent a consumer project toward `uv tool install --force` for a CLI its session was not
running. `install_kind()` is derived from where the
running package's directory sits, never from a stored setting -- a setting can go
stale the moment a second install route exists beside it, which is exactly what
happened here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import buildinfo

from .conftest import invoke

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------- c1: the four honest kinds


def test_this_checkout_s_own_package_is_a_working_tree() -> None:
    """The running case, exercised for real: this repository's own `src/kyosei` is a
    working tree, because :func:`buildinfo.source_tree_root` already finds `.git`
    above it -- the pre-existing, stronger signal `install_kind` defers to first."""
    assert buildinfo.install_kind(buildinfo.package_dir()) == buildinfo.INSTALL_KIND_WORKING_TREE


def test_a_package_dir_under_the_retired_kyo_runtime_is_no_longer_its_own_kind(
    tmp_path: Path,
) -> None:
    """KYO-450 retired the desktop app's bundled runtime, and its kind with it: a copy
    laid out where the packed app used to stage one
    (`<resources>/kyo-runtime/lib/python3.12/site-packages/kyosei`) is no longer told
    apart -- it gets the honest answer, whose route is the install that replaces it."""
    package = (
        tmp_path
        / "Kyosei.app"
        / "Contents"
        / "Resources"
        / "kyo-runtime"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "kyosei"
    )
    package.mkdir(parents=True)
    assert buildinfo.install_kind(package) == buildinfo.INSTALL_KIND_UNKNOWN
    assert not hasattr(buildinfo, "INSTALL_KIND_BUNDLED")
    assert "bundled" not in buildinfo.INSTALL_KIND_LABELS
    assert all("bundled" not in label for label in buildinfo.INSTALL_KIND_LABELS.values())


def test_a_package_dir_under_uv_tools_is_a_uv_tool_install(tmp_path: Path) -> None:
    """`uv tool install`'s own layout, measured directly against a real `uv tool
    install kyosei`: `<uv data dir>/uv/tools/kyosei/lib/python3.12/
    site-packages/kyosei` -- the `uv/tools/<name>/...` segment pair is what
    :func:`buildinfo.install_kind` looks for, and the prefix ahead of it (here,
    `.local/share`, but XDG- or `UV_TOOL_DIR`-configurable) is deliberately ignored."""
    package = (
        tmp_path
        / ".local"
        / "share"
        / "uv"
        / "tools"
        / "kyosei"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "kyosei"
    )
    package.mkdir(parents=True)
    assert buildinfo.install_kind(package) == buildinfo.INSTALL_KIND_UV_TOOL


def test_a_uv_tools_directory_configured_elsewhere_is_still_recognised(tmp_path: Path) -> None:
    """The segment pair, not the prefix, is what matters -- a `UV_TOOL_DIR` override or
    a different `XDG_DATA_HOME` must not silently fall through to `cannot-tell`."""
    package = tmp_path / "somewhere-else" / "uv" / "tools" / "kyosei" / "site-packages" / "kyosei"
    package.mkdir(parents=True)
    assert buildinfo.install_kind(package) == buildinfo.INSTALL_KIND_UV_TOOL


def test_a_plain_site_packages_install_cannot_be_told_apart(tmp_path: Path) -> None:
    """The fourth, honest answer: a `pip install` or a `pipx` install lands in
    site-packages too, but under neither marker -- this must never be guessed as a
    uv tool, which is exactly the mistake KYO-317 exists to stop this project making
    a second time."""
    package = tmp_path / "some-venv" / "lib" / "python3.12" / "site-packages" / "kyosei"
    package.mkdir(parents=True)
    assert buildinfo.install_kind(package) == buildinfo.INSTALL_KIND_UNKNOWN


def test_an_unrelated_empty_directory_cannot_be_told_apart(tmp_path: Path) -> None:
    """No structure at all -- the same honest answer, not a crash and not a guess."""
    assert buildinfo.install_kind(tmp_path) == buildinfo.INSTALL_KIND_UNKNOWN


# ------------------------------------------- the bundled marker is gone everywhere

_KYO_TS = REPO_ROOT / "apps" / "desktop" / "src" / "main" / "kyo.ts"
_BUILD_MJS = REPO_ROOT / "apps" / "desktop" / "build.mjs"


def test_neither_the_desktop_resolver_nor_the_packer_names_the_runtime_any_more() -> None:
    """KYO-450: this marker used to be held to `BUNDLED_RUNTIME_DIR` in `kyo.ts` and
    `RUNTIME_DIR_NAME` in `build.mjs`. All three are gone together; a runtime staged
    back into the app would have to reintroduce the name, and this says where."""
    for path in (_KYO_TS, _BUILD_MJS):
        text = path.read_text(encoding="utf-8")
        assert "BUNDLED_RUNTIME_DIR" not in text, path
        assert '"kyo-runtime"' not in text, path


# --------------------------------------------------------------- the route per kind


def test_an_older_build_s_retired_bundled_kind_is_sent_to_the_install() -> None:
    """KYO-450: a PATH build older than this one can still answer `bundled` (KYO-335's
    probe reads another build's own kind). Its route is no longer a re-pack of the app,
    which ships no `kyo` now -- it is the install that replaces it."""
    route = buildinfo.upgrade_route("bundled")
    assert route == buildinfo.upgrade_route(buildinfo.INSTALL_KIND_UNKNOWN)
    assert "uv tool install kyosei" in route
    assert "npm run pack" not in route
    assert "re-pack" not in route


def test_upgrade_route_names_the_resnapshot_for_an_unreleased_uv_tool() -> None:
    """KYO-446.c3: an unreleased uv tool is a snapshot of a checkout; its route is
    the re-snapshot, and `uv tool upgrade` is named only as what NOT to reach for."""
    route = buildinfo.upgrade_route(buildinfo.INSTALL_KIND_UV_TOOL, release=False)
    assert buildinfo.RESNAPSHOT_TARGET in route
    assert "uv tool install --force ." in route
    assert "npm run pack" not in route
    assert "unreleased" in route


def test_upgrade_route_names_uv_tool_upgrade_for_a_released_uv_tool() -> None:
    """KYO-446.c3: a release build came from PyPI's route and moves along it."""
    route = buildinfo.upgrade_route(buildinfo.INSTALL_KIND_UV_TOOL, release=True)
    assert buildinfo.UV_TOOL_UPGRADE_COMMAND in route
    assert buildinfo.RESNAPSHOT_TARGET not in route
    assert "uv tool install --force" not in route
    assert "npm run pack" not in route


def test_upgrade_route_reads_an_unstated_release_as_unreleased() -> None:
    """A kind passed without a release fact describes another build that did not say;
    every build that cannot say predates KYO-445, and so every release."""
    route = buildinfo.upgrade_route(buildinfo.INSTALL_KIND_UV_TOOL)
    assert buildinfo.RESNAPSHOT_TARGET in route


@pytest.mark.parametrize("release", [True, False])
def test_upgrade_route_with_no_arguments_asks_this_copy_for_both_facts(
    monkeypatch, release
) -> None:
    """The hooks' NOTHING WAS CHECKED line calls `upgrade_route()` bare: a uv-tool copy
    answering it must use its OWN release fact, not the unreleased default."""
    real = buildinfo.build_identity()
    monkeypatch.setattr(
        buildinfo, "install_kind", lambda *_a, **_k: buildinfo.INSTALL_KIND_UV_TOOL
    )
    monkeypatch.setattr(
        buildinfo,
        "build_identity",
        lambda *_a, **_k: buildinfo.BuildIdentity(**{**real.__dict__, "release": release}),
    )
    assert buildinfo.upgrade_route() == buildinfo.upgrade_route(
        buildinfo.INSTALL_KIND_UV_TOOL, release
    )


@pytest.mark.parametrize("release", [True, False])
def test_kyo_version_s_upgrade_line_carries_this_copy_s_release(monkeypatch, release) -> None:
    """`kyo version`'s "to move to a newer build" line splits a uv tool by release too."""
    real = buildinfo.build_identity()
    fake = buildinfo.BuildIdentity(
        **{**real.__dict__, "kind": buildinfo.INSTALL_KIND_UV_TOOL, "release": release}
    )
    monkeypatch.setattr(buildinfo, "build_identity", lambda *_a, **_k: fake)
    result = invoke(["version"])
    assert result.code == 0, result
    route_line = next(
        line for line in result.out.splitlines() if "to move to a newer build" in line
    )
    expected = (
        buildinfo.UV_TOOL_UPGRADE_COMMAND if release else buildinfo.RESNAPSHOT_TARGET
    )
    assert expected in route_line, route_line


def test_upgrade_route_names_nothing_to_reinstall_for_a_working_tree() -> None:
    route = buildinfo.upgrade_route(buildinfo.INSTALL_KIND_WORKING_TREE)
    assert "nothing to reinstall" in route
    assert "uv tool install" not in route
    assert "npm run pack" not in route


def test_upgrade_route_names_both_routes_when_it_cannot_tell() -> None:
    """The honest answer's route: both uv routes, named, rather than a guess (KYO-317's
    task: "a fourth honest answer ('cannot tell') whose upgrade line names both routes
    rather than guessing"). KYO-450: the two routes are now the published release and
    the re-snapshot -- the desktop app's Install Command Line Tools and its re-pack are
    retired with its bundled runtime."""
    route = buildinfo.upgrade_route(buildinfo.INSTALL_KIND_UNKNOWN)
    assert "uv tool install kyosei" in route
    assert buildinfo.RESNAPSHOT_TARGET in route
    assert "uv tool install --force ." in route
    assert "npm run pack" not in route
    assert "Install Command Line Tools" not in route


# ------------------------------------------------------------- BuildIdentity.kind


def test_build_identity_carries_the_kind_of_this_running_copy() -> None:
    identity = buildinfo.build_identity()
    assert identity.kind == buildinfo.install_kind(buildinfo.package_dir())
    assert identity.kind in buildinfo.INSTALL_KIND_LABELS


def test_build_identity_as_data_carries_kind_too() -> None:
    assert buildinfo.build_identity().as_data()["kind"] == buildinfo.build_identity().kind


def test_a_copy_with_no_stamp_and_no_checkout_names_a_route_for_its_own_kind(
    tmp_path: Path,
) -> None:
    """The "build unknown" sentence used to hardcode the uv route regardless of kind;
    an unrecognised site-packages directory with no stamp now gets the fourth,
    honest ("cannot-tell") answer instead of a guessed one."""
    identity = buildinfo.build_identity(tmp_path)
    assert identity.kind == buildinfo.INSTALL_KIND_UNKNOWN
    line = buildinfo.version_line(identity)
    assert "build unknown" in line
    assert "uv tool install kyosei" in line
    assert "uv tool install --force ." in line
    assert "Install Command Line Tools" not in line


# --------------------------------------------------------------- `kyo version`'s CLI


def test_kyo_version_states_which_kind_is_running() -> None:
    """c1: `kyo version` says which kind of install is running, in prose."""
    result = invoke(["version"])
    assert result.code == 0, result
    assert any(line.strip().startswith("installed as:") for line in result.out.splitlines()), (
        result.out
    )
    kind = buildinfo.install_kind(buildinfo.package_dir())
    assert buildinfo.INSTALL_KIND_LABELS[kind] in result.out


def test_kyo_version_json_carries_the_kind() -> None:
    payload = json.loads(invoke(["version", "--json"]).out)
    assert payload["kind"] == buildinfo.install_kind(buildinfo.package_dir())
    assert payload["kind"] in buildinfo.INSTALL_KIND_LABELS


def test_kyo_version_s_upgrade_line_names_the_route_for_this_kind() -> None:
    result = invoke(["version"])
    identity = buildinfo.build_identity()
    if not identity.known:
        return  # the "to move to a newer build" line only prints when a build is known
    route_line = next(
        line for line in result.out.splitlines() if "to move to a newer build" in line
    )
    assert buildinfo.upgrade_route(identity.kind) in route_line


def test_a_working_tree_s_upgrade_line_says_pull_not_uv_tool_install() -> None:
    """This checkout's own copy IS a working tree (asserted above), so `kyo version`
    run from it must not tell a working-tree reader to run `uv tool install --force`
    -- KYO-317's own measured defect, reproduced and then fixed."""
    identity = buildinfo.build_identity()
    assert identity.kind == buildinfo.INSTALL_KIND_WORKING_TREE
    result = invoke(["version"])
    route_line = next(
        line for line in result.out.splitlines() if "to move to a newer build" in line
    )
    assert "uv tool install --force" not in route_line, route_line
    assert "nothing to reinstall" in route_line, route_line


# ----------------------------------------------------------------- a fresh wheel too


def test_a_real_wheel_s_package_lives_under_site_packages_not_a_working_tree(
    tmp_path: Path,
) -> None:
    """A built and installed wheel is neither a working tree nor under either marker
    -- structurally the same "cannot tell" case a plain `pip install` is, proven
    against a real build rather than a hand-made directory."""
    venv = tmp_path / "venv"
    # `sys.executable` and not a bare `python3`: this suite always runs through the
    # project's own uv-provisioned 3.12 venv (AGENTS.md), and the host `python3` can
    # be older -- `pyproject.toml` requires >= 3.12.
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, cwd=REPO_ROOT)
    pip = venv / "bin" / "pip"
    subprocess.run(
        [str(pip), "install", "--quiet", "--no-deps", str(REPO_ROOT)],
        check=True,
        cwd=REPO_ROOT,
    )
    found = list(venv.glob("lib/python3.*/site-packages/kyosei"))
    assert found, "the wheel did not install kyosei where a venv puts site-packages"
    package_dir = found[0]
    assert buildinfo.install_kind(package_dir) == buildinfo.INSTALL_KIND_UNKNOWN


# ------------------------------------ KYO-450.c6: the two remedies KYO-446.d1 left


@pytest.mark.parametrize("release", [True, False])
def test_changelog_load_s_route_carries_this_build_s_release(monkeypatch, release) -> None:
    """`changelog.load`'s "no packaged changelog" sentence used to pass the kind alone,
    which reads as unreleased: a released uv tool was told to re-snapshot. It now passes
    the build's own release flag -- and never names the desktop app's re-pack."""
    from kyosei import changelog

    real = buildinfo.build_identity()
    monkeypatch.setattr(buildinfo, "source_tree_root", lambda *_a, **_k: None)
    monkeypatch.setattr(changelog, "read_packaged_data", lambda *_a, **_k: None)
    monkeypatch.setattr(changelog, "_nearby_source_checkout", lambda *_a, **_k: None)
    monkeypatch.setattr(
        buildinfo, "install_kind", lambda *_a, **_k: buildinfo.INSTALL_KIND_UV_TOOL
    )
    monkeypatch.setattr(
        buildinfo,
        "build_identity",
        lambda *_a, **_k: buildinfo.BuildIdentity(**{**real.__dict__, "release": release}),
    )
    said = changelog.load().unavailable or ""
    # The unreleased route names `uv tool upgrade` too, as what NOT to reach for, so the
    # release case is told apart by the re-snapshot being absent.
    assert buildinfo.UV_TOOL_UPGRADE_COMMAND in said, said
    assert (buildinfo.RESNAPSHOT_TARGET in said) is (not release), said
    assert "npm run pack" not in said and "re-pack" not in said


@pytest.mark.parametrize("release", [True, False])
def test_the_installed_build_lag_notice_names_this_build_s_route(
    monkeypatch, tmp_path: Path, release
) -> None:
    """KYO-309's notice said "re-pack and reinstall the tool" whatever was running. It
    now names :func:`buildinfo.upgrade_route` for the running build, release flag and
    all: `uv tool upgrade kyosei` for a release, the re-snapshot for a snapshot."""
    from kyosei import install_skill

    real = buildinfo.build_identity()
    monkeypatch.setattr(
        buildinfo, "install_kind", lambda *_a, **_k: buildinfo.INSTALL_KIND_UV_TOOL
    )
    monkeypatch.setattr(
        buildinfo,
        "build_identity",
        lambda *_a, **_k: buildinfo.BuildIdentity(**{**real.__dict__, "release": release}),
    )
    # A checkout whose source for the skill differs from what this build packaged.
    source = tmp_path / install_skill.SKILL_ASSET.source_path
    source.parent.mkdir(parents=True)
    source.write_text(install_skill.packaged_skill_text() + "\n<!-- newer -->\n")
    lag = [line for line in install_skill.installed_build_lag(tmp_path) if "skill" in line]
    assert lag, "the fixture did not produce a lag finding"
    assert buildinfo.UV_TOOL_UPGRADE_COMMAND in lag[0], lag[0]
    assert (buildinfo.RESNAPSHOT_TARGET in lag[0]) is (not release), lag[0]
    assert "re-pack" not in lag[0]
