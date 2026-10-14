"""KYO-149.c4 — nothing a worker is told to open may be a path only this repo has.

The defect this file exists to stop recurring: every dispatch brief Kyosei has ever
generated told its worker to write the report "per ``templates/report.schema.json``",
and a worker-audience ``enforced-by`` line said the same. ``templates/`` is a
directory of the SOURCE tree. ``pyproject.toml`` ships ``defaults/*`` and
``defaults/hooks/*`` and nothing else, so in every consumer project that has ever
existed that citation named a file the reader could not open — in ``enforced-by``,
the one field whose entire job is to name a mechanism the reader can go and check.

It was invisible from inside this repository for the plainest reason available:
this repository *is* the source tree, so the path resolved here, on every dispatch
this store has ever run. "We dogfood the tool" is this project's main evidence that
its surfaces work, and it is structurally blind to this whole class.

**Why this is not a grep for ``templates/``.** A fixed-string check would pass on
the day someone cited ``docs/design/charter.md`` or ``tests/conftest.py`` instead,
which is the same defect wearing a different path. So the check asks the real
question — *would an installed consumer have this?* — and takes its answer from the
authority on that, ``[tool.setuptools.package-data]``, rather than from a list
maintained here. Add a file to the package and this test starts allowing citations
of it, with no edit; drop one and it starts refusing them.

**The three-way classification**, all of it derived:

``a path in the reader's own project``
    ``kyosei/export``, ``kyosei/kyosei.db``, ``.claude/settings.json``, the
    project's own ``CLAUDE.md`` — not files of this tool at all, even though this
    repository happens to have all of them, being a Kyosei project itself. The
    directories come from the constants that define them (:data:`kyosei.config.KYOSEI_DIR`,
    :data:`kyosei.scaffold.IDEAS_DIR`, :data:`kyosei.install_hooks.CLAUDE_SETTINGS_REL`),
    so they cannot drift from what ``kyo init`` and ``kyo install-hooks`` actually
    create. :data:`PROJECT_OWNED_FILES` is the one genuinely hand-kept part of this
    check, and its risk is stated there rather than buried.

``a path an installed consumer has``
    A module under ``src/kyosei/``, or a data file matched by a package-data
    pattern. ``src/kyosei/cmd_guard.py`` and ``report.schema.json`` are both fine.

``a path only this repository has``
    Anything else tracked here: ``templates/``, ``docs/``, ``apps/``,
    ``pyproject.toml``, and ``tests/``. Citing one of these to a worker is the
    defect — *unless* the citation is marked with :data:`UPSTREAM_MARKER`.

**About that marker.** Two worker-audience rules cite ``tests/test_procedures.py``,
and they are not making the same mistake: they name where a guarantee is enforced
in Kyosei's own repository, not a file the reader was invited to open. Deleting
that would remove true and useful information; silently exempting ``tests/`` would
be the hand-list this check was built to avoid, and would leave the *reader* still
unable to tell "I should be able to open this" from "I cannot". So the distinction
is written into the prose the reader actually sees — the citation says, in words,
that the file is in Kyosei's own repository — and this check reads the same words.
It is a real escape hatch and could be abused; what it cannot be is invisible,
which is the property the old broken citation lacked.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path

import pytest

from kyosei import procedures
from kyosei.cli import build_parser
from kyosei.config import KYOSEI_DIR
from kyosei.install_hooks import CLAUDE_SETTINGS_REL
from kyosei.scaffold import IDEAS_DIR
from tests.prose_text import REPO_ROOT

#: The words that mark a citation as pointing into Kyosei's own repository rather
#: than into the reader's project. Written for the reader first: a worker who meets
#: it knows immediately that the file is not theirs to open, which is precisely what
#: a bare ``tests/test_procedures.py`` failed to tell them.
UPSTREAM_MARKER = "in kyosei's own repository"

#: The store, the intake layer and the harness config: directories a consumer
#: project has too, so a citation of one is a citation of the reader's own tree.
#: Taken from the constants that create them rather than spelled a second time
#: (law 3) — if ``kyo init`` ever renames the store, this follows.
PROJECT_OWNED_DIRS = (
    f"{KYOSEI_DIR}/",
    f"{IDEAS_DIR}/",
    f"{CLAUDE_SETTINGS_REL.parts[0]}/",
)

#: Root prose every project has a copy of, this repository included. **This is the
#: one hand-kept list in the check, and it is the one place a mistake could hide a
#: real defect**: if Kyosei's own ``README.md`` were cited to a worker meaning the
#: *tool's* README, this would wave it through as though it meant theirs. It is two
#: entries and it earns them — brief generation's worktree-tooling block says "no
#: matter what the project's README, CLAUDE.md or Makefile say", which is a true and
#: useful sentence about the reader's own repository. There is no derivation
#: available here: the same filename means the tool in one sentence and the reader's
#: project in the next, and only prose distinguishes them. ``pyproject.toml`` is the
#: third (KYO-514): `kyo onboard`'s prompt and `--check` read the reader's own to find
#: how a fresh checkout is provisioned; no consumer surface cites Kyosei's.
PROJECT_OWNED_FILES = ("README.md", "CLAUDE.md", "pyproject.toml")

#: Anything with a slash and a plausible file/dir shape, or a bare file with one of
#: this repo's source extensions. Deliberately generous: over-matching costs a
#: classification step, under-matching costs the whole check.
PATH_RE = re.compile(
    r"(?<![\w/.-])"
    r"((?:[\w.-]+/)+[\w.-]*[\w-]|[\w-]+\.(?:py|md|json|yml|yaml|toml|db|ts|cfg|ini))"
    r"(?![\w])"
)


#: Markup to drop before looking for a marker beside a path. Deliberately NOT
#: :func:`tests.prose_text.flatten_prose`, and the difference is the whole reason
#: this exists: that reader strips ``_`` as emphasis, which is correct for prose and
#: silently turns ``tests/test_procedures.py`` into ``tests/testprocedures.py``. A
#: path-matching check built on it would report "no marker found" on a marker that
#: is plainly there — the same false-negative shape as the hard-wrapped grep that
#: let KYO-35 be declared already-fixed. Backticks and asterisks go; underscores stay.
_PATH_MARKUP_RE = re.compile(r"[`*]")


def flatten_for_paths(text: str) -> str:
    """One whitespace-normalised, lowercased line with code-span markup removed."""
    return " ".join(_PATH_MARKUP_RE.sub("", text).split()).lower()


@lru_cache(maxsize=1)
def _tracked_files() -> frozenset[str]:
    """Every file git tracks here — the definition of "a file of this repository".

    Cached since KYO-156. KYO-149 called :func:`unreachable_citations` on two texts, so
    a ``git ls-files`` per call cost nothing; KYO-156 calls it on every string literal
    in the package, every node of the argparse tree and every packaged data file — a
    few thousand texts, at which point the uncached form takes minutes rather than a
    second. The cache is what makes quantifying over the surfaces affordable at all.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return frozenset(name for name in out.split("\0") if name)


def _package_data_patterns() -> tuple[str, ...]:
    """``[tool.setuptools.package-data]`` — the authority on what ships — as patterns
    relative to ``src/kyosei/``. A subpackage's key (``kyosei.harness``, KYO-593) holds
    patterns relative to its own directory, so they are prefixed with it here."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    table = config["tool"]["setuptools"]["package-data"]
    patterns = list(table["kyosei"])
    assert patterns, "package-data declares nothing; this check would pass vacuously"
    for package, extra in table.items():
        if package.startswith("kyosei."):
            prefix = "/".join(package.split(".")[1:]) + "/"
            patterns += [prefix + pattern for pattern in extra]
    return tuple(patterns)


@lru_cache(maxsize=1)
def _installed_consumer_files() -> frozenset[str]:
    """Repo-relative paths an installed consumer actually has.

    A wheel contains ``src/kyosei/**/*.py`` (setuptools installs the package) plus
    every file under ``src/kyosei/`` matched by a package-data pattern, and nothing
    else — measured against a real build in the KYO-149 dispatch, where adding
    ``"../../templates/*"`` to package-data produced a wheel that exited 0, warned
    about nothing, and contained no ``templates/`` file at all.
    """
    prefix = "src/kyosei/"
    patterns = _package_data_patterns()
    shipped = set()
    for path in _tracked_files():
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix) :]
        if relative.endswith(".py") or any(fnmatch(relative, p) for p in patterns):
            shipped.add(path)
    assert f"{prefix}report.schema.json" in shipped, (
        "the report schema is not shipped; `kyo report-schema` would print nothing "
        "in a consumer project, which is the whole of KYO-149"
    )
    return frozenset(shipped)


#: A parenthetical group in flattened prose, captured non-nested (this repo's
#: markers never nest parens, and KYO-165's near-miss fixture does not either).
_PAREN_GROUP_RE = re.compile(r"\(([^()]*)\)")


def _near_miss_hint(candidate: str, flat: str) -> bool:
    """Does some parenthetical touching ``candidate`` already name Kyosei,
    just not in :data:`UPSTREAM_MARKER`'s exact words?

    KYO-165.c3: ``src/kyosei/defaults/skill.md`` once read
    ``(`docs/design/charter.md` in the Kyosei source repo)`` — a parenthetical
    that told the reader exactly what the canonical marker tells them, wrapping
    the path rather than following it. It was flagged with the same message as
    a citation that said nothing about Kyosei at all, as though the author had
    tried nothing. This is the fact that message needs to tell those two cases
    apart: the group is read whichever side of the path it opens on, so a
    marker-shaped parenthetical AND this wrapping shape both count.

    Deliberately not itself an escape hatch (KYO-165.c4): a near miss still
    fails :func:`unreachable_paths` — this only changes what the message says
    about *why*, and the fixed phrase is still the only string that clears it.
    """
    for group in _PAREN_GROUP_RE.findall(flat):
        if candidate in group and "kyosei" in group and UPSTREAM_MARKER not in group:
            return True
    return False


def unreachable_citations(text: str) -> list[str]:
    """Every path in ``text`` that only this repository has, unmarked, as a message.

    ``text`` is prose a reader reads — an ``enforced-by`` line, generated brief output,
    or (since KYO-156) any other surface :func:`consumer_surfaces` yields. The empty
    list is the passing answer.

    KYO-165.c3: an author who already qualified their citation with a parenthetical
    naming Kyosei — just not in the exact phrase this check reads — is told what is
    missing from the phrase they already wrote, rather than sent back to "cite a
    command ... or mark it" as though they had said nothing.
    """
    flat = flatten_for_paths(text)
    messages = []
    for path in unreachable_paths(text):
        candidate = path.rstrip("/").lower()
        if _near_miss_hint(candidate, flat):
            messages.append(
                f"{path!r} is nearly marked as upstream — a parenthetical beside it "
                f"already names Kyosei, but not in the exact phrase this check reads; "
                f"the exact phrase is '({UPSTREAM_MARKER})'"
            )
        else:
            messages.append(
                f"{path!r} exists in this source tree but no installed consumer has it; "
                f"cite a command that prints it, or mark it '({UPSTREAM_MARKER})'"
            )
    return messages


def unreachable_paths(text: str) -> list[str]:
    """The bare paths behind :func:`unreachable_citations`.

    Split out by KYO-156: quantifying over thousands of surfaces means grouping
    findings BY PATH, and recovering a path by picking apart a rendered message is the
    kind of round-trip that works until someone rewords the message.
    """
    tracked = _tracked_files()
    installed = _installed_consumer_files()
    tracked_dirs = {name.rsplit("/", 1)[0] for name in tracked if "/" in name}
    # Flattened in KYO-35's spirit: markup stripped and line breaks joined, so a
    # marker that got hard-wrapped away from its path is still found. A fixed-string
    # search on raw lines would repeat exactly the mistake that let KYO-35 be
    # declared already-fixed on the strength of a grep this repo's wrapping defeated.
    flat = flatten_for_paths(text)

    problems = []
    for cited in dict.fromkeys(PATH_RE.findall(text)):
        candidate = cited.rstrip("/")
        if candidate.startswith(PROJECT_OWNED_DIRS) or candidate in PROJECT_OWNED_FILES:
            continue  # the reader's own store, intake layer, harness config or prose
        if candidate not in tracked and candidate not in tracked_dirs:
            continue  # not a file of this repository at all
        if candidate in installed:
            continue  # shipped: the reader can open it
        # The marker opens the parenthetical that follows the path; what comes after
        # it inside those brackets is the author's to write, and the ledger uses that
        # room to say the useful part out loud ("not a file in your project, and not
        # one you can open from here"). Requiring a closing bracket immediately would
        # forbid exactly the sentence that makes the marker worth having.
        if f"{candidate.lower()} ({UPSTREAM_MARKER}" in flat:
            continue  # marked, in the reader's own view, as upstream
        problems.append(cited)
    return problems


# ------------------------------------------------- the two surfaces a worker reads


def test_worker_audience_enforced_by_lines_cite_only_reachable_paths() -> None:
    """The half the reporter argued matters most.

    ``enforced-by`` is the one field in this system whose purpose is to name
    something a reader can go and check — which is why the rules ledger draws such
    careful distinctions between ``mechanism``, ``mechanism, partial`` and
    ``aspirational``. A citation the reader cannot open converts that field into
    decoration for exactly the audience it was written for. Worse, the failure is
    silent *and* misattributed: a worker who cannot find the file has no way to tell
    whether it is missing or whether they are looking in the wrong place, so it
    reads as their own mistake.
    """
    text = (REPO_ROOT / "src" / "kyosei" / "defaults" / "procedures.md").read_text(
        encoding="utf-8"
    )
    rules = procedures.worker_rules(text)
    assert rules, "no worker-audience rules parsed; this check would pass vacuously"

    problems = []
    for rule in rules:
        for problem in unreachable_citations(rule.enforced_by):
            problems.append(f"{rule.title}: enforced-by cites {problem}")
    assert not problems, "\n".join(problems)


def test_generated_brief_cites_only_reachable_paths(kyo, git_project) -> None:
    """The Report section, and every worker rule injected into the brief.

    Generated rather than read off ``brief.py``'s string literals on purpose: the
    brief is assembled from several sources — the Report section, the standing-rules
    block, the worktree-tooling block — and a check that read only one of them would
    have missed the second copy of this very sentence.
    """
    result = kyo("new", "bug", "--title", "a bug to dispatch", "--json")
    record = result.json["record"]["id"]
    dispatched = kyo("--full", "dispatch", record, "--json")
    brief = dispatched.json["record"]["dispatches"][0]["brief"]
    assert "## Report" in brief

    problems = unreachable_citations(brief)
    assert not problems, "\n".join(problems)


def test_the_brief_sends_the_worker_to_the_command(kyo, git_project) -> None:
    """KYO-149.c2, stated positively — the check above only forbids the bad path."""
    record = kyo("new", "bug", "--title", "another bug", "--json").json["record"]["id"]
    brief = kyo("--full", "dispatch", record, "--json").json["record"]["dispatches"][0]["brief"]
    assert "`kyo report-schema`" in brief
    assert "templates/report.schema.json" not in brief


# ----------------------------------------------------------- the check has teeth


@pytest.mark.parametrize(
    "cited",
    [
        "templates/brief-template.md",
        "templates/hooks/README.md",
        "tests/conftest.py",
        "docs/design/charter.md",
        # Not pyproject.toml: a Python consumer has its own, which `kyo onboard` reads
        # (KYO-514), so it is in PROJECT_OWNED_FILES like README.md and CLAUDE.md.
        "build_backend/kyosei_build.py",
    ],
)
def test_an_unshipped_citation_is_caught(cited: str) -> None:
    """Every one of these is a real file here and absent from every consumer.

    Parametrised across five directories rather than asserted once on
    ``templates/``, because a check that only knows the path that produced the bug
    would pass on the day the next one is written.
    """
    assert unreachable_citations(f"mechanism — see `{cited}` for what it does")


def test_a_shipped_or_project_owned_citation_is_allowed() -> None:
    reachable = (
        "`src/kyosei/cmd_guard.py`",  # a module: installed with the package
        "`src/kyosei/defaults/procedures.md`",  # matched by `defaults/*`
        "`src/kyosei/report.schema.json`",  # matched by `report.schema.json`
        "`kyosei/export/`",  # the reader's own store
        "`ideas/`",  # the reader's own intake layer
        "`.claude/settings.json`",  # the reader's own harness config
        "`CLAUDE.md`",  # the reader's own project instructions
    )
    for text in reachable:
        assert not unreachable_citations(text), text


def test_the_upstream_marker_is_the_only_escape_and_it_is_visible() -> None:
    """Marked passes, unmarked fails, and the marker is prose the worker reads."""
    bare = "`tests/test_procedures.py` checks the flag list"
    marked = f"`tests/test_procedures.py` ({UPSTREAM_MARKER}) checks the flag list"
    assert unreachable_citations(bare)
    assert not unreachable_citations(marked)
    # The marker may be elaborated on — that is the point of it — but the elaboration
    # has to follow the marker, not replace it.
    elaborated = (
        f"`tests/test_procedures.py` ({UPSTREAM_MARKER} — not a file in your "
        "project) checks the flag list"
    )
    assert not unreachable_citations(elaborated)
    assert unreachable_citations("`tests/test_procedures.py` (in your project) checks")
    # It exempts the file it names and nothing else standing beside it.
    assert unreachable_citations(f"{marked}, and see `docs/design/charter.md`")
    # And it does not travel: a marker beside one path does not cover another.
    assert unreachable_citations(f"{marked}, unlike `templates/brief-template.md`")


def test_a_near_miss_that_already_names_kyosei_gets_a_different_message() -> None:
    """KYO-165.c3, on the real historical defect. ``src/kyosei/defaults/skill.md``
    once read ``(`docs/design/charter.md` in the Kyosei source repo)`` — a
    parenthetical that told the reader exactly what the canonical marker tells
    them, just not in its exact words. It was flagged with the same message as a
    citation that said nothing about Kyosei at all, as though the author had
    tried nothing. The near miss must read differently from the bare miss, and
    name the exact phrase.
    """
    near_miss = "see (`docs/design/charter.md` in the Kyosei source repo) for the reasoning"
    bare = "see `docs/design/charter.md` for the reasoning"

    near_miss_messages = unreachable_citations(near_miss)
    bare_messages = unreachable_citations(bare)

    assert near_miss_messages and bare_messages
    assert near_miss_messages != bare_messages
    assert "nearly marked" in near_miss_messages[0]
    assert f"({UPSTREAM_MARKER})" in near_miss_messages[0]
    assert "nearly marked" not in bare_messages[0]


def test_the_near_miss_message_does_not_loosen_what_actually_clears_the_check() -> None:
    """KYO-165.c4: the fixed phrase stays fixed. A near miss is a better message,
    not a second escape hatch — it must still fail, and only the exact phrase
    (asserted already by :func:`test_the_upstream_marker_is_the_only_escape_and_it_is_visible`)
    clears it."""
    near_miss = "see (`docs/design/charter.md` in the Kyosei source repo) for the reasoning"
    assert unreachable_citations(near_miss), "a near miss must not itself pass"
    exactly_marked = f"`docs/design/charter.md` ({UPSTREAM_MARKER})"
    assert not unreachable_citations(exactly_marked)


# =====================================================================================
# KYO-156 — the same question, asked of every surface instead of the two that prompted it
# =====================================================================================
#
# KYO-149 fixed two citations and checked two surfaces: worker-audience ``enforced-by``
# lines, and generated brief text. Both tests above are still the right tests. What was
# wrong was the SCOPE.
#
# `kyo reference` prints a header telling its reader to "regenerate it with `kyo
# reference --output docs/reference/cli.md`" and citing
# `tests/test_kyo138_command_reference.py` as the reason a stale copy cannot ship. All
# three paths exist only here. A consumer was pointed at a directory their project has
# no reason to have and at a test file they cannot open, offered as the guarantee for a
# copy it does not guard. That is precisely the KYO-149 defect — and KYO-149's own check
# could not see it, because a command's stdout is neither an ``enforced-by`` line nor
# brief-generated text.
#
# **The fix that guarantees a fourth case is adding a third case.** So this section does
# not add ``kyo reference`` to a list of surfaces. It asks what a surface IS, and derives
# the set:
#
#   1. ``argparse:`` — every description, epilog, help string and metavar reachable by
#      walking :func:`kyosei.cli.build_parser`. Authority: the parser itself. A new
#      command or flag is covered the moment it is registered, with no edit here.
#   2. ``package-data:`` — every non-Python file that ``[tool.setuptools.package-data]``
#      actually ships: the rules ledger, the skill, ``kinds.yml``, the hook templates,
#      the report schema. Authority: pyproject, the same authority
#      :func:`_installed_consumer_files` already trusts. A new packaged document is
#      covered by adding it to the wheel, which is the act that makes it a surface.
#   3. ``literal:`` — every string literal in the package that is not a docstring.
#      Authority: the package's own source. This is the one that makes c3 true rather
#      than aspirational: a refusal message, a generated-document header, an ``Output``
#      line and a warning are all *string literals in this package*, so a NEW emitting
#      surface cannot be written without appearing here. Nobody registers anything.
#
# Families 1 and 2 are largely subsumed by 3, and that redundancy is deliberate: 1 and 2
# check the text as ASSEMBLED (argparse collapses whitespace; a data file is prose in
# one piece), where 3 checks it as WRITTEN. A citation split across an f-string boundary
# reads differently in the two, and the marker has to survive both.
#
# Why this is not simply "grep the package for `docs/`": every path is classified by
# :func:`unreachable_citations`, which asks whether an installed consumer would have the
# file and takes its answer from package-data. The escape hatch stays the one KYO-149
# designed — :data:`UPSTREAM_MARKER`, visible in the prose the reader sees.


#: Rendered in place of an interpolation this checker cannot resolve. Deliberately
#: path-free: substituting something like ``x/y`` would manufacture citations.
_UNRESOLVED = "…"


@lru_cache(maxsize=1)
def _package_py_files() -> tuple[Path, ...]:
    """Every tracked ``.py`` under ``src/kyosei/`` — the package's own source."""
    return tuple(
        REPO_ROOT / name
        for name in sorted(_tracked_files())
        if name.startswith("src/kyosei/") and name.endswith(".py")
    )


@lru_cache(maxsize=1)
def _package_data_files() -> tuple[Path, ...]:
    """Every tracked non-Python file the wheel actually ships.

    Reuses :func:`_installed_consumer_files` rather than re-deriving the patterns, so
    "what ships" has exactly one definition in this file (law 3).
    """
    return tuple(
        REPO_ROOT / name
        for name in sorted(_installed_consumer_files())
        if not name.endswith(".py")
    )


def _render(node: ast.AST, constants: dict[str, str]) -> str | None:
    """The text of a string literal, with resolvable interpolations substituted in.

    Substitution is the point. ``kyo reference``'s header reads
    ``f"...regenerate it with `{REFERENCE_REGENERATE_COMMAND}`"``, and the path that
    misleads a consumer is inside that constant, not inside the literal. A checker that
    read only the literal parts would have declared the header clean.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = value.value
                parts.append(
                    constants.get(inner.id, _UNRESOLVED)
                    if isinstance(inner, ast.Name)
                    else _UNRESOLVED
                )
            else:
                parts.append(_UNRESOLVED)
        return "".join(parts)
    return None


def _module_level_assignments(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """``NAME = <string literal>`` at module level, in source order."""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(
                node.value, (ast.Constant, ast.JoinedStr)
            ):
                out.append((target.id, node.value))
    return out


@lru_cache(maxsize=1)
def _package_constants() -> dict[str, str]:
    """Package-wide ``NAME -> value`` for module-level string constants.

    Two passes, because constants are built from constants:
    ``REFERENCE_PATH = "docs/reference/cli.md"`` then
    ``REFERENCE_REGENERATE_COMMAND = f"kyo reference --output {REFERENCE_PATH}"``.
    One pass would leave the second unresolved and the header unchecked.

    Names are pooled across modules rather than resolved per-module, because that is how
    they are actually used — ``cmd_views`` imports ``cli``'s constants. A name defined
    twice with different values resolves to one of them; the cost is a slightly wrong
    string in a path-detection check, which is why this is acceptable and stated rather
    than defended.
    """
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in _package_py_files()}
    constants: dict[str, str] = {}
    for _ in range(2):
        for tree in trees.values():
            for name, node in _module_level_assignments(tree):
                rendered = _render(node, constants)
                if rendered is not None and _UNRESOLVED not in rendered:
                    constants[name] = rendered
    return constants


@lru_cache(maxsize=1)
def _interpolated_names() -> frozenset[str]:
    """Every name the package interpolates into an f-string anywhere.

    Used to decide where a constant gets CHECKED. A constant that flows into prose is
    checked at the point of use — which is the only place the :data:`UPSTREAM_MARKER`
    can meaningfully live, since a marker belongs in a sentence a reader reads, not
    inside a path-valued constant. A constant that flows nowhere (``EXPORT_README``,
    written verbatim into a consumer's store; ``DDL``) is checked at its definition,
    because nothing else will check it.

    The alternative — skipping every module-level constant — would have silently
    exempted ``EXPORT_README``, which is real prose shipped into a real consumer's
    project. The alternative in the other direction — checking definitions too — flags
    ``REFERENCE_PATH`` for being a path, which is its whole job.
    """
    names = set()
    for path in _package_py_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name):
                names.add(node.value.id)
    return frozenset(names)


def _literal_surfaces() -> list[tuple[str, str]]:
    """``(label, text)`` for every non-docstring string literal in the package."""
    constants = _package_constants()
    interpolated = _interpolated_names()
    surfaces = []
    for path in _package_py_files():
        relative = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))

        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            holds_docstring = isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            if holds_docstring and body and isinstance(body[0], ast.Expr):
                first = body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    docstrings.add(id(first))

        # A JoinedStr's own Constant children are checked as part of the assembled
        # string, never on their own: reporting both doubles every finding.
        inner = {
            id(child)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for child in node.values
        }

        # Definitions of constants that are interpolated somewhere: checked at use.
        checked_at_use = {
            id(node)
            for name, node in _module_level_assignments(tree)
            if name in interpolated
        }

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr)):
                continue
            if id(node) in docstrings or id(node) in inner or id(node) in checked_at_use:
                continue
            text = _render(node, constants)
            if not text:
                continue
            surfaces.append((f"literal:{relative}:{node.lineno}", text))
    return surfaces


def _argparse_surfaces() -> list[tuple[str, str]]:
    """``(label, text)`` for every piece of prose in the argparse tree."""
    surfaces = []

    def walk(parser, prog: str) -> None:
        for what, text in (("description", parser.description), ("epilog", parser.epilog)):
            if text:
                surfaces.append((f"argparse:{prog}:{what}", text))
        for action in parser._actions:
            name = "/".join(action.option_strings) or action.dest
            if action.help:
                surfaces.append((f"argparse:{prog}:{name}:help", action.help))
            if isinstance(action.metavar, str):
                surfaces.append((f"argparse:{prog}:{name}:metavar", action.metavar))
            for sub_name, sub in getattr(action, "_name_parser_map", {}).items():
                walk(sub, f"{prog} {sub_name}")

    walk(build_parser(), "kyo")
    return surfaces


def _package_data_surfaces() -> list[tuple[str, str]]:
    """``(label, text)`` for every packaged document a consumer installs."""
    surfaces = []
    for path in _package_data_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary asset carries no prose to check
        surfaces.append((f"package-data:{path.relative_to(REPO_ROOT)}", text))
    return surfaces


def consumer_surfaces() -> list[tuple[str, str]]:
    """Every surface of this package that emits prose, derived rather than listed."""
    return _argparse_surfaces() + _package_data_surfaces() + _literal_surfaces()


#: **Empty since KYO-162, and that is the whole point of it.**
#:
#: KYO-156 derived the surface set, found thirteen citations already standing in shipped
#: documents, and could not fix them: every one lived outside its declared touches, and
#: several have byte-synced twins under ``templates/`` guarded by other tests. It
#: quarantined them here and said so — a quarantine is a promise to come back. KYO-162 is
#: that return, and it emptied the set.
#:
#: **This is a quarantine, not an allow-list, and the difference is load-bearing.** It
#: never exempted a *surface* — the surfaces are derived, and anything not listed here
#: fails, which is what keeps KYO-156.c3 true. It exempted specific ``(surface, path)``
#: pairs that predated the check. The assertion below is equality, not containment, so the
#: set can only shrink: fix a citation without deleting its line and the test says so. That
#: property is why the set reached empty instead of ossifying, and it is why the empty set
#: is worth keeping rather than deleting along with its last entry — an empty frozenset
#: compared by equality is the strongest form of this check, and the next person tempted to
#: quarantine rather than fix has to write the entry, and the reason for it, in public.
#:
#: What "fixed" meant, per citation, is recorded in the files themselves rather than only
#: here (KYO-162.c2). The three legitimate resolutions, and where each was used:
#:
#: *Reworded so the reader who lacks the file loses nothing.*
#:     ``defaults/skill.md`` -> ``templates/brief-template.md`` (the worst of the thirteen:
#:     the skill every consumer's controller reads pointed at a source-tree file for the
#:     brief's shape). The skill now states the brief's sections and names ``Task`` as the
#:     one controller-written slot, and points at ``kyo dispatch --brief-out`` for a real
#:     brief rather than a description of one. Same for
#:     ``defaults/skill.md`` -> ``templates/hooks/README.md``, which was the only account a
#:     controller had of what each hook is for: the skill now says it, per event, in prose
#:     ``tests/test_hook_prose_currency.py`` checks against the packaged fragments.
#: *Marked with* :data:`UPSTREAM_MARKER`.
#:     Every provenance citation — the charter from the two hook fragments, ``kinds.yml``,
#:     ``skill.md`` and ``report.schema.json``; an earlier project's snapshot from
#:     ``procedures.md``; and the two "here is where the guarantee lives" citations of
#:     ``tests/test_skill_cli_sync.py`` and ``tests/test_hook_templates_sync.py``. These are
#:     true and useful and there is nothing to make reachable, which is the case the marker
#:     was designed for. Note that the marker and the section-citation shape
#:     ``tests/test_citation_sections_resolve.py`` reads (``<path>, "Section"``) cannot both
#:     sit on one mention — the marker requires ``(`` directly after the path. Where a file
#:     cites the same section more than once, one mention carries the marker and the others
#:     stay in citable form; where it cites it once, the marker won.
#: *Deleted, because the citation was the defect.*
#:     ``defaults/scopes.yml`` -> ``apps/desktop``. KYO-156 called this "arguably not a
#:     defect" since ``apps/desktop/*`` was an EXAMPLE of the glob dialect. On the return it
#:     reads as one: an example in a shipped config that names a real directory of the
#:     tool's own repository invites exactly the "do I have this?" question the file exists
#:     to settle, so the example now uses the same invented ``src/server`` as the commented
#:     scopes at the foot of that file. The enumeration inside ``subagent-stop.json`` of
#:     which files an upstream test covers (``templates/hooks``, ``templates/hooks/README.md``)
#:     went the same way: the fact that survives is that the test covers this comment and its
#:     documentation twin, which is said in words.
UNFIXED_PACKAGED_CITATIONS: frozenset[tuple[str, str]] = frozenset()


def observed_unreachable_citations() -> set[tuple[str, str]]:
    """Every ``(surface, path)`` pair the derived surface set currently yields."""
    return {
        (label, path)
        for label, text in consumer_surfaces()
        for path in unreachable_paths(text)
    }


def test_no_consumer_surface_cites_a_source_tree_only_path() -> None:
    """KYO-156.c1/c2 — the whole derived set, not the two cases that prompted it.

    A failure here is one of two things, and the message says which:

    *A new citation.* Some surface — a refusal message, a help string, a generated
    header, a packaged document — now names a path only this repository has. That is
    the KYO-149 defect recurring on a surface nobody had thought of, which is the
    entire reason this quantifies rather than lists.

    *A quarantined citation that got fixed.* Good news, and the fix is to delete the
    line from :data:`UNFIXED_PACKAGED_CITATIONS`. Equality rather than containment is
    what makes that set shrink instead of ossifying into an allow-list nobody prunes.
    """
    observed = observed_unreachable_citations()

    new = sorted(observed - UNFIXED_PACKAGED_CITATIONS)
    assert not new, "surfaces citing a path no installed consumer has:\n" + "\n".join(
        f"  {label}\n    cites {path!r} — cite a command that prints it, reword it, "
        f"or mark it '({UPSTREAM_MARKER})'"
        for label, path in new
    )

    fixed = sorted(UNFIXED_PACKAGED_CITATIONS - observed)
    assert not fixed, (
        "these citations are quarantined in UNFIXED_PACKAGED_CITATIONS but no longer "
        "occur — delete them from that set so it keeps shrinking:\n"
        + "\n".join(f"  {label} -> {path}" for label, path in fixed)
    )


def test_the_surface_enumeration_covers_the_package_without_a_registry() -> None:
    """KYO-156.c3 — the property that makes the check survive the next surface.

    Not "the list has the right entries" but "there is no list". Every module in the
    package, every packaged document, and every subcommand of the parser is reached by
    construction, so a new emitting surface is covered by the act of writing it. Someone
    adding ``kyo whatever`` with a refusal message that cites ``docs/`` is caught having
    registered nothing anywhere.

    This is the discipline KYO-140 applied to the desktop command list and KYO-158 to
    config carry-forward: a hand-maintained list that must agree with something the code
    already knows is a defect waiting for the next person who forgets.
    """
    labels = [label for label, _ in consumer_surfaces()]
    assert len(labels) > 1000, f"only {len(labels)} surfaces; the sweep collapsed"

    # Every package module contributes, so a new module cannot be invisible.
    literal_files = {label.split(":")[1] for label in labels if label.startswith("literal:")}
    package_modules = {
        str(path.relative_to(REPO_ROOT))
        for path in _package_py_files()
        # __init__ and other pure-docstring modules legitimately hold no checkable
        # literal; requiring one would be asserting a fact about their contents.
        if any(
            isinstance(node, (ast.Constant, ast.JoinedStr))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    }
    missing_modules = sorted(m for m in package_modules if m not in literal_files)
    assert not missing_modules, f"package modules contributing no surface: {missing_modules}"

    # Every packaged document contributes.
    data_labels = {label.split(":", 1)[1] for label in labels if label.startswith("package-data:")}
    missing_data = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in _package_data_files()
        if str(p.relative_to(REPO_ROOT)) not in data_labels
    )
    assert not missing_data, f"packaged documents contributing no surface: {missing_data}"

    # Every subcommand contributes, reached through the parser rather than named here.
    sub = _subcommand_names()
    reached = {label.split(":")[1] for label in labels if label.startswith("argparse:")}
    missing_commands = sorted(f"kyo {name}" for name in sub if f"kyo {name}" not in reached)
    assert not missing_commands, f"subcommands contributing no surface: {missing_commands}"
    assert "kyo reference" not in missing_commands and sub, "no subcommands found at all"


def _subcommand_names() -> list[str]:
    """The registered subcommands, read off the parser."""
    parser = build_parser()
    for action in parser._actions:
        mapping = getattr(action, "_name_parser_map", None)
        if mapping:
            return sorted(mapping)
    raise AssertionError("no subparser action on the root parser")


# ------------------------------------------------ KYO-156's own record, both directions


def _reference_header() -> str:
    """The paragraph `kyo reference` prints above everything else."""
    from kyosei.cli import reference_markdown

    return reference_markdown(build_parser()).split("\n\n")[1]


def test_the_reference_header_promises_a_consumer_nothing_they_lack() -> None:
    """KYO-156.c1, on the surface that produced the record.

    Asserted on the ASSEMBLED stdout rather than on ``cli.py``'s literals, because the
    header is stitched from three literals and two constants and the misleading path
    lives inside a constant. This is the check KYO-149.c4 could not express: not an
    ``enforced-by`` line, not brief-generated text — a command's own output.
    """
    assert not unreachable_paths(_reference_header()), _reference_header()


def test_the_reference_header_still_tells_a_source_tree_reader_exactly_what_to_run() -> None:
    """KYO-156.c4 — the boundary, stated as a test so it cannot be dodged.

    The cheap fix was to go vague: "regenerate with ``kyo reference``" cites nothing and
    so passes the test above. It also strands the reader who is standing in this
    repository and needs to know which file to write and that a test guards it. Both
    audiences read this one string; satisfying only the consumer half would have been
    the same failure with the sign flipped.
    """
    from kyosei.cli import REFERENCE_PATH, REFERENCE_REGENERATE_COMMAND

    header = _reference_header()
    assert REFERENCE_REGENERATE_COMMAND in header, (
        "the exact regenerate command for this repository is gone from the header; a "
        "reader in the source tree can no longer tell what to run"
    )
    assert REFERENCE_PATH in header, "the header no longer names Kyosei's committed copy"
    assert "test_kyo138_command_reference.py" in header, (
        "the header no longer names the test that keeps the committed copy honest"
    )
    # And the consumer half: the paths are there, marked as not theirs.
    flat = flatten_for_paths(header)
    assert f"{REFERENCE_PATH} ({UPSTREAM_MARKER}" in flat


def test_the_output_message_names_the_path_the_caller_actually_wrote(tmp_path) -> None:
    """KYO-156.c1 on ``kyo reference --output`` — law 1, a remedy that works.

    This message used to print Kyosei's own ``--output docs/reference/cli.md`` to a
    caller who had just written somewhere else, so the single remedy it named was the
    one path the reader could not use.
    """
    from kyosei.cmd_views import cmd_reference

    destination = tmp_path / "cli.md"
    args = type("Args", (), {"output": str(destination), "json": False})()
    out = cmd_reference(None, args)
    joined = "\n".join(out.lines)
    assert str(destination) in joined
    assert not unreachable_paths(joined)
