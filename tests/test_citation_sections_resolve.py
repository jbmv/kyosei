"""KYO-35.c3 — a citation that names a section must name a file that has it.

The charter moved from ``CLAUDE.md`` to ``docs/design/charter.md`` and the
citations did not move with it: 41 of this repo's 43 ``CLAUDE.md`` mentions
under ``src/kyosei/``, ``templates/`` and the two skill copies meant the
charter, and every one of them resolved to a file that no longer contained the
cited section. That failure is silent by construction — a reader follows the
citation, finds nothing, and cannot tell "the reasoning moved" from "there was
no reasoning".

This is the check that would have failed at the moment of the move. It reads
citations of the shape a maintainer actually writes:

    docs/design/charter.md, "Sweep"
    docs/design/charter.md's "Sweep" section
    docs/design/charter.md, Claude user stories: "..."
    docs/design/charter.md, "Core schema": "reports: ... not retyped ..."

and requires the named section to exist in the named file — as a markdown
heading, or as the bold run-in label this repo uses for a titled block
(``**Sweep** (flags, never auto-edits): ...``). Where a citation quotes the
file rather than naming a section, the quotation itself must appear there.

It reads *flattened* prose (:mod:`tests.prose_text`), so a citation wrapped
across a line break is found. That is not a nicety: the instance that produced
KYO-35 was declared already-fixed on the strength of a fixed-string grep for a
phrase this repo's hard wrapping had split in two.

Deliberately not scanned: ``docs/`` — ``docs/design/charter.md`` is the frozen
founding record and ``docs/reference/*`` are external evidence documents that
quote other projects' files verbatim; neither is prose this build maintains.
``tests/`` is not scanned either, since a test that asserts on a stale citation
is doing its job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kyosei.install_skill import INSTALLED_SKILL_DIR, installed_skill_text
from tests.prose_text import REPO_ROOT, flatten_prose, iter_source_files, read_flat

SCAN_GLOBS = (
    "src/kyosei/**/*.py",
    "src/kyosei/**/*.md",
    "src/kyosei/**/*.yml",
    "src/kyosei/**/*.json",
    "templates/**/*.md",
    "templates/**/*.json",
    # The skill (SKILL.md, and KYO-328's second tier REFERENCE.md) is read from its
    # packaged source under the src/kyosei/**/*.md glob above: this repository keeps
    # no .claude/skills/kyosei/ mirror since it moved onto the plugin (KYO-610).
    "README.md",
    # KYO-516: the README's reference sections moved here, and the check moved with them.
    "docs/reference/topics/*.md",
    "CLAUDE.md",
    # The contributor text CLAUDE.md imports with an @ line (KYO-517): moved, not
    # retired, so it keeps the check it had while it lived in CLAUDE.md.
    "AGENTS.md",
)

#: A markdown file being cited, then the section it names. The four alternatives
#: are the four shapes this repo's citations actually take; each requires an
#: explicit link token (``,`` ``:`` ``'s``) so a bare path mention — "see
#: `templates/hooks/README.md` for what each hook does" — is not read as a
#: section citation at all.
#:
#: KYO-165: an optional parenthetical may sit between the path and the link
#: token — ``docs/design/charter.md (in Kyosei's own repository), "Shift
#: continuity"`` — so a citation marked with KYO-149's ``UPSTREAM_MARKER``
#: (:mod:`tests.test_kyo149_consumer_paths`) is not, by that marking alone,
#: unwritable in section-citable form. Before this, the two checks demanded
#: incompatible shapes of the same sentence: the marker requires ``(`` directly
#: after the path, and this regex required the link token there instead, so no
#: single mention could satisfy both — every marking traded a section citation
#: away. The parenthetical is generic (any parenthetical aside, not only the
#: marker's exact words), which is what keeps this a shape fix rather than a
#: second place that has to know the marker's fixed phrase.
CITATION_RE = re.compile(
    r"(?P<target>(?:[\w.-]+/)*[\w.-]+\.md)"
    r"(?:\s*\([^()]*\))?"
    r"(?P<link>'s|’s|,|:)"
    r"\s*"
    r"(?:"
    r'"(?P<dq>[^"]{2,300})"'
    r"|“(?P<cq>[^”]{2,300})”"
    r"|'(?P<sq>[^']{2,300})'"
    r"|(?P<named>[A-Za-z][\w.\- ]{2,60}?)\s+(?:section|table)\b"
    r"|(?P<colon>[A-Z][\w.\- ]{2,60}?):\s*[\"'“]"
    r"|(?P<paren>[A-Z][\w.'’\- ]{2,60}?)\s*\)"
    r")"
)

_DASHES = re.compile(r"[—–−]|--")
_QUOTES = re.compile(r"[‘’“”]")


def normalise(text: str) -> str:
    """Case-, dash- and quote-insensitive form used for every comparison here."""
    text = _QUOTES.sub("'", text)
    text = _DASHES.sub("-", text)
    text = re.sub(r"[`*_]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
#: The run-in label this repo's design prose uses instead of a heading:
#: ``**Sweep** (flags, never auto-edits): ...`` and ``- **reports**: ...``.
#: A citation naming one of those is naming a section as surely as a ``##`` is.
_RUNIN_RE = re.compile(r"^(?:[-*+]\s+|\d+\.\s+)?\*\*(.+?)\*\*")


def section_labels(text: str) -> list[str]:
    labels = []
    for raw in text.splitlines():
        line = raw.strip()
        m = _HEADING_RE.match(line)
        if m:
            labels.append(normalise(m.group(1)))
        m = _RUNIN_RE.match(line)
        if m:
            labels.append(normalise(m.group(1)))
    return labels


def looks_like_a_section_name(cited: str) -> bool:
    """A short title-shaped phrase names a section; a sentence quotes the file."""
    stripped = cited.strip()
    if "..." in stripped or "…" in stripped or ";" in stripped:
        return False
    if stripped.endswith((".", "!", "?")):
        return False
    return len(stripped.split()) <= 8


def section_is_present(cited: str, labels: list[str]) -> bool:
    want = normalise(cited)
    for label in labels:
        if label == want:
            return True
        if label.startswith(want) and not label[len(want) :].lstrip()[:1].isalnum():
            return True
    return False


def quotation_is_present(cited: str, target_text: str) -> bool:
    """Every substantial fragment of an elided quotation must be in the file."""
    haystack = normalise(target_text)
    fragments = re.split(r"\.\.\.|…", cited)
    checked = 0
    for fragment in fragments:
        piece = normalise(fragment).strip(" ,:;'\"")
        if len(piece.split()) < 3:
            continue
        checked += 1
        if piece not in haystack:
            return False
    # A quotation with nothing substantial left to check cannot be verified here;
    # treat it as resolved rather than inventing a failure.
    return True


def find_citations(flat: str) -> list[tuple[str, str]]:
    """``(target path, cited section-or-quotation)`` pairs in flattened prose."""
    found = []
    for m in CITATION_RE.finditer(flat):
        cited = next(
            (
                m.group(name)
                for name in ("dq", "cq", "sq", "named", "colon", "paren")
                if m.group(name)
            ),
            None,
        )
        if cited:
            found.append((m.group("target"), cited.strip()))
    return found


#: Where a citation of the installed skill resolves in this repository. The packaged
#: skill names its reference as ``${CLAUDE_SKILL_DIR}/REFERENCE.md``, which the scan
#: reads in its installed form (``.claude/skills/kyosei/REFERENCE.md``, the one
#: substitution `kyo install-skill` makes, KYO-593.c3); since KYO-610 this repository
#: has no copy at that path, so the citation resolves against the packaged source.
PACKAGED_SKILL_DIR = REPO_ROOT / "src/kyosei/harness/claude-code/plugin/skills/kyosei"


def citation_target_path(target: str) -> Path:
    """The file in this repository a citation of ``target`` names."""
    prefix = INSTALLED_SKILL_DIR + "/"
    if target.startswith(prefix):
        return PACKAGED_SKILL_DIR / target[len(prefix) :]
    return REPO_ROOT / target


def read_cited_prose(path: Path) -> str:
    """``path`` flattened, with the skill's directory token in its installed form."""
    return flatten_prose(installed_skill_text(path.read_text(encoding="utf-8")))


def unresolved_citations() -> list[str]:
    problems: list[str] = []
    label_cache: dict[str, list[str]] = {}
    text_cache: dict[str, str] = {}
    for path in iter_source_files(SCAN_GLOBS):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for target, cited in find_citations(read_cited_prose(path)):
            target_path = citation_target_path(target)
            if not target_path.is_file():
                # Not a file in this repo: a consumer project's own document, or
                # a path this check has no business resolving.
                continue
            if target not in text_cache:
                text_cache[target] = target_path.read_text(encoding="utf-8")
                label_cache[target] = section_labels(text_cache[target])
            if looks_like_a_section_name(cited):
                if not section_is_present(cited, label_cache[target]):
                    problems.append(
                        f"{rel}: cites {target!r}, {cited!r} — {target} has no such section"
                    )
            elif not quotation_is_present(cited, text_cache[target]):
                problems.append(
                    f"{rel}: cites {target!r} for {cited[:60]!r}... — {target} does not say that"
                )
    return problems


def test_every_section_citation_names_a_file_that_has_that_section() -> None:
    """KYO-35.c1/c3 together: nothing in this repo's prose points a reader at a
    file for reasoning that file does not contain."""
    problems = unresolved_citations()
    assert not problems, "\n".join(
        ["citations naming a section their target does not have:", *problems]
    )


def test_the_skill_s_citations_of_its_reference_are_resolved() -> None:
    """KYO-610: with the .claude/skills/kyosei/ mirror gone, the packaged skill's
    ``${CLAUDE_SKILL_DIR}/REFERENCE.md, "..." section`` citations would name no file in
    this repository and be skipped as a consumer's document. They are read in installed
    form and resolved against the packaged reference instead, and are still checked."""
    skill = PACKAGED_SKILL_DIR / "SKILL.md"
    assert skill in iter_source_files(SCAN_GLOBS)
    cited = [
        target
        for target, _ in find_citations(read_cited_prose(skill))
        if target == INSTALLED_SKILL_DIR + "/REFERENCE.md"
    ]
    assert len(cited) >= 5, cited
    assert citation_target_path(cited[0]) == PACKAGED_SKILL_DIR / "REFERENCE.md"
    assert citation_target_path(cited[0]).is_file()


# --------------------------------------------------------------- the check's own mechanism


def test_a_citation_naming_an_absent_section_is_detected() -> None:
    labels = section_labels("# Title\n\n## Shift continuity — retired\n")
    assert section_is_present("Shift continuity", labels)
    assert not section_is_present("Sweep", labels)


def test_a_run_in_bold_label_counts_as_a_section() -> None:
    """The charter states several cited "sections" as bold run-in labels rather
    than headings (``**Sweep** (flags, never auto-edits): ...``). A check that
    only understood ``#`` headings would report every one of those as missing."""
    labels = section_labels("**Sweep** (flags, never auto-edits): git diff ...\n")
    assert section_is_present("Sweep", labels)


def test_detection_survives_the_hard_wrapping_that_hid_the_original_instance() -> None:
    """The trap this record came from. The controller grepped for a fixed phrase,
    this repo's prose is hard-wrapped, the phrase spanned a line break, and the
    grep reported absence — so a live stale citation was recorded as fixed.
    """
    wrapped = (
        "#: The flag classes `kyo sweep` produces. `docs/design/charter.md`'s\n"
        '#: "Sweep"\n'
        "#: section names several more.\n"
    )
    assert '"Sweep"' in wrapped
    # The way the instance was originally (and wrongly) looked for:
    assert 'charter.md`\'s "Sweep"' not in wrapped, "fixed-string search must not find it"
    # The way this check looks for it:
    assert find_citations(flatten_prose(wrapped)) == [("docs/design/charter.md", "Sweep")]


def test_a_quotation_must_appear_in_the_file_it_is_attributed_to() -> None:
    body = "## Core schema\n\n- **reports**: structured worker output — ingested by `kyo report`.\n"
    assert quotation_is_present(
        "reports: structured worker output ... ingested by kyo report", body
    )
    assert not quotation_is_present("record moves are NOT implied by a dispatch", body)


@pytest.mark.parametrize(
    "flat,expected",
    [
        ('docs/design/charter.md, "Sweep"', [("docs/design/charter.md", "Sweep")]),
        ("docs/design/charter.md's Sweep section", [("docs/design/charter.md", "Sweep")]),
        (
            "docs/design/charter.md, 'Shift continuity'",
            [("docs/design/charter.md", "Shift continuity")],
        ),
        ('docs/design/charter.md, Core schema: "x"', [("docs/design/charter.md", "Core schema")]),
        ("see templates/hooks/README.md for what each hook does", []),
        (
            # KYO-165: the marker parenthetical sits between the path and the
            # link token; the section citation must still be read.
            "docs/design/charter.md (in Kyosei's own repository), \"Shift continuity\"",
            [("docs/design/charter.md", "Shift continuity")],
        ),
        (
            # A parenthetical that never resolves into a link+section is still
            # not a citation -- the optional group must not manufacture one.
            "docs/design/charter.md (see the charter itself) for more",
            [],
        ),
    ],
)
def test_citation_shapes(flat: str, expected: list[tuple[str, str]]) -> None:
    """Each alternative in :data:`CITATION_RE`, and the bare-mention case that
    must *not* be read as a citation."""
    assert find_citations(flat) == expected


def test_a_mention_marked_upstream_can_still_be_section_verified() -> None:
    """KYO-165.c1: marking a citation with KYO-149's escape hatch
    (:data:`tests.test_kyo149_consumer_paths.UPSTREAM_MARKER`) must not, by
    construction, remove it from this check's coverage -- the defect this
    record exists to fix. Before the ``CITATION_RE`` change above, the marker
    required ``(`` directly after the path and this check required the link
    token there instead, so no single mention could satisfy both. One sentence,
    both checks, at once.
    """
    from tests.test_kyo149_consumer_paths import UPSTREAM_MARKER, unreachable_citations

    sentence = (
        "see `docs/design/charter.md` (in Kyosei's own repository), "
        '"Shift continuity"'
    )
    assert UPSTREAM_MARKER in sentence.lower(), "fixture no longer carries the marker"
    assert not unreachable_citations(sentence), "still flagged as an unreachable path"
    assert find_citations(flatten_prose(sentence)) == [
        ("docs/design/charter.md", "Shift continuity")
    ], "marking the citation removed it from section-citation coverage"


def test_an_earlier_projects_snapshot_is_described_without_its_path() -> None:
    """KYO-579 supersedes KYO-165.c2 here. The packaged ledger's preamble used to cite
    an earlier project's snapshot by its path, and that path carries a consumer project's
    name into every consumer that installs the ledger. KYO-620 then dropped the
    description too: the preamble states the reading-budget finding without naming the
    snapshot it was measured from, which no reader of the ledger can open. So no
    citation in the packaged ledger may target a snapshot file under docs/reference/,
    and the preamble may not describe the snapshot either.
    """
    path = REPO_ROOT / "src" / "kyosei" / "defaults" / "procedures.md"
    citations = find_citations(read_flat(path))
    snapshot_citations = [
        (target, section)
        for target, section in citations
        if target.startswith("docs/reference/") and "snapshot" in target
    ]
    assert not snapshot_citations, (
        f"the packaged ledger cites a snapshot by path again: {snapshot_citations}"
    )
    text = read_flat(path)
    assert "dated snapshot" not in text and "predecessor" not in text, (
        "the packaged ledger describes an earlier project's snapshot again"
    )
    assert "reading budget is the binding constraint" in text
