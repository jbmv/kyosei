"""KYO-531: `kyo brief` prints each orchestrator-audience rule as one line, and a rule
whose text changed since the previous brief in this project in full, marked.

The previous brief's state is the brief's own event in the events ledger: each brief
records a digest of every orchestrator rule (``orchestrator_rule_digests``), and the
next one compares against the latest brief event that carries them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kyosei.brief import enforcement_kind
from kyosei.cmd_brief import RULE_DIGESTS_KEY, RULE_TEXTS_KEY, rule_change_summary
from kyosei.procedures import orchestrator_rules, rule_bodies, rule_sections

FULL_TEXT_LINE = '  `kyo rules "<title>"` prints one rule in full.'
NO_BASELINE = "no earlier brief recorded these rules' text"


def _procedures(project: Path) -> Path:
    return project / "kyosei" / "procedures.md"


def _block(out: str) -> list[str]:
    return out.split("orchestrator-audience rules:\n")[1].splitlines()


def _rules(project: Path):
    rules = orchestrator_rules(_procedures(project).read_text(encoding="utf-8"))
    assert rules, "the packaged ledger must carry orchestrator rules"
    return rules


def _append_to_rule(project: Path, title: str, sentence: str) -> None:
    path = _procedures(project)
    text = path.read_text(encoding="utf-8")
    section = rule_sections(text)[title]
    assert text.count(section) == 1, "the section must appear verbatim exactly once"
    path.write_text(text.replace(section, f"{section}\n\n{sentence}"), encoding="utf-8")


# ------------------------------------------------------------------------ c1


@pytest.mark.parametrize("compact", [False, True])
def test_each_rule_is_one_line_then_one_pointer_to_the_full_text(
    kyo, project: Path, compact: bool
) -> None:
    rules = _rules(project)
    assert kyo("brief").code == 0  # a baseline, so the second brief is a returning one
    args = ["brief", "--compact"] if compact else ["brief"]
    lines = _block(kyo(*args).out)
    assert lines[0].startswith("  cite a record to the maintainer like this: ")
    assert lines[1 : 1 + len(rules)] == [
        f"  - {rule.title} ({enforcement_kind(rule.enforced_by)})" for rule in rules
    ]
    assert lines[1 + len(rules)] == FULL_TEXT_LINE
    assert lines[2 + len(rules) :] == []
    assert sum(1 for line in lines if "kyo rules" in line) == 1
    for rule in rules:
        if rule.first_line:
            assert rule.first_line not in "\n".join(lines)


def test_the_first_brief_says_it_had_nothing_to_compare_against(kyo, project: Path) -> None:
    first = kyo("brief").out
    assert NO_BASELINE in first
    assert "CHANGED since" not in first
    assert NO_BASELINE not in kyo("brief").out


@pytest.mark.parametrize(
    ("enforced_by", "kind"),
    [
        ("aspirational", "aspirational"),
        ("aspirational in v1 — the reason", "aspirational"),
        ("aspirational, with one mechanical edge — nothing counts", "aspirational"),
        ("mechanism, partial — `kyo report`", "mechanism"),
        ("mechanism, opt-in — when declared", "mechanism"),
        ("`SubagentStop` hook, `kyo check-clean` — run it yourself", "mechanism"),
    ],
)
def test_enforcement_kind_is_one_word(enforced_by: str, kind: str) -> None:
    assert enforcement_kind(enforced_by) == kind


# ------------------------------------------------------------------------ c2


def test_a_rule_changed_since_the_previous_brief_is_marked_with_what_changed(
    kyo, project: Path
) -> None:
    """KYO-531.c2's marker, in KYO-548's form: the changed rule's title, marked, then
    one line saying what changed and where the full text is -- not the rule again."""
    rules = _rules(project)
    changed, unchanged = rules[0], rules[1:]
    first = kyo("brief", "--json").json
    _append_to_rule(project, changed.title, "A sentence KYO-531's test added.")

    result = kyo("brief")
    lines = _block(result.out)
    marker = (
        f"  - {changed.title} ({enforcement_kind(changed.enforced_by)}) -- CHANGED since "
        f"the previous brief ({first['brief_run_id']})"
    )
    assert marker in lines
    assert lines[lines.index(marker) + 1] == (
        "      what changed: added \"A sentence KYO-531's test added.\"; "
        f"full text: `kyo rules \"{changed.title}\"`"
    )
    assert "**enforced-by**" not in "\n".join(lines)
    for rule in unchanged:
        assert f"  - {rule.title} ({enforcement_kind(rule.enforced_by)})" in lines
    assert sum(1 for line in lines if "CHANGED since" in line) == 1

    # Shown once: the next brief compares against the one that showed it.
    again = kyo("brief").out
    assert "CHANGED since" not in again
    assert f"  - {changed.title} ({enforcement_kind(changed.enforced_by)})\n" in again


def test_compact_brief_marks_a_changed_rule_too(kyo, project: Path) -> None:
    """The SessionStart hook runs `--compact`; a change that only the full form showed
    would not reach the session that most needs it."""
    rule = _rules(project)[-1]
    assert kyo("brief", "--compact").code == 0
    _append_to_rule(project, rule.title, "Compact must show this.")
    out = kyo("brief", "--compact").out
    assert f"  - {rule.title} ({enforcement_kind(rule.enforced_by)}) -- CHANGED since" in out
    assert 'what changed: added "Compact must show this."' in out


def test_a_new_rule_is_marked_new(kyo, project: Path) -> None:
    assert kyo("brief").code == 0
    path = _procedures(project)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n### A rule KYO-531's test added\n\n- **audience**: orchestrator\n"
        "- **enforced-by**: aspirational — nothing checks it\n\nIt exists for one test.\n",
        encoding="utf-8",
    )
    out = kyo("brief").out
    assert "  - A rule KYO-531's test added (aspirational) -- NEW since the previous brief" in out
    assert "      It exists for one test." in out


def test_rewrapping_a_rule_is_not_a_change(kyo, project: Path) -> None:
    rule = _rules(project)[0]
    assert kyo("brief").code == 0
    path = _procedures(project)
    text = path.read_text(encoding="utf-8")
    section = rule_sections(text)[rule.title]
    last = [line for line in section.splitlines() if " " in line.strip()][-1]
    rewrapped = section.replace(last, last.replace(" ", "\n", 1))
    assert rewrapped != section
    path.write_text(text.replace(section, rewrapped), encoding="utf-8")
    assert "CHANGED since" not in kyo("brief").out


def test_the_baseline_survives_a_rebuild(kyo, project: Path) -> None:
    """The previous state is the brief's event in the git-canonical events ledger,
    so a database rebuilt from the export still knows what the last brief printed."""
    rule = _rules(project)[0]
    assert kyo("brief").code == 0
    assert kyo("rebuild", "--force").code == 0
    _append_to_rule(project, rule.title, "After a rebuild.")
    assert "CHANGED since" in kyo("brief").out


def test_removing_the_recorded_digests_removes_the_change_marker(kyo, project: Path) -> None:
    """The canary: the marker comes from the digests the previous brief's event
    recorded, and from nothing else. Strip them from the ledger and rebuild, and the
    same edit marks nothing."""
    rule = _rules(project)[0]
    assert kyo("brief").code == 0
    ledger_dir = project / "kyosei" / "export" / "_events"
    for ledger in ledger_dir.glob("*.jsonl"):
        text = ledger.read_text(encoding="utf-8")
        if RULE_DIGESTS_KEY in text:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            for row in rows:
                if isinstance(row.get("after"), dict):
                    row["after"].pop(RULE_DIGESTS_KEY, None)
            ledger.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
    assert kyo("rebuild", "--force").code == 0
    _append_to_rule(project, rule.title, "Nothing to compare against.")
    out = kyo("brief").out
    assert "CHANGED since" not in out
    assert NO_BASELINE in out


# ------------------------------------------------------------------------ c3


@pytest.mark.parametrize("compact", [False, True])
def test_json_carries_every_rules_full_text(kyo, project: Path, compact: bool) -> None:
    text = _procedures(project).read_text(encoding="utf-8")
    sections = rule_sections(text)
    args = ["brief", "--compact", "--json"] if compact else ["brief", "--json"]
    payload = kyo(*args).json
    rules = payload["orchestrator_rules"]
    assert [r["title"] for r in rules] == [r.title for r in orchestrator_rules(text)]
    for rule in rules:
        assert rule["text"] == sections[rule["title"]]
        assert rule["enforced_by"] in " ".join(rule["text"].split())
        assert rule["enforcement"] == enforcement_kind(rule["enforced_by"])
        assert rule["change"] is None
    assert payload["orchestrator_rules_compared_to"] is None
    assert kyo(*args).json["orchestrator_rules_compared_to"] == payload["brief_run_id"]


def test_a_brief_between_session_starts_does_not_use_up_the_marker(kyo, project: Path) -> None:
    """A hand-run brief, a `--json` call or a viewer's poll through `kyo serve` must not
    consume the CHANGED marker before the next session-start brief shows it."""
    rule = _rules(project)[0]
    assert kyo("brief", "--session-start", stdin="").code == 0
    _append_to_rule(project, rule.title, "Seen at the next session start.")
    assert kyo("brief", "--json").code == 0  # e.g. a viewer, mid-session
    assert kyo("brief").code == 0  # e.g. a hand run
    opening = kyo("brief", "--session-start", stdin="").out
    assert f"  - {rule.title} ({enforcement_kind(rule.enforced_by)}) -- CHANGED since" in opening
    assert 'what changed: added "Seen at the next session start."' in opening
    # The session that saw it does not see it again.
    assert "CHANGED since" not in kyo("brief").out


# ------------------------------------------------------------------ KYO-548


def _rename_in_rule_bodies(project: Path, old: str, new: str) -> None:
    """A vocabulary-only rewording: ``old`` -> ``new`` as a whole word on every line but
    a rule heading and an ``audience`` line, which would rename or re-route a rule."""
    path = _procedures(project)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(
        "".join(
            line
            if line.startswith("### ") or "**audience**" in line
            else re.sub(rf"\b{old}\b", new, line)
            for line in lines
        ),
        encoding="utf-8",
    )


def _strip_recorded_rule_texts(project: Path) -> None:
    """What a baseline written by a build older than KYO-548 looks like: digests, and
    no text behind them."""
    for ledger in (project / "kyosei" / "export" / "_events").glob("*.jsonl"):
        text = ledger.read_text(encoding="utf-8")
        if RULE_TEXTS_KEY in text:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            for row in rows:
                if isinstance(row.get("after"), dict):
                    row["after"].pop(RULE_TEXTS_KEY, None)
            ledger.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )


@pytest.mark.parametrize("compact", [False, True])
def test_a_renamed_only_rule_prints_its_substitutions_and_not_its_text(
    kyo, project: Path, compact: bool
) -> None:
    """KYO-548.c1, from a consumer report: an upstream rename rewords most rules at
    once; each prints its title, the substitution and how often, and the command for
    the full text -- never the rule itself."""
    before = _procedures(project).read_text(encoding="utf-8")
    first = kyo("brief", "--json").json
    _rename_in_rule_bodies(project, "orchestrator", "conductor")
    after = _procedures(project).read_text(encoding="utf-8")
    old_bodies, new_bodies = rule_bodies(before), rule_bodies(after)
    renamed = [r for r in _rules(project) if old_bodies[r.title] != new_bodies[r.title]]
    assert len(renamed) >= 2, "the fixture must rename words in several rules"

    args = ["brief", "--compact"] if compact else ["brief"]
    lines = _block(kyo(*args).out)
    for rule in renamed:
        times = new_bodies[rule.title].count("conductor")
        marker = (
            f"  - {rule.title} ({enforcement_kind(rule.enforced_by)}) -- CHANGED since "
            f"the previous brief ({first['brief_run_id']})"
        )
        summary = "orchestrator -> conductor" + (f" (x{times})" if times > 1 else "")
        assert marker in lines
        assert lines[lines.index(marker) + 1] == (
            f"      what changed: {summary}; full text: `kyo rules \"{rule.title}\"`"
        )
    block = "\n".join(lines)
    assert "**enforced-by**" not in block
    assert "in full:" not in block
    for rule in renamed:
        for line in rule_sections(after)[rule.title].splitlines():
            if len(line.strip()) > 40:
                assert line.strip() not in block, f"{rule.title} printed its text"


def test_a_changed_rules_summary_survives_a_rebuild(kyo, project: Path) -> None:
    """KYO-548.c2: the previous TEXT is in the git-canonical events ledger beside the
    digests, so a database rebuilt from the export can still say what changed."""
    rule = _rules(project)[0]
    assert kyo("brief").code == 0
    assert kyo("rebuild", "--force").code == 0
    _append_to_rule(project, rule.title, "Added after a rebuild.")
    out = kyo("brief").out
    assert 'what changed: added "Added after a rebuild."' in out
    assert "      Added after a rebuild." not in out


def test_a_baseline_without_text_marks_changed_without_the_text(kyo, project: Path) -> None:
    """KYO-548.c2, and the canary for the recorded text: a baseline from an older build
    carries digests only, so the rule is marked CHANGED and says the change is not
    recorded -- and its full text is not printed either."""
    rule = _rules(project)[0]
    first = kyo("brief", "--json").json
    _strip_recorded_rule_texts(project)
    assert kyo("rebuild", "--force").code == 0
    _append_to_rule(project, rule.title, "No text to compare against.")
    lines = _block(kyo("brief").out)
    marker = (
        f"  - {rule.title} ({enforcement_kind(rule.enforced_by)}) -- CHANGED since "
        f"the previous brief ({first['brief_run_id']})"
    )
    assert marker in lines
    assert lines[lines.index(marker) + 1] == (
        "      what changed is not recorded (the previous brief kept only a digest of "
        f"this rule); full text: `kyo rules \"{rule.title}\"`"
    )
    block = "\n".join(lines)
    assert "No text to compare against." not in block
    assert "**enforced-by**" not in block


def test_rule_text_is_recorded_once_per_wording_not_once_per_brief(
    kyo, project: Path
) -> None:
    """KYO-548.c2's cost: the first brief records every rule's text, a brief whose
    rules did not change records none, and a reworded rule records only itself."""
    rules = _rules(project)
    first = kyo("brief", "--json").json
    second = kyo("brief", "--json").json
    _append_to_rule(project, rules[0].title, "One more sentence.")
    third = kyo("brief", "--json").json

    def recorded(brief_run_id: str) -> dict | None:
        for ledger in (project / "kyosei" / "export" / "_events").glob("*.jsonl"):
            for line in ledger.read_text(encoding="utf-8").splitlines():
                row = json.loads(line) if line.strip() else {}
                after = row.get("after")
                if isinstance(after, dict) and after.get("brief_run_id") == brief_run_id:
                    return after.get(RULE_TEXTS_KEY)
        raise AssertionError(f"no event for {brief_run_id}")

    assert sorted(recorded(first["brief_run_id"])) == sorted(
        r["digest"] for r in first["orchestrator_rules"]
    )
    assert recorded(second["brief_run_id"]) is None
    changed = [r for r in third["orchestrator_rules"] if r["change"] == "changed"]
    assert [r["title"] for r in changed] == [rules[0].title]
    assert list(recorded(third["brief_run_id"])) == [changed[0]["digest"]]
    assert changed[0]["change_summary"] == 'added "One more sentence."'


@pytest.mark.parametrize(
    ("previous", "current", "summary"),
    [
        (
            "the controller runs; a controller's brief names the controller",
            "the orchestrator runs; a orchestrator's brief names the orchestrator",
            "controller -> orchestrator (x3)",
        ),
        (
            "a controller reads a brief",
            "an orchestrator reads a brief",
            "a -> an, controller -> orchestrator",
        ),
        (
            "a b c d e f",
            "z y x w v u",
            "a -> z, b -> y, c -> x, d -> w, and 2 more substitution(s)",
        ),
        ("Run it once.", "Run it once. Then stop.", 'added "Then stop."'),
        ("Run it once. Then stop.", "Run it once.", 'removed "Then stop."'),
        (
            "Run the suite once, then merge.",
            "Run the whole suite twice, then merge and push.",
            'added "whole", and 2 more edit(s)',
        ),
        ("one\ntwo", "one two", "wording changed (whitespace or layout only)"),
    ],
)
def test_rule_change_summary(previous: str, current: str, summary: str) -> None:
    assert rule_change_summary(previous, current) == summary


def test_rule_change_summary_trims_a_long_edit() -> None:
    added = "word " * 40
    out = rule_change_summary("Start.", f"Start. {added}")
    assert out.startswith('added "word word') and out.endswith('..."')
    assert len(out) < 80
