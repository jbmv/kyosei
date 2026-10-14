"""KYO-524: landing 2 of the controller -> orchestrator rename (KYO-513).

The packaged ledger says `audience: orchestrator`, `## Orchestrator-audience rules`, and
orchestrator in its rule text and preamble. A project that never edited its ledger moves
to that text on a plain `kyo install-procedures` (KYO-515's table, and for the preamble
the table this record adds); a project that reworded a rule keeps its words; and a
project still under the old heading gets no second section (also held by
`tests/test_kyo519_orchestrator_rename.py`).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from kyosei import procedures
from kyosei.install_procedures import install_procedures, packaged_procedures_text
from kyosei.procedures import (
    PACKAGED_LEDGER_SOURCE_PATH,
    PREAMBLE_TITLE,
    SUPERSEDED_PREAMBLES,
    preamble,
    refresh_superseded_preamble,
    rule_bodies,
    wording_digest,
)

from .test_kyo_procedures_unedited import _historical_ledgers

REPO = Path(__file__).resolve().parents[1]

#: Spellings the packaged ledger may still carry because a command or key has them.
#: Empty today: no packaged rule quotes `kyo controller` or `controller_only_commands`.
QUOTED_SPELLINGS: tuple[str, ...] = ()

#: An orchestrator-audience rule whose text this landing changed.
REWORDED_RULE = "Run the full suite yourself, once, on the merged tree"


def _ledger(store_root: Path) -> Path:
    return store_root / "kyosei" / "procedures.md"


def _landing_1_ledger() -> str:
    """The packaged ledger as landing 1 (KYO-519) left it: the newest committed version
    that still says `audience: controller`."""
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout of Kyosei; no history to read landing 1 from")
    shas = subprocess.run(
        ["git", "log", "--format=%H", "--", PACKAGED_LEDGER_SOURCE_PATH],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for sha in shas:
        shown = subprocess.run(
            ["git", "show", f"{sha}:{PACKAGED_LEDGER_SOURCE_PATH}"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        if shown.returncode == 0 and "## Controller-audience rules" in shown.stdout:
            return shown.stdout
    pytest.skip("no committed packaged ledger under the old heading in this checkout")


# ----------------------------------------------------------- c1: the packaged ledger


def test_the_packaged_ledger_says_orchestrator() -> None:
    text = packaged_procedures_text()
    assert "## Orchestrator-audience rules" in text
    assert "## Controller-audience rules" not in text
    assert "`audience: orchestrator` rules" in preamble(text)
    assert procedures.orchestrator_rules(text)
    for rule in procedures.parse_procedures(text):
        assert "controller" not in procedures.audiences_of(rule), rule.title


def test_no_packaged_rule_or_preamble_says_controller_except_a_quoted_spelling() -> None:
    text = packaged_procedures_text()
    for spelling in QUOTED_SPELLINGS:
        text = text.replace(spelling, "")
    found = [m.group(0) for m in re.finditer(r"(?i)\w*controller\w*", text)]
    assert found == [], found


# --------------------------------------- c2: an unedited project moves on a plain run


def test_an_unedited_landing_1_ledger_moves_entirely_on_a_plain_rerun(kyo, project: Path) -> None:
    old = _landing_1_ledger()
    _ledger(project).write_text(old, encoding="utf-8")
    before = kyo("doctor", "--json")
    assert any(n.startswith("[procedures_rules_differ] ") for n in before.json["notices"])

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["differing"] == []
    titles = {r["title"] for r in result.json["refreshed"]}
    assert REWORDED_RULE in titles and PREAMBLE_TITLE in titles

    after = _ledger(project).read_text(encoding="utf-8")
    packaged = packaged_procedures_text()
    assert rule_bodies(after) == rule_bodies(packaged)
    assert preamble(after) == preamble(packaged)
    assert procedures.compare_ledgers(packaged, after) == []
    notices = kyo("doctor", "--json").json["notices"]
    assert not [n for n in notices if n.startswith("[procedures_rules_differ] ")], notices


def test_every_committed_packaged_preamble_is_in_the_table() -> None:
    """The preamble's counterpart of KYO-515's history check: rewording the preamble and
    committing it fails here until the table carries the old one."""
    current = preamble((REPO / PACKAGED_LEDGER_SOURCE_PATH).read_text(encoding="utf-8"))
    expected = {
        wording_digest(preamble(text))
        for text in _historical_ledgers()
        if preamble(text) != current
    }
    missing = sorted(expected - SUPERSEDED_PREAMBLES)
    assert not missing, (
        "src/kyosei/procedures.py's SUPERSEDED_PREAMBLES lacks preambles the packaged "
        "default has shipped; add:\n" + "\n".join(f'        "{d}",' for d in missing)
    )
    assert wording_digest(current) not in SUPERSEDED_PREAMBLES


def test_the_preamble_table_is_what_decides() -> None:
    packaged = packaged_procedures_text()
    old = _landing_1_ledger()
    merged, moved = refresh_superseded_preamble(packaged, old, superseded=frozenset())
    assert (merged, moved) == (old, None)
    merged, moved = refresh_superseded_preamble(packaged, old)
    assert moved is not None
    assert preamble(merged) == preamble(packaged)
    assert rule_bodies(merged) == rule_bodies(old), "the preamble refresh moved a rule"


def test_kyoseis_own_checkout_never_refreshes_the_preamble(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _ledger(store).parent.mkdir(parents=True)
    old = _landing_1_ledger()
    _ledger(store).write_text(old, encoding="utf-8")
    code = tmp_path / "code"
    source = code / PACKAGED_LEDGER_SOURCE_PATH
    source.parent.mkdir(parents=True)
    source.write_text(packaged_procedures_text(), encoding="utf-8")

    result = install_procedures(store, repo_root=code)
    assert result.refreshed == []
    assert _ledger(store).read_text(encoding="utf-8") == old


def test_adopt_preamble_is_reported_as_an_adopt_not_a_refresh(kyo, project: Path) -> None:
    _ledger(project).write_text(_landing_1_ledger(), encoding="utf-8")
    result = kyo("install-procedures", "--adopt-preamble", "--json")
    assert result.code == 0, result
    assert result.json["preamble_adopted"] is not None
    assert PREAMBLE_TITLE not in {r["title"] for r in result.json["refreshed"]}


# ------------------------------------------------- c3: a reworded project keeps its words


def test_a_reworded_rule_and_preamble_survive_the_rerun(kyo, project: Path) -> None:
    old = _landing_1_ledger()
    body = procedures.rule_sections(old)[REWORDED_RULE]
    last = body.splitlines()[-1]
    edited = old.replace(last, last + " Our own sentence.", 1)
    edited = edited.replace("standing-rules ledger", "standing-rules ledger, our way", 1)
    _ledger(project).write_text(edited, encoding="utf-8")

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["differing"] == [REWORDED_RULE]
    assert REWORDED_RULE not in {r["title"] for r in result.json["refreshed"]}
    assert PREAMBLE_TITLE not in {r["title"] for r in result.json["refreshed"]}

    after = _ledger(project).read_text(encoding="utf-8")
    assert rule_bodies(after)[REWORDED_RULE] == rule_bodies(edited)[REWORDED_RULE]
    assert preamble(after) == preamble(edited)
    (notice,) = [
        n for n in kyo("doctor", "--json").json["notices"]
        if n.startswith("[procedures_rules_differ] ")
    ]
    assert REWORDED_RULE in notice and "--adopt" in notice


def test_a_landing_1_project_with_none_of_the_section_gets_no_second_section(
    kyo, project: Path
) -> None:
    """No second section, on the real landing-1 ledger: the old heading kept, every
    orchestrator-audience rule gone; the rerun puts them under the old heading and
    appends no new one."""
    old = _landing_1_ledger()
    head, rest = old.split("## Controller-audience rules", 1)
    tail = rest[rest.find("\n## ") :]
    _ledger(project).write_text(
        head + "## Controller-audience rules\n\nOur own words.\n" + tail, encoding="utf-8"
    )
    assert kyo("install-procedures").code == 0
    after = _ledger(project).read_text(encoding="utf-8")
    assert after.count("-audience rules\n") == 3, after
    assert "## Orchestrator-audience rules" not in after
    section = after.split("## Controller-audience rules", 1)[1].split("\n## ", 1)[0]
    assert f"### {REWORDED_RULE}" in section
