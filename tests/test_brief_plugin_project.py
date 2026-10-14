"""KYO-608: the session-start brief does not tell a project on the plugin to reinstall copies.

In a consumer project's first session after `kyo harness migrate`, the brief's "this build
vs this project's copies" block listed every packaged hook as missing from
``.claude/settings.json`` and named `kyo install-hooks`, which would have put the copies
back beside the plugin and run every hook twice. `kyo doctor` already gated its copy checks
on the plugin (KYO-596); the brief now asks the same function.
"""

from __future__ import annotations

import json
from pathlib import Path

#: A settings file that is not empty but carries no Kyosei hook -- a consumer project's shape
#: after `kyo harness migrate`: the project's own permission rules, nothing else.
PERMISSIONS_ONLY = {"permissions": {"allow": ["Bash(git status:*)"]}}


def _write_settings(project: Path) -> None:
    claude = project / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.json").write_text(json.dumps(PERMISSIONS_ONLY), encoding="utf-8")


def _enable_plugin(project: Path) -> None:
    (project / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"kyosei@kyosei": True}}), encoding="utf-8"
    )


def _copy_assets(findings: list[dict]) -> set[str]:
    return {f["asset"] for f in findings if f["asset"] in ("skill", "hooks")}


def test_a_project_on_the_copies_still_hears_about_missing_hooks(kyo, project: Path) -> None:
    """The premise: without the plugin, this settings file is behind the build, and the
    brief says so and names `kyo install-hooks` -- the behaviour KYO-608 must keep."""
    _write_settings(project)
    findings = kyo("brief", "--json").json["asset_drift"]
    assert "hooks" in _copy_assets(findings), findings
    assert any(f["remedy"] == "kyo install-hooks" for f in findings if f["asset"] == "hooks")


def test_a_project_on_the_plugin_hears_nothing_about_the_copies(kyo, project: Path) -> None:
    _write_settings(project)
    _enable_plugin(project)
    result = kyo("brief", "--json")
    assert _copy_assets(result.json["asset_drift"]) == set(), result.json["asset_drift"]
    text = kyo("brief").out
    # The bare commands, as the copies block names them; `kyo install-hooks --copies` in
    # the no-session-start hint is a different, deliberate remedy.
    assert "`kyo install-hooks`" not in text
    assert "`kyo install-skill`" not in text
