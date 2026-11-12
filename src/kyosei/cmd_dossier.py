"""``kyo dossier``: one decision, set out for a reader outside the project (KYO-659).

Decision KYO-654 (DC8) accepted section 8 of the KYO-651 proposal: a page of eight parts,
each saying what in the store it was drawn from. The reader is someone who does not know
Kyosei, such as a hiring manager, so the page uses no record ids in its text. An id found
in a title or a description becomes a number in brackets, and the ids are listed against
those numbers at the foot.

WHAT IT READS. The store, and the turn index beside it (:mod:`kyosei.cmd_turns`), which
holds ids and times and no words. It opens no transcript. The only words of a conversation
on the page are the quotes credits already store as evidence artifacts, and those are
printed verbatim: an id inside a person's quoted words is left as they wrote it.

EXCERPT PACKS. ``kyo excerpt`` (KYO-660) makes them, apart from this page, and no page
attaches one. Part eight reads the credit's ``span_seal`` (KYO-663) and says "not sealed" for a credit
without one (:func:`seal_sentence`).

WHO PROPOSED AN OPTION (KYO-786). An alternative of a decided credit may name its proposer.
Part three names the proposer of each option, and credits the agent with an option whose
proposer the credit does not name, saying that it did so. The rule is the maintainer's: an
option is the person's only when their own words introduced it.

THE WORK A DECISION LED TO (KYO-786). Part seven lists the records that hold an
``implements`` link to the decision's record, beside the records filed from it and the
records it was recorded as blocking.

A GO-AHEAD IS NEVER A DECISION. A credit of role ``authorized`` approves a plan already
stated (KYO-644.c8). It is printed as a go-ahead, apart from the credits that decided, and
a record that holds only a go-ahead is said to have no credit naming who decided it.

RECONSTRUCTED. Whether a credit was written after the fact is derived by
:func:`kyosei.render.credit_reconstructed`, as everywhere else. A reconstructed credit is
printed with the date it was written and the approval it rests on (its ``basis_id``).

THE FORM. Markdown: it reads as plain text in a terminal, renders where a portfolio or a
repository is read, and converts to HTML or PDF with ordinary tools, so one renderer serves
all three. ``--json`` carries the same parts as data, each with its ``source``.
"""

from __future__ import annotations

import re
from argparse import Namespace
from dataclasses import dataclass, field

from . import cmd_discuss, cmd_turns, cmd_views, export, ids
from .commands import Context, Output, _load_registry
from .config import person_label
from .credits import AGENT_PROPOSERS, proposer_of
from .errors import NotFound, UsageError
from .render import _instant, credit_reconstructed, current_credits, dossier_markdown
from .store import Store

#: How many related records part seven lists before it counts the rest.
RELATED_LIMIT = 12

#: Part eight's two sentences about the seal. ``span_seal`` is the credit field the accepted
#: design names for it (KYO-651 section 7.1, built by KYO-663); a credit without one, which
#: is every credit until that lands and every reconstructed credit after it, is not sealed.
#: No page attaches an excerpt pack (KYO-660), so neither sentence offers one.
NOT_SEALED = (
    "Not sealed: the credit stores no seal of the conversation, so no excerpt of it can "
    "be verified against this record, and none is attached."
)
SEALED = (
    "Sealed: the credit stores a seal over the discussion it cites, made from commitments "
    "written as each message was sent. No excerpt is attached to this page."
)


def seal_sentence(credit: dict | None) -> str:
    return SEALED if credit and credit.get("span_seal") else NOT_SEALED


#: The relation of a record that only names the decision in its title or description. The
#: store does not say such a record follows from the decision, so part seven lists it apart
#: from the linked work and the timeline leaves it out.
NAMES_IT = "names this decision in its text"

#: The relation of a record that holds an ``implements`` link to the decision (KYO-786).
IMPLEMENTS_IT = "recorded as carrying out this decision"

#: Part three's sentence when a credit names no proposer for an option (KYO-786).
PROPOSER_DEFAULTED = (
    "Where the credit names no proposer for an option, this page credits the agent. The "
    "project's rule is that an option is the person's only when their own words "
    "introduced it."
)

_OUTCOME_CLAUSE = {
    "accepted": "accepting the recommendation",
    "refined": "accepting the recommendation and changing it in a later message",
    "overrode": "overriding the recommendation",
    "unprompted": "without a recommendation having been made",
}

_CHECK_SENTENCE = {
    "matched": (
        "The quoted words were checked against the transcript of the session when the "
        "credit was written, and were found in a message the person sent."
    ),
    "matched-paste-unknown": (
        "The quoted words were checked against the transcript of the session when the "
        "credit was written, and were found in a message the person sent; the transcript "
        "does not say whether that message was typed or pasted."
    ),
    "transcript-absent": (
        "The quoted words were not checked: the transcript of the session was no longer "
        "available when the credit was written."
    ),
    "unreadable": (
        "The quoted words were not checked: the transcript of the session could not be read."
    ),
    "not-applicable": (
        "The quoted words were not checked against a transcript: they were said outside "
        "a recorded session, or the project does not read transcripts."
    ),
}

_METHOD_WORDS = {
    "human-observed": "observed by the person",
    "agent-observed": "observed by an agent",
    "machine-checked": "checked by a test",
    "reasoned-about": "argued from reading, not observed",
}

_CHILD_WORDS = {
    "criterion": "a criterion",
    "note": "a note",
    "gate": "a gate",
    "touch": "a declared file",
    "claim": "a claim",
    "verification": "a verdict",
    "evidence": "an evidence artifact",
    "dispatch": "a hand-off to an agent",
    "report": "an agent's report",
    "flag": "a flag",
    "doc-ref": "a document reference",
    "docs-obligation": "a documentation obligation",
    "credit": "a credit",
    "turn-ref": "a turn reference",
}


# ------------------------------------------------------------------- references


@dataclass
class _Refs:
    """The numbered references of one page. An id is given a number the first time the
    page cites it, and the foot lists the ids in that order."""

    store: Store
    numbers: dict[str, int] = field(default_factory=dict)
    whats: dict[str, str] = field(default_factory=dict)
    _titles: dict[str, tuple[str, str] | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        prefix = re.escape(self.store.prefix)
        self._id = rf"{prefix}-[A-Z]?[1-9][0-9]*(?:\.[a-z][1-9][0-9]*)*"
        self._id_re = re.compile(rf"(?<![\w-]){self._id}(?!\d)")
        # A markdown link whose text is an id (`[KYO-1](kyosei://…)`): the id alone is kept.
        self._link_re = re.compile(rf"\[({self._id})\]\([^)\s]*\)")

    def record(self, record_id: str) -> tuple[str, str] | None:
        """``(kind, title)`` of a record, or None when the store holds none by that id."""
        if record_id not in self._titles:
            try:
                row = self.store.record(record_id)
                self._titles[record_id] = (row["kind"], row["title"])
            except NotFound:
                self._titles[record_id] = None
        return self._titles[record_id]

    def describe(self, identifier: str) -> str:
        owner = ids.record_of_child(identifier)
        if owner is not None:
            child = _CHILD_WORDS.get(ids.child_kind(identifier) or "", "a part")
            return f"{child} of {self.describe(owner)}"
        if not ids.is_record_id(identifier):
            return "an entry in the project's ledgers"
        found = self.record(identifier)
        if found is None:
            return "a record this store does not hold"
        return f'the {found[0]} record "{found[1]}"'

    def cite(self, identifier: str, what: str | None = None) -> str:
        """The bracketed number of ``identifier``, allocated on first use."""
        if identifier not in self.numbers:
            self.numbers[identifier] = len(self.numbers) + 1
            self.whats[identifier] = what or self.describe(identifier)
        return f"[{self.numbers[identifier]}]"

    def scrub(self, text: str) -> str:
        """``text`` with every record id replaced by its reference number."""
        text = self._link_re.sub(lambda m: m.group(1), text)
        return self._id_re.sub(lambda m: self.cite(m.group(0)), text)

    def listing(self) -> list[dict]:
        return [
            {"n": n, "id": identifier, "what": self.whats[identifier]}
            for identifier, n in sorted(self.numbers.items(), key=lambda item: item[1])
        ]


# ---------------------------------------------------------------------- helpers


def _when(stamp: str | None) -> str:
    """``2026-09-19 20:17 UTC`` from a stored timestamp."""
    if not stamp:
        return "at a time the store does not hold"
    return f"{stamp[:10]} {stamp[11:16]} UTC"


def _span_of_time(seconds: float) -> str:
    seconds = int(round(abs(seconds)))
    if seconds < 120:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours"
    return f"{hours // 24} days"


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _quote_block(text: str) -> str:
    """A person's words as a markdown block quote, verbatim."""
    return "\n".join(f"> {line}" if line else ">" for line in text.strip("\n").split("\n"))


def _first_line(text: str) -> str:
    lines = [line for line in text.strip().split("\n") if line.strip()]
    if not lines:
        return ""
    return lines[0] + (" [...]" if len(lines) > 1 else "")


def _opening(description: str, sentences: int = 3) -> tuple[str, bool]:
    """The first paragraph of a description, cut to ``sentences`` sentences, and whether
    anything of the description was left off. Headings and the ``Spawned from`` footer
    `kyo spawn` appends are not prose and are skipped."""
    body = description.split("\n---\n")[0]
    paragraphs = [
        " ".join(line.strip() for line in block.split("\n"))
        for block in re.split(r"\n\s*\n", body)
        if block.strip() and not block.lstrip().startswith("#")
    ]
    if not paragraphs:
        return "", False
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[\"'`*(])", paragraphs[0])
    shown = " ".join(parts[:sentences])
    return shown, len(parts) > sentences or len(paragraphs) > 1


def _quotes(doc: dict) -> dict[str, str]:
    return {e["id"]: e["text"] for e in doc["evidence_artifacts"] if not e["redacted_at"]}


def _decided(doc: dict) -> list[dict]:
    return [c for c in current_credits(doc) if c["role"] == "decided"]


def _authorized(doc: dict) -> list[dict]:
    return [c for c in current_credits(doc) if c["role"] == "authorized"]


@dataclass
class _Page:
    """What every part reads: the store, the decision's document, its credits and the
    records around it."""

    ctx: Context
    store: Store
    doc: dict
    refs: _Refs
    credits: list[dict]
    quotes: dict[str, str]
    discussion: dict | None = None
    related: list[dict] = field(default_factory=list)
    related_left_off: int = 0

    def person(self, key: str) -> str:
        return person_label(self.store.config, key)

    def labelled(self, blocks: list[str], index: int) -> list[str]:
        """``blocks`` under a label when the record holds several decided credits."""
        if len(self.credits) < 2:
            return blocks
        return [f"*Decision {index + 1} of {len(self.credits)} on this record.*", *blocks]


# ------------------------------------------------------------- the related work


def _related_ids(store: Store, doc: dict) -> list[tuple[str, str]]:
    """``(record id, how it relates)`` for the work the store ties to this decision: the
    record itself when it carries criteria or hand-offs, records filed from it, records
    linked as implementing it (KYO-786), records it was recorded as blocking, and work
    records whose title or description names it."""
    registry = _load_registry(store)
    out: dict[str, str] = {}
    if doc["criteria"] or doc["dispatches"]:
        out[doc["id"]] = "the record the decision is on"
    for link in doc["links"]:
        if link["type"] == "produced-by" and link["direction"] == "incoming":
            out.setdefault(link["other"], "filed from this decision")
    for link in doc["links"]:
        if link["type"] == "implements" and link["direction"] == "incoming":
            out.setdefault(link["other"], IMPLEMENTS_IT)
    for link in doc["links"]:
        if link["type"] == "blocks" and link["direction"] == "outgoing":
            out.setdefault(link["other"], "recorded as waiting on this decision")
    sources = {
        link["other"]
        for link in doc["links"]
        if link["type"] == "produced-by" and link["direction"] == "outgoing"
    }
    named = re.compile(rf"(?<![\w-]){re.escape(doc['id'])}(?!\d)")
    rows = store.conn.execute(
        "SELECT id, kind, title, description FROM records "
        "WHERE (title LIKE ? OR description LIKE ?) AND id != ? ORDER BY seq",
        (f"%{doc['id']}%", f"%{doc['id']}%", doc["id"]),
    )
    for row in rows:
        kind = registry.get(row["kind"])
        if kind is None or kind.archetype != "work" or row["id"] in sources:
            continue
        if named.search(row["title"]) or named.search(row["description"]):
            out.setdefault(row["id"], NAMES_IT)
    return list(out.items())


def _related(page: _Page) -> None:
    found = _related_ids(page.store, page.doc)
    page.related_left_off = max(0, len(found) - RELATED_LIMIT)
    docs = []
    for record_id, relation in found[:RELATED_LIMIT]:
        other = (
            page.doc
            if record_id == page.doc["id"]
            else export.record_document(page.store, record_id)
        )
        docs.append({**other, "relation": relation})
    standings = cmd_views._standings(page.store, docs)
    page.related = [{**d, "standing": standings[d["id"]]} for d in docs]


def _criteria_clause(doc: dict) -> str | None:
    live = [c for c in doc["criteria"] if not c["state"].startswith("void")]
    if not live:
        return None
    met = [c for c in live if c["state"] == "met"]
    clause = f"{len(met)} of {_count(len(live), 'criterion', 'criteria')} met"
    methods: dict[str, int] = {}
    for criterion in met:
        if criterion["verifications"]:
            method = criterion["verifications"][-1]["method"]
            methods[method] = methods.get(method, 0) + 1
    if methods:
        clause += ": " + ", ".join(
            f"{n} {_METHOD_WORDS.get(method, method)}" for method, n in methods.items()
        )
    return clause


# -------------------------------------------------------------------- the parts


def _part(key: str, heading: str, blocks: list[str], source: str) -> dict:
    return {"key": key, "heading": heading, "blocks": [b for b in blocks if b], "source": source}


def _part_decision(page: _Page) -> dict:
    title = re.sub(r"^Decision:\s*", "", page.doc["title"]).strip()
    title = page.refs.scrub(title[:1].upper() + title[1:])
    return _part(
        "decision",
        "The decision",
        [title if title.endswith((".", "?", "!")) else f"{title}."],
        f"the title of the decision record {page.refs.cite(page.doc['id'])}.",
    )


def _part_problem(page: _Page) -> dict:
    shown, cut = _opening(page.doc["description"])
    blocks = [page.refs.scrub(shown)] if shown else ["The record carries no description."]
    drawn = ["the opening of the decision record's description, as written"]
    if cut:
        drawn[0] += " (the rest is left off)"
    filed_from: list[str] = []
    for link in page.doc["links"]:
        if link["type"] != "produced-by" or link["direction"] != "outgoing":
            continue
        found = page.refs.record(link["other"])
        if found is None:
            continue
        filed_from.append(
            f'the {found[0]} "{page.refs.scrub(found[1])}" {page.refs.cite(link["other"])}'
        )
    if page.discussion and not any(
        link["other"] == page.discussion["id"] for link in page.doc["links"]
    ):
        found = page.refs.record(page.discussion["id"])
        if found is not None:
            filed_from.append(
                f'the discussion "{page.refs.scrub(found[1])}" '
                f"{page.refs.cite(page.discussion['id'])}"
            )
    if filed_from:
        blocks.append(f"The decision was filed from {' and '.join(filed_from)}.")
        drawn.append("the titles of the records it was filed from")
    return _part("problem", "The problem", blocks, "; ".join(drawn) + ".")


def _recommender(page: _Page, credit: dict) -> str:
    by = credit.get("recommended_by")
    if not by:
        return "The agent"
    if by == credit["person"]:
        return page.person(by)
    return f"The agent ({by})"


def _proposer(page: _Page, alternative: dict) -> str:
    """Who proposed an option, in the page's words: a declared person by name, the
    orchestrator as "the agent", a worker as an agent the work was handed to."""
    by = proposer_of(alternative)
    if by == "worker":
        return "an agent the work was handed to"
    if by in AGENT_PROPOSERS:
        return "the agent"
    return page.person(by)


def _part_table(page: _Page) -> dict:
    blocks: list[str] = []
    for index, credit in enumerate(page.credits):
        own: list[str] = []
        alternatives = credit.get("alternatives") or []
        if alternatives:
            own.append(
                "\n".join(
                    f"- {page.refs.scrub(a['text'])}"
                    + (" (chosen)" if a.get("chosen") else "")
                    + f", proposed by {_proposer(page, a)}"
                    for a in alternatives
                )
            )
            if not all(a.get("proposed_by") for a in alternatives):
                own.append(PROPOSER_DEFAULTED)
        if credit.get("recommended"):
            recommended = page.refs.scrub(credit["recommended"]).rstrip()
            own.append(
                f"{_recommender(page, credit)} recommended: {recommended}"
                + ("" if recommended.endswith((".", "?", "!")) else ".")
            )
        elif credit.get("outcome") == "unprompted":
            own.append("No recommendation was made: the person decided unprompted.")
        else:
            own.append("The store does not record what, if anything, was recommended.")
        if not alternatives:
            own.append("The credit lists no alternatives.")
        blocks.extend(page.labelled(own, index))
    notes = _discussion_notes(page)
    if notes:
        blocks.append("What the agent noted as put to the person during the discussion:")
        blocks.extend(notes)
    if not page.credits:
        blocks.append(
            "The store holds no credit naming who decided this, and so no account of "
            "what was recommended or what else was weighed."
        )
    source = (
        "the recommendation, the alternatives and the proposer of each stored on the "
        "credit naming who decided"
    )
    if page.credits:
        source += " " + ", ".join(page.refs.cite(c["id"]) for c in page.credits)
    if notes:
        source += "; the notes of the discussion record"
    return _part("table", "What was on the table", blocks, source + ".")


def _discussion_notes(page: _Page) -> list[str]:
    if not page.discussion:
        return []
    document = export.record_document(page.store, page.discussion["id"])
    texts = {n["id"]: n["text"] for n in document["notes"] if not n["redacted_at"]}
    out = []
    for ref in document["turn_refs"]:
        if ref["role"] == cmd_discuss.ROLE_NOTE and texts.get(ref.get("note_id")):
            out.append(_quote_block(page.refs.scrub(texts[ref["note_id"]])))
            page.refs.cite(ref["note_id"])
    return out


def _words(page: _Page, credit: dict) -> list[str]:
    quote = page.quotes.get(credit["quote_id"])
    if quote is None:
        return ["The words were removed from the store, and are not shown."]
    form = credit.get("words_form")
    cited = page.refs.cite(credit["quote_id"], "the person's words, stored verbatim")
    lead = f"Their words {cited}:"
    if form == "choice":
        lead = (
            "They chose among options the agent offered. The text is the option they "
            f"chose, not their writing {cited}:"
        )
    elif form == "accepted-suggestion":
        lead = (
            "They sent a prompt the tool had suggested. The text is their choice, not "
            f"their writing {cited}:"
        )
    elif form == "mid-turn":
        lead = f"Their words, sent while the agent was working {cited}:"
    return [lead, _quote_block(quote)]


def _part_who(page: _Page) -> dict:
    blocks: list[str] = []
    cited: list[str] = []
    for index, credit in enumerate(page.credits):
        clause = _OUTCOME_CLAUSE.get(credit.get("outcome") or "")
        sentence = f"{page.person(credit['person'])} decided"
        sentence += f", {clause}." if clause else "; the store does not record the outcome."
        said = ""
        if credit.get("source_note"):
            said = f" The words were said outside a recorded session: {credit['source_note']}."
        cited.append(page.refs.cite(credit["id"]))
        blocks.extend(page.labelled([sentence + said, *_words(page, credit)], index))
    if not page.credits:
        blocks.append("The store holds no credit naming who decided this.")
    for credit in _authorized(page.doc):
        blocks.append(
            f"{page.person(credit['person'])} gave a go-ahead for work to proceed. A "
            "go-ahead approves a plan already stated; it is not a decision and is not "
            "counted as one."
        )
        blocks.extend(_words(page, credit))
        cited.append(page.refs.cite(credit["id"]))
    source = "the person, outcome and quoted words of the credits on the decision record"
    if cited:
        source += " " + ", ".join(cited)
    return _part("who", "Who decided, and how", blocks, source + ".")


def _index_turns(page: _Page, credit: dict) -> tuple[list[dict], bool]:
    """The person's confirmed turns the index holds between the discussion's start and the
    deciding words, and whether the index holds any line for that session."""
    session = credit.get("span_session") or (page.discussion or {}).get("opened_session")
    start = credit.get("span_started_at") or (page.discussion or {}).get("opened_at")
    end = credit.get("span_ended_at") or credit.get("observed_at")
    if not session or not start or not end:
        return [], False
    lines = [line for line in cmd_turns.read_lines(page.ctx.root) if line.get("session") == session]
    low, high = _instant(start), _instant(end)
    seen: set[str] = set()
    turns = []
    for line in lines:
        at = _instant(line.get("at") or "")
        key = line.get("prompt_id") or line.get("turn")
        if line.get("origin") != cmd_turns.HUMAN or at is None or key in seen:
            continue
        # The hook's clock and the transcript's differ by a fraction of a second.
        if low is not None and high is not None and low - 1 <= at <= high + 1:
            seen.add(key)
            turns.append(line)
    return turns, bool(lines)


def _part_timeline(page: _Page) -> dict:
    events: list[tuple[str, str]] = []
    drawn = ["the store's own timestamps"]
    known_turns: set[str] = set()
    unindexed = False
    discussion_doc = (
        export.record_document(page.store, page.discussion["id"]) if page.discussion else None
    )
    for ref in (discussion_doc or {}).get("turn_refs", []):
        at = ref.get("at") or ref["created_at"]
        stamped = "" if ref.get("at") else " (the time the reference was written)"
        if ref["role"] == cmd_discuss.ROLE_OPENED:
            if ref.get("turn"):
                known_turns.add(ref["turn"])
            if ref["late"]:
                events.append(
                    (at, "The person sends the message the discussion began at. The "
                     "discussion record naming it was opened afterwards." + stamped)
                )  # fmt: skip
            else:
                events.append(
                    (at, "A discussion record is opened at a message from the person." + stamped)
                )
            drawn.append("the discussion's turn references")
        elif ref["role"] == cmd_discuss.ROLE_NOTE:
            events.append((ref["created_at"], "The agent notes what was put to the person."))
    for credit in page.credits:
        who = page.person(credit["person"])
        if not page.discussion and credit.get("span_started_at"):
            events.append((credit["span_started_at"], "The discussion the credit cites begins."))
        turns, any_lines = _index_turns(page, credit)
        if credit.get("span_session") and not any_lines:
            unindexed = True
        for line in turns:
            if line.get("turn") in known_turns or line.get("turn") == credit.get("source_turn"):
                continue
            events.append((line["at"], f"{who} sends a message."))
            drawn.append("the turn index")
        if credit.get("observed_at"):
            events.append((credit["observed_at"], f"{who} sends the words quoted above."))
        if credit_reconstructed(page.doc, credit):
            events.append(
                (
                    credit["created_at"],
                    "The credit naming who decided is written, from the record of the session.",
                )
            )
    events.append((page.doc["created_at"], "The decision is recorded in the store."))
    for credit in _authorized(page.doc):
        events.append(
            (credit.get("observed_at") or credit["created_at"],
             f"{page.person(credit['person'])} gives a go-ahead for work to proceed.")
        )  # fmt: skip
    for other in page.related:
        if other["relation"] == NAMES_IT:
            continue
        title = page.refs.scrub(other["title"])
        if other["dispatches"]:
            events.append(
                (other["dispatches"][0]["created_at"], f'Work is handed to an agent: "{title}".')
            )
        for credit in current_credits(other):
            if credit["role"] != "observed":
                continue
            events.append(
                (credit.get("observed_at") or credit["created_at"],
                 f'{page.person(credit["person"])} checks a result of "{title}".')
            )  # fmt: skip
    events.sort(key=lambda event: _instant(event[0]) or 0.0)
    seen: set[tuple[str, str]] = set()
    lines = []
    for at, text in events:
        key = (_when(at), text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {_when(at)}: {text}")
    blocks = ["\n".join(lines)]
    if unindexed:
        blocks.append(
            "The turn index holds no lines for the session of this discussion, so the "
            "person's messages between its start and the decision are counted under "
            "Depth and not timed here."
        )
    source = ", ".join(dict.fromkeys(drawn))
    return _part(
        "timeline",
        "Timeline",
        blocks,
        f"{source}. Every time was written by the tool; none was typed.",
    )


def _part_depth(page: _Page) -> dict:
    blocks: list[str] = []
    for index, credit in enumerate(page.credits):
        if credit.get("span_session") is None:
            own = ["Not measured: the credit cites no span of discussion."]
        elif credit.get("span_person_turns") is None:
            own = [
                "Not measured: the credit cites a span of discussion, and no transcript "
                "was read to count it."
            ]
        else:
            own = [
                f"{_count(credit['span_person_turns'], 'message', 'messages')} from "
                f"{page.person(credit['person'])} and "
                f"{_count(credit['span_agent_turns'], 'reply', 'replies')} from the agent, "
                f"over {_span_of_time(credit['span_active_seconds'])} of active time. "
                "Gaps longer than fifteen minutes are left out of the active time."
            ]
        blocks.extend(page.labelled(own, index))
    if not page.credits:
        blocks.append("Not measured: there is no credit to carry a span of discussion.")
    return _part(
        "depth",
        "Depth",
        blocks,
        "counts and times the tool measured over the session when the credit was "
        "written; the store holds the counts and none of the text.",
    )


def _next_lines(page: _Page, other: dict) -> list[str]:
    details = [f"{other['kind']}, {other['standing']}"]
    if other["relation"] != NAMES_IT:
        details.insert(0, other["relation"])
    if other["dispatches"]:
        details.append(
            f"handed to an agent {_count(len(other['dispatches']), 'time', 'times')}, "
            f"first on {_when(other['dispatches'][0]['created_at'])}"
        )
    criteria = _criteria_clause(other)
    if criteria:
        details.append(criteria)
    lines = [
        f'- "{page.refs.scrub(other["title"])}" {page.refs.cite(other["id"])}: '
        + "; ".join(details)
        + "."
    ]
    quotes = _quotes(other)
    for credit in current_credits(other):
        if credit["role"] == "decided" or (
            credit["role"] == "authorized" and other["id"] == page.doc["id"]
        ):
            continue
        words = quotes.get(credit["quote_id"])
        said = f': "{_first_line(words)}"' if words else ""
        verb = (
            "gave a go-ahead for the work (not a decision)"
            if credit["role"] == "authorized"
            else "observed the result"
        )
        lines.append(
            f"  - {page.person(credit['person'])} {verb} on "
            f"{_when(credit.get('observed_at') or credit['created_at'])}"
            f"{said} {page.refs.cite(credit['id'])}"
        )
    return lines


def _part_next(page: _Page) -> dict:
    linked = [o for o in page.related if o["relation"] != NAMES_IT]
    naming = [o for o in page.related if o["relation"] == NAMES_IT]
    blocks: list[str] = []
    if linked:
        blocks.append("\n".join(line for o in linked for line in _next_lines(page, o)))
    else:
        blocks.append("The store links no work to this decision.")
    if naming:
        blocks.append(
            "Other work records name this decision in their text. The store does not "
            "record whether they follow from it:"
        )
        blocks.append("\n".join(line for o in naming for line in _next_lines(page, o)))
    if page.related_left_off:
        blocks.append(
            f"{_count(page.related_left_off, 'further record', 'further records')} "
            "naming this decision "
            f"{'is' if page.related_left_off == 1 else 'are'} not listed."
        )
    return _part(
        "next",
        "What happened next",
        blocks,
        "the record's links (records filed from the decision, records linked as "
        "implementing it, records it was recorded as blocking) and a search of work "
        "records for its id; for each, its hand-offs "
        "to agents, the method of the latest verdict on each met criterion, and the "
        "person's credited observations.",
    )


def _made(page: _Page, credit: dict) -> list[str]:
    out: list[str] = []
    who = page.person(credit["person"])
    if credit_reconstructed(page.doc, credit):
        lag = (_instant(credit["created_at"]) or 0) - (_instant(page.doc["created_at"]) or 0)
        sentence = (
            f"Reconstructed: the credit naming {who} was written on "
            f"{_when(credit['created_at'])}, {_span_of_time(lag)} after the decision record."
        )
        if credit.get("basis_id"):
            try:
                basis = page.store.credit(credit["basis_id"])
                found = page.refs.record(basis["record_id"])
                title = f' "{page.refs.scrub(found[1])}"' if found else ""
                sentence += (
                    f" It rests on an approval {page.person(basis['person'])} gave on "
                    f"{_when(basis['observed_at'] or basis['created_at'])}, recorded as"
                    f"{title} {page.refs.cite(credit['basis_id'])}."
                )
            except NotFound:
                sentence += " The approval it names is not in this store."
        else:
            sentence += " The store records no approval for it."
        out.append(sentence)
    else:
        sentence = "Recorded live: the credit was written with the decision record."
        observed, made = _instant(credit.get("observed_at") or ""), _instant(credit["created_at"])
        if observed is not None and made is not None and made >= observed:
            sentence += (
                f" That was {_span_of_time(made - observed)} after {who} sent the quoted words."
            )
        out.append(sentence)
    out.append(_CHECK_SENTENCE.get(credit["transcript_check"], credit["transcript_check"]))
    out.append(seal_sentence(credit))
    return out


def _part_made(page: _Page) -> dict:
    blocks: list[str] = []
    for index, credit in enumerate(page.credits):
        blocks.extend(page.labelled(_made(page, credit), index))
    if not page.credits:
        blocks.append(
            "The decision record was written on "
            f"{_when(page.doc['created_at'])}. No credit names who decided it, so "
            "nothing was checked against a transcript."
        )
        blocks.append(seal_sentence(None))
    discussion = page.discussion
    if discussion is None:
        blocks.append(
            "No discussion record is tied to this decision, so the store did not record "
            "when the discussion began."
        )
    elif discussion["late"]:
        blocks.append(
            "The discussion record was opened late, after the decision. The turn it "
            "names as the start was chosen afterwards and not recorded as it happened."
        )
    elif discussion["index_state"] != "indexed":
        blocks.append(
            "A discussion record was opened before the decision, at a time taken from "
            "the clock: the turn index held no line to stamp it from."
        )
    else:
        blocks.append(
            f"A discussion record was opened before the decision, at the person's "
            f"message of {_when(discussion['opened_at'])}, stamped from the turn index."
        )
    return _part(
        "made",
        "How this record was made",
        blocks,
        "the timestamps of the credit and of the decision record, the credit's stored "
        "transcript check and approval, and the discussion's opening reference.",
    )


# ------------------------------------------------------------------ the command


def _select(doc: dict, credit_id: str | None) -> list[dict]:
    decided = _decided(doc)
    if credit_id is None:
        return decided
    chosen = [c for c in decided if c["id"] == credit_id]
    if not chosen:
        raise UsageError(
            f"{credit_id} is not a current credit of role `decided` on {doc['id']}; "
            "a dossier is drawn from a credit that names who decided"
        )
    return chosen


def dossier(ctx: Context, store: Store, identifier: str) -> dict:
    """The dossier of one decision as data: its eight parts and its references."""
    credit_id = identifier if ids.child_kind(identifier) == "credit" else None
    record_id = ids.record_of_child(identifier) if credit_id else identifier
    if not ids.is_record_id(record_id or ""):
        raise UsageError(
            f"a dossier is drawn from a record id or the id of a credit; {identifier!r} is neither"
        )
    doc = export.record_document(store, record_id)
    credits = _select(doc, credit_id)
    if doc["kind"] != "decision" and not credits:
        raise UsageError(
            f"{record_id} is a {doc['kind']!r} that no credit records a decision on; "
            "a dossier describes one decision"
        )
    page = _Page(
        ctx=ctx,
        store=store,
        doc=doc,
        refs=_Refs(store),
        credits=credits,
        quotes=_quotes(doc),
        discussion=cmd_discuss.discussion_summary(store, record_id),
    )
    _related(page)
    parts = [
        _part_decision(page),
        _part_problem(page),
        _part_table(page),
        _part_who(page),
        _part_timeline(page),
        _part_depth(page),
        _part_next(page),
        _part_made(page),
    ]
    return {
        "object": "dossier",
        "id": record_id,
        "record_id": record_id,
        "credit_ids": [c["id"] for c in credits],
        "reconstructed": any(credit_reconstructed(doc, c) for c in credits),
        "sealed": bool(credits) and all(c.get("span_seal") for c in credits),
        "format": "markdown",
        "parts": parts,
        "references": page.refs.listing(),
    }


def cmd_dossier(ctx: Context, args: Namespace) -> Output:
    data = dossier(ctx, ctx.store(), args.id)
    page = dossier_markdown(data)
    return Output([page], {**data, "markdown": page}, bare=True)
