"""KYO-515: a rule the project never edited moves with the packaged wording.

`kyo install-procedures` never rewrites a rule already present (KYO-212.c3), because a
project may have reworded it on purpose. But a rule whose text is exactly a wording an
earlier build of this package shipped was written by nobody in the project, and leaving
it on that wording — with `kyo doctor` calling it a deliberate difference forever — is
the defect three consumer projects showed. The build carries a table of
every superseded packaged wording (:data:`kyosei.procedures.SUPERSEDED_RULE_WORDINGS`);
this file proves the rerun uses it, that a reworded rule still survives, that doctor
stops reporting the refreshed rule, and that the table cannot fall behind the packaged
default's own git history.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import procedures
from kyosei.install_procedures import install_procedures, packaged_procedures_text
from kyosei.procedures import (
    PACKAGED_LEDGER_SOURCE_PATH,
    SUPERSEDED_RULE_WORDINGS,
    is_superseded_wording,
    refresh_superseded_rules,
    rule_bodies,
    wording_digest,
)

from .test_procedures import (
    POST_KYO93_WORDING,
    PRE_KYO93_WORDING,
    PROSE_RULE_HEADING,
    _with_the_prose_rule_reverted,
    _with_the_prose_rule_reworded,
)

REPO = Path(__file__).resolve().parents[1]


def _ledger(store_root: Path) -> Path:
    return store_root / "kyosei" / "procedures.md"


# ----------------------------------------------------------------- c1: the rerun


def test_the_fixture_wording_is_one_an_earlier_build_actually_shipped() -> None:
    """The premise every test below rests on: the pre-KYO-93 prose rule is the exact
    text the three consumer projects carried, and it is in the table."""
    body = rule_bodies(_with_the_prose_rule_reverted(packaged_procedures_text()))[
        PROSE_RULE_HEADING
    ]
    assert is_superseded_wording(PROSE_RULE_HEADING, body)
    reworded = _with_the_prose_rule_reworded(packaged_procedures_text())
    edited = rule_bodies(reworded)[PROSE_RULE_HEADING]
    assert not is_superseded_wording(PROSE_RULE_HEADING, edited)


def test_a_rerun_moves_an_unedited_rule_to_the_packaged_wording(kyo, project: Path) -> None:
    ledger = _ledger(project)
    stale = _with_the_prose_rule_reverted(ledger.read_text(encoding="utf-8"))
    ledger.write_text(stale, encoding="utf-8")

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    (refreshed,) = result.json["refreshed"]
    assert refreshed["title"] == PROSE_RULE_HEADING
    assert PRE_KYO93_WORDING in refreshed["before"]
    assert POST_KYO93_WORDING in refreshed["after"]
    assert result.json["differing"] == []
    assert result.json["changed"] is True

    upgraded = ledger.read_text(encoding="utf-8")
    assert rule_bodies(upgraded) == rule_bodies(packaged_procedures_text())
    assert PRE_KYO93_WORDING not in upgraded


def test_print_names_the_refresh_and_writes_nothing(kyo, project: Path) -> None:
    ledger = _ledger(project)
    stale = _with_the_prose_rule_reverted(ledger.read_text(encoding="utf-8"))
    ledger.write_text(stale, encoding="utf-8")

    preview = kyo("install-procedures", "--print")
    assert preview.code == 0, preview
    assert "would update" in preview.out and PROSE_RULE_HEADING in preview.out
    assert ledger.read_text(encoding="utf-8") == stale, "--print wrote to the file"


def test_a_rerun_leaves_a_reworded_rule_exactly_as_the_project_wrote_it(kyo, project: Path) -> None:
    ledger = _ledger(project)
    edited = _with_the_prose_rule_reworded(ledger.read_text(encoding="utf-8"))
    ledger.write_text(edited, encoding="utf-8")

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["refreshed"] == []
    assert result.json["differing"] == [PROSE_RULE_HEADING]
    assert ledger.read_text(encoding="utf-8") == edited, "a reworded rule was rewritten"


def test_only_the_unedited_rule_moves_when_both_kinds_are_present() -> None:
    """The pure function, with one inherited rule and one reworded rule side by side:
    exactly the first moves, and no other byte of the file does."""
    packaged = packaged_procedures_text()
    stale = _with_the_prose_rule_reverted(packaged)
    other = [t for t in rule_bodies(packaged) if t != PROSE_RULE_HEADING][0]
    sections = procedures.rule_sections(stale)
    first_line = sections[other].splitlines()[-1]
    store = stale.replace(first_line, first_line + " Our own addition.", 1)
    assert rule_bodies(store)[other] != rule_bodies(packaged)[other]

    merged, refreshed = refresh_superseded_rules(packaged, store)
    assert [title for title, _before, _after in refreshed] == [PROSE_RULE_HEADING]
    assert rule_bodies(merged)[PROSE_RULE_HEADING] == rule_bodies(packaged)[PROSE_RULE_HEADING]
    assert rule_bodies(merged)[other] == rule_bodies(store)[other]
    # Reverting the refreshed rule gives back the input exactly.
    assert _with_the_prose_rule_reverted(merged) == store


def test_the_table_is_what_decides_and_an_empty_one_refreshes_nothing() -> None:
    """The mechanism is the table and nothing else: with it emptied, the same inherited
    wording is left alone, as it was before KYO-515."""
    packaged = packaged_procedures_text()
    stale = _with_the_prose_rule_reverted(packaged)
    merged, refreshed = refresh_superseded_rules(packaged, stale, superseded={})
    assert (merged, refreshed) == (stale, [])


def test_kyoseis_own_checkout_is_never_refreshed(tmp_path: Path, monkeypatch) -> None:
    """In the repository the package is built from, the ledger is compared against the
    source file and the refusal of KYO-318 governs; the refresh must not run there even
    when the installed build matches the source."""
    store = tmp_path / "store"
    _ledger(store).parent.mkdir(parents=True)
    stale = _with_the_prose_rule_reverted(packaged_procedures_text())
    _ledger(store).write_text(stale, encoding="utf-8")
    code = tmp_path / "code"
    source = code / PACKAGED_LEDGER_SOURCE_PATH
    source.parent.mkdir(parents=True)
    source.write_text(packaged_procedures_text(), encoding="utf-8")

    result = install_procedures(store, repo_root=code)
    assert result.refreshed == []
    assert result.differing == [PROSE_RULE_HEADING]
    assert _ledger(store).read_text(encoding="utf-8") == stale


# ------------------------------------------------------------ c2: doctor afterwards


def test_doctor_names_the_plain_rerun_for_an_unedited_rule_and_is_silent_after_it(
    kyo, project: Path
) -> None:
    ledger = _ledger(project)
    ledger.write_text(
        _with_the_prose_rule_reverted(ledger.read_text(encoding="utf-8")), encoding="utf-8"
    )

    before = kyo("doctor", "--json")
    assert before.code == 0, before
    (notice,) = before.json["notices"]
    assert notice.startswith("[procedures_rules_differ] ")
    assert PROSE_RULE_HEADING in notice
    assert "earlier build" in notice and "`kyo install-procedures`" in notice
    assert "--adopt" not in notice, "an inherited wording needs no deliberate adopt"

    assert kyo("install-procedures").code == 0
    after = kyo("doctor", "--json")
    assert after.code == 0, after
    assert after.json["notices"] == [], "the refreshed rule is still reported"


def test_doctor_still_reports_a_reworded_rule_after_the_rerun(kyo, project: Path) -> None:
    ledger = _ledger(project)
    reworded = _with_the_prose_rule_reworded(ledger.read_text(encoding="utf-8"))
    ledger.write_text(reworded, encoding="utf-8")
    assert kyo("install-procedures").code == 0
    (notice,) = kyo("doctor", "--json").json["notices"]
    assert PROSE_RULE_HEADING in notice and "--adopt" in notice


# ------------------------------------------- the table cannot fall behind the history


def _historical_ledgers() -> list[str]:
    """Every committed version of the packaged default, renames followed."""
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout of Kyosei; no history to compare against")
    log = subprocess.run(
        [
            "git",
            "log",
            "--follow",
            "--name-only",
            "--format=commit %H",
            "--",
            PACKAGED_LEDGER_SOURCE_PATH,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    texts: list[str] = []
    sha = None
    for line in log.splitlines():
        if line.startswith("commit "):
            sha = line.split()[1]
        elif line.strip() and sha is not None:
            shown = subprocess.run(
                ["git", "show", f"{sha}:{line.strip()}"],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            if shown.returncode == 0:
                texts.append(shown.stdout)
    if not texts:
        pytest.skip("the packaged default has no git history in this checkout")
    return texts


def expected_superseded_wordings() -> dict[str, frozenset[str]]:
    """What :data:`SUPERSEDED_RULE_WORDINGS` must contain, from git history."""
    current = rule_bodies((REPO / PACKAGED_LEDGER_SOURCE_PATH).read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for text in _historical_ledgers():
        for title, body in rule_bodies(text).items():
            if current.get(title) == body:
                continue
            found.setdefault(title, set()).add(wording_digest(body))
    return {title: frozenset(digests) for title, digests in found.items()}


def render_table(table: dict[str, frozenset[str]]) -> str:
    """The literal to paste into ``procedures.py`` in place of the current table."""
    lines = ["SUPERSEDED_RULE_WORDINGS: dict[str, frozenset[str]] = {"]
    for title in sorted(table):
        lines += ["    (", f"        {json.dumps(title, ensure_ascii=False)}", "    ): frozenset("]
        lines.append("        {")
        lines += [f'            "{digest}",' for digest in sorted(table[title])]
        lines.append("        }")
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines)


def test_every_committed_packaged_wording_is_in_the_table() -> None:
    """Rewording a packaged rule and committing it moves the old wording into history;
    this fails until the table carries it, and prints the table to paste. A superset is
    enough (history rewritten or shallow loses nothing the table already knows)."""
    expected = expected_superseded_wordings()
    merged = {
        title: SUPERSEDED_RULE_WORDINGS.get(title, frozenset()) | digests
        for title, digests in {**SUPERSEDED_RULE_WORDINGS, **expected}.items()
    }
    missing = {
        title: digests - SUPERSEDED_RULE_WORDINGS.get(title, frozenset())
        for title, digests in expected.items()
        if digests - SUPERSEDED_RULE_WORDINGS.get(title, frozenset())
    }
    assert not missing, (
        "src/kyosei/procedures.py's SUPERSEDED_RULE_WORDINGS lacks wordings the packaged "
        f"default has shipped ({sorted(missing)}). Replace it with:\n\n" + render_table(merged)
    )


def test_no_current_packaged_wording_is_in_the_table() -> None:
    """A rule's CURRENT wording listed as superseded would be harmless to the merge
    (it only looks at rules that differ) but would mean the table was generated from
    the wrong file; catch that here."""
    current = rule_bodies(packaged_procedures_text())
    for title, body in current.items():
        assert not is_superseded_wording(title, body), title
