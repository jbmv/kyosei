"""KYO-565: `kyo rules add` writes one well-formed project rule into
``kyosei/procedures.md``, under ``## Project rules``, and refuses -- writing nothing --
anything the ledger's own parser would not read back as given."""

from __future__ import annotations

from pathlib import Path

import pytest

from kyosei.procedures import (
    KNOWN_AUDIENCES,
    PROJECT_RULES_SECTION,
    audiences_of,
    orchestrator_rules,
    parse_procedures,
    worker_rules,
)

TITLE = "Never push to the release branch by hand"
REASON = (
    "The release branch is cut by CI and a hand push skips the changelog. "
    "It happened twice in one week."
)


def _ledger(project: Path) -> Path:
    return project / "kyosei" / "procedures.md"


def _without_the_section(project: Path) -> str:
    """KYO-563 ships ``## Project rules`` in the packaged ledger, so a fresh project has
    it. A project initialised before that build does not: cut it back off to be one."""
    ledger = _ledger(project)
    text = ledger.read_text(encoding="utf-8")
    cut = text.index(f"\n---\n\n## {PROJECT_RULES_SECTION}")
    ledger.write_text(text[:cut].rstrip("\n") + "\n", encoding="utf-8")
    return ledger.read_text(encoding="utf-8")


def _reason_file(tmp_path: Path, text: str = REASON) -> Path:
    path = tmp_path / "reason.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _add(kyo, tmp_path: Path, *, title: str = TITLE, audience: str = "worker",
         enforced_by: str = "aspirational", reason: str = REASON):
    return kyo(
        "rules", "add",
        "--title", title,
        "--audience", audience,
        "--enforced-by", enforced_by,
        "--reason-file", str(_reason_file(tmp_path, reason)),
    )


def test_c1_the_added_rule_reads_back_with_exactly_the_values_given(kyo, project, tmp_path):
    before = parse_procedures(_ledger(project).read_text(encoding="utf-8"))
    result = _add(kyo, tmp_path, enforced_by="`make check` in CI")
    assert result.code == 0, result

    after = parse_procedures(_ledger(project).read_text(encoding="utf-8"))
    assert len(after) == len(before) + 1
    [rule] = [r for r in after if r.title == TITLE]
    assert rule.audience == "worker"
    assert rule.enforced_by == "`make check` in CI"
    assert rule.first_line == "The release branch is cut by CI and a hand push skips the changelog."
    # The rules that were there are untouched, in order.
    assert [r for r in after if r.title != TITLE] == before


def test_c1_the_reason_reads_from_stdin(kyo, project):
    result = kyo(
        "rules", "add", "--title", TITLE, "--audience", "human",
        "--enforced-by", "aspirational", "--reason-file", "-", stdin=REASON,
    )
    assert result.code == 0, result
    text = _ledger(project).read_text(encoding="utf-8")
    assert REASON in text
    assert [r.title for r in parse_procedures(text) if r.audience == "human"][-1] == TITLE


def test_c2_the_section_is_created_at_the_end_when_the_file_has_none(kyo, project, tmp_path):
    ledger = _ledger(project)
    original = _without_the_section(project)
    assert f"## {PROJECT_RULES_SECTION}" not in original

    assert _add(kyo, tmp_path).code == 0
    text = ledger.read_text(encoding="utf-8")
    assert text.startswith(original.rstrip("\n"))
    tail = text[len(original.rstrip("\n")):]
    assert tail.lstrip("\n").startswith(f"## {PROJECT_RULES_SECTION}\n\n### {TITLE}\n")
    assert text.count(f"## {PROJECT_RULES_SECTION}") == 1


def test_c2_a_second_rule_joins_the_existing_section(kyo, project, tmp_path):
    ledger = _ledger(project)
    # A section that is not last: the rule goes into it, not to the end of the file.
    ledger.write_text(
        _without_the_section(project).rstrip("\n")
        + f"\n\n## {PROJECT_RULES_SECTION}\n\n---\n\n## Something after\n\nProse.\n",
        encoding="utf-8",
    )
    assert _add(kyo, tmp_path).code == 0
    assert _add(kyo, tmp_path, title="Second project rule").code == 0
    text = ledger.read_text(encoding="utf-8")
    assert text.count(f"## {PROJECT_RULES_SECTION}") == 1
    section = text.index(f"## {PROJECT_RULES_SECTION}")
    after = text.index("## Something after")
    assert section < text.index(f"### {TITLE}") < text.index("### Second project rule") < after
    titles = [r.title for r in parse_procedures(text)]
    assert titles[-2:] == [TITLE, "Second project rule"]


def _refused_without_writing(kyo, project, tmp_path, **kwargs):
    ledger = _ledger(project)
    before = ledger.read_bytes()
    result = _add(kyo, tmp_path, **kwargs)
    assert result.code != 0, result
    assert ledger.read_bytes() == before
    return result.err + result.out


def test_c3_an_unknown_audience_is_refused_and_the_known_ones_named(kyo, project, tmp_path):
    message = _refused_without_writing(kyo, project, tmp_path, audience="worker,wrokers")
    assert "wrokers" in message
    for token in KNOWN_AUDIENCES:
        assert token in message


def test_c4_a_title_an_existing_rule_has_is_refused(kyo, project, tmp_path):
    existing = parse_procedures(_ledger(project).read_text(encoding="utf-8"))[0].title
    message = _refused_without_writing(kyo, project, tmp_path, title=existing)
    assert "already exists" in message
    # The rule this command itself added counts too, case aside.
    assert _add(kyo, tmp_path).code == 0
    _refused_without_writing(kyo, project, tmp_path, title=TITLE.upper())


@pytest.mark.parametrize(
    ("field", "value"),
    [("enforced_by", ""), ("enforced_by", "   "), ("reason", ""), ("reason", "  \n\n")],
)
def test_c5_an_empty_reason_or_enforced_by_is_refused(kyo, project, tmp_path, field, value):
    _refused_without_writing(kyo, project, tmp_path, **{field: value})


def test_c5_aspirational_is_accepted_verbatim(kyo, project, tmp_path):
    assert _add(kyo, tmp_path, enforced_by="aspirational").code == 0
    text = _ledger(project).read_text(encoding="utf-8")
    assert f"### {TITLE}\n\n- **audience**: worker\n- **enforced-by**: aspirational\n" in text
    [rule] = [r for r in parse_procedures(text) if r.title == TITLE]
    assert rule.enforced_by == "aspirational"


def test_a_reason_that_would_break_the_ledger_is_refused(kyo, project, tmp_path):
    _refused_without_writing(kyo, project, tmp_path, reason="Why.\n\n### Not a rule")
    _refused_without_writing(kyo, project, tmp_path, reason="- a bullet first")


def test_c6_a_two_audience_rule_reaches_both_selections(kyo, project, tmp_path):
    assert _add(kyo, tmp_path, audience="worker, orchestrator").code == 0
    text = _ledger(project).read_text(encoding="utf-8")
    assert TITLE in [r.title for r in worker_rules(text)]
    assert TITLE in [r.title for r in orchestrator_rules(text)]
    [rule] = [r for r in parse_procedures(text) if r.title == TITLE]
    assert audiences_of(rule) == {"worker", "orchestrator"}


def test_c7_install_procedures_leaves_the_added_rule_unchanged(kyo, project, tmp_path):
    assert _add(kyo, tmp_path).code == 0
    ledger = _ledger(project)
    text = ledger.read_text(encoding="utf-8")
    start = text.index(f"### {TITLE}")
    added = text[start:]

    result = kyo("install-procedures")
    assert result.code == 0, result
    after = ledger.read_text(encoding="utf-8")
    assert added in after
    [rule] = [r for r in parse_procedures(after) if r.title == TITLE]
    assert (rule.audience, rule.enforced_by) == ("worker", "aspirational")


def test_c8_the_two_read_forms_still_work_beside_add(kyo, project, tmp_path):
    listed = kyo("rules", "--json")
    assert listed.code == 0, listed
    titles = [r["title"] for r in listed.json["rules"]]
    assert titles

    one = kyo("rules", titles[0])
    assert one.code == 0, one
    assert titles[0] in one.out

    assert _add(kyo, tmp_path).code == 0
    listed = kyo("rules", "--json")
    assert [r["title"] for r in listed.json["rules"]] == [*titles, TITLE]
    printed = kyo("rules", TITLE)
    assert printed.code == 0 and REASON.split(".")[0] in printed.out


def test_a_rule_titled_add_stays_printable_by_the_spelling_the_help_names(
    kyo, project, tmp_path
):
    assert _add(kyo, tmp_path, title="Add a changelog line to every release").code == 0
    bare = kyo("rules", "add")
    assert bare.code != 0
    assert "kyo rules Add" in bare.err + bare.out
    printed = kyo("rules", "Add")
    assert printed.code == 0, printed
    assert "Add a changelog line to every release" in printed.out


def test_the_add_flags_are_refused_without_add(kyo, project, tmp_path):
    ledger = _ledger(project)
    before = ledger.read_bytes()
    result = kyo("rules", "--audience", "worker")
    assert result.code != 0
    assert "kyo rules add" in result.err + result.out
    assert ledger.read_bytes() == before


def test_a_missing_ledger_is_refused_and_install_procedures_clears_it(kyo, project, tmp_path):
    _ledger(project).unlink()
    refused = _add(kyo, tmp_path)
    assert refused.code != 0
    assert "kyo install-procedures" in refused.err + refused.out
    assert not _ledger(project).exists()

    assert kyo("install-procedures").code == 0
    assert _add(kyo, tmp_path).code == 0


# ------------------------------------------- the remedies each refusal names, followed
# (classified in tests/test_kyo5_error_remedies.py, which points here)


def test_the_stray_flag_refusal_names_the_add_form_and_running_it_clears(kyo, project, tmp_path):
    refused = kyo("rules", "--title", TITLE)
    assert refused.code != 0
    assert "kyo rules add" in refused.err + refused.out
    assert _add(kyo, tmp_path).code == 0


def test_the_missing_flags_refusal_names_the_full_form_and_running_it_clears(
    kyo, project, tmp_path
):
    refused = kyo("rules", "add", "--title", TITLE)
    message = refused.err + refused.out
    assert refused.code != 0
    for flag in ("--audience", "--enforced-by", "--reason-file"):
        assert flag in message
    assert _add(kyo, tmp_path).code == 0


def test_the_duplicate_title_refusal_names_the_command_that_prints_the_rule(
    kyo, project, tmp_path
):
    assert _add(kyo, tmp_path).code == 0
    message = _refused_without_writing(kyo, project, tmp_path)
    assert f'kyo rules "{TITLE}"' in message
    printed = kyo("rules", TITLE)
    assert printed.code == 0 and TITLE in printed.out


def test_no_project_is_refused_and_root_clears_it(project, tmp_path):
    from .conftest import invoke

    elsewhere = tmp_path / "not-a-project"
    elsewhere.mkdir()
    args = [
        "rules", "add", "--title", TITLE, "--audience", "worker",
        "--enforced-by", "aspirational", "--reason-file", str(_reason_file(tmp_path)),
    ]
    refused = invoke(["--root", str(elsewhere), *args])
    assert refused.code != 0
    assert "--root <project>" in refused.err + refused.out

    cleared = invoke(["--root", str(project), *args])
    assert cleared.code == 0, cleared
    assert TITLE in _ledger(project).read_text(encoding="utf-8")
