"""The operator's guide (``docs/guide/``), ``llms.txt`` and ``AGENTS.md``.

``docs/guide/setup-decisions.md`` is the one list of setup decisions. ``kyo onboard``'s
prompt asks them (``tests/test_kyo_onboard.py`` holds it to that); the guide explains
them. This file holds the guide to its half: every decision id is linked, by id and with
an anchor that resolves to the decision's own heading, from some guide page other than the
decisions list itself. A decision added to the list and explained nowhere fails here.

It also holds the guide's reading order to the pages that exist, ``llms.txt`` to the
llmstxt.org shape and its two routes, and ``CLAUDE.md`` to importing ``AGENTS.md`` rather
than carrying a second copy of it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDE = REPO_ROOT / "docs" / "guide"
DECISIONS = GUIDE / "setup-decisions.md"
INDEX = GUIDE / "README.md"

#: The same heading shape tests/test_kyo_onboard.py reads decision ids from.
DECISION_HEADING = re.compile(r"^### (([A-Z]\d+)\. .*)$", re.MULTILINE)
#: A link to a decision: ``[D1](setup-decisions.md#d1-worker-model-one-tier-or-two)``.
DECISION_LINK = re.compile(r"\[([A-Z]\d+)\]\(setup-decisions\.md#([\w-]+)\)")
#: A relative markdown link (no scheme, no leading slash), with any anchor split off.
RELATIVE_LINK = re.compile(r"\]\((?![a-z]+:)(?!/)([^)#\s]+)(?:#[^)\s]*)?\)")


def github_slug(heading: str) -> str:
    """The anchor GitHub renders for a heading: lowercase, punctuation dropped, spaces
    to hyphens. Backticks, asterisks, parentheses, periods and apostrophes all go."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def decision_anchors(decisions: str) -> dict[str, str]:
    return {m.group(2): github_slug(m.group(1)) for m in DECISION_HEADING.finditer(decisions)}


def guide_pages() -> dict[str, str]:
    return {
        p.name: p.read_text(encoding="utf-8") for p in sorted(GUIDE.glob("*.md")) if p != DECISIONS
    }


def decisions_the_guide_misses(decisions: str, pages: dict[str, str]) -> list[str]:
    """Every decision id no guide page links, and every decision link whose anchor is not
    that decision's heading (a link that would land a reader at the top of the list)."""
    anchors = decision_anchors(decisions)
    problems: list[str] = []
    linked: set[str] = set()
    for name, text in pages.items():
        for ident, anchor in DECISION_LINK.findall(text):
            if anchors.get(ident) != anchor:
                problems.append(f"{name}: [{ident}] links #{anchor}, not #{anchors.get(ident)}")
            linked.add(ident)
    problems += [f"{ident}: explained on no guide page" for ident in anchors if ident not in linked]
    return problems


# ---------------------------------------------------------- c2: every decision explained


def test_every_setup_decision_is_linked_from_a_guide_page() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    assert len(decision_anchors(decisions)) >= 25, "the decisions document stopped parsing"
    assert decisions_the_guide_misses(decisions, guide_pages()) == []


def test_the_check_fails_on_a_decision_no_page_explains() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    pages = {
        name: re.sub(r"\[D5\]\(setup-decisions\.md#[\w-]+\)", "D5", text)
        for name, text in guide_pages().items()
    }
    assert decisions_the_guide_misses(decisions, pages) == ["D5: explained on no guide page"]


def test_the_check_fails_on_a_link_to_the_wrong_heading() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    pages = guide_pages()
    pages["extra.md"] = "[D1](setup-decisions.md#d1-somewhere-else)\n"
    assert decisions_the_guide_misses(decisions, pages) == [
        "extra.md: [D1] links #d1-somewhere-else, not #d1-worker-model-one-tier-or-two"
    ]


def test_the_slug_matches_githubs_for_the_awkward_headings() -> None:
    assert github_slug("C3. Point the project's CLAUDE.md at Kyosei *(added)*") == (
        "c3-point-the-projects-claudemd-at-kyosei-added"
    )
    assert github_slug("E3. `kyo serve`") == "e3-kyo-serve"


# --------------------------------------------------------- c1: the pages and their order


def test_the_reading_order_lists_every_page_and_every_listed_page_exists() -> None:
    index = INDEX.read_text(encoding="utf-8")
    listed = re.findall(r"^\d+\. \[[^\]]+\]\(([^)#]+)\)", index, re.MULTILINE)
    pages = set(guide_pages()) - {INDEX.name}
    assert listed, "the reading order stopped parsing"
    assert sorted(listed) == sorted(pages)
    assert len(pages) >= 5


def test_every_page_opens_with_what_the_reader_can_do_after_it() -> None:
    for name, text in guide_pages().items():
        if name == INDEX.name:
            continue
        first_paragraph = text.split("\n\n")[1]
        assert first_paragraph.startswith("After this page you will"), name


def test_links_between_guide_pages_resolve() -> None:
    """Links inside docs/guide/ only: links out of it (the README, docs/reference/) are
    held by their own owners, and may land in a different merge from this one."""
    for name, text in guide_pages().items():
        for target in RELATIVE_LINK.findall(text):
            resolved = (GUIDE / target).resolve()
            if resolved.parent == GUIDE.resolve():
                assert resolved.exists(), f"{name} links {target}, which does not exist"


# ----------------------------------------------------------------- c3: llms.txt, AGENTS.md


def test_llms_txt_has_the_llmstxt_org_shape_and_routes_both_visitors() -> None:
    text = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("# ") and not lines[0].startswith("## ")
    assert sum(1 for line in lines if line.startswith("# ")) == 1
    assert lines[2].startswith("> "), "the summary blockquote follows the title"
    sections = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    assert len(sections) >= 2
    for section in sections:
        items = [line for line in section.splitlines()[1:] if line.strip()]
        assert items and all(re.match(r"- \[[^\]]+\]\([^)]+\)", line) for line in items), section
    assert "kyo onboard" in text and "uv tool install kyosei" in text
    assert "(AGENTS.md)" in text


def test_claude_md_imports_agents_md_rather_than_duplicating_it() -> None:
    """Where a CLAUDE.md is present it imports AGENTS.md rather than repeating it. A
    checkout without one (the public tree carries AGENTS.md alone) has nothing to hold."""
    if not (REPO_ROOT / "CLAUDE.md").is_file():
        pytest.skip("no CLAUDE.md in this checkout")
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert re.search(r"^@AGENTS\.md$", claude, re.MULTILINE)
    for contributor_fact in ("make venv", "schema_version", "Do not install by hand"):
        assert contributor_fact in agents
        assert contributor_fact not in claude


# c4 (no home paths in the guide, llms.txt or AGENTS.md) is held by the whole-tree
# guard, tests/test_defaults_name_no_consumer.py::test_no_home_path_in_the_public_tree,
# which treats every one of those files as prose (KYO-628).
