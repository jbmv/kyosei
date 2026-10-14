"""KYO-561: onboarding's [E4] answer -- whether the maintainer reads the orchestrator
through the desktop app -- is recorded under ``onboarding:`` in ``kyosei/config.yml``,
and a "no" (``E4: declined``) drops the ``kyosei://`` citation rule and its worked
example from `kyo brief`. From a consumer project's report on its onboarding.
"""

from __future__ import annotations

import re
from pathlib import Path

from kyosei.install_procedures import packaged_procedures_text
from kyosei.procedures import rule_bodies
from kyosei.scaffold import defaults_dir

from .conftest import new_record

EXAMPLE_LEAD = "cite a record to the maintainer like this:"


def _append_config(project: Path, text: str) -> None:
    path = project / "kyosei" / "config.yml"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _rules_block(out: str) -> str:
    return out.split("orchestrator-audience rules:")[1]


def _citation_titles(data: dict) -> list[str]:
    return [r["title"] for r in data["orchestrator_rules"] if "kyosei://" in r["title"]]


def _assert_links_kept(kyo, record: str) -> None:
    expected = f"[{record}](kyosei://TST/{record})"
    for flags in ((), ("--compact",)):
        section = _rules_block(kyo("brief", *flags).out)
        assert f"  {EXAMPLE_LEAD} {expected}\n" in section
        assert "Cite every record to the maintainer" in section
    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["link_example"] == expected
    assert len(_citation_titles(result.json)) == 1


def test_c3_with_no_e4_answer_the_citation_line_and_rule_are_unchanged(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    _assert_links_kept(kyo, record)


def test_c1_an_e4_declined_drops_the_example_and_the_rule(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _append_config(project, "onboarding:\n  E4: declined\n")
    for flags in ((), ("--compact",)):
        result = kyo("brief", *flags)
        assert result.code == 0, result
        assert EXAMPLE_LEAD not in result.out
        assert "kyosei://" not in result.out
        assert "Cite every record to the maintainer" not in result.out
        # the other orchestrator rules still print
        assert "orchestrator-audience rules:" in result.out
    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["link_example"] is None
    assert _citation_titles(result.json) == []
    assert result.json["orchestrator_rules"], "only the citation rule is withdrawn"
    # Deleting the answer restores both: the mechanism is the recorded answer.
    path = project / "kyosei" / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("onboarding:\n  E4: declined\n", ""),
        encoding="utf-8",
    )
    _assert_links_kept(kyo, record)


def test_c1_the_mapping_form_with_a_reason_declines_too(kyo, project: Path) -> None:
    new_record(kyo, criteria=["a"])
    _append_config(
        project, "onboarding:\n  E4:\n    status: declined\n    reason: reads in a terminal\n"
    )
    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["link_example"] is None
    assert _citation_titles(result.json) == []


def test_c3_deferred_or_another_decision_leaves_the_links(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _append_config(project, "onboarding:\n  E4: deferred\n  B2: declined\n")
    _assert_links_kept(kyo, record)


def test_a_malformed_onboarding_map_does_not_blind_the_brief(kyo, project: Path) -> None:
    """The brief is a session-start hook; `kyo onboard --check` names the fault."""
    record = new_record(kyo, criteria=["a"])
    _append_config(project, "onboarding:\n  E4: no\n")
    _assert_links_kept(kyo, record)


def test_c2_onboard_prompt_says_where_the_e4_answer_is_recorded() -> None:
    text = (defaults_dir() / "onboard.md").read_text(encoding="utf-8")
    match = re.search(r"^- \*\*\[E4\].*?(?=^- \*\*\[)", text, re.S | re.M)
    assert match, "onboard.md lost its [E4] item"
    item = " ".join(match.group(0).split())
    assert "`E4: declined`" in item
    assert "`onboarding:`" in item
    assert "`kyosei/config.yml`" in item


def test_c4_the_citation_rule_says_it_applies_only_where_e4_is_not_no() -> None:
    bodies = rule_bodies(packaged_procedures_text())
    (title,) = [t for t in bodies if "kyosei://" in t]
    body = " ".join(bodies[title].split())
    assert "applies only where onboarding's [E4] answer is not no" in body
    assert "`E4: declined`" in body
