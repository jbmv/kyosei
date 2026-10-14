"""KYO-556.c5/c6: an existing store learns what an upgrade's kind policy asks of it.

KYO-554 gave the packaged ``decision`` ``raises_docs_obligations: true``. No command
merges kinds.yml, so a store scaffolded before it keeps a ``decision`` without the key,
reads the absence as false, and never raises a decision obligation. c5: `kyo doctor`
notices the missing key, prints the exact line to add, and one config key declines it.
c6: when the session-start brief says BUILD CHANGED, it prints every kind-registry
notice under it, so the upgrade is stated without anyone running `kyo doctor`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from kyosei import buildinfo, doctor_registry
from kyosei import config as config_module
from kyosei import doctor as doctor_module
from kyosei.buildinfo import BuildIdentity
from kyosei.doctor import CHECKS, NOTICE_CHECKS, TIER_NOTICE, tier_of
from kyosei.errors import EXIT_DOCTOR, KyoseiError
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

POLICY_LINE = "      raises_docs_obligations: true"


def _kinds(project: Path) -> Path:
    return project / "kyosei" / "kinds.yml"


def _drop_decision_policy_key(project: Path) -> None:
    """The pre-KYO-554 shape: `decision`'s policy without `raises_docs_obligations`."""
    lines = _kinds(project).read_text(encoding="utf-8").splitlines(keepends=True)
    start = lines.index("  decision:\n")
    for index in range(start, len(lines)):
        if lines[index].startswith(POLICY_LINE):
            del lines[index]
            # its continuation comment lines, indented past the key
            while lines[index].lstrip().startswith("#") and lines[index].startswith(" " * 30):
                del lines[index]
            break
    else:
        raise AssertionError("no raises_docs_obligations line under decision")
    _kinds(project).write_text("".join(lines), encoding="utf-8")


def _apply_printed_lines(project: Path, finding: str) -> int:
    """Add every line a finding names under the kind's `policy:`, parsed out of the
    finding itself, so what is proved is that the printed TEXT is the remedy."""
    edits = re.findall(r"under `(\w+):`'s `policy:` add `(\w+: [^`]+)`", finding)
    for kind, line in edits:
        lines = _kinds(project).read_text(encoding="utf-8").splitlines(keepends=True)
        start = lines.index(f"  {kind}:\n")
        policy = next(i for i in range(start, len(lines)) if lines[i] == "    policy:\n")
        lines.insert(policy + 1, f"      {line}\n")
        _kinds(project).write_text("".join(lines), encoding="utf-8")
    return len(edits)


def _set_config_key(project: Path, key: str, value: object) -> None:
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        raw.pop(key, None)
    else:
        raw[key] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _findings(kyo) -> list[str]:
    result = kyo("doctor", "--json")
    assert result.code in (0, EXIT_DOCTOR), result
    return result.json["checks"]["kind_policy_missing"]["failures"]


# ------------------------------------------------------------------ c5: the doctor notice


def test_kind_policy_missing_is_a_registered_notice_and_silent_on_a_fresh_store(kyo) -> None:
    assert CHECKS["kind_policy_missing"] is doctor_registry.check_kind_policy_missing
    assert "kind_policy_missing" in NOTICE_CHECKS
    assert tier_of("kind_policy_missing") == TIER_NOTICE
    assert _findings(kyo) == []


def test_a_pre_kyo554_decision_is_noticed_and_the_printed_line_clears_it(
    kyo, project: Path
) -> None:
    _drop_decision_policy_key(project)
    result = kyo("doctor", "--json")
    assert result.code == 0, result  # a notice, never a failure
    findings = result.json["checks"]["kind_policy_missing"]["failures"]
    assert len(findings) == 1, findings
    (finding,) = findings
    assert "'decision'" in finding and "'raises_docs_obligations'" in finding, finding
    assert "under `decision:`'s `policy:` add `raises_docs_obligations: true`" in finding
    assert "kind_policy_notice: false" in finding, finding
    assert len(finding) < doctor_module.MAX_FINDING_CHARS, len(finding)
    assert "[kind_policy_missing]" in kyo("doctor").out

    assert _apply_printed_lines(project, finding) == 1
    assert kyo("doctor", "--json").json["checks"]["kind_registry"]["failures"] == []
    assert _findings(kyo) == []


def test_a_key_the_project_states_as_false_is_its_answer_not_a_finding(
    kyo, project: Path
) -> None:
    text = _kinds(project).read_text(encoding="utf-8")
    _kinds(project).write_text(
        text.replace(POLICY_LINE, "      raises_docs_obligations: false", 1), encoding="utf-8"
    )
    assert _findings(kyo) == []


def test_a_project_can_decline_the_notice_with_one_config_line(kyo, project: Path) -> None:
    _drop_decision_policy_key(project)
    assert _findings(kyo) != []
    _set_config_key(project, "kind_policy_notice", False)
    assert config_module.load_config(project).kind_policy_notice is False
    assert _findings(kyo) == []
    assert kyo("doctor", "--json").json["checks"]["config_consumers"]["failures"] == []
    _set_config_key(project, "kind_policy_notice", True)
    assert _findings(kyo) != []


def test_the_config_key_is_registered_and_refuses_a_non_boolean(kyo, project: Path) -> None:
    assert CONFIG_CONSUMERS["kind_policy_notice"] == (
        "doctor_registry.check_kind_policy_missing",
    )
    assert "kind_policy_notice" in OPTIONAL_CONFIG_KEYS
    _set_config_key(project, "kind_policy_notice", "no")
    with pytest.raises(KyoseiError, match="kind_policy_notice: false"):
        config_module.load_config(project)
    refused = kyo("doctor")
    assert refused.code != 0
    # The remedy the refusal names, made: write `false`, and doctor runs again.
    _set_config_key(project, "kind_policy_notice", False)
    assert kyo("doctor", "--json").code == 0


# ---------------------------------------------------------- c6: under BUILD CHANGED


OLD = ("a" * 40, "2026-09-20T10:00:00.000Z")
NEW = ("b" * 40, "2026-09-25T02:34:00.000Z")


@pytest.fixture
def running(monkeypatch):
    def use(commit: str, commit_time: str) -> None:
        identity = BuildIdentity(
            version="0.1.0",
            commit=commit,
            commit_date=commit_time[:10],
            dirty=False,
            source=buildinfo.SOURCE_STAMP,
            kind=buildinfo.INSTALL_KIND_UV_TOOL,
            commit_time=commit_time,
        )
        monkeypatch.setattr(buildinfo, "build_identity", lambda *a, **k: identity)

    return use


def _notice(out: str) -> str:
    lines = out.splitlines()
    assert lines[0].startswith("BUILD CHANGED"), lines[:3]
    return "\n".join(lines[: lines.index("")])


def _narrow_task_grant(project: Path) -> None:
    text = _kinds(project).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    start = lines.index("  task:\n")
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("    accepts_from:"):
            lines[index] = "    accepts_from: [idea, bug, investigation, reminder]\n"
            break
    _kinds(project).write_text("".join(lines), encoding="utf-8")


def test_build_changed_prints_every_kind_registry_notice_under_it(
    kyo, project: Path, running
) -> None:
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    _drop_decision_policy_key(project)
    _narrow_task_grant(project)
    running(*NEW)
    result = kyo("brief", "--session-start", "--compact")
    assert result.code == 0, result
    notice = _notice(result.out)
    assert "add `raises_docs_obligations: true`" in notice, notice
    assert "under `task:` write `accepts_from:" in notice, notice
    payload = kyo("brief", "--session-start", "--json").json
    assert payload["build_change"]["state"] == "unchanged"
    assert payload["build_change"]["registry_notices"] == []


def test_build_changed_with_a_declined_notice_prints_only_what_is_left(
    kyo, project: Path, running
) -> None:
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    _drop_decision_policy_key(project)
    _set_config_key(project, "kind_policy_notice", False)
    running(*NEW)
    notice = _notice(kyo("brief", "--session-start", "--compact").out)
    assert "raises_docs_obligations" not in notice
    assert "kinds.yml asks something" not in notice


def test_an_unchanged_build_prints_no_registry_notice(kyo, project: Path, running) -> None:
    running(*NEW)
    assert kyo("brief", "--session-start").code == 0
    _drop_decision_policy_key(project)
    result = kyo("brief", "--session-start", "--compact")
    assert "raises_docs_obligations" not in result.out.split("\n\n")[0]


def test_removing_the_registry_lines_fails_this_test(
    kyo, project: Path, running, monkeypatch
) -> None:
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    _drop_decision_policy_key(project)
    running(*NEW)
    monkeypatch.setattr(doctor_registry, "kind_registry_notices", lambda store: [])
    notice = _notice(kyo("brief", "--session-start", "--compact").out)
    assert "raises_docs_obligations" not in notice
