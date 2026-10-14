"""KYO-518: `kyo brief`'s asset-drift block reads a rule on an earlier packaged wording
the way `kyo doctor` does (KYO-515): inherited, not reworded, remedy `kyo install-procedures`.
A rule the project genuinely reworded keeps its remedy-less line."""

from __future__ import annotations

from pathlib import Path

from kyosei.install_procedures import packaged_procedures_text

from .test_procedures import (
    PROSE_RULE_HEADING,
    _with_the_prose_rule_reverted,
    _with_the_prose_rule_reworded,
)


def _procedures_findings(kyo) -> list[dict]:
    result = kyo("brief", "--json")
    assert result.code == 0, result
    return [f for f in result.json["asset_drift"] if f["asset"] == "procedures"]


def _write(project: Path, rewrite) -> None:
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(rewrite(ledger.read_text(encoding="utf-8")), encoding="utf-8")


def test_brief_names_a_superseded_wording_as_inherited_with_the_remedy(kyo, project) -> None:
    _write(project, _with_the_prose_rule_reverted)
    (finding,) = _procedures_findings(kyo)
    assert finding["remedy"] == "kyo install-procedures"
    assert PROSE_RULE_HEADING in finding["detail"]
    assert "earlier build" in finding["detail"]
    assert "not reworded" in finding["detail"]
    assert "`kyo doctor` prints both" not in finding["detail"]
    text = kyo("brief").out
    assert "kyo install-procedures" in text and PROSE_RULE_HEADING in text


def test_the_remedy_the_brief_names_clears_the_finding(kyo, project) -> None:
    _write(project, _with_the_prose_rule_reverted)
    assert _procedures_findings(kyo)
    assert kyo("install-procedures").code == 0
    assert _procedures_findings(kyo) == []


def test_brief_still_shows_a_reworded_rule_as_reworded_with_no_remedy(kyo, project) -> None:
    _write(project, _with_the_prose_rule_reworded)
    (finding,) = _procedures_findings(kyo)
    assert finding["remedy"] is None
    assert "worded differently" in finding["detail"]
    assert PROSE_RULE_HEADING in finding["detail"]
    assert "earlier build" not in finding["detail"]
    assert packaged_procedures_text()  # the packaged default is what it differs from
