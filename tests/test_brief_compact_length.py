"""KYO-419: the session-start brief (`kyo brief --compact`) is shorter.

The brief ran to ~146 wrapped lines at every session start, 47% of them controller
rules repeated in part and six empty sections each spending a heading, a "(none)" and a
blank line on saying nothing. The compact form now names the empty sections on one
line, prints each controller rule as its title, and drops the unobserved section's
parenthetical. The full form is unchanged, and so is everything a session acts on.
"""

from __future__ import annotations

import re
from pathlib import Path

from kyosei.procedures import orchestrator_rules
from kyosei.render import (
    COMPACT_EMPTY_SECTIONS_LEAD,
    UNGATED_UNOBSERVED_SENTENCE,
    brief_text,
)

from .conftest import invoke

#: The six count-sections c1 names, as the collapsed line spells them.
EMPTY_SECTION_NAMES = (
    "scope unknown",
    "finished but not moved",
    "built, awaiting verification",
    "unobserved, waiting on a person, ungated",
    "running dispatches",
    "terminated-no-report dispatches",
)

#: The full-form headings of the same six, each printed with "(0):" when empty.
FULL_HEADINGS = (
    "scope unknown, NOT dispatchable now (0):",
    "finished but not moved (0):",
    "built, awaiting verification (0):",
    "unobserved, waiting on a person, ungated (0):",
    "running dispatches (0):",
    "terminated-no-report dispatches (0):",
)

GATE_DO = "run the migration on staging, then read the row counts back"
HANDOFF_TEXT = "the release branch is cut; nothing merges to it without a second read"

#: KYO-419.c8: the pre-change `--compact` rendering of :func:`build_fixture_payload`'s
#: store (with `build` and `build_change` removed, see :func:`_measured`), measured on
#: the renderer before this change: 63 lines, 4,850 bytes. Pinned rather
#: than recomputed because the old renderer no longer exists to call.
PRE_CHANGE_COMPACT_BYTES = 4850


def _run(root: Path, *args: str, stdin: str | None = None):
    result = invoke(["--root", str(root), *args], stdin=stdin)
    assert result.code == 0, result
    return result


def build_fixture_payload(tmp: Path) -> dict:
    """A store with the packaged controller rules, all six count-sections empty, one
    dispatchable record, one gated record and a handoff note; returns the payload of
    a session-start `kyo brief --compact --json` on it."""
    root = tmp / "proj"
    _run(tmp, "init", str(root), "--in-repo", "--prefix", "TST")
    kinds = root / "kyosei" / "kinds.yml"
    import yaml

    raw = yaml.safe_load(kinds.read_text(encoding="utf-8"))
    entry = raw["kinds"]["task"]
    entry["accepts_from"] = [*(entry.get("accepts_from") or []), "intake"]
    kinds.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    def new(title: str) -> str:
        made = _run(
            root, "new", "task", "--title", title, "--criteria-file", "-", "--json",
            stdin="it works\n",
        )
        return made.json["record"]["id"]

    new("A dispatchable task")
    gated = new("A task waiting on a person")
    _run(root, "gate", gated, "awaiting:human-run", "--do", GATE_DO)
    _run(root, "brief", "--session-start")
    note = tmp / "handoff.md"
    note.write_text(HANDOFF_TEXT, encoding="utf-8")
    _run(root, "note", "--shift", "--file", str(note))
    return _run(root, "brief", "--compact", "--session-start", "--json").json


def _measured(data: dict) -> dict:
    """The payload without the two build fields, whose text depends on how the
    running `kyo` was installed rather than on the store — so the byte count is the
    store's, on any machine. KYO-586's ``prior_art_unchosen`` is left out for the same
    reason: it prints only where the running `kyo` can import recx_cli."""
    return {
        k: v
        for k, v in data.items()
        if k not in ("build", "build_change", "prior_art_unchosen")
    }


def _collapsed_line(out: str) -> str:
    lines = [line for line in out.splitlines() if line.startswith(COMPACT_EMPTY_SECTIONS_LEAD)]
    assert len(lines) == 1, out
    return lines[0]


# ------------------------------------------------------------------ c1 / c2


def test_every_empty_section_is_named_on_one_line_under_compact(tmp_path: Path) -> None:
    """KYO-419.c1: absence stays printed (KYO-48), on one line, by name."""
    out = brief_text(build_fixture_payload(tmp_path), compact=True)
    collapsed = _collapsed_line(out)
    for name in EMPTY_SECTION_NAMES:
        assert name in collapsed, name


def test_no_empty_section_keeps_a_heading_or_none_line_under_compact(tmp_path: Path) -> None:
    """KYO-419.c1's second half: fails if the collapse is removed and each empty
    section goes back to its own heading, "(none)" and blank separator."""
    out = brief_text(build_fixture_payload(tmp_path), compact=True)
    for heading in FULL_HEADINGS:
        assert heading not in out, heading
    # The six are gone; the "(none)" lines that remain belong to sections c1 does not
    # name (and there is one each of those at most in this fixture).
    assert out.count("  (none)") <= 1, out


def test_an_occupied_section_keeps_its_own_heading_and_leaves_the_line(tmp_path: Path) -> None:
    """KYO-419.c2: one running dispatch, the others empty."""
    data = build_fixture_payload(tmp_path)
    data["running_dispatches"] = [
        {"id": "TST-1.d1", "record_id": "TST-1", "created_at": "2026-09-26T00:00:00.000Z"}
    ]
    out = brief_text(data, compact=True)
    assert "running dispatches (1):\n  TST-1.d1  TST-1  since 2026-09-26T00:00:00.000Z" in out
    collapsed = _collapsed_line(out)
    assert "running dispatches" not in collapsed
    for name in EMPTY_SECTION_NAMES:
        if name != "running dispatches":
            assert name in collapsed, name


def test_answered_terminated_dispatches_are_counted_on_the_collapsed_line(
    tmp_path: Path,
) -> None:
    """The withheld-as-answered count (KYO-314.c1) does not vanish with the heading."""
    data = build_fixture_payload(tmp_path)
    data["terminated_no_report_answered_count"] = 5
    collapsed = _collapsed_line(brief_text(data, compact=True))
    assert "terminated-no-report dispatches (5 answered, not shown)" in collapsed


# ------------------------------------------------------------------------ c3


def test_the_not_a_clean_bill_caveat_survives_compact(tmp_path: Path) -> None:
    """KYO-419.c3."""
    data = build_fixture_payload(tmp_path)
    data["unresolved_flags"] = []
    data["staleness"] = {"coverage": "unanswered", "detail": "no recorded base"}
    out = brief_text(data, compact=True)
    assert "^ not a clean bill: staleness was not answered" in out


# ------------------------------------------------------------------ c4 / c5


def test_compact_orchestrator_rules_are_titles_only(tmp_path: Path) -> None:
    """KYO-419.c4, as KYO-531.c1 settled it: one line per rule, the title and its
    enforced-by kind in a word -- never the enforced-by argument or first sentence."""
    data = build_fixture_payload(tmp_path)
    rules = data["orchestrator_rules"]
    assert rules, "the packaged ledger must carry controller rules"
    out = brief_text(data, compact=True)
    section = out.split("orchestrator-audience rules:\n")[1]
    lines = section.splitlines()
    assert lines[0] == f"  cite a record to the maintainer like this: {data['link_example']}"
    assert "kyosei://TST/" in lines[0]
    assert lines[1 : 1 + len(rules)] == [
        f"  - {rule['title']} ({rule['enforcement']})" for rule in rules
    ]
    assert lines[1 + len(rules)] == '  `kyo rules "<title>"` prints one rule in full.'
    for rule in rules:
        if rule["first_line"]:
            assert rule["first_line"] not in out, rule["title"]
        assert "enforced-by" not in section


def test_full_brief_keeps_rule_text_and_every_empty_heading(tmp_path: Path) -> None:
    """KYO-419.c5: the full form is not shortened by accident -- except the rules,
    which KYO-531.c1 cut to one line each in both forms (the full text is in --json
    and `kyo rules`)."""
    root_payload = build_fixture_payload(tmp_path)
    out = brief_text(root_payload, compact=False)
    text = (tmp_path / "proj" / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    for rule in orchestrator_rules(text):
        assert f"  - {rule.title} (" in out
        assert f"(enforced-by: {rule.enforced_by})" not in out
    lines = out.splitlines()
    for heading in FULL_HEADINGS:
        at = next(i for i, line in enumerate(lines) if line == heading)
        end = lines.index("", at)
        assert "  (none)" in lines[at + 1 : end], heading
    assert not any(line.startswith(COMPACT_EMPTY_SECTIONS_LEAD) for line in lines)


# ------------------------------------------------------------------------ c6


def test_the_unobserved_parenthetical_is_compact_only_dropped(tmp_path: Path) -> None:
    """KYO-419.c6: gone under --compact; still in the full brief and in `kyo next`."""
    data = build_fixture_payload(tmp_path)
    assert UNGATED_UNOBSERVED_SENTENCE not in brief_text(data, compact=True)
    assert UNGATED_UNOBSERVED_SENTENCE in brief_text(data, compact=False)
    root = tmp_path / "proj"
    assert UNGATED_UNOBSERVED_SENTENCE in _run(root, "next").out


def test_the_unobserved_parenthetical_stays_when_occupied(tmp_path: Path) -> None:
    """KYO-419.c6 as restated: only an EMPTY section loses it; with an item waiting the
    remedy is the point of the section (KYO-267)."""
    data = build_fixture_payload(tmp_path)
    data["ungated_unobserved"] = [
        {
            "id": "TST-9", "kind": "task", "status": "open", "title": "Look at it",
            "criteria": [], "remedy": "gate it",
        }
    ]
    out = brief_text(data, compact=True)
    assert "unobserved, waiting on a person, ungated (1):" in out
    assert "TST-9" in out
    assert UNGATED_UNOBSERVED_SENTENCE in out


# ------------------------------------------------------------------------ c7


def test_what_a_session_acts_on_still_prints_in_full_under_compact(tmp_path: Path) -> None:
    """KYO-419.c7: BUILD notice, handoff body, asset drift, dispatchable and gated rows."""
    data = build_fixture_payload(tmp_path)
    data["build_change"] = {
        "state": "cannot-tell",
        "previous_brief_id": "TST-B1",
        "previous_brief_at": "2026-09-25T00:00:00.000Z",
        "reason": "the previous brief recorded no build",
        "current": {},
    }
    data["asset_drift"] = [{"asset": "skill", "detail": "stale; run `kyo install-skill`"}]
    out = brief_text(data, compact=True)
    assert out.startswith("BUILD: cannot tell whether the `kyo` under this project changed")
    assert HANDOFF_TEXT in out
    assert "  skill: stale; run `kyo install-skill`" in out
    for item in data["ready"]:
        assert item["id"] in out and item["title"] in out
    assert data["ready"], "the fixture must carry a dispatchable record"
    # KYO-496 put the gate's age (`open <n>d`) between its type and `do:`; the payload
    # still prints in full, which is what this asserts.
    assert re.search(rf"awaiting:human-run  open \d+d  do: {re.escape(GATE_DO)}", out)


# ------------------------------------------------------------------------ c8


def test_compact_is_at_most_half_its_pre_change_size(tmp_path: Path) -> None:
    """KYO-419.c8: the ratio, pinned against the pre-change measurement."""
    out = brief_text(_measured(build_fixture_payload(tmp_path)), compact=True)
    size = len(out.encode("utf-8"))
    assert size * 2 <= PRE_CHANGE_COMPACT_BYTES, (size, PRE_CHANGE_COMPACT_BYTES)
