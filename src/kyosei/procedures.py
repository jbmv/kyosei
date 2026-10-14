"""Parse ``kyosei/procedures.md``'s standing-rules ledger (law 15: audience is a
first-class property of every rule).

KYO-7.c1: brief generation injects only ``audience: worker`` rules into the frozen
dispatch brief, rendered as title + ``enforced-by`` + the rule's first *sentence*
of justifying prose (KYO-9: never a mid-sentence cut at the first physical source
line) — the exact shape ``kyosei/procedures.md``'s own "Rule format" section
specifies:

    ### <short imperative title>

    - **audience**: worker | orchestrator | human (``controller`` is the same audience
      under its pre-KYO-519 name, and is read permanently), or several, comma-separated
    - **enforced-by**: <mechanism> | aspirational

    <one to three sentences ...>

This module never writes ``kyosei/procedures.md`` itself: its functions take the
ledger's text and return text, and the commands that own a write (`kyo
install-procedures`, `kyo rules add`) do the writing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .errors import KyoseiError

#: One ``###`` section per rule. Everything up to the next ``###`` (or a ``##``
#: section boundary, or end of file) belongs to it.
_SECTION_RE = re.compile(r"^### (.+)$", re.MULTILINE)
#: KYO-27: was ``(\S+)`` — one token, so a rule had exactly one audience and there
#: was no syntax for two. That is what hid "never state a tool-derivable fact from
#: memory" from the controller: the rule is ``audience: worker``, brief generation
#: injects worker rules into the *worker's* brief, and the controller — who writes
#: the brief's task-specific prose, and therefore writes more tool-derivable facts
#: than any worker does — never had it in context. It propagated "npm test is 62
#: tests, ~20s" into two frozen briefs; both workers independently corrected it to
#: 114 tests / ~100s. Law 15
#: working correctly on a mis-assigned input, and the absence of a two-audience
#: syntax was itself the finding.
#:
#: KYO-413.c3: both bullet keys are matched case-insensitively,
#: and ``enforced-by`` is also accepted as ``enforced by``. The exact-spelling match
#: dropped a rule written ``**Audience**`` or ``**enforced by**`` from every brief with
#: exit 0 — the parser skips a section it cannot read, which is right for the fenced
#: "Rule format" example and wrong for a real rule with a capital letter in it.
_AUDIENCE_RE = re.compile(
    r"^\s*-\s*\*\*audience\*\*:\s*([^\n]+?)\s*$", re.MULTILINE | re.IGNORECASE
)
#: The bullet's own text may soft-wrap across several physical lines (markdown
#: convention: a continuation line is not itself prefixed with ``- ``). Capture
#: everything up to the next blank line or the next ``- **...**:`` bullet.
_ENFORCED_BY_RE = re.compile(
    r"^\s*-\s*\*\*enforced[- ]by\*\*:\s*(.+?)(?=\n\s*\n|\n\s*-\s*\*\*|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
#: KYO-9: the first sentence of a (whitespace-collapsed) paragraph — text up to
#: and including the first ``.``/``!``/``?`` that is followed by whitespace or
#: the end of the string. Non-greedy so it stops at the *first* such boundary,
#: not the last.
_SENTENCE_END_RE = re.compile(r"(.*?[.!?])(?:\s|$)")


#: The audiences a rule may name. A token outside this set is dropped rather than
#: guessed at — `kyo doctor`'s `procedures` check is what reports it, so a typo
#: surfaces as a named failure instead of a rule that silently reaches nobody.
#:
#: KYO-519: ``orchestrator`` is known beside ``controller``, permanently,
#: and :func:`orchestrator_rules` selects either. The packaged ledger says
#: ``orchestrator`` from landing 2 of the controller-to-orchestrator rename (KYO-524),
#: which waited until every kyo that reads a store knew the new token, because an older
#: build drops a token it does not know and the rule silently leaves the session brief.
#: Projects that never adopt keep ``controller`` and lose nothing.
KNOWN_AUDIENCES: frozenset[str] = frozenset({"worker", "orchestrator", "controller", "human"})

#: KYO-519: the audience tokens that name the orchestrator session -- the
#: canonical one and the one it had before the rename.
ORCHESTRATOR_AUDIENCES: frozenset[str] = frozenset({"orchestrator", "controller"})

#: KYO-519: the ``##`` headings that are one section of the ledger under
#: two names. ``_section_end`` reads a project's copy under either, so a project that
#: has none of the section's rules gets the rule appended to its existing section rather
#: than a second section with the other name appended at the end of the file.
SECTION_ALIASES: dict[str, frozenset[str]] = {
    name: frozenset({"Orchestrator-audience rules", "Controller-audience rules"})
    for name in ("Orchestrator-audience rules", "Controller-audience rules")
}


@dataclass(frozen=True)
class Rule:
    """One parsed ``### ...`` section from ``procedures.md``."""

    title: str
    audience: str
    enforced_by: str
    first_line: str


#: KYO-563: where a rule ends -- at the next rule's ``###`` heading, or at the next
#: ``#``/``##`` section heading, whichever comes first. The packaged ledger ends with a
#: ``## Project rules`` section whose note sits above any rule, and cutting only at
#: ``###`` read that note as the closing paragraphs of the rule before the heading:
#: `kyo rules` printed it as part of that rule, and every project ledger without the
#: note compared as having reworded it.
_RULE_END_RE = re.compile(r"^#{1,3} ", re.MULTILINE)


def _rule_spans(stripped: str) -> list[tuple[str, int, int]]:
    """``(title, body start, body end)`` for every ``### <title>`` rule in the
    fence-stripped ``stripped``, each ending where :data:`_RULE_END_RE` says."""
    spans: list[tuple[str, int, int]] = []
    for match in _SECTION_RE.finditer(stripped):
        end = _RULE_END_RE.search(stripped, match.end())
        spans.append((match.group(1).strip(), match.end(), end.start() if end else len(stripped)))
    return spans


def _sections(text: str) -> list[tuple[str, str]]:
    """``(title, body)`` for every non-fenced ``### <title>`` section in ``text``."""
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return [(title, stripped[start:end]) for title, start, end in _rule_spans(stripped)]


def incomplete_rules(text: str) -> list[tuple[str, list[str]]]:
    """KYO-413.c4: ``(title, missing bullets)`` for every non-fenced ``###`` section
    that :func:`parse_procedures` skips because it lacks an ``audience`` or an
    ``enforced-by`` bullet — a rule that reaches nobody, which `kyo doctor` names
    rather than letting it vanish from every brief with exit 0."""
    found: list[tuple[str, list[str]]] = []
    for title, body in _sections(text):
        missing = [
            name
            for name, pattern in (("audience", _AUDIENCE_RE), ("enforced-by", _ENFORCED_BY_RE))
            if not pattern.search(body)
        ]
        if missing:
            found.append((title, missing))
    return found


def parse_procedures(text: str) -> list[Rule]:
    """Parse every ``### <title>`` rule section in ``text``.

    A section missing ``audience`` or ``enforced-by`` is skipped rather than
    raising (:func:`incomplete_rules` reports it, KYO-413.c4) — ``procedures.md``
    also carries a "Rule format" ``###``-titled example inside a fenced code
    block, which has no real bullets to parse and is not itself a rule; reading it as
    one would silently inject bogus rows into every worker's brief. (Fenced blocks
    are also stripped first for the same reason: a ``### ...`` line shown as example
    prose inside triple backticks must not be parsed as a real section.)
    """
    rules: list[Rule] = []
    for title, body in _sections(text):
        audience_match = _AUDIENCE_RE.search(body)
        enforced_match = _ENFORCED_BY_RE.search(body)
        if not audience_match or not enforced_match:
            continue
        audience = audience_match.group(1).strip()
        # Collapse a soft-wrapped bullet's internal newlines to single spaces —
        # it is markdown source wrapping, not an intentional line break.
        enforced_by = " ".join(enforced_match.group(1).split())

        after_bullets = body[enforced_match.end() :]
        # Skip to the next blank-line-separated paragraph (the justifying prose),
        # then take just its first *sentence* as "the first line" (procedures.md's
        # own "Rule format": "the one-line 'what to do'" that reaches a brief).
        #
        # KYO-9: a paragraph's first sentence routinely soft-wraps across several
        # physical source lines in markdown, so cutting at the first '\n' (the
        # previous behaviour) sliced a sentence in half mid-word — briefs carried
        # danglers like "...deleted words mid-sentence three times in one session
        # of the". Collapse the paragraph's internal newlines to spaces first, then
        # cut at the first sentence-ending punctuation followed by whitespace/end,
        # so the injected text is always a complete sentence regardless of how the
        # source happens to be wrapped.
        paragraphs = re.split(r"\n\s*\n", after_bullets.strip("\n"))
        first_line = ""
        for para in paragraphs:
            collapsed = " ".join(para.strip().split())
            if not collapsed or collapsed.startswith("-"):
                continue
            sentence_end = _SENTENCE_END_RE.match(collapsed)
            first_line = sentence_end.group(1) if sentence_end else collapsed
            break

        rules.append(
            Rule(title=title, audience=audience, enforced_by=enforced_by, first_line=first_line)
        )
    return rules


def audiences_of(rule: Rule) -> set[str]:
    """The audiences one rule names. ``audience: worker, controller`` is two."""
    return {token.strip() for token in rule.audience.split(",") if token.strip()}


def _for_audience(text: str, audience: str | frozenset[str]) -> list[Rule]:
    wanted = {audience} if isinstance(audience, str) else set(audience)
    return [r for r in parse_procedures(text) if wanted & audiences_of(r)]


def worker_rules(text: str) -> list[Rule]:
    """Every rule naming ``worker``, in file order (law 15's brief-injection filter)."""
    return _for_audience(text, "worker")


def orchestrator_rules(text: str) -> list[Rule]:
    """Every rule naming ``orchestrator`` or ``controller`` (KYO-8.c6: `kyo brief`
    injects these into the session brief — procedures.md's own "Rule format" section
    names this filter alongside :func:`worker_rules`). KYO-519 renamed it from
    ``controller_rules`` and made it read both tokens."""
    return _for_audience(text, ORCHESTRATOR_AUDIENCES)


def human_rules(text: str) -> list[Rule]:
    """Every rule naming ``human`` (procedures.md: shown in `kyo brief`'s
    human-facing sections, never injected into a worker's context)."""
    return _for_audience(text, "human")


def unknown_audiences(text: str) -> list[tuple[str, str]]:
    """``(rule title, bad token)`` for every audience token outside
    :data:`KNOWN_AUDIENCES` — a rule that reaches nobody, reported by `kyo doctor`
    rather than silently dropped (law 2: a field with no consumer is decoration,
    and an audience nothing selects on is exactly that)."""
    found: list[tuple[str, str]] = []
    for rule in parse_procedures(text):
        for token in sorted(audiences_of(rule) - KNOWN_AUDIENCES):
            found.append((rule.title, token))
    return found


# --------------------------------------------------------------------------------
# KYO-95 / KYO-212: comparing two copies of the ledger, rule by rule
#
# Every project has two copies of this file: the one this build packages
# (``src/kyosei/defaults/procedures.md``, what `kyo init` scaffolds) and the one the
# project owns (``kyosei/procedures.md``, what `kyo dispatch` injects into a brief).
# The second is written once, at init, and nothing ever looked at the packaged copy
# again — so a rule added to the default reached no existing project (KYO-212).
#
# This lived as ``ledger_differences`` in ``tests/test_procedures.py``, where it
# compared *this* repository's two copies. KYO-212.c4 moves it here so that the test,
# `kyo doctor`'s two ledger checks (``procedures_rules_missing``, a problem, and
# ``procedures_rules_differ``, a notice) and `kyo install-procedures` cannot disagree
# about what a difference is: one comparison, four callers, and the consumer-facing
# ones render its result rather than re-deriving it.
#
# WHAT "DIFFERENT" MEANS, decided in KYO-95 and unchanged here. Not "the two files
# differ": a store copy is *supposed* to be able to carry rules of its own (this
# repository's carries two the packaged default deliberately does not ship), and a
# byte comparison would forbid that forever. The comparison is rule-scoped — a rule's
# identity is its ``###`` title, its text is everything under that title — and it
# reports three things: a packaged rule the store copy lacks, a rule both carry whose
# text disagrees, and a rule only the store copy carries. Only the first two are
# problems anywhere; the third is a finding for this repository's own test (which
# allowlists the two it means) and never for a consumer.
#
# THE COMPARISON IS OF RULES, NOT OF FILE LAYOUT. ``## ...`` section headings and
# ``---`` breaks sit *between* rules, so a naive "### to the next ###" split
# attributes them to whichever rule they happen to follow — and two copies do not
# agree about where they sit, because inserting a rule moves them. :func:`rule_bodies`
# drops that scaffolding. Heading structure is therefore out of scope by choice: it is
# a property of one file, not a disagreement between two.

#: A packaged rule the store copy does not carry — the drift KYO-212 is about, and the
#: only kind `kyo install-procedures` fixes.
LEDGER_MISSING = "missing"
#: A rule both copies carry, with different text. Never rewritten by anything: a
#: project may have edited its own copy on purpose (KYO-212.c3).
LEDGER_CHANGED = "changed"
#: A rule only the store copy carries. Legitimate everywhere; a finding only for the
#: repository that ships the packaged default and allowlists its own local rules.
LEDGER_STORE_ONLY = "store-only"
#: The two copies disagree ABOVE the first rule (KYO-167). Not a rule at all, which is
#: why it is its own kind rather than a :data:`LEDGER_CHANGED` with an invented title:
#: a caller that iterates rule titles must not be handed something that is not one.
LEDGER_PREAMBLE = "preamble"

#: What a :data:`LEDGER_PREAMBLE` difference puts in :attr:`LedgerDifference.title` —
#: a description, because the thing it names has no title of its own.
PREAMBLE_TITLE = "(the text above the first rule)"

#: How the packaged copy is named in a rendered difference when the caller says
#: nothing. A consumer has no path to their build's default, so it is described
#: rather than located.
PACKAGED_LABEL = "this build's packaged procedures.md"

#: Where the packaged default's *source* sits — in **Kyosei's own repository**, and
#: nowhere else. An installed consumer has this file inside their site-packages and no
#: repo-relative path to it, which is why :data:`PACKAGED_LABEL` exists for every
#: message a consumer reads. This constant is for the one audience that does have the
#: path: a dispatch in this repository whose touches name it (KYO-151.c1 — the brief
#: block that tells such a worker the store copy is a hand-off it cannot perform), and
#: ``tests/test_procedures.py``'s binding of the cross-copy check to these two files.
#: Spelled once here rather than in each so the trigger and the check cannot drift
#: apart (law 3).
PACKAGED_LEDGER_SOURCE_PATH = "src/kyosei/defaults/procedures.md"
#: The project's own copy, relative to the store root — the one `kyo dispatch` injects
#: into a brief, and the one `kyo check-branch` forbids a dispatch branch to diff.
STORE_LEDGER_PATH = "kyosei/procedures.md"


@dataclass(frozen=True)
class LedgerDifference:
    """One way two copies of the ledger disagree about one rule.

    ``packaged_excerpt``/``store_excerpt`` are filled for :data:`LEDGER_CHANGED`
    only — the first stretch of the rule's text where the two copies stop agreeing,
    from each side.
    """

    kind: str
    title: str
    packaged_excerpt: str = ""
    store_excerpt: str = ""


_RULE_HEADING_RE = re.compile(r"^### (.+)$", re.MULTILINE)
_SECTION_HEADING_RE = re.compile(r"^#{1,2} ")
_THEMATIC_BREAK_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def rule_sections(text: str) -> dict[str, str]:
    """Every ``### `` rule in ``text``, title -> its section **verbatim**, heading
    excluded (KYO-327.c1).

    :func:`rule_bodies`' sibling and deliberately not the same function. That one
    normalizes for *comparison* — it drops headings and thematic breaks and re-flows
    every paragraph onto one line — which is exactly right for asking whether two
    copies of a rule say the same thing and exactly wrong for showing a rule to a
    person. `kyo rules` claims to print a rule as the ledger wrote it, and a re-flowed
    bullet list is not that. Same cut as :func:`rule_bodies` (next ``### `` heading, or
    the end), over the same fence-stripped text, so the two agree about where a rule
    ends and only about that.
    """
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return {
        title: stripped[start:end].strip("\n") for title, start, end in _rule_spans(stripped)
    }


def rule_bodies(text: str) -> dict[str, str]:
    """Every ``### `` rule in ``text``, title -> its body, normalized for comparison.

    Normalization drops exactly what is ledger *scaffolding* rather than rule content:
    fenced code blocks (the "Rule format" example is not a rule, and
    :func:`parse_procedures` strips them for the same reason), ``#``/``##`` section
    headings, and thematic breaks. Soft-wrap is collapsed within each paragraph too —
    re-filling a paragraph changes no rule, and a hand-applied copy re-wraps.
    """
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    bodies: dict[str, str] = {}
    for title, start, end in _rule_spans(stripped):
        kept = [
            line
            for line in stripped[start:end].splitlines()
            if not _SECTION_HEADING_RE.match(line)
            and not _THEMATIC_BREAK_RE.match(line.strip())
        ]
        paragraphs = (" ".join(p.split()) for p in re.split(r"\n\s*\n", "\n".join(kept)))
        bodies[title] = "\n".join(p for p in paragraphs if p)
    return bodies


def preamble(text: str) -> str:
    """Everything above the first rule, normalized to one line of words (KYO-167).

    THE GAP THIS CLOSES. :func:`rule_bodies` splits at ``### `` and keeps what follows,
    so everything before the first rule — the Law 15 paragraph that explains what the
    ledger is and who reads it, the "Rule format" section that specifies the shape every
    rule must take — was outside the comparison entirely. The two copies could say
    different things there forever and nothing in the repository would notice; by
    KYO-165's merge they did, and it took a dispatch reporting a prediction that failed
    to come true to find it.

    The first rule is located in :func:`_masked` text, never in the raw source: the
    "Rule format" section shows a ``### <short imperative title>`` heading inside a
    fence, and cutting there would put half the preamble outside the comparison for
    exactly the reason the whole preamble already was.

    NORMALIZED TO WORDS, and nothing weaker. Headings and fenced examples are kept —
    unlike :func:`rule_bodies`, which drops them because they sit *between* rules and
    the two copies legitimately disagree about where; above the first rule there is no
    such ambiguity, and a renamed section or an altered format example is content, not
    scaffolding. What is dropped is line breaks and runs of whitespace, because the hand
    application this comparison guards re-wraps prose and a re-wrap changes nothing
    anyone reads.
    """
    return " ".join(_raw_preamble(text).split())


def _raw_preamble(text: str) -> str:
    """The literal text above the first rule, unnormalized — what :func:`preamble`
    reduces to one line of words for comparison. Exposed separately for
    :func:`adopt_preamble` (KYO-151.c2), which splices the packaged copy's actual
    formatting into the store copy rather than the word-normalized string a
    comparison needs.
    """
    masked = _masked(text)
    first = _RULE_HEADING_RE.search(masked)
    return text[: first.start() if first else len(text)]


def _divergence_point(a: str, b: str) -> int:
    """Where ``a`` and ``b`` stop agreeing, backed up to the last word boundary.

    Word-aligned because the excerpt cut here is read by a person: a rule's
    ``enforced-by`` bullet runs several hundred characters and two versions of it
    typically share the first third, so a mid-word cut is the usual case, not the
    unlucky one.
    """
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return a.rfind(" ", 0, i) + 1


def _excerpt(body: str, other: str, width: int = 200) -> str:
    """The first paragraph of ``body`` that ``other`` lacks, from where it diverges.

    Quoting a differing paragraph from its *head* would show the reader only the words
    that match, for the reason :func:`_divergence_point` describes.
    """
    theirs = other.split("\n")
    mine = [p for p in body.split("\n") if p not in set(theirs)]
    if not mine:
        return "(nothing of its own; the two differ only in what the other side adds)"
    paragraph = mine[0]
    counterpart = max(theirs, key=lambda p: _divergence_point(p, paragraph), default="")
    cut = _divergence_point(counterpart, paragraph)
    tail = paragraph[cut:]
    if len(tail) > width:
        tail = tail[:width].rsplit(" ", 1)[0] + " ..."
    return ("..." if cut else "") + tail


def compare_ledgers(packaged: str, store: str) -> list[LedgerDifference]:
    """Compare two copies of the ledger rule by rule (KYO-212.c4).

    The one comparison behind `kyo doctor`'s ``procedures_rules_missing`` and
    ``procedures_rules_differ`` checks, `kyo install-procedures`, and this repository's own
    ``test_this_repos_store_ledger_agrees_with_the_packaged_default``. Packaged rules
    come first, in packaged order, then the store copy's own; what each caller *does*
    with a :data:`LEDGER_STORE_ONLY` entry is the caller's business.
    """
    packaged_bodies = rule_bodies(packaged)
    store_bodies = rule_bodies(store)
    found: list[LedgerDifference] = []

    # KYO-167, and first because it is the part of the file that says what all the rest
    # of it is. A caller that reads only rule titles filters this out by kind — the two
    # doctor checks do — rather than being handed a title that names no rule.
    packaged_preamble, store_preamble = preamble(packaged), preamble(store)
    if packaged_preamble != store_preamble:
        found.append(
            LedgerDifference(
                LEDGER_PREAMBLE,
                PREAMBLE_TITLE,
                packaged_excerpt=_excerpt(packaged_preamble, store_preamble),
                store_excerpt=_excerpt(store_preamble, packaged_preamble),
            )
        )

    for title, packaged_body in packaged_bodies.items():
        store_body = store_bodies.get(title)
        if store_body is None:
            found.append(LedgerDifference(LEDGER_MISSING, title))
        elif store_body != packaged_body:
            found.append(
                LedgerDifference(
                    LEDGER_CHANGED,
                    title,
                    packaged_excerpt=_excerpt(packaged_body, store_body),
                    store_excerpt=_excerpt(store_body, packaged_body),
                )
            )

    for title in store_bodies:
        if title not in packaged_bodies:
            found.append(LedgerDifference(LEDGER_STORE_ONLY, title))
    return found


def ledger_differences(
    packaged: str,
    store: str,
    store_only: frozenset[str] = frozenset(),
    *,
    packaged_label: str = PACKAGED_LABEL,
    store_label: str = "kyosei/procedures.md",
    store_only_hint: str = "",
) -> list[str]:
    """Every way a store copy disagrees with the packaged default it came from.

    One human-readable entry per disagreement, each naming the rule and the direction —
    which copy has what. "The ledgers differ" would send the reader back to a diff of
    two files of a couple of hundred lines each, and the hand step this exists to catch
    is performed one rule at a time.

    ``store_only`` is an allowlist of rules the store copy is *known* to carry alone;
    anything outside it is reported, and an entry naming a rule the store copy no
    longer carries is reported too (law 2 applied to the allowlist itself). A caller
    that keeps such an allowlist passes ``store_only_hint`` to say where it lives —
    the sentence naming that file belongs to the caller, because a path in *this*
    file's message would be a path no installed consumer has (KYO-149/KYO-156).

    Nothing consumer-facing calls this with a non-empty ``store_only``: `kyo doctor`
    reports the two directions a consumer owes something about and renders
    :func:`compare_ledgers` itself. This rendering exists for the repository that
    ships the packaged default, where "the store copy carries a rule nothing ships"
    is a real finding.
    """
    found: list[str] = []
    seen_store_only: set[str] = set()
    for difference in compare_ledgers(packaged, store):
        title = difference.title
        if difference.kind == LEDGER_PREAMBLE:
            # KYO-167. Worded differently from the rule cases on purpose: there is no
            # `kyo install-procedures` for this one — the command merges rules — so the
            # message says which copy has to be edited and by whom, rather than naming a
            # remedy that would not fire.
            found.append(
                f"{packaged_label} and {store_label} disagree ABOVE the first rule, "
                f"where nothing is compared rule by rule and the two can drift silently. "
                f"There is no merge for this: a person edits one copy to match the "
                f"other.\n"
                f"    {packaged_label} has: {difference.packaged_excerpt}\n"
                f"    {store_label} has: {difference.store_excerpt}"
            )
        elif difference.kind == LEDGER_MISSING:
            found.append(
                f"{store_label} is missing the rule {title!r}, which {packaged_label} "
                f"ships — a rule was added to the packaged default and never applied to "
                f"this project's store copy, so its workers are not being told it"
            )
        elif difference.kind == LEDGER_CHANGED:
            found.append(
                f"{packaged_label} and {store_label} disagree about the rule {title!r}:\n"
                f"    {packaged_label} has: {difference.packaged_excerpt}\n"
                f"    {store_label} has: {difference.store_excerpt}"
            )
        else:
            seen_store_only.add(title)
            if title not in store_only:
                found.append(
                    f"{store_label} carries the rule {title!r}, which {packaged_label} "
                    f"does not ship. If it is deliberately local to this project, it "
                    f"belongs in the allowlist; if it was meant for every project, it "
                    f"belongs in the packaged default as well.{store_only_hint}"
                )

    for title in sorted(store_only - seen_store_only):
        found.append(
            f"the allowlist names {title!r}, which {store_label} no longer carries — "
            f"drop it from that set, or restore the rule.{store_only_hint}"
        )
    return found


# ------------------------------------------------- KYO-212.c2: merging by rule
#
# Overwriting is the wrong verb, which is why this is not "add procedures.md to
# `kyo install-skill`". The store copy is project-editable by design: `kyo init`
# copies the default in precisely so the project can then own it. So the act is a
# merge BY RULE — packaged rules the store copy lacks are inserted, rules whose text
# differs are left exactly where they are and reported instead, and rules only the
# store copy carries are never touched.


@dataclass(frozen=True)
class _Block:
    """One ``###`` rule as it sits in a file: its title, its ``##`` section, and the
    span of the source it occupies (trailing blank lines excluded)."""

    title: str
    section: str
    start: int
    end: int


def _masked(text: str) -> str:
    """``text`` with every fenced code block blanked out, offsets preserved.

    The "Rule format" section shows a ``### <short imperative title>`` heading inside
    a fence; parsing it as a rule would mean inserting into the middle of an example.
    Blanking rather than deleting keeps every offset below an offset into the real
    text.
    """
    return re.sub(
        r"```.*?```",
        lambda m: re.sub(r"[^\n]", " ", m.group(0)),
        text,
        flags=re.DOTALL,
    )


def _blocks(text: str) -> list[_Block]:
    """Every rule in ``text`` in file order, with the span it occupies."""
    masked = _masked(text)
    headings = [
        (m.start(), m.end(), m.group(0)) for m in re.finditer(r"^#{2,3} .+$", masked, re.MULTILINE)
    ]
    blocks: list[_Block] = []
    section = ""
    for i, (start, _end, line) in enumerate(headings):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        stop = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        # Back off the scaffolding that sits *between* rules — blank lines, and the
        # `---` break before the next `## ` section — so an insertion after this rule
        # lands beside it rather than after the next section's heading rule.
        body = text[start:stop]
        while True:
            trimmed = body.rstrip()
            if trimmed.endswith("---") and _THEMATIC_BREAK_RE.match(trimmed.rsplit("\n", 1)[-1]):
                body = trimmed.rsplit("\n", 1)[0]
                continue
            body = trimmed
            break
        blocks.append(
            _Block(
                title=line[4:].strip(),
                section=section,
                start=start,
                end=start + len(body),
            )
        )
    return blocks


def _section_end(text: str, section: str) -> int | None:
    """Where ``## <section>``'s content ends in ``text``, or ``None`` if it has no
    such heading — used only for a section whose rules the store copy has none of."""
    masked = _masked(text)
    headings = [(m.start(), m.group(0)) for m in re.finditer(r"^## .+$", masked, re.MULTILINE)]
    names = SECTION_ALIASES.get(section, frozenset({section}))
    for i, (start, line) in enumerate(headings):
        if line[3:].strip() not in names:
            continue
        stop = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        body = text[start:stop]
        while True:
            trimmed = body.rstrip()
            if trimmed.endswith("---") and _THEMATIC_BREAK_RE.match(trimmed.rsplit("\n", 1)[-1]):
                body = trimmed.rsplit("\n", 1)[0]
                continue
            body = trimmed
            break
        return start + len(body)
    return None


def _packaged_block_text(packaged: str, block: _Block) -> str:
    """One rule's source, heading included, ending in exactly one newline."""
    return packaged[block.start : block.end].rstrip("\n") + "\n"


def merge_ledger(packaged: str, store: str) -> tuple[str, list[str]]:
    """``(the store copy with every missing packaged rule inserted, titles added)``.

    Pure insertion: a rule already present is never rewritten, whatever its text says,
    and no other byte of the file moves. Each inserted rule lands beside the packaged
    rule it follows — the nearest earlier rule of its own section that the store copy
    already has — so packaged order within an audience section survives, and a store
    copy's own local rules stay where the project put them.
    """
    added: list[str] = []
    merged = store
    for block in _blocks(packaged):
        present = {b.title for b in _blocks(merged)}
        if block.title in present:
            continue
        text = _packaged_block_text(packaged, block)
        merged = _insert(merged, block, text, packaged)
        added.append(block.title)
    return merged, added


def _after(store: str, at: int, text: str) -> str:
    """``store`` with ``text`` spliced in after offset ``at``, one blank line either
    side, and whatever followed left exactly as it was."""
    tail = store[at:].lstrip("\n")
    return store[:at] + "\n\n" + text + ("\n" + tail if tail else "")


def _insert(store: str, block: _Block, text: str, packaged: str) -> str:
    """Insert one packaged rule's ``text`` into ``store`` at its packaged position."""
    store_blocks = {b.title: b for b in _blocks(store)}
    siblings = [b for b in _blocks(packaged) if b.section == block.section]
    index = next(i for i, b in enumerate(siblings) if b.title == block.title)

    for earlier in reversed(siblings[:index]):
        anchor = store_blocks.get(earlier.title)
        if anchor is not None:
            return _after(store, anchor.end, text)
    for later in siblings[index + 1 :]:
        anchor = store_blocks.get(later.title)
        if anchor is not None:
            return store[: anchor.start] + text + "\n" + store[anchor.start :]

    # No rule of this section in the store copy at all: append to the section if it
    # has one, and to the end of the file if it does not — a copy that lost a whole
    # audience section still gets the rule rather than silently getting nothing.
    end = _section_end(store, block.section)
    if end is None:
        return store.rstrip("\n") + f"\n\n## {block.section}\n\n" + text
    return _after(store, end, text)


# ------------------------------------------- KYO-565: writing a project's own rule
#
# Onboarding tells an agent to put a project's own restriction into the ledger as a
# rule, and writing one meant knowing the `###` shape, the two bullets, the audience
# tokens and where in the file a project's rule belongs -- worked out from prose and
# the source. `kyo rules add` is that knowledge as a command,
# and this is its pure half: text in, text out, nothing written.

#: The ``##`` heading a project's own rules live under. KYO-563 ships the same heading
#: in the packaged ledger, so the two meet: a project that ran `kyo rules add` before
#: installing that build already has the section the packaged file names.
PROJECT_RULES_SECTION = "Project rules"

#: A line inside a rule's reasoning that would start a new rule or section, and so
#: silently cut the rule short or move every rule after it into another section.
_STRUCTURAL_LINE_RE = re.compile(r"^#{1,3} ", re.MULTILINE)


def rule_text(title: str, audiences: list[str], enforced_by: str, reason: str) -> str:
    """One rule in the ledger's own "Rule format", ending in one newline."""
    return (
        f"### {title}\n\n"
        f"- **audience**: {', '.join(audiences)}\n"
        f"- **enforced-by**: {enforced_by}\n\n"
        f"{reason.strip()}\n"
    )


def add_project_rule(
    text: str, *, title: str, audiences: list[str], enforced_by: str, reason: str
) -> str:
    """``text`` with one new rule appended under ``## Project rules`` (KYO-565).

    The section is created at the end of the file when ``text`` has none. Raises
    :class:`KyoseiError`, and so writes nothing, for a malformed rule: an empty or
    multi-line title or enforced-by line, an empty reason, a reason line that would
    start a ``#``-heading of its own, an audience outside :data:`KNOWN_AUDIENCES`, or
    a title an existing rule already has. The result is read back through
    :func:`parse_procedures` before it is returned, so a rule the reader would not see
    with exactly these values is refused rather than written.
    """
    title = title.strip()
    enforced_by = enforced_by.strip()
    reason = reason.strip()
    known = ", ".join(sorted(KNOWN_AUDIENCES))
    if not title or "\n" in title:
        raise KyoseiError("a rule's title is one non-empty line; --title was empty or multi-line")
    if not enforced_by or "\n" in enforced_by:
        raise KyoseiError(
            "a rule's enforced-by is one non-empty line naming the mechanism that holds "
            "it up, or the word `aspirational` when nothing does; --enforced-by was "
            "empty or multi-line"
        )
    if not reason:
        raise KyoseiError(
            "a rule needs its reasoning -- one to three sentences on why it exists; "
            "the --reason-file was empty"
        )
    if _STRUCTURAL_LINE_RE.search(reason):
        raise KyoseiError(
            "the reason has a line starting with `#`, `##` or `###`, which the ledger "
            "reads as a new section or rule; reword or indent that line"
        )
    if reason.startswith("-"):
        raise KyoseiError(
            "the reason starts with `-`, which the ledger reads as another bullet, so "
            "its first sentence would never reach a brief; start it with prose"
        )
    if not audiences:
        raise KyoseiError(f"a rule names at least one audience; known audiences: {known}")
    unknown = [a for a in audiences if a not in KNOWN_AUDIENCES]
    if unknown:
        raise KyoseiError(
            f"unknown audience {', '.join(repr(a) for a in unknown)}: a rule naming it "
            f"reaches nobody. Known audiences: {known}"
        )
    existing = {b.title.strip().lower(): b.title for b in _blocks(text)}
    if title.lower() in existing:
        same = existing[title.lower()]
        raise KyoseiError(
            f'a rule titled {same!r} already exists; `kyo rules "{same}"` prints it. '
            "Pick a different title, or edit that rule in place"
        )

    block = rule_text(title, audiences, enforced_by, reason)
    end = _section_end(text, PROJECT_RULES_SECTION)
    if end is None:
        result = text.rstrip("\n") + f"\n\n## {PROJECT_RULES_SECTION}\n\n" + block
    else:
        result = _after(text, end, block)
        if not result.endswith("\n"):
            result += "\n"

    before = parse_procedures(text)
    after = parse_procedures(result)
    added = [r for r in after if r.title == title]
    if (
        len(after) != len(before) + 1
        or len(added) != 1
        or added[0].audience != ", ".join(audiences)
        or added[0].enforced_by != " ".join(enforced_by.split())
    ):
        raise KyoseiError(
            f"the rule {title!r} would not read back as written (the ledger parser saw "
            "something else), so nothing was written"
        )
    return result


# --------------------------------------------------- KYO-151.c2: adopting a rule
#
# `merge_ledger` never rewrites a rule already present, by design (KYO-212.c3): the
# ledger is project-editable, and a rule both copies carry may be the project's own
# considered wording rather than drift. That is right for the plain merge, and wrong
# for exactly one situation `kyo doctor`'s `procedures_rules_differ` surfaces as a
# NOTICE rather than a problem precisely because nothing could act on it: a packaged
# rule was REWORDED (or the preamble was), and the project wants the new wording, not
# its own old one. `merge_ledger` cannot say that on anyone's behalf; a caller says it
# by naming the rule.
#
# adopt_rule/adopt_preamble are a REPLACE, never an insert, and never silent: each
# raises rather than doing nothing when there is nothing sound to adopt, so a typo'd
# title or a no-op request is a named error instead of a report claiming a change that
# did not happen.


def adopt_rule(packaged: str, store: str, title: str) -> tuple[str, str, str]:
    """``(the store copy with ``title``'s text replaced by the packaged wording, the
    rule's old text, its new text)``.

    Raises :class:`KyoseiError`, naming which, when there is nothing sound to adopt:

    * ``title`` names no packaged rule at all — a rule only ``store`` carries
      (:data:`LEDGER_STORE_ONLY`) is legitimately local to the project, and there is
      no packaged wording to adopt it FROM;
    * ``store`` does not carry ``title`` yet (:data:`LEDGER_MISSING`) — that is what
      the plain merge is for; this function only replaces a rule both copies already
      have, never inserts one;
    * the two copies already read the same — adopting would claim a change that did
      not happen.
    """
    packaged_block = next((b for b in _blocks(packaged) if b.title == title), None)
    if packaged_block is None:
        raise KyoseiError(
            f"--adopt {title!r} names no rule {PACKAGED_LABEL} ships. A rule "
            f"{STORE_LEDGER_PATH} carries that the packaged default does not is "
            f"legitimately local to this project — there is no packaged wording to "
            f"adopt it from."
        )
    store_body = rule_bodies(store).get(title)
    if store_body is None:
        raise KyoseiError(
            f"--adopt {title!r} names a rule {STORE_LEDGER_PATH} does not carry yet. "
            f"`kyo install-procedures` (without --adopt) inserts every missing "
            f"packaged rule first; --adopt only replaces the text of a rule both "
            f"copies already have."
        )
    if store_body == rule_bodies(packaged)[title]:
        raise KyoseiError(
            f"--adopt {title!r}: {PACKAGED_LABEL} and {STORE_LEDGER_PATH} already "
            f"read the same for this rule — there is nothing to adopt."
        )
    store_block = next(b for b in _blocks(store) if b.title == title)
    old_text = store[store_block.start : store_block.end]
    new_text = packaged[packaged_block.start : packaged_block.end]
    merged = store[: store_block.start] + new_text + store[store_block.end :]
    return merged, old_text, new_text


def adopt_preamble(packaged: str, store: str) -> tuple[str, str, str]:
    """``(the store copy with its preamble replaced by the packaged wording, the old
    preamble's raw text, the new one's)``.

    :func:`compare_ledgers` can only SAY the two preambles disagree
    (:data:`LEDGER_PREAMBLE`, KYO-167) — there was no merge for it and no way to act
    on it except a person editing one copy by hand. This is that act, named rather
    than implied: raises :class:`KyoseiError` when the two already agree, so a
    request that would change nothing is a named error instead of a silent no-op.
    """
    if preamble(packaged) == preamble(store):
        raise KyoseiError(
            "--adopt-preamble: the preamble already reads the same in both copies "
            "— there is nothing to adopt."
        )
    old_raw = _raw_preamble(store)
    new_raw = _raw_preamble(packaged)
    merged = new_raw + store[len(old_raw) :]
    return merged, old_raw, new_raw


# ------------------------------- KYO-515: wordings an earlier build installed
#
# `merge_ledger` never rewrites a rule already present (KYO-212.c3), because a rule
# both copies carry may be the project's own considered wording. But one case is not a
# judgement at all: a rule whose text is, byte for byte after `rule_bodies`'
# normalization, a wording some earlier build of this package SHIPPED. Nobody chose
# that text; `kyo init` or an earlier `kyo install-procedures` put it there, and the
# project never touched it. Reporting it as a deliberate difference forever (and
# making the controller `--adopt` it by hand in every project) is the defect KYO-515
# records, measured in three consumer projects at once.
#
# HOW AN EARLIER WORDING IS RECOGNISED: by digest, against the table below, which lists
# for each rule title every body that title has had in any committed version of the
# packaged default (`src/kyosei/defaults/procedures.md`, and `defaults/procedures.md`
# before it moved) other than the current one. Every build is made from a commit, so
# every wording any build could have installed is in that history. The table is
# Python rather than a file under `defaults/` because nothing copies it into a
# consumer's tree (it is the build's knowledge of its own past, not an asset), and
# rather than a read of git at run time because a consumer's site-packages has no
# history to read. `tests/test_kyo_procedures_unedited.py` walks the git history of the
# packaged default and fails, printing the replacement literal, the moment a committed
# wording is missing here — so rewording a packaged rule and committing it without
# regenerating this table is caught by the suite rather than by a consumer.
#
# Keyed by title, so a body is only ever recognised as an earlier wording of THE SAME
# rule. A rule that was RENAMED is out of reach of this by construction: the project's
# copy under the old title is a store-only rule and the new title is a missing one.

#: title -> sha256 hex digests of every superseded packaged body of that rule
#: (:func:`wording_digest`). Generated; see the block comment above.
SUPERSEDED_RULE_WORDINGS: dict[str, frozenset[str]] = {
    (
        "Cite every record to the maintainer as a markdown link with a `kyosei://` target"
    ): frozenset(
        {
            "24a85ecea8b4fc8797f8990f6b14e67b0cdcdf80100f80aec2ca4483dc5c8cc8",
            "28ca5acbb34d319d87350ed2e8f7b7aa1dfad4f52198e51615a5cd63259a3bc9",
            "3387ea0d101bb337450b6e9c6a7500d0bef12ea5bf733a47991cd55463363e4e",
        }
    ),
    (
        "Commit before your final message; do not park on a stop signal with a dirty worktree"
    ): frozenset(
        {
            "9357dae86236e888856ff3735ecef1ed08f09caf80a61d196a873fd401c1571d",
            "a5decd7cf038b9373120e04745213abc90cadf6e0ce493a5258600b6a0290c3a",
            "ac0a2bd991f9e6353da5e961dc2461c8d3478fc07c392f12c5991acbdef66d82",
        }
    ),
    (
        "Declare the worker model in the store, not in your head"
    ): frozenset(
        {
            "37e7ad6bc778c80e55316066a81246be1c222caf58ce8e8a3358d3f342d78743",
            "3836d3b24fc4afecb22bbb905d9bb18fee3c3699f9dc2ace97b913c6f712c094",
            "dd69476719662e7534d9ad4232d336eb3f8342f3cf904a965baa1ee317d883b1",
        }
    ),
    (
        "Flag conflicts with the brief; do not silently resolve them yourself"
    ): frozenset(
        {
            "308f6a2863773ecdb5ca18ca8cb7e4decfb50f22d7f349a3c6f0ac2d15b28d98",
        }
    ),
    (
        "Group records into one dispatch when a worker would otherwise ingest the same "
        "context twice"
    ): frozenset(
        {
            "eba19cab73f18725fedfe532e6261683b45aff11096a0734deff81fc1ec7ae14",
            "faebea4d812cb508dba9935faf3407103c92a599b48fd10d580edd0424a4eb75",
        }
    ),
    (
        "Keep every foreground command under about four minutes"
    ): frozenset(
        {
            "33179a1f0fc52ed244584b2990d7ae8ba530a00a5e02e12f14f1f7ebb0e23f25",
            "33d39c8f66aa909458783ad2cea84b18821afc9b7834695f77604b6edc594214",
            "e8b1eaedad0c33d0a51a8001f86b7a5fdccef4b4e7dcf80174057f8121741f4a",
            "e8c513b3376d10938126fa04864a4a6a3cc247c23859bdb1cf3e513fd3f3b7a2",
        }
    ),
    (
        "Never claim absence from a phrase search; search one distinctive word and read the context"
    ): frozenset(
        {
            "7c609501b878510b38e6de3ce6b46bf3d034d9fc0135b7bd3d3f83371f3659c5",
            "9a6b493d8965f0a0141c981488114e7a26edf31277705b2889e38e627adf30c3",
        }
    ),
    (
        "Never deploy, and never mutate a production system outside the declared `write_scope`"
    ): frozenset(
        {
            "676d7016fddc098ab5f012bbbad87d1cfb0e10d398b2be6ecf64f2161ad13f5f",
        }
    ),
    (
        "Never state a timestamp, line count, or other tool-derivable fact from memory"
    ): frozenset(
        {
            "730b91b6e231f25853c59b568fbb6e50e190c9f750cbc51d09e4e9fa61a94ffd",
            "ac7f174f38a18790729bc0c73fab2f314dea108d2405cd9b527e263bf5e7c1e3",
            "b42dc5828880de48e64f0deeae7fe9634f24dff8d5b8ae64da5f929d52a36982",
        }
    ),
    (
        "Never write to the structured store directly"
    ): frozenset(
        {
            "2ea2baae1a6f030c65aa8f060032416a681537914d7f3b9cb4f0dfdce648da8c",
            "a49caf2041098a9e7f0fcedc3615ef8e2ce2b82a40695c2e1bbd822b1fd412f9",
            "f493d39ef4ec108243519b7a99c6fb55058c677398ebdce2639a4b5994afd868",
        }
    ),
    (
        "Prose enters the store only via stdin or a file, never as a shell argument"
    ): frozenset(
        {
            "6d530b64f5633537dcbc563b87533f29d238e2f886b05d84bed386e6f89f454f",
        }
    ),
    (
        "Prose enters the store only via stdin or a file; the one-line text flags are the exception"
    ): frozenset(
        {
            "1aedae98de1825b126d20aa09390c9bc8dbda096a0ab1a43433ccb8280e2f224",
            "1b5b4a34f89b53460ca08dda9f7a0c9557b9749733efb9de260dd50e207f9ae2",
            "5dd866fae127ac7968e2f666930074d6993a56ab332b4974cc0aa1d1856cf4a2",
            "ef4b84f8185b980fb8e43e2b3d0d03cac8b5fe0ec0d49e0f06a44679c1aebd03",
            "ff5742314a03adfa5ddfcf78b7aaa6a6d3177635447cfef410d8b2ddc25432ea",
        }
    ),
    (
        "Re-verify a record's premises immediately before every dispatch"
    ): frozenset(
        {
            "34bbb93556188f0ed1bb4bd37f4f27de48b2d9b274a1a3d094735067e01d70fe",
            "4eb11f9ac8013f25b2f1762054196d2d0c794dcbdda887646013ab76e22eb6ae",
            "c9074eea55a29288bd94a442b211d17836b44b01dfdb77ed76701e4537149cdd",
        }
    ),
    (
        "Read each governed section your branch raises, and propose its verdict in "
        "`docs_verdicts[]`"
    ): frozenset(
        {
            "159400722bb974d372205dfce12fffb81f3c284b077798b65de81c5d7963a226",
            "24f41ce6da03893deb63111a427c92b3fb7d7a044b1630bc8b16090ef41182f8",
        }
    ),
    (
        "Review a report's `premise_false[]` and `disagreements[]` before its `criteria_verdicts`"
    ): frozenset(
        {
            "6bcaa7b18f8aafd7305601cc36f950f6de59983e2c987c986120a5dea9011b22",
            "c1bf00fa087b8d7df213e2a0c0cb3926dd1b67ad530f0ba83b7f5627e7fb2a28",
        }
    ),
    (
        "Run the full suite yourself, once, on the merged tree"
    ): frozenset(
        {
            "1d1a71188852347129afbcb883b33a58808303ba57ee3814cb072bb673863d7c",
            "3ab6ec291ca371fceb42e7a3ab871149f95a2e2c57080667cef81b02f9ca3fe0",
            "4e9d3b0322e2fbc01f7fbc7212487bbbc889f3c2ae8e2510fcde8c00da6ba147",
            "947648a05d1e27d061324ecb44c4fc53022eb5025ed348aad6b9cc0488a841d8",
            "e5a4238a5064c87db9c96591ea7980dcd77da279fe079b0eb3325ef4c87d3921",
        }
    ),
    (
        "Run the tests that cover your change, not the whole suite"
    ): frozenset(
        {
            "3b20769c021f486be6ea52feeee3c245fb8cdbde8fcee13343530f0946629d32",
            "3de9f0c81d6066651a36e7b9b58221a5bb0751adfbbbfb9539f51d84231b3d2b",
            "5aad2740f30442cf815c22711dc252ebeced516f579061beec47669aa66362d1",
            "78049ca355dcc1b5fffbce0294c44b695b8ad75c9b8888679c4903510829817e",
            "ef47992f7ca6d963fc9384b577b44eab8ce3ee8215ef311576fe4dd984a81d4c",
        }
    ),
    (
        "Spawn helpers read-only if at all, and own everything they do"
    ): frozenset(
        {
            "a21837fc5d963558aca87e693c7bd260f3e718ca1f38f5316117abc1f243df82",
        }
    ),
    (
        "Stay inside the dispatch's `write_scope`"
    ): frozenset(
        {
            "eb26f7fc31bc9fadb5f3b8e332e7c4a22df4283d056f3ca5b3b8b339a514c2d8",
        }
    ),
    (
        "Write a long run to a file and read the file; never pipe a suite through `tail`"
    ): frozenset(
        {
            "21e551380be19d8b539d534d1dd795ad63e047f36752294460cc38c4ce759f35",
            "3c79250e407ed016972c4366dc07d3b138efaa7e344a3d6b58cefea0fa9386b8",
            "a297ace2754f9d9ace69d29230770deefed5bc9d9b321b7982c2a059a78298ee",
        }
    ),
}


def wording_digest(body: str) -> str:
    """The digest :data:`SUPERSEDED_RULE_WORDINGS` stores for one normalized rule body
    (a value of :func:`rule_bodies`)."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def is_superseded_wording(
    title: str,
    body: str,
    superseded: dict[str, frozenset[str]] | None = None,
) -> bool:
    """Whether ``body`` (normalized, as :func:`rule_bodies` returns it) is a wording an
    earlier build shipped for the rule ``title`` — i.e. text nobody in the project
    wrote (KYO-515)."""
    table = SUPERSEDED_RULE_WORDINGS if superseded is None else superseded
    return wording_digest(body) in table.get(title, frozenset())


def refresh_superseded_rules(
    packaged: str,
    store: str,
    superseded: dict[str, frozenset[str]] | None = None,
) -> tuple[str, list[tuple[str, str, str]]]:
    """``(store with every superseded-but-unedited rule moved to the packaged wording,
    [(title, old text, new text), ...])`` (KYO-515).

    Only a rule that is :data:`LEDGER_CHANGED` AND whose store body is an earlier
    packaged wording of that same rule is touched; a rule the project reworded matches
    no digest and is left exactly as it is, which is KYO-212.c3 unchanged. The rewrite
    itself is :func:`adopt_rule`'s, so a refreshed rule lands exactly as an `--adopt`ed
    one would.
    """
    store_bodies = rule_bodies(store)
    refreshed: list[tuple[str, str, str]] = []
    merged = store
    for difference in compare_ledgers(packaged, store):
        if difference.kind != LEDGER_CHANGED:
            continue
        if not is_superseded_wording(
            difference.title, store_bodies[difference.title], superseded
        ):
            continue
        merged, before, after = adopt_rule(packaged, merged, difference.title)
        refreshed.append((difference.title, before, after))
    return merged, refreshed


# KYO-524: the same reasoning, for the text above the first rule. Landing 2 of the
# controller-to-orchestrator rename rewords the preamble as well as the rules, and a
# preamble nobody in the project edited is exactly as inherited as an unedited rule.
# Without this, every project that never touched its preamble would need a deliberate
# `--adopt-preamble` to stop telling its readers that `kyo brief` injects
# `audience: controller` rules. Recognised the same way: the digest of
# :func:`preamble`'s normalized text, for every committed version of the packaged
# default other than the current one; `tests/test_kyo524_orchestrator_ledger.py` walks
# the history and fails, printing the literal, when one is missing.

#: sha256 hex digests (:func:`wording_digest`) of every superseded packaged preamble,
#: normalized by :func:`preamble`. Generated; see the comment above.
SUPERSEDED_PREAMBLES: frozenset[str] = frozenset(
    {
        # KYO-533: the Rule format section as KYO-531 left it, before it described the
        # two briefs' different trims.
        "11fa3ebe6ed89b5cc203d2a8808d6b41f85d5aae1d9487028e5b2401817d6338",
        "707269b4b7e526450b18b072c3198e4b093856e559bd634845f26975bc02a6d4",
        "9c178c92f4f2808e05505e0f6e07e8bc9aaf79ccba094258a1f65f32d8ff8e43",
        "aa64968ed8a97541a08f5e9638d5f9d13e2a1488c564293a5ef3cf4dcf53583e",
        "b405f451e3bf342dbf9fa262b93c77015a85b60a9a41a1f03726a70fce2bd8d2",
        # KYO-563: the Rule format section before it said one rule may name several
        # audiences.
        "ad38a6329aea7dcd1598d5f306997ff5e0dd9ae840e447a503cf54ab3b2dabd1",
        # KYO-579: the preamble before it stopped citing an earlier project's
        # instructions snapshot by its path.
        "f34d97af451560cddf277685ac50c119539b759401e23661002af877f4ca0736",
        # KYO-620: the preamble before it stopped describing that measurement.
        "5abf686686d5cf5353cb4aa0493977ad7fe8175fbcf9ff6c890256242347418e",
    }
)


def refresh_superseded_preamble(
    packaged: str,
    store: str,
    superseded: frozenset[str] | None = None,
) -> tuple[str, tuple[str, str] | None]:
    """``(store with its preamble moved to the packaged wording if it is one an earlier
    build shipped, (old raw text, new raw text) or None)`` (KYO-524).

    A preamble the project reworded matches no digest and is left exactly as it is; the
    rewrite itself is :func:`adopt_preamble`'s.
    """
    table = SUPERSEDED_PREAMBLES if superseded is None else superseded
    if preamble(store) == preamble(packaged):
        return store, None
    if wording_digest(preamble(store)) not in table:
        return store, None
    merged, before, after = adopt_preamble(packaged, store)
    return merged, (before, after)


__all__ = [
    "KNOWN_AUDIENCES",
    "LEDGER_CHANGED",
    "LEDGER_MISSING",
    "LEDGER_PREAMBLE",
    "LEDGER_STORE_ONLY",
    "PACKAGED_LABEL",
    "PACKAGED_LEDGER_SOURCE_PATH",
    "PREAMBLE_TITLE",
    "STORE_LEDGER_PATH",
    "SUPERSEDED_PREAMBLES",
    "SUPERSEDED_RULE_WORDINGS",
    "LedgerDifference",
    "Rule",
    "adopt_preamble",
    "adopt_rule",
    "audiences_of",
    "compare_ledgers",
    "human_rules",
    "is_superseded_wording",
    "ledger_differences",
    "merge_ledger",
    "orchestrator_rules",
    "parse_procedures",
    "preamble",
    "refresh_superseded_preamble",
    "refresh_superseded_rules",
    "rule_bodies",
    "rule_sections",
    "unknown_audiences",
    "worker_rules",
    "wording_digest",
]
