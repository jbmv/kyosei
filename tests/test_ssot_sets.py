"""KYO-552: ``ssot:`` document sets, slice 1 of KYO-551 — ids, one owner per id, bare-id
resolution and tombstones (src/kyosei/docs_ssot.py, docs/reference/topics/
docs-governance.md)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import docs_check, docs_ssot
from kyosei import registry as registry_module
from kyosei.docs_parse import SectionStatusSet, ssot_declaration

from .conftest import git_init

_SET = """\
documents:
  specs:
    summary: The specs.
    globs: [docs/specs/**/*.md, docs/foundation.md]
    section_status:
      statuses: [current, superseded]
    ssot:
      id_heading: '{id}. {title}'
      id_pattern: '[A-Z]{1,4}-\\d+'
      prefixes:
        P: docs/foundation.md
        A: docs/specs/a.md
        API: docs/specs/b.md
"""


def _write(project: Path, rel: str, text: str) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _declare(project: Path, body: str = _SET) -> None:
    _write(project, "kyosei/documents.yml", body)


def _problems(kyo) -> list[dict]:
    return kyo("docs", "check", "--json").json["section_problems"]


def _reasons(kyo) -> list[str]:
    return [problem["reason"] for problem in _problems(kyo)]


def _specs(project: Path, docs: dict[str, str]) -> None:
    _declare(project)
    for rel, text in docs.items():
        _write(project, rel, text)


# ------------------------------------------------------------- c1: the documents.yml block


def test_a_valid_block_is_accepted_read_with_its_default_and_stated(kyo, project: Path) -> None:
    _specs(project, {"docs/specs/a.md": "## A-1. A thing\n\nstatus: current\n\nProse.\n"})
    result = kyo("docs", "check")
    assert result.code == 0, result
    assert "ssot: ids `{id}. {title}`" in result.out
    assert "tombstones at most 3 line(s)" in result.out

    declaration = ssot_declaration(
        {"id_heading": "{id}. {title}", "id_pattern": r"[A-Z]{1,4}-\d+", "prefixes": {"P": "x.md"}}
    )
    assert declaration is not None
    assert declaration.tombstone_max_lines == registry_module.SSOT_TOMBSTONE_MAX_LINES_DEFAULT == 3
    assert declaration.owners == {"P": "x.md"}
    assert declaration.heading_re.fullmatch("P-12. A rule title").group("id") == "P-12"
    limited = ssot_declaration(
        {"id_heading": "{id}. {title}", "id_pattern": "P-1", "prefixes": {"P": "x.md"},
         "tombstone_max_lines": 7}
    )
    assert limited is not None and limited.tombstone_max_lines == 7


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("docs/specs/**/*.md", "docs/specs/a.md", True),
        ("docs/specs/**/*.md", "docs/specs/deep/b.md", True),
        ("docs/specs/*.md", "docs/specs/deep/b.md", False),
        ("docs/foundation.md", "docs/foundation.md", True),
        ("docs/*.md", "README.md", False),
        ("*.md", "README.md", True),
    ],
)
def test_glob_matches_follows_path_glob(glob: str, path: str, matches: bool) -> None:
    assert registry_module.glob_matches(glob, path) is matches


@pytest.mark.parametrize(
    ("broken", "named"),
    [
        # c9: an unknown key — the remedy is removing it, which is the valid block
        (_SET + "      tombstone_max_line: 3\n", "unknown key(s) tombstone_max_line"),
        # c9: ssot together with numbered — remove one of them
        (_SET.replace("    section_status:", "    numbered:\n      heading: '## {number}.'\n"
                      "      fragments: docs/fragments\n    section_status:"),
         "numbered are declared together"),
        # c9: section_status.statuses lacks superseded — add it
        (_SET.replace("[current, superseded]", "[current, amended]"),
         "add superseded to section_status.statuses"),
        # c9: no section_status at all — add one
        (_SET.replace("    section_status:\n      statuses: [current, superseded]\n", ""),
         "needs a section_status block"),
        # c1: id_heading without {title}
        (_SET.replace("'{id}. {title}'", "'{id}.'"), "exactly once each"),
        # c1: id_heading written as a markdown heading
        (_SET.replace("'{id}. {title}'", "'## {id}. {title}'"), "must not begin with '#'"),
        # c1: an id_pattern that does not compile
        (_SET.replace("'[A-Z]{1,4}-\\d+'", "'[A-Z{1,4}-\\d+'"), "compiles"),
        # c1: an anchored id_pattern
        (_SET.replace("'[A-Z]{1,4}-\\d+'", "'^[A-Z]{1,4}-\\d+$'"), "is anchored"),
        # c1: prefixes empty
        (_SET.split("      prefixes:")[0] + "      prefixes: {}\n", "non-empty mapping"),
        # c1: a prefix mapped to a path the set's globs do not match
        (_SET.replace("P: docs/foundation.md", "P: docs/other.md"),
         "none of this set's globs"),
        # c1: tombstone_max_lines not a positive integer
        (_SET + "      tombstone_max_lines: 0\n", "must be a positive integer"),
        # c1: a required key missing
        (_SET.replace("      id_pattern: '[A-Z]{1,4}-\\d+'\n", ""), "is missing id_pattern"),
    ],
)
def test_a_malformed_ssot_block_is_refused_by_name_and_the_remedy_clears_it(
    kyo, project: Path, broken: str, named: str
) -> None:
    """KYO-552.c1 and c9: each refusal names its key and its remedy, and the declaration
    with the remedy applied (the valid block) is accepted."""
    _write(project, "docs/specs/a.md", "## A-1. A thing\n\nProse.\n")
    _declare(project, broken)
    result = kyo("docs", "check")
    assert result.code != 0, result
    assert "ssot" in result.err and named in result.err, result.err

    _declare(project)
    assert kyo("docs", "check").code == 0


# ------------------------------------------------------------- c2: one owner per id


def test_an_undeclared_prefix_in_a_heading_is_refused_and_declaring_it_clears(
    kyo, project: Path
) -> None:
    _specs(project, {"docs/specs/a.md": "## A-1. Fine\n\nProse.\n\n## Q-1. Stray\n\nProse.\n"})
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_PREFIX_UNDECLARED
    assert problem["section"] == "Q-1. Stray"
    assert "add Q to ssot.prefixes" in problem["remedy"]

    _declare(project, _SET + "        Q: docs/specs/a.md\n")
    assert _problems(kyo) == []


def test_an_id_owned_in_the_wrong_document_is_refused_and_moving_it_clears(
    kyo, project: Path
) -> None:
    _specs(project, {"docs/specs/a.md": "## A-1. Fine\n\n## API-1. Misplaced\n\nProse.\n"})
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_ID_WRONG_DOCUMENT
    assert "docs/specs/b.md" in problem["says"] and "move the section to" in problem["remedy"]

    _write(project, "docs/specs/a.md", "## A-1. Fine\n")
    _write(project, "docs/specs/b.md", "### API-1. Misplaced\n\nProse.\n")
    assert _problems(kyo) == []


def test_an_id_owned_twice_across_ssot_sets_is_refused_naming_both(
    kyo, project: Path
) -> None:
    """Ids are unique across every document of every set declaring ssot:."""
    other = (
        "  other:\n    summary: Another set.\n    globs: [docs/other/*.md]\n"
        "    section_status:\n      statuses: [current, superseded]\n"
        "    ssot:\n      id_heading: '{id} -- {title}'\n      id_pattern: 'A-\\d+'\n"
        "      prefixes:\n        A: docs/other/x.md\n"
    )
    _declare(project, _SET + other)
    _write(project, "docs/specs/a.md", "## A-1. First\n\nProse.\n")
    _write(project, "docs/other/x.md", "# A-1 -- Second\n\nProse.\n")
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_ID_DUPLICATE
    assert problem["doc_path"] == "docs/specs/a.md"
    assert "docs/other/x.md [A-1 -- Second]" in problem["says"]
    assert "give one of them a new id" in problem["remedy"]

    _write(project, "docs/other/x.md", "# A-2 -- Second\n\nProse.\n")
    assert _problems(kyo) == []


def test_a_duplicate_in_one_document_at_two_levels_is_refused(kyo, project: Path) -> None:
    _specs(project, {"docs/specs/a.md": "## A-1. First\n\n### A-1. Again\n\nProse.\n"})
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_ID_DUPLICATE
    assert problem["section"] == "A-1. Again" and "[A-1. First]" in problem["says"]


# -------------------------------------------------- c3: a bare id resolves repo-wide


_OLD = "## A-1. Old rule\n\nstatus: superseded\nsuperseded-by: API-31\n\nShort.\n"


def test_superseded_by_a_bare_id_resolves_to_its_owner_in_another_document(
    kyo, project: Path
) -> None:
    """KYO-552.c3's named case: `superseded-by: API-31` in docs/specs/a.md resolves to the
    heading in docs/specs/b.md, and the link rules apply through it."""
    _specs(
        project,
        {
            "docs/specs/a.md": _OLD,
            "docs/specs/b.md": "## API-31. New rule\n\nThis replaces A-1.\n",
        },
    )
    assert _problems(kyo) == []

    def read(path: str) -> str | None:
        target = project / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    corpus = docs_check._Corpus(read)
    declaration = ssot_declaration(
        {"id_heading": "{id}. {title}", "id_pattern": r"[A-Z]{1,4}-\d+",
         "prefixes": {"A": "docs/specs/a.md", "API": "docs/specs/b.md"}}
    )
    corpus.ids = docs_ssot.SsotIndex(
        corpus, {"docs/specs/a.md": declaration, "docs/specs/b.md": declaration}
    )
    found = corpus.resolve("docs/specs/a.md", "API-31")
    assert not isinstance(found, str)
    assert (found.doc_path, found.section.heading) == ("docs/specs/b.md", "API-31. New rule")


def test_the_link_rules_apply_through_the_resolution_and_their_remedies_clear(
    kyo, project: Path
) -> None:
    # not cited back: API-31 never names A-1
    _specs(project, {"docs/specs/a.md": _OLD, "docs/specs/b.md": "## API-31. New rule\n\nNew.\n"})
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_NOT_CITED_BACK
    assert "docs/specs/b.md [API-31. New rule]" in problem["says"]
    _write(project, "docs/specs/b.md", "## API-31. New rule\n\nThis replaces A-1.\n")
    assert _problems(kyo) == []

    # unresolved: no heading owns API-32
    _write(project, "docs/specs/a.md", _OLD.replace("API-31", "API-32"))
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_UNRESOLVED
    assert "no heading in any ssot document owns API-32" in problem["says"]
    assert "`API-32. <title>` in docs/specs/b.md" in problem["says"]
    _write(project, "docs/specs/a.md", _OLD)
    assert _problems(kyo) == []

    # to itself
    _write(project, "docs/specs/a.md", _OLD.replace("superseded-by: API-31", "superseded-by: A-1"))
    assert docs_check.SECTION_LINK_TO_ITSELF in _reasons(kyo)


# ---------------------------------------------------------- c4: a bare id in prose


def test_an_unowned_bare_id_in_prose_is_refused_and_other_tokens_never_are(
    kyo, project: Path
) -> None:
    prose = (
        "## P-1. Foundation\n\narea: API-77\n\n"
        "See API-12. Encoded as UTF-8, hashed with SHA-256, filed as KYO-12.\n\n"
        "```\nAPI-78 in a fence is an example\n```\n"
    )
    _specs(project, {"docs/foundation.md": prose})
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_ID_UNRESOLVED
    assert problem["doc_path"] == "docs/foundation.md" and "API-12" in problem["says"]
    assert "`API-12. <title>`" in problem["remedy"] and "docs/specs/b.md" in problem["remedy"]

    _write(project, "docs/specs/b.md", "## API-12. Owned now\n\nProse.\n")
    assert _problems(kyo) == []


def test_a_bare_id_outside_an_ssot_document_is_not_read(kyo, project: Path) -> None:
    _specs(project, {"docs/notes.md": "## Notes\n\nSee API-12.\n"})
    assert _problems(kyo) == []


# ----------------------------------------------------------- c5: citing a tombstone


def test_citing_a_tombstone_without_its_successor_is_refused_naming_it(
    kyo, project: Path
) -> None:
    _specs(
        project,
        {
            "docs/specs/a.md": _OLD,
            "docs/specs/b.md": "## API-31. New rule\n\nThis replaces A-1.\n",
            "docs/foundation.md": "## P-1. Foundation\n\nThe rule is A-1.\n",
        },
    )
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_CITES_TOMBSTONE
    assert problem["doc_path"] == "docs/foundation.md"
    assert "cite API-31 instead" in problem["remedy"]

    _write(project, "docs/foundation.md", "## P-1. Foundation\n\nThe rule was A-1, now\nAPI-31.\n")
    assert _problems(kyo) == []
    _write(project, "docs/foundation.md", "## P-1. Foundation\n\nThe rule is API-31.\n")
    assert _problems(kyo) == []


def test_the_successor_named_is_the_head_of_the_chain(kyo, project: Path) -> None:
    _specs(
        project,
        {
            "docs/specs/a.md": _OLD,
            "docs/specs/b.md": (
                "## API-31. New rule\n\nstatus: superseded\nsuperseded-by: API-40\n\n"
                "Replaced A-1.\n\n## API-40. Newest rule\n\nReplaces API-31.\n"
            ),
            "docs/foundation.md": "## P-1. Foundation\n\nThe rule is A-1, see API-31.\n",
        },
    )
    reasons = _problems(kyo)
    # API-31's own paragraph names A-1 without API-40, and P-1 names two tombstones
    assert {p["reason"] for p in reasons} == {docs_ssot.SSOT_CITES_TOMBSTONE}
    assert all("API-40" in p["remedy"] for p in reasons)
    _write(
        project,
        "docs/specs/b.md",
        "## API-31. New rule\n\nstatus: superseded\nsuperseded-by: API-40\n\n"
        "Replaced A-1; see API-40.\n\n## API-40. Newest rule\n\nReplaces API-31.\n",
    )
    _write(project, "docs/foundation.md", "## P-1. Foundation\n\nThe rule is API-40.\n")
    assert _problems(kyo) == []


# ----------------------------------------------------------------- c6: tombstone body


def test_a_tombstone_with_a_body_over_the_limit_is_refused_and_cutting_it_clears(
    kyo, project: Path
) -> None:
    body = "one\ntwo\n\nthree\nfour\n"
    _specs(
        project,
        {
            "docs/specs/a.md": _OLD.replace("Short.\n", body),
            "docs/specs/b.md": "## API-31. New rule\n\nThis replaces A-1.\n",
        },
    )
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_TOMBSTONE_BODY
    assert "keeps 4 non-blank line(s)" in problem["says"]
    assert "tombstone_max_lines is 3" in problem["says"]
    assert "cut it to at most 3 line(s)" in problem["remedy"] and "API-31" in problem["remedy"]

    _write(project, "docs/specs/a.md", _OLD.replace("Short.\n", "one\ntwo\nthree\n"))
    assert _problems(kyo) == []
    _write(project, "docs/specs/a.md", _OLD.replace("Short.\n", body))
    _declare(project, _SET + "      tombstone_max_lines: 4\n")
    assert _problems(kyo) == []


def test_metadata_lines_include_a_projects_own_fields_and_a_comment() -> None:
    body = ["", "<!--", "status: superseded", "priority: P0", "-->", "Prose.", "status: x"]
    assert docs_ssot.metadata_lines(body) == {1, 2, 3, 4}
    assert docs_ssot.metadata_lines(["status: current", "area: x", "", "Prose."]) == {0, 1}


# ---------------------------------------------------------- c7: one pass, and the hook


_EVERY = {
    "docs/specs/a.md": (
        "## A-1. Old rule\n\nstatus: superseded\nsuperseded-by: API-31\n\n1\n2\n3\n4\n\n"
        "## Q-1. Undeclared\n\n## API-2. Misplaced\n\n## A-5. Once\n\n### A-5. Twice\n"
    ),
    "docs/specs/b.md": "## API-31. New rule\n\nThis replaces A-1.\n",
    "docs/foundation.md": "## P-1. Foundation\n\nSee A-1 and API-99.\n",
}


def test_docs_check_reports_every_ssot_refusal_in_one_pass(kyo, project: Path) -> None:
    _specs(project, _EVERY)
    result = kyo("docs", "check")
    assert result.code == 3, result
    for reason in docs_ssot.SSOT_REFUSALS:
        assert reason in result.err, reason
    assert sorted(set(_reasons(kyo))) == sorted(docs_ssot.SSOT_REFUSALS)


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def test_check_branch_subagent_stop_blocks_on_the_ssot_refusals_a_branch_makes(
    kyo, project: Path
) -> None:
    _specs(project, {"docs/specs/b.md": "## API-31. New rule\n\nProse.\n"})
    git_init(project)
    _git(project, "checkout", "-q", "-b", "feature")
    _write(project, "docs/foundation.md", "## P-1. Foundation\n\nSee API-99.\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "work")
    payload = json.dumps(
        {"session_id": "s", "cwd": str(project), "hook_event_name": "SubagentStop",
         "stop_hook_active": False}
    )

    def stop(*args: str):
        return kyo("check-branch", "--subagent-stop", "--against", "main", *args, stdin=payload)

    refused = stop()
    assert refused.code == 2, refused
    assert docs_ssot.SSOT_ID_UNRESOLVED in refused.out + refused.err
    data = stop("--json").json
    rows = (data.get("data") or data)["section_status_problems"]
    assert [row["reason"] for row in rows] == [docs_ssot.SSOT_ID_UNRESOLVED]

    _write(project, "docs/foundation.md", "## P-1. Foundation\n\nSee API-31.\n")
    _git(project, "commit", "-q", "-am", "fix")
    assert stop().code == 0


def test_check_branch_ignores_an_ssot_problem_already_on_main(kyo, project: Path) -> None:
    _specs(project, {"docs/foundation.md": "## P-1. Foundation\n\nSee API-99.\n"})
    git_init(project)
    _git(project, "checkout", "-q", "-b", "feature")
    _write(project, "src/x.py", "x = 1\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "work")
    assert kyo("check-branch", "--against", "main").code == 0


# ---------------------------------------------------------------- c8: the documentation


_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_reference_documents_the_block_its_keys_and_every_refusal() -> None:
    text = (_REPO_ROOT / "docs/reference/topics/docs-governance.md").read_text(encoding="utf-8")
    assert "ssot:" in text
    for key in (*registry_module.SSOT_ID_KEYS, *registry_module.SSOT_OPTIONAL):
        assert key in text, key
    for reason in docs_ssot.SSOT_REFUSALS:
        assert reason in text, reason


def test_the_guide_says_when_to_declare_ssot_rather_than_numbered() -> None:
    text = (_REPO_ROOT / "docs/guide/06-kinds-scopes-documents.md").read_text(encoding="utf-8")
    assert "ssot:" in text and "numbered:" in text


def test_a_section_status_set_without_ssot_is_unchanged() -> None:
    assert SectionStatusSet(name="x", globs=(), statuses=("current",)).ssot is None


# ------------------------------------------- KYO-553: ssot: without ids, an id-less set


_IDLESS = """\
  notes:
    summary: Current-state notes whose sections carry no ids.
    globs: [docs/notes/*.md]
    section_status:
      statuses: [current, superseded]
    ssot: {}
"""

_IDLESS_ONLY = "documents:\n" + _IDLESS


@pytest.mark.parametrize("block", ["{}", "{tombstone_max_lines: 2}"])
def test_an_ssot_block_with_none_of_the_id_keys_is_accepted(
    kyo, project: Path, block: str
) -> None:
    """KYO-553.c1: ``ssot: {}`` and a block holding only tombstone_max_lines."""
    _declare(project, _IDLESS_ONLY.replace("ssot: {}", f"ssot: {block}"))
    _write(project, "docs/notes/n.md", "## Notes\n\nstatus: current\n\nProse.\n")
    result = kyo("docs", "check")
    assert result.code == 0, result
    assert "ssot: no section ids; tombstones at most" in result.out

    declaration = ssot_declaration({})
    assert declaration is not None and not declaration.has_ids
    assert (declaration.id_heading, declaration.id_pattern, declaration.prefixes) == (
        None,
        None,
        (),
    )
    # An empty prefixes never means every prefix.
    assert not declaration.declares("P-1") and declaration.owners == {}
    with pytest.raises(ValueError):
        declaration.heading_re  # noqa: B018 -- the property refuses an id-less set
    with pytest.raises(ValueError):
        declaration.token_re  # noqa: B018
    assert ssot_declaration({"tombstone_max_lines": 2}).tombstone_max_lines == 2


_ID_LINES = {
    "id_heading": "      id_heading: '{id}. {title}'\n",
    "id_pattern": "      id_pattern: '[A-Z]{1,4}-\\d+'\n",
    "prefixes": "      prefixes:\n        A: docs/notes/a.md\n",
}


def _notes_block(keys: tuple[str, ...]) -> str:
    if not keys:
        return _IDLESS_ONLY
    return _IDLESS_ONLY.replace(
        "    ssot: {}\n", "    ssot:\n" + "".join(_ID_LINES[key] for key in keys)
    )


@pytest.mark.parametrize(
    "present",
    [
        ("id_heading",),
        ("id_pattern",),
        ("prefixes",),
        ("id_heading", "id_pattern"),
        ("id_heading", "prefixes"),
        ("id_pattern", "prefixes"),
    ],
)
@pytest.mark.parametrize("remedy", ["all", "none"])
def test_some_but_not_all_id_keys_is_refused_and_each_remedy_clears_it(
    kyo, project: Path, present: tuple[str, ...], remedy: str
) -> None:
    """KYO-553.c2: the refusal names the missing keys and both remedies; declaring all
    three, or none of them, clears it."""
    _write(project, "docs/notes/a.md", "## A-1. A thing\n\nProse.\n")
    _declare(project, _notes_block(present))
    result = kyo("docs", "check")
    assert result.code != 0, result
    missing = [key for key in registry_module.SSOT_ID_KEYS if key not in present]
    assert f"is missing {', '.join(missing)}" in result.err, result.err
    assert "declare all of id_heading, id_pattern, prefixes" in result.err
    assert "or none of them" in result.err

    fixed = registry_module.SSOT_ID_KEYS if remedy == "all" else ()
    _declare(project, _notes_block(fixed))
    result = kyo("docs", "check")
    assert result.code == 0, result


def test_an_id_less_set_gets_the_tombstone_rule_and_no_id_rule(kyo, project: Path) -> None:
    """KYO-553.c3: id-shaped tokens in the prose and headings of an id-less set's
    documents -- an undeclared-looking prefix, the same id twice, an unowned id in prose,
    one naming a superseded section -- report nothing; a tombstone over the limit does."""
    _declare(project, _IDLESS_ONLY)
    text = (
        "## A-1. Old rule\n\nstatus: superseded\nsuperseded-by: A-1. New rule\n\n"
        "Was API-31.\none\ntwo\nthree\n\n"
        "## A-1. New rule\n\nReplaces A-1. Old rule. See A-1 and API-99; Q-7 too.\n\n"
        "### Q-9. Stray\n\nSee A-1.\n"
    )
    _write(project, "docs/notes/a.md", text)
    _write(project, "docs/notes/b.md", "## API-99. Elsewhere\n\nCites A-1.\n")
    problems = _problems(kyo)
    assert [p["reason"] for p in problems] == [docs_ssot.SSOT_TOMBSTONE_BODY], problems
    assert problems[0]["section"] == "A-1. Old rule"
    assert "tombstone_max_lines is 3" in problems[0]["says"]
    id_rules = set(docs_ssot.SSOT_REFUSALS) - {docs_ssot.SSOT_TOMBSTONE_BODY}
    assert not id_rules.intersection(p["reason"] for p in problems)

    _write(project, "docs/notes/a.md", text.replace("one\ntwo\nthree\n", ""))
    assert _problems(kyo) == []


def test_an_id_bearing_set_beside_an_id_less_one_checks_as_before(
    kyo, project: Path
) -> None:
    """KYO-553.c4: the id-bearing set's ids still resolve repo-wide, its id rules still
    report, and the id-less set's documents neither own its ids nor get read for them."""
    _declare(project, _SET + _IDLESS)
    _write(project, "docs/specs/a.md", _OLD)
    _write(project, "docs/specs/b.md", "## API-31. New rule\n\nThis replaces A-1.\n")
    # An id-less document with a heading shaped like an owned id and an unowned one.
    _write(project, "docs/notes/n.md", "## API-31. Copy\n\nSee API-77 and A-1.\n")
    assert _problems(kyo) == []

    # The bare id in the id-bearing set resolves across documents, not to the copy.
    def read(path: str) -> str | None:
        target = project / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    corpus = docs_check._Corpus(read)
    bearing = ssot_declaration(
        {"id_heading": "{id}. {title}", "id_pattern": r"[A-Z]{1,4}-\d+",
         "prefixes": {"A": "docs/specs/a.md", "API": "docs/specs/b.md"}}
    )
    idless = ssot_declaration({})
    corpus.ids = docs_ssot.SsotIndex(
        corpus,
        {"docs/specs/a.md": bearing, "docs/specs/b.md": bearing, "docs/notes/n.md": idless},
    )
    found = corpus.resolve("docs/specs/a.md", "API-31")
    assert not isinstance(found, str)
    assert (found.doc_path, found.section.heading) == ("docs/specs/b.md", "API-31. New rule")
    assert corpus.ids.heading_problems == []

    # The id-bearing set's id rules still report: an unowned id in its prose.
    _write(project, "docs/foundation.md", "## P-1. Foundation\n\nSee API-77.\n")
    [problem] = _problems(kyo)
    assert problem["reason"] == docs_ssot.SSOT_ID_UNRESOLVED
    assert problem["doc_path"] == "docs/foundation.md"


@pytest.mark.parametrize(
    "rel",
    [
        "docs/reference/topics/docs-governance.md",
        "docs/guide/06-kinds-scopes-documents.md",
        "docs/design/charter.md",
    ],
)
def test_the_documents_say_the_id_keys_are_optional_together(rel: str) -> None:
    """KYO-553.c5: each says an id-less set declares `ssot: {}` and what it is checked
    for."""
    text = " ".join((_REPO_ROOT / rel).read_text(encoding="utf-8").split())
    assert "ssot: {}" in text, rel
    assert "ssot-tombstone-body" in text or "the tombstone rule" in text, rel
    assert "id_heading" in text and "prefixes" in text, rel
