"""KYO-600: RECX.yaml as a governed document, the ``recx-manifest`` document format.

Each module entry in RECX.yaml governs its own path, and a project may narrow a module
to named targets instead. Each criterion's tests carry its number:

* **c1** ``format: recx-manifest`` on a document set, matching exactly RECX.yaml, and the
  markdown-only fields refused on it by name.
* **c2** the sections the reader derives, and the file it refuses to derive them from.
* **c3** a module governs every changed path its path prefixes, read from both sides.
* **c4** ``project_governs`` and ``evidence_governs``.
* **c5** an obligation on a module section, through preview, verdicts, settle and the
  completion move, and a standing obligation from a decision's doc-refs.
* **c6** no ``recx_cli`` import, and no change for a project without the format.
* **c7** the reference topic.
* **c8** ``module_governs``.

``tests/fixtures/recx/RECX.yaml`` is recx's own manifest at the time of writing, copied
so the reader is tested against a real one without reading another repository.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import docs_check, docs_obligations, docs_parse, docs_recx, git_queries
from kyosei import registry as registry_module
from kyosei.docs_parse import PREAMBLE

from .conftest import new_record, valid_report, verify_criterion
from .test_kyo137_docs_obligations import _commit, _declare_touches, _git, _write

REPO_ROOT = Path(__file__).resolve().parents[1]
RECX_FIXTURE = Path(__file__).parent / "fixtures" / "recx" / "RECX.yaml"

MANIFEST = """\
recx: "0.1"
name: demo
summary: A demonstration project.
license: MIT
status: experimental
modules:
  - id: core
    path: src/
    summary: Everything in src.
  - id: cli
    path: src/cli/
    summary: The command line.
  - id: action
    path: action.yml
    summary: The GitHub Action.
evidence:
  - id: tests
    summary: The tests pass.
    run: pytest
"""

RECX_SET = """\
documents:
  manifest:
    summary: The recx manifest; each module entry governs its own path.
    globs: [RECX.yaml]
    format: recx-manifest
"""


def _documents_yml(project: Path, text: str) -> None:
    _write(project, "kyosei/documents.yml", text)


def _recx_project(project: Path, manifest: str = MANIFEST, documents: str = RECX_SET) -> str:
    """A committed project whose RECX.yaml is a recx-manifest document. Returns the base."""
    _documents_yml(project, documents)
    _write(project, "RECX.yaml", manifest)
    _write(project, "src/app.py", "x = 1\n")
    _write(project, "src/cli/main.py", "y = 1\n")
    _write(project, "action.yml", "runs: {}\n")
    _write(project, "pyproject.toml", "[project]\nname = 'demo'\n")
    _commit(project, "baseline")
    return _git(project, "rev-parse", "HEAD")


def _branch(project: Path, name: str, edits: dict[str, str | None]) -> None:
    """Make ``name`` off HEAD with ``edits`` (``None`` deletes), commit, and come back."""
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", name)
    for path, text in edits.items():
        if text is None:
            _git(project, "rm", "-q", path)
        else:
            _write(project, path, text)
    _commit(project, f"work on {name}")
    _git(project, "checkout", "-q", base)


def _raised(project: Path, base: str, branch: str) -> dict[str, tuple[str, ...]]:
    """What ``branch`` raises, as ``{section: changed paths}`` on RECX.yaml."""
    documents_in = docs_parse.document_set(project / "kyosei")
    changed = git_queries.branch_changed_files(project, base, branch)
    found = docs_obligations.obligations_for_branch(
        project, base, branch, changed, documents_in=documents_in
    )
    return {o.section: o.changed for o in found if o.doc_path == "RECX.yaml"}


def _load(project: Path) -> registry_module.Registry:
    return registry_module.load_document_registry(project / "kyosei")


# ------------------------------------------------------------------ c1: the declaration


def test_c1_a_set_declares_the_format_and_markdown_stays_the_default(project: Path) -> None:
    _documents_yml(
        project,
        RECX_SET
        + "  prose:\n    summary: Prose.\n    globs: ['docs/**/*.md']\n"
        + "  explicit:\n    summary: Explicit.\n    globs: ['*.md']\n    format: markdown\n",
    )
    documents_in = docs_parse.document_set(project / "kyosei")
    assert documents_in.recx_manifest is not None
    assert documents_in.recx_manifest.name == "manifest"
    assert documents_in.is_recx_manifest("RECX.yaml")
    assert not documents_in.is_recx_manifest("docs/a.md")

    _documents_yml(project, "documents:\n  prose:\n    summary: P.\n    globs: ['*.md']\n")
    assert docs_parse.document_set(project / "kyosei").recx_manifest is None


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        ("    section_status: {statuses: [current]}\n", "section_status"),
        ("    ssot: {}\n", "ssot"),
        ("    numbered: {heading: '## {number}.', fragments: docs/frags}\n", "numbered"),
    ],
)
def test_c1_markdown_features_are_refused_on_a_recx_set_by_name(
    project: Path, extra: str, named: str
) -> None:
    _documents_yml(project, RECX_SET + extra)
    with pytest.raises(registry_module.RegistryError) as raised:
        _load(project)
    assert f"{named} is a markdown feature" in str(raised.value)
    _documents_yml(project, RECX_SET)
    assert _load(project).get("manifest") is not None


@pytest.mark.parametrize("glob", ["*.yaml", "docs/RECX.yaml", "RECX.yml"])
def test_c1_any_other_match_is_refused_by_name(project: Path, glob: str) -> None:
    _documents_yml(project, RECX_SET.replace("[RECX.yaml]", f"[RECX.yaml, '{glob}']"))
    with pytest.raises(registry_module.RegistryError) as raised:
        _load(project)
    assert f"its globs name {glob!r}" in str(raised.value)
    assert "set globs: [RECX.yaml]" in str(raised.value)
    _documents_yml(project, RECX_SET)
    assert _load(project).get("manifest") is not None


def test_c1_an_unknown_format_and_a_stray_block_are_refused(project: Path) -> None:
    _documents_yml(project, RECX_SET.replace("recx-manifest", "asciidoc"))
    with pytest.raises(registry_module.RegistryError, match="markdown, recx-manifest"):
        _load(project)
    _documents_yml(
        project,
        "documents:\n  prose:\n    summary: P.\n    globs: ['*.md']\n"
        "    recx_manifest: {project_governs: [README.md]}\n",
    )
    with pytest.raises(registry_module.RegistryError, match="declared on a markdown set"):
        _load(project)
    _documents_yml(
        project,
        RECX_SET + RECX_SET.split("documents:\n", 1)[1].replace("manifest:", "second:"),
    )
    with pytest.raises(registry_module.RegistryError, match="each declare format"):
        _load(project)


# ------------------------------------------------------------------ c2: the sections


def test_c2_the_reader_derives_the_sections_of_recx_own_manifest() -> None:
    text = RECX_FIXTURE.read_text(encoding="utf-8")
    read = docs_recx.manifest_sections(text)
    assert read.problems == ()
    assert [section.heading for section in read.sections] == [
        PREAMBLE,
        "module schema",
        "module fixtures",
        "module validator",
        "module plugin",
        "module action",
        "evidence schema-is-valid",
        "evidence manifests-validate",
        "evidence validator-tests",
    ]
    by_heading = {section.heading: section for section in read.sections}
    assert [t.raw for t in by_heading["module validator"].resolved] == ["src/recx_cli/"]
    assert all(t.prefix for t in by_heading["module validator"].resolved)
    # A section's body is the entry's own lines, so a reader answering it sees them.
    assert by_heading["module validator"].body[0].strip() == "- id: validator"
    assert any("name: recx" in line for line in by_heading[PREAMBLE].body)
    assert not any("id: validator" in line for line in by_heading[PREAMBLE].body)
    # The preamble and the evidence sections govern nothing until documents.yml says so.
    assert by_heading[PREAMBLE].resolved == ()
    assert by_heading["evidence validator-tests"].resolved == ()


BROKEN = {
    "invalid YAML": ("modules: [\n  - id: x\n", "not valid YAML"),
    "an anchor": (
        "modules:\n  - &m\n    id: a\n    path: src/\n", "defines an anchor (&m)"
    ),
    "an alias": (
        "base: &b src/\nmodules:\n  - id: a\n    path: *b\n", "defines an anchor (&b)"
    ),
    "a bare alias": ("modules:\n  - id: a\n    path: src/\nother: *x\n", "uses an alias (*x)"),
    "a merge key": (
        "modules:\n  - <<: {id: a}\n    path: src/\n", "uses a merge key (<<)"
    ),
    "no modules": ("name: demo\n", "has no modules list"),
    "modules not a list": ("modules: {a: b}\n", "has no modules list"),
    "an entry not a mapping": ("modules:\n  - src/\n", "modules entry 1 is not a mapping"),
    "no string id": ("modules:\n  - id: 7\n    path: src/\n", "has no string id"),
    "no path": ("modules:\n  - id: a\n", "modules entry 1 (a) has no string path"),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_c2_an_unreadable_manifest_is_one_ungoverned_preamble(case: str) -> None:
    text, why = BROKEN[case]
    read = docs_recx.manifest_sections(text)
    assert [s.heading for s in read.sections] == [PREAMBLE]
    assert read.sections[0].resolved == () and read.sections[0].governs == []
    (problem,) = read.problems
    assert why in problem.reason


def test_c2_kyo_docs_check_reports_an_unreadable_manifest(kyo, project: Path) -> None:
    _documents_yml(project, RECX_SET)
    _write(project, "RECX.yaml", BROKEN["a merge key"][0])
    result = kyo("docs", "check")
    assert result.code == 3, result
    assert "RECX.yaml cannot be read as a recx manifest" in result.out + result.err
    assert "merge key" in result.out + result.err

    _write(project, "RECX.yaml", MANIFEST)
    for path in ("src/app.py", "src/cli/main.py", "action.yml"):
        _write(project, path, "x\n")
    cleared = kyo("docs", "check")
    assert cleared.code == 0, cleared


# ------------------------------------------------------------------ c3: the prefix rule


@pytest.mark.parametrize("checkout", ["base", "branch"])
@pytest.mark.parametrize(
    ("case", "edits", "expected"),
    [
        ("addition", {"src/new.py": "z = 1\n"}, {"module core": ("src/new.py",)}),
        ("edit", {"src/app.py": "x = 2\n"}, {"module core": ("src/app.py",)}),
        ("deletion", {"src/app.py": None}, {"module core": ("src/app.py",)}),
        (
            "a file under two modules",
            {"src/cli/main.py": "y = 2\n"},
            {"module core": ("src/cli/main.py",), "module cli": ("src/cli/main.py",)},
        ),
        ("a file module", {"action.yml": "runs: {x: 1}\n"}, {"module action": ("action.yml",)}),
        ("outside every module", {"pyproject.toml": "[project]\n"}, {}),
    ],
)
def test_c3_a_module_raises_on_every_changed_path_its_path_prefixes(
    git_project: Path, case: str, edits: dict, expected: dict, checkout: str
) -> None:
    base = _recx_project(git_project)
    _branch(git_project, "work", edits)
    if checkout == "branch":
        _git(git_project, "checkout", "-q", "work")
    assert _raised(git_project, base, "work") == expected, case


@pytest.mark.parametrize("checkout", ["base", "branch"])
def test_c3_a_move_that_updates_the_module_path_raises_on_both_locations(
    git_project: Path, checkout: str
) -> None:
    """The module paths are the union of both sides, so the old location still raises
    from the orchestrator's checkout (base side) and the worker's (branch side)."""
    manifest = MANIFEST.replace("path: action.yml", "path: tools/action/")
    base = _recx_project(git_project, manifest=manifest)
    _write(git_project, "tools/action/run.sh", "echo\n")
    base = _commit(git_project, "add tools")
    _git(git_project, "checkout", "-q", "-b", "move")
    _git(git_project, "mv", "tools/action", "ci")
    _write(git_project, "RECX.yaml", manifest.replace("path: tools/action/", "path: ci/"))
    _commit(git_project, "move the action")
    _git(git_project, "checkout", "-q", "main" if checkout == "base" else "move")
    raised = _raised(git_project, base, "move")
    assert raised == {"module action": ("ci/run.sh", "tools/action/run.sh")}

    found = docs_obligations.obligations_for_branch(
        git_project,
        base,
        "move",
        git_queries.branch_changed_files(git_project, base, "move"),
        documents_in=docs_parse.document_set(git_project / "kyosei"),
    )
    (action,) = [o for o in found if o.section == "module action"]
    assert set(action.targets) == {"tools/action/", "ci/"}


def test_c3_a_module_the_branch_removed_still_raises_on_its_section(git_project: Path) -> None:
    base = _recx_project(git_project)
    removed = MANIFEST.replace(
        "  - id: action\n    path: action.yml\n    summary: The GitHub Action.\n", ""
    )
    _branch(git_project, "drop", {"RECX.yaml": removed, "action.yml": None})
    assert _raised(git_project, base, "drop") == {"module action": ("action.yml",)}


def test_c3_the_obligation_says_which_path_and_which_change(git_project: Path) -> None:
    base = _recx_project(git_project)
    _branch(git_project, "work", {"src/cli/main.py": "y = 3\n"})
    found = docs_obligations.obligations_for_branch(
        git_project,
        base,
        "work",
        ["src/cli/main.py"],
        documents_in=docs_parse.document_set(git_project / "kyosei"),
    )
    says = {o.section: o.says for o in found}
    assert says["module cli"] == (
        "RECX.yaml [module cli] governs src/cli/, and this dispatch changed src/cli/main.py"
    )


def test_c3_prefix_matching_is_by_path_segment() -> None:
    assert docs_recx.prefix_matches("src/", "src/a.py")
    assert docs_recx.prefix_matches("src", "src/a.py")
    assert docs_recx.prefix_matches("./src/", "src/a/b.py")
    assert docs_recx.prefix_matches("action.yml", "action.yml")
    assert not docs_recx.prefix_matches("src/", "srcs/a.py")
    assert not docs_recx.prefix_matches("action.yml", "action.yml.bak")


# ------------------------------------------------------------------ KYO-606: comment-only


def _raised_and_suppressed(project: Path, base: str, branch: str):
    """What ``branch`` raises on RECX.yaml as ``{section: changed}``, and what it quieted
    as ``{section: row}``."""
    documents_in = docs_parse.document_set(project / "kyosei")
    changed = git_queries.branch_changed_files(project, base, branch)
    raised, suppressed = docs_obligations.obligations_and_suppressions_for_branch(
        project, base, branch, changed, documents_in=documents_in
    )
    return (
        {o.section: o.changed for o in raised if o.doc_path == "RECX.yaml"},
        {o.section: o for o in suppressed if o.doc_path == "RECX.yaml"},
    )


@pytest.mark.parametrize("checkout", ["base", "branch"])
def test_kyo606_c1_a_comment_only_change_under_a_module_path_raises_nothing(
    git_project: Path, checkout: str
) -> None:
    """KYO-530's rule, applied to a module's prefix target: src/app.py gains comments and
    src/cli/main.py a docstring, neither moves a code token, so neither `core` nor `cli`
    is raised, and each is listed as quieted with the marking a ``governs:`` row carries."""
    base = _recx_project(git_project)
    _branch(
        git_project,
        "comments",
        {
            "src/app.py": "# a comment\nx = 1  # and another\n",
            "src/cli/main.py": '"""A module docstring."""\ny = 1\n',
        },
    )
    if checkout == "branch":
        _git(git_project, "checkout", "-q", "comments")
    raised, suppressed = _raised_and_suppressed(git_project, base, "comments")
    assert raised == {}
    assert set(suppressed) == {"module core", "module cli"}
    for row in suppressed.values():
        assert row.comment_only
        assert "changed only comments and docstrings" in row.says
    assert docs_obligations.suppression_lines([suppressed["module cli"]]) == [
        f"    RECX.yaml [module cli] — {suppressed['module cli'].says}"
    ]


def test_kyo606_c1_a_module_path_only_the_other_side_declares_goes_through_the_rule(
    git_project: Path,
) -> None:
    """A module path read from the other side of the diff (KYO-600's union) is governed
    for the rule too: the branch re-points the module and changes only a comment under
    the old path, which is on neither the disk's manifest nor the branch's."""
    manifest = MANIFEST.replace("path: action.yml", "path: tools/")
    _recx_project(git_project, manifest=manifest)
    _write(git_project, "tools/run.py", "z = 1\n")
    base = _commit(git_project, "add tools")
    _branch(
        git_project,
        "repoint",
        {
            "RECX.yaml": manifest.replace("path: tools/", "path: ci/"),
            "tools/run.py": "z = 1  # only a comment\n",
        },
    )
    _git(git_project, "checkout", "-q", "repoint")
    raised, suppressed = _raised_and_suppressed(git_project, base, "repoint")
    assert "module action" not in raised
    assert suppressed["module action"].comment_only


@pytest.mark.parametrize(
    ("case", "edits", "expected"),
    [
        (
            "a code change raises",
            {"src/app.py": "x = 2  # comment\n"},
            {"module core": ("src/app.py",)},
        ),
        (
            "a code change beside a docstring change raises",
            {"src/cli/main.py": '"""New docstring."""\ny = 2\n'},
            {"module core": ("src/cli/main.py",), "module cli": ("src/cli/main.py",)},
        ),
        (
            "a code change in one file raises though another is comment-only",
            {"src/app.py": "x = 1  # comment\n", "src/cli/main.py": "y = 2\n"},
            {"module core": ("src/cli/main.py",), "module cli": ("src/cli/main.py",)},
        ),
        (
            "a new function carrying a docstring raises",
            {"src/app.py": 'def f():\n    """Doc."""\n    return 1\nx = 1\n'},
            {"module core": ("src/app.py",)},
        ),
    ],
)
def test_kyo606_c2_a_code_token_under_the_module_path_still_raises(
    git_project: Path, case: str, edits: dict, expected: dict
) -> None:
    base = _recx_project(git_project)
    _write(git_project, "src/cli/main.py", '"""Old docstring."""\ny = 1\n')
    base = _commit(git_project, "docstring")
    _branch(git_project, "work", edits)
    raised, suppressed = _raised_and_suppressed(git_project, base, "work")
    assert raised == expected, case
    assert not any(row.comment_only for row in suppressed.values()), case


# ------------------------------------------------------------------ c4: the target lists


def test_c4_project_and_evidence_targets_govern_their_sections(git_project: Path) -> None:
    documents = RECX_SET + (
        "    recx_manifest:\n"
        "      project_governs: [pyproject.toml]\n"
        "      evidence_governs:\n"
        "        tests: [pyproject.toml, 'src/app.py#x']\n"
    )
    base = _recx_project(git_project, documents=documents)
    _branch(git_project, "deps", {"pyproject.toml": "[project]\nname = 'demo2'\n"})
    assert _raised(git_project, base, "deps") == {
        PREAMBLE: ("pyproject.toml",),
        "evidence tests": ("pyproject.toml",),
    }
    declared = docs_parse.document_set(git_project / "kyosei").recx_manifest
    assert declared.project_governs == ("pyproject.toml",)
    assert declared.evidence_governs == {"tests": ("pyproject.toml", "src/app.py#x")}


@pytest.mark.parametrize(
    ("block", "why"),
    [
        ("project_governs: pyproject.toml", "project_governs must be a non-empty list"),
        ("evidence_governs: [a]", "evidence_governs must be a non-empty mapping"),
        ("evidence_governs: {tests: []}", "evidence_governs.tests must be a non-empty list"),
        ("module_governs: {core: [1]}", "module_governs.core must list only non-empty"),
        ("unknown_governs: [x]", "unknown key(s) unknown_governs"),
    ],
)
def test_c4_c8_a_malformed_block_is_refused(project: Path, block: str, why: str) -> None:
    _documents_yml(project, RECX_SET + f"    recx_manifest: {{{block}}}\n")
    with pytest.raises(registry_module.RegistryError) as raised:
        _load(project)
    assert why in str(raised.value)


# ------------------------------------------------------------------ c5: the machinery


def _verdicts_report(kyo, project: Path, dispatch_id: str, record: str, verdicts: list[dict]):
    path = project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=_git(project, "rev-parse", "kyo-600"),
                docs_verdicts=verdicts,
            )
        ),
        encoding="utf-8",
    )
    return kyo("report", dispatch_id, "--file", str(path), "--json")


def test_c5_a_module_obligation_is_previewed_answered_settled_and_blocks_completion(
    kyo, git_project: Path
) -> None:
    base = _recx_project(git_project)
    record = new_record(kyo, criteria=["the CLI changes"])
    _declare_touches(kyo, git_project, record, "src/cli/main.py")
    _branch(git_project, "kyo-600", {"src/cli/main.py": "y = 2\n"})

    _git(git_project, "checkout", "-q", "kyo-600")
    preview = kyo("docs", "preview", "--against", base, "--json")
    assert preview.code == 0, preview
    previewed = {(row["doc_path"], row["section"]) for row in preview.json["obligations"]}
    assert previewed == {("RECX.yaml", "module core"), ("RECX.yaml", "module cli")}
    _git(git_project, "checkout", "-q", "main")

    dispatch_id = kyo("dispatch", record, "--branch", "kyo-600", "--json").json["dispatch_id"]
    verdict = {
        "doc_path": "RECX.yaml",
        "section": "module cli",
        "verdict": "not-affected",
        "method": "read the module entry against the diff",
        "reason": "the entrypoints are unchanged",
    }
    reported = _verdicts_report(kyo, git_project, dispatch_id, record, [verdict])
    assert reported.code == 0, reported
    raised = {
        (row["doc_path"], row["section"]): row["id"]
        for row in reported.json["docs_obligations"]["raised"]
    }
    assert set(raised) == previewed

    verify_criterion(kyo, f"{record}.c1")
    refused = kyo("move", record, "done")
    assert refused.code == 2, refused
    assert "module cli" in refused.err and "RECX.yaml" in refused.err

    applied = kyo("docs", "settle", "--from-report", dispatch_id, "--json")
    assert applied.code == 0, applied
    assert applied.json["settled"] == 1
    still = kyo("move", record, "done")
    assert still.code == 2, still
    assert "module core" in still.err and "module cli" not in still.err

    settled = kyo(
        "docs", "settle", raised[("RECX.yaml", "module core")],
        "--verdict", "updated", "--method", "rewrote the module summary",
    )
    assert settled.code == 0, settled
    moved = kyo("move", record, "done")
    assert moved.code == 0, moved


def test_c5_a_decision_naming_a_module_section_raises_a_standing_obligation(
    kyo, project: Path, write_file
) -> None:
    _recx_project_files(project)
    decided = kyo(
        "new", "decision", "--title", "The validator takes a new flag",
        "--evidence-file", write_file("words.md", "The maintainer, today:\n\"yes\"\n"),
        "--json",
    )
    assert decided.code == 0, decided
    record = decided.json["record_id"]
    refs = write_file("refs.txt", "RECX.yaml#module cli\nRECX.yaml\n")
    edited = kyo("edit", record, "--doc-refs-file", refs, "--json")
    assert edited.code == 0, edited
    standing = [
        (o["doc_path"], o["section"], o["cause"])
        for o in kyo("show", record, "--json").json["docs_obligations"]
        if o["dispatch_id"] is None
    ]
    assert standing == [
        ("RECX.yaml", "module cli", "doc-refs"),
        ("RECX.yaml", PREAMBLE, "doc-refs"),
    ]


def test_c5_a_doc_ref_naming_no_module_is_still_refused(kyo, project: Path, write_file) -> None:
    _recx_project_files(project)
    record = new_record(kyo, criteria=["a"])
    refs = write_file("refs.txt", "RECX.yaml#module nonesuch\n")
    refused = kyo("edit", record, "--doc-refs-file", refs)
    assert refused.code == 1, refused
    assert "module nonesuch" in refused.err


def _recx_project_files(project: Path) -> None:
    _documents_yml(project, RECX_SET)
    _write(project, "RECX.yaml", MANIFEST)


# ------------------------------------------------------------------ c6: no recx, no change


def test_c6_no_kyosei_module_imports_recx_cli_for_governance() -> None:
    """``prior_art`` is the one module that imports recx_cli (the prior-art search, which
    is not governance); nothing else may."""
    importers = sorted(
        path.name
        for path in (REPO_ROOT / "src" / "kyosei").rglob("*.py")
        if any(
            line.strip().startswith(("import recx_cli", "from recx_cli"))
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    )
    assert importers in ([], ["prior_art.py"]), importers


def test_c6_governance_runs_where_recx_cli_cannot_be_imported(tmp_path: Path) -> None:
    script = (
        "import sys\n"
        "sys.modules['recx_cli'] = None\n"
        "from kyosei import docs_recx\n"
        f"text = open({str(RECX_FIXTURE)!r}).read()\n"
        "read = docs_recx.manifest_sections(text)\n"
        "assert len(read.sections) == 9, read\n"
        "assert 'recx_cli' not in [m for m in sys.modules if sys.modules[m] is not None]\n"
        "print('ok')\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=tmp_path
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "ok"


def test_c6_without_the_format_a_recx_yaml_reads_exactly_as_before(project: Path) -> None:
    """A markdown set whose glob happens to match RECX.yaml reads it as markdown, the
    way every build before this one did, and the docs check says nothing new."""
    _documents_yml(
        project, "documents:\n  everything:\n    summary: All.\n    globs: ['*.yaml', '*.md']\n"
    )
    _write(project, "RECX.yaml", MANIFEST)
    documents_in = docs_parse.document_set(project / "kyosei")
    assert documents_in.recx_manifest is None
    (document,) = [
        d
        for d in docs_obligations.documents(project, documents_in=documents_in)
        if d.path == "RECX.yaml"
    ]
    assert [s.heading for s in document.sections] == [
        s.heading for s in docs_parse._sections(MANIFEST)
    ]
    assert document.format == "markdown"
    checked = docs_check.check_docs(project, documents_in=documents_in)
    assert "recx manifest" not in checked.says


# ------------------------------------------------------------------ c7: the reference


def test_c7_the_reference_topic_describes_the_format() -> None:
    text = (REPO_ROOT / "docs/reference/topics/docs-governance.md").read_text(encoding="utf-8")
    for phrase in (
        "format: recx-manifest",
        "`module <id>`",
        "`evidence <id>`",
        "(preamble)",
        "prefix",
        "project_governs",
        "evidence_governs",
        "module_governs",
    ):
        assert phrase in text, phrase


# ------------------------------------------------------------------ c8: narrowed targets


NARROWED = RECX_SET + (
    "    recx_manifest:\n"
    "      module_governs:\n"
    "        core: [src/cli/main.py]\n"
)


def test_c8_a_narrowed_module_raises_on_its_targets_and_not_its_path(git_project: Path) -> None:
    base = _recx_project(git_project, documents=NARROWED)
    _branch(git_project, "inside", {"src/app.py": "x = 2\n"})
    assert _raised(git_project, base, "inside") == {}, "src/app.py is under core's path only"
    _branch(git_project, "target", {"src/cli/main.py": "y = 2\n"})
    # core raises through its narrowed target; cli, not named, keeps its path.
    assert _raised(git_project, base, "target") == {
        "module core": ("src/cli/main.py",),
        "module cli": ("src/cli/main.py",),
    }


def test_c8_an_unknown_id_and_a_missing_target_are_reported_and_clear(
    kyo, git_project: Path
) -> None:
    documents = RECX_SET + (
        "    recx_manifest:\n"
        "      module_governs:\n"
        "        nonesuch: [src/app.py]\n"
        "        cli: [src/cli/gone.py]\n"
    )
    _recx_project(git_project, documents=documents)
    before = (git_project / "RECX.yaml").read_bytes()
    result = kyo("docs", "check")
    assert result.code == 3, result
    out = result.out + result.err
    assert "module_governs.nonesuch" in out and "has no module with that id" in out
    assert "src/cli/gone.py" in out
    assert (git_project / "RECX.yaml").read_bytes() == before, "nothing is written to RECX.yaml"

    _documents_yml(git_project, NARROWED)
    cleared = kyo("docs", "check")
    assert cleared.code == 0, cleared
    assert (git_project / "RECX.yaml").read_bytes() == before


def test_c8_a_module_path_naming_nothing_is_an_unresolved_target(kyo, git_project: Path) -> None:
    _recx_project(git_project, manifest=MANIFEST.replace("path: action.yml", "path: gone/"))
    result = kyo("docs", "check")
    assert result.code == 3, result
    assert "'gone/': a module path in RECX.yaml" in result.out + result.err
    _write(git_project, "gone/thing.txt", "here now\n")
    assert kyo("docs", "check").code == 0


# ------------------------------------------------------------------ KYO-614: Recx-Checked


NO_RECX_SET = """\
documents:
  guide:
    summary: The guide.
    globs: ['docs/*.md']
"""

TRAILER_SENTENCE = "put a git trailer naming that module id on your branch's last commit"


def _brief_of(kyo, dispatch_id: str) -> str:
    shown = kyo("show", dispatch_id, "--json")
    assert shown.code == 0, shown
    return shown.json["brief"]


def test_kyo614_c1_the_brief_states_the_trailer_only_where_a_recx_set_is_declared(
    kyo, git_project: Path
) -> None:
    _recx_project(git_project)
    record = new_record(kyo, criteria=["the CLI changes"])
    _declare_touches(kyo, git_project, record, "src/cli/main.py")
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-614", "--json").json["dispatch_id"]
    brief = _brief_of(kyo, dispatch_id)
    assert TRAILER_SENTENCE in brief
    assert "`Recx-Checked: core, cli`" in brief
    assert "recx-cli 0.1.1" in brief
    assert "RECX.yaml [module <id>]" in brief
    # Beside the docs_verdicts[] paragraph, not somewhere else in the brief.
    verdicts_at = brief.index("your report may carry **`docs_verdicts[]`**")
    trailer_at = brief.index(TRAILER_SENTENCE)
    assert verdicts_at < trailer_at < brief.index("If you are resumed on this dispatch")


def test_kyo614_c1_a_brief_without_a_recx_set_carries_none_of_it(
    kyo, git_project: Path
) -> None:
    # The same project and the same RECX.yaml, with no recx-manifest set declared.
    _recx_project(git_project, documents=NO_RECX_SET)
    record = new_record(kyo, criteria=["the CLI changes"])
    _declare_touches(kyo, git_project, record, "src/cli/main.py")
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-614", "--json").json["dispatch_id"]
    brief = _brief_of(kyo, dispatch_id)
    assert "docs_verdicts[]" in brief
    assert "Recx-Checked" not in brief
    assert TRAILER_SENTENCE not in brief
    assert "recx-cli" not in brief


def _trailer_branch(project: Path, messages: list[str]) -> None:
    """``kyo-600`` off HEAD with one commit per message, each editing src/cli/main.py."""
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", "kyo-600")
    for n, message in enumerate(messages):
        _write(project, "src/cli/main.py", f"y = {n + 2}\n")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", message)
    _git(project, "checkout", "-q", base)


def _module_verdict(module_id: str, verdict: str = "not-affected") -> dict:
    return {
        "doc_path": "RECX.yaml",
        "section": f"module {module_id}",
        "verdict": verdict,
        "method": "read the module entry against the diff",
        "reason": "the entry still describes the code",
    }


def _report_with_trailers(
    kyo, project: Path, messages: list[str], answered: list[str], documents: str = RECX_SET
):
    _recx_project(project, documents=documents)
    record = new_record(kyo, criteria=["the CLI changes"])
    _declare_touches(kyo, project, record, "src/cli/main.py")
    _trailer_branch(project, messages)
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-600", "--json").json["dispatch_id"]
    verdicts = [_module_verdict(module_id) for module_id in answered]
    return _verdicts_report(kyo, project, dispatch_id, record, verdicts)


@pytest.mark.parametrize(
    "message",
    [
        "work\n\nRecx-Checked: core, cli\n",
        "work\n\nrecx-checked: core\nRECX-CHECKED: cli\n",
        "work\n\nRecx-Checked: cli core\n",
    ],
    ids=["comma-separated", "repeated-any-case", "space-separated"],
)
def test_kyo614_c2_a_tip_trailer_naming_every_answered_module_warns_nothing(
    kyo, git_project: Path, message: str
) -> None:
    reported = _report_with_trailers(kyo, git_project, [message], ["core", "cli"])
    assert reported.code == 0, reported
    assert reported.json["recx_trailers"]["unchecked"] == []
    assert reported.json["recx_trailers"]["answered"] == ["core", "cli"]
    assert "recx trailer:" not in kyo_text_of(kyo, git_project, reported)


def kyo_text_of(kyo, project: Path, reported) -> str:
    """The text rendering of the same ingest: re-run without --json as an amendment."""
    report_id = reported.json["report_id"]
    path = project / "report.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["amends"] = report_id
    path.write_text(json.dumps(document), encoding="utf-8")
    text = kyo("report", reported.json["dispatch_id"], "--file", str(path))
    assert text.code == 0, text
    return text.out


def test_kyo614_c2_an_unnamed_module_warns_and_never_refuses(kyo, git_project: Path) -> None:
    reported = _report_with_trailers(
        kyo, git_project, ["work\n\nRecx-Checked: cli\n"], ["core", "cli"]
    )
    assert reported.code == 0, reported
    assert reported.json["recx_trailers"]["unchecked"] == ["core"]
    text = kyo_text_of(kyo, git_project, reported)
    assert "recx trailer: 1 module(s)" in text
    assert "warning: module core" in text
    assert "warning: module cli" not in text


def test_kyo614_c2_a_trailer_only_on_an_earlier_commit_warns(kyo, git_project: Path) -> None:
    reported = _report_with_trailers(
        kyo,
        git_project,
        ["first\n\nRecx-Checked: core, cli\n", "after a stop hook sent it back"],
        ["core", "cli"],
    )
    assert reported.code == 0, reported
    assert reported.json["recx_trailers"]["unchecked"] == ["core", "cli"]
    text = kyo_text_of(kyo, git_project, reported)
    assert "warning: module core" in text and "warning: module cli" in text
    assert "a trailer on an earlier commit does not cover" in text


def test_kyo614_c2_the_trailer_in_the_body_is_not_a_trailer(kyo, git_project: Path) -> None:
    # Followed by a prose paragraph, the line is body text, which git does not parse.
    reported = _report_with_trailers(
        kyo, git_project, ["work\n\nRecx-Checked: core, cli\n\nMore prose after it.\n"],
        ["core", "cli"],
    )
    assert reported.code == 0, reported
    assert reported.json["recx_trailers"]["unchecked"] == ["core", "cli"]


def test_kyo614_c2_without_a_recx_set_kyo_report_is_unchanged(kyo, git_project: Path) -> None:
    reported = _report_with_trailers(
        kyo, git_project, ["work"], ["core"],
        documents=NO_RECX_SET,
    )
    assert reported.code == 0, reported
    assert "recx_trailers" not in reported.json
    assert "recx trailer" not in kyo_text_of(kyo, git_project, reported)


def test_kyo614_c2_a_report_answering_no_module_warns_nothing(kyo, git_project: Path) -> None:
    reported = _report_with_trailers(kyo, git_project, ["work"], [])
    assert reported.code == 0, reported
    assert reported.json["recx_trailers"]["answered"] == []
    assert "recx trailer" not in kyo_text_of(kyo, git_project, reported)


def test_kyo614_c3_the_reference_topic_describes_the_trailer() -> None:
    text = (REPO_ROOT / "docs/reference/topics/docs-governance.md").read_text(encoding="utf-8")
    section = text[text.index("RECX.yaml as a governed document"):]
    assert "recx's own drift reminder is\nunaffected" not in section
    for phrase in (
        "Recx-Checked: <module-id>[, <module-id>...]",
        "recx-cli 0.1.1",
        "SubagentStop",
        "case-insensitively",
        "later commits and uncommitted changes are still reminded",
    ):
        assert phrase in " ".join(section.split()), phrase
