"""``kyo excerpt``: a credit's discussion, trimmed by the person and committed to a pack (KYO-660).

Decision KYO-654 (DC7) accepted sections 7.2 to 7.4 of the KYO-651 proposal. A credit that
cites a discussion span (KYO-644.c5) holds ``span_seal``, the hash of the span's last line
of the turn index (KYO-663). The turn index commits every message by line, with a salt the
reader cannot guess, so a person can show some lines of a message and withhold the rest, and
a reader can check that what is shown was in the message committed when it was sent. This
command makes that pack. A reader checks it with ``tools/kyosei-verify-excerpt.py``, which
imports nothing from this package.

THE THREE MODES, in the order the person meets them.

``--draft`` writes the readable page of every line in the span, the agent's included, to the
session's scratch directory, with a marker ``[m2.l3]`` (message 2, line 3) before each line,
and lists the lines that hold a term from the store's private-words list (and the other
private terms :mod:`kyosei.private_terms` knows), by marker and kind and never by the term.
The draft is the selection: a line deleted from it is a line withheld.

``--preview --from <draft>`` renders the pack as its reader will see it, withheld positions
included. ``--pack --from <draft> --approved-by <credit>`` writes it, and only with the
person's approval: an ``authorized`` credit (KYO-644.c8) on the same record as the excerpted
credit. Without one the command is refused under the contract ``excerpt-requires-approval``.

WHAT THE PACK HOLDS. The credit as the export holds it, with the words it quotes; the id of
the approving credit; and, for each message of the span, its index line and, for each line
shared, its text, its salt and its Merkle proof. A line not shared is its position only. The
seal key, the text of a line not shared and the transcript never enter it.

WHERE THE TEXT COMES FROM. The turn index holds no words, so the command reads the session's
transcript, or its archived copy, and takes from it the text a message's root commits. A text
is used only if :func:`kyosei.seal.verify_message` accepts it against the root on the index
line; a message whose transcript text does not reproduce its root (the hook saw a prompt the
transcript records differently) is listed in the draft as unreproducible and is withheld
from every pack, never shown on a guess. A message with no root, which is every message sent
mid-turn (KYO-663), was never committed and is withheld the same way.

The salts, proofs and index lines come from :mod:`kyosei.seal`, the functions the hooks and
``kyo turns --verify`` use, so a pack and that check cannot disagree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path

from . import cmd_turns, export, ids, private_terms, seal
from .cmd_sessions import SESSION_ID_ENV, read_sessions, recorded_transcript_path, sessions_path
from .commands import Context, Output
from .config import transcripts_opted_in
from .credits import ROLE_AUTHORIZED
from .errors import ContractViolation, UsageError
from .harness import claude_code_transcript as transcript
from .render import current_credits

CONTRACT_EXCERPT_REQUIRES_APPROVAL = "excerpt-requires-approval"

PACK_OBJECT = "excerpt-pack"
PACK_FORMAT = 1

#: A draft line: its marker, then the line's text. ``m`` counts the span's messages from 1
#: and ``l`` counts a message's lines from 0, which is how the pack's proofs count them.
MARKER = re.compile(r"^\[m(\d+)\.l(\d+)\] ?(.*)$")

#: The index fields a pack carries for each message, beside the committed ``root``.
INDEX_FIELDS = ("seal", "session", "prompt_id", "at", "role", "root", "lines", "prev", "origin")

_DRAFT_HEADING = "# Excerpt draft"


@dataclass
class Message:
    """One message of the span: its index line and, when it can be shared, its text."""

    number: int
    index: dict
    lines: list[str] | None = None
    #: Why no text is available, when ``lines`` is None.
    why: str = ""

    @property
    def shareable(self) -> bool:
        return self.lines is not None


@dataclass
class Span:
    credit: dict
    record_id: str
    session: str
    messages: list[Message] = field(default_factory=list)
    quote: str | None = None


# --------------------------------------------------------------------------- the span


def _credit_of(ctx: Context, identifier: str) -> tuple[dict, dict]:
    """``(export document, current credit)`` for a credit id."""
    if ids.child_kind(identifier) != "credit":
        raise UsageError(f"{identifier!r} is not a credit id; an excerpt is drawn from one credit")
    record_id = ids.record_of_child(identifier) or ""
    doc = export.record_document(ctx.store(), record_id)
    for credit in current_credits(doc):
        if credit["id"] == identifier:
            return doc, credit
    raise UsageError(f"{identifier} is not a current credit of {record_id}")


def _refuse_unsealed(credit: dict) -> None:
    if not credit.get("span_session") or not credit.get("span_last_turn"):
        raise UsageError(f"{credit['id']} cites no discussion span, so there is nothing to excerpt")
    if not credit.get("span_seal"):
        raise UsageError(
            f"{credit['id']} has an empty span_seal: its turns were never sealed in the turn "
            "index (a reconstructed credit, or a span indexed before sealing began), so no "
            "excerpt of it can be verified and none can be packed"
        )


def _transcript_records(ctx: Context, session: str) -> list[dict]:
    """The session's top-level transcript records in file order, each uuid once, from the
    original or the archive."""
    from .transcript_archive import archive_dir

    try:
        paths = transcript.candidate_transcripts(
            session,
            config=transcript.config_dir(ctx.environ),
            recorded=recorded_transcript_path(ctx.root, session),
            archive=archive_dir(ctx.store().config, ctx.environ),
        )
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    if not paths:
        raise UsageError(
            f"no transcript, original or archived, for session {session}: the text of its "
            "messages cannot be read, so no excerpt can be drafted"
        )
    records: dict[str, dict] = {}
    for path in paths:
        try:
            for record in transcript._parse_lines(path):  # noqa: SLF001 - the module's own reader
                uuid = record.get("uuid")
                if isinstance(uuid, str) and not record.get("isSidechain"):
                    records.setdefault(uuid, record)
        except OSError as exc:
            raise UsageError(f"the transcript of session {session} cannot be read: {exc}") from exc
    return list(records.values())


@dataclass
class Transcript:
    """What the candidates are drawn from: records by uuid, and the agent's texts by the
    prompt id of the turn they fall in."""

    by_uuid: dict[str, dict]
    replies: dict[str, list[str]]

    @classmethod
    def of(cls, records: list[dict]) -> Transcript:
        replies: dict[str, list[str]] = {}
        current: str | None = None
        for record in records:
            opened = transcript._opens_prompt(record)  # noqa: SLF001
            if opened is not None:
                current = opened
            if record.get("type") != "assistant" or current is None:
                continue
            text = transcript._block_text((record.get("message") or {}).get("content"))  # noqa: SLF001
            if text.strip():
                replies.setdefault(current, []).append(text)
        return cls({r["uuid"]: r for r in records}, replies)


def _candidates(line: dict, source: Transcript) -> list[str]:
    """Texts the transcript offers for the message on ``line``, most likely first."""
    if line.get("role") == seal.ROLE_ASSISTANT:
        # The reply the Stop hook committed is the last text of its turn; an earlier Stop
        # of the same turn committed an earlier one, so every text of the turn is offered.
        return list(reversed(source.replies.get(line.get("prompt_id") or "", [])))
    record = source.by_uuid.get(line.get("turn") or "")
    if record is None:
        return []
    if record.get("type") == "attachment":
        prompt = (record.get("attachment") or {}).get("prompt")
        return [prompt] if isinstance(prompt, str) else []
    content = (record.get("message") or {}).get("content")
    found: list[str] = []
    for text in (transcript._block_text(content), transcript._text_of(content)):  # noqa: SLF001
        if isinstance(text, str) and text not in found:
            found.append(text)
    return found


def _read_key(ctx: Context) -> bytes:
    try:
        key = seal.read_key(seal.chosen_store(ctx.environ), ctx.environ)
    except seal.SealKeyError as exc:
        raise UsageError(f"the seal key cannot be read: {exc}") from exc
    if key is None:
        raise UsageError(
            "this machine holds no seal key, so the salts of the committed lines cannot be "
            "made and nothing can be packed; the key is the one secret to back up "
            f"(kept in the {seal.chosen_store(ctx.environ)} store)"
        )
    return key


def build_span(ctx: Context, identifier: str) -> tuple[Span, bytes]:
    """The credit's span as messages with their text, and the seal key."""
    if not transcripts_opted_in(ctx.store().config):
        raise UsageError(
            "this project does not read transcripts (read_transcripts is not set), so no "
            "message text is available to excerpt"
        )
    doc, credit = _credit_of(ctx, identifier)
    _refuse_unsealed(credit)
    session = credit["span_session"]
    held = cmd_turns.session_lines(ctx.root, session)
    problems = seal.verify_chain(held)
    if problems:
        raise UsageError(f"the turn index of session {session} does not verify: {problems[0]}")
    first = next(
        (n for n, line in enumerate(held) if line.get("turn") == credit["span_first_turn"]), None
    )
    last = next(
        (n for n, line in enumerate(held) if line.get("turn") == credit["span_last_turn"]), None
    )
    if first is None or last is None or first > last:
        raise UsageError(
            f"the turn index of session {session} has no line for the span's first and last "
            "turns, in that order"
        )
    chosen = held[first : last + 1]
    if not all(seal.is_sealed(line) for line in chosen):
        raise UsageError(f"the span of {identifier} includes lines indexed before sealing began")
    if seal.line_hash(chosen[-1]) != credit["span_seal"]:
        raise UsageError(
            f"the last line of the span in the turn index does not hash to {identifier}'s "
            "span_seal, so the index was changed after the credit was written"
        )
    key = _read_key(ctx)
    source = Transcript.of(_transcript_records(ctx, session))
    quotes = {e["id"]: e["text"] for e in doc["evidence_artifacts"] if not e["redacted_at"]}
    span = Span(
        credit=credit, record_id=doc["id"], session=session, quote=quotes.get(credit["quote_id"])
    )
    for number, line in enumerate(chosen, 1):
        message = Message(number, line)
        if not line.get("root"):
            message.why = "it was not committed when it was sent (a message sent mid-turn)"
        else:
            for text in _candidates(line, source):
                if seal.verify_message(key, line, text):
                    message.lines = seal.split_lines(text)
                    break
            else:
                message.why = (
                    "the transcript's text of it does not reproduce the root committed when "
                    "it was sent"
                )
        span.messages.append(message)
    return span, key


# --------------------------------------------------------------------------- the draft


def _terms(ctx: Context) -> private_terms.Terms:
    if ctx.layout is None:
        return private_terms.compile_terms((), (), common=private_terms.COMMON_WORDS)
    return private_terms.project_terms(ctx.layout, ctx.environ)


def _who(message: Message) -> str:
    return "the agent" if message.index.get("role") == seal.ROLE_ASSISTANT else "the person"


def private_hits(span: Span, terms: private_terms.Terms) -> list[tuple[str, list[str]]]:
    """``(marker, kinds)`` for each shareable line holding a private term, in order."""
    hits = []
    for message in span.messages:
        for i, text in enumerate(message.lines or []):
            kinds = terms.kinds_on(text)
            if kinds:
                hits.append((f"m{message.number}.l{i}", kinds))
    return hits


def render_draft(span: Span, terms: private_terms.Terms) -> str:
    hits = private_hits(span, terms)
    out = [
        f"{_DRAFT_HEADING} for {span.credit['id']}",
        "",
        f"Session {span.session}: {len(span.messages)} message(s). Each line of a message "
        "begins with its marker, such as [m2.l3] (message 2, line 3).",
        "",
        "Delete the lines you do not want to share. A line left here is shared, and a line "
        "deleted is withheld; do not change the text of a line you keep.",
        "",
        "## Lines holding a term from the private-words list",
    ]
    out += [f"- [{marker}] {', '.join(kinds)}" for marker, kinds in hits] or ["- none"]
    for message in span.messages:
        index = message.index
        count = len(message.lines) if message.lines is not None else index.get("lines")
        out += [
            "",
            f"## Message {message.number}: {_who(message)}, {index.get('at')}, "
            f"{count if count is not None else 'an unknown number of'} line(s)"
            + ("" if message.shareable else f"; not shareable, because {message.why}"),
        ]
        for i, text in enumerate(message.lines or []):
            out.append(f"[m{message.number}.l{i}] {text}")
    return "\n".join(out) + "\n"


def read_selection(span: Span, path: Path) -> dict[int, list[int]]:
    """The lines a draft keeps, by message number; refused when they are not lines of the
    span, in order."""
    try:
        draft = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"the draft cannot be read: {exc}") from exc
    by_number = {m.number: m for m in span.messages}
    chosen: dict[int, list[int]] = {}
    previous: tuple[int, int] = (0, -1)
    for position, raw in enumerate(draft.split("\n"), 1):
        match = MARKER.match(raw)
        if match is None:
            continue
        number, i, text = int(match.group(1)), int(match.group(2)), match.group(3)
        message = by_number.get(number)
        if message is None or not message.shareable or i >= len(message.lines or []):
            raise UsageError(
                f"line {position} of the draft is [m{number}.l{i}], which is not a shareable "
                f"line of the span; write a new draft with `kyo excerpt {span.credit['id']} "
                "--draft` and delete lines from it"
            )
        if (number, i) <= previous:
            raise UsageError(
                f"line {position} of the draft, [m{number}.l{i}], is out of order or repeated; "
                "the lines of an excerpt keep the order of the span"
            )
        if text.rstrip() != (message.lines or [])[i].rstrip():
            raise UsageError(
                f"line {position} of the draft, [m{number}.l{i}], does not hold the text of "
                "that line of the span; a line can be deleted from a draft but not changed"
            )
        previous = (number, i)
        chosen.setdefault(number, []).append(i)
    return chosen


# ---------------------------------------------------------------------------- the pack


def build_pack(
    span: Span, key: bytes, selection: dict[int, list[int]], approved_by: str | None
) -> dict:
    messages = []
    for message in span.messages:
        index = {name: message.index.get(name) for name in INDEX_FIELDS}
        shared = []
        if message.shareable and selection.get(message.number):
            text = "\n".join(message.lines or [])
            leaves = seal.leaves_of(
                key, span.session, index["prompt_id"], index["role"], index["prev"], text
            )
            for i in selection[message.number]:
                shared.append(
                    {
                        "i": i,
                        "text": (message.lines or [])[i],
                        "salt": seal.salt(
                            key, span.session, index["prompt_id"], index["role"], index["prev"], i
                        ).hex(),
                        "proof": [
                            [side, node.hex()] for side, node in seal.merkle_proof(leaves, i)
                        ],
                    }
                )
        messages.append({"index": index, "shared": shared})
    return {
        "object": PACK_OBJECT,
        "pack_format": PACK_FORMAT,
        "credit": span.credit,
        "quote": span.quote,
        "approved_by": approved_by,
        "messages": messages,
    }


def _clock(stamp: object) -> str:
    return str(stamp)[11:19] if isinstance(stamp, str) and len(stamp) >= 19 else str(stamp)


def render_pack(pack: dict) -> list[str]:
    """The pack as its reader sees it: shared lines, and the positions of withheld ones."""
    out = [
        f"Excerpt of {pack['credit']['id']}, approved by "
        f"{pack.get('approved_by') or '(not yet approved)'}."
    ]
    for n, message in enumerate(pack["messages"], 1):
        index, shared = message["index"], message["shared"]
        who = "the agent" if index.get("role") == seal.ROLE_ASSISTANT else "the person"
        total = index.get("lines")
        head = f"message {n} ({who}) at {_clock(index.get('at'))}"
        if total is None:
            out.append(f"{head}: not committed when it was sent, withheld")
        elif not shared:
            out.append(f"{head}, {total} line(s), withheld")
        else:
            out.append(f"{head}, {len(shared)} of {total} line(s) shown")
            at = 0
            for line in shared:
                if line["i"] > at:
                    out.append(f"    [{line['i'] - at} line(s) withheld]")
                out.append(f"    {line['i']:>3}  {line['text']}")
                at = line["i"] + 1
            if at < total:
                out.append(f"    [{total - at} line(s) withheld]")
    return out


# ---------------------------------------------------------------------------- approval


def _require_approval(ctx: Context, credit: dict, approved_by: str | None) -> dict:
    record_id = ids.record_of_child(credit["id"]) or ""
    how = (
        f"record the person's approval of this pack as an authorized credit on {record_id}, "
        "quoting their words: `kyo credit <id> --role authorized --quote-file <their words> "
        f"...`, where <id> is the record itself if it holds no current credit, or else a "
        f"cleared gate or a verdict of {record_id}; then pass that credit's id with --approved-by"
    )
    if not approved_by:
        raise ContractViolation(
            CONTRACT_EXCERPT_REQUIRES_APPROVAL,
            [
                "an excerpt leaves the machine only after the person approves it, and "
                "--approved-by names no credit that records that",
                how,
            ],
        )
    if ids.child_kind(approved_by) != "credit" or ids.record_of_child(approved_by) != record_id:
        raise ContractViolation(
            CONTRACT_EXCERPT_REQUIRES_APPROVAL,
            [
                f"{approved_by} is not a credit on {record_id}, the record of the excerpted credit",
                how,
            ],
        )
    doc = export.record_document(ctx.store(), record_id)
    found = next((c for c in current_credits(doc) if c["id"] == approved_by), None)
    if found is None or found["role"] != ROLE_AUTHORIZED:
        said = "is not a current credit" if found is None else f"has the role {found['role']}"
        raise ContractViolation(
            CONTRACT_EXCERPT_REQUIRES_APPROVAL,
            [f"{approved_by} {said}, and approval is an authorized credit", how],
        )
    return found


# ---------------------------------------------------------------------------- the command


def _scratch(ctx: Context, name: str, out: str | None) -> Path:
    if out:
        return Path(out).expanduser()
    session = ctx.environ.get(SESSION_ID_ENV) or os.environ.get(SESSION_ID_ENV)
    if session:
        for entry in read_sessions(sessions_path(ctx.root)).entries:
            if entry.get("session_id") == session and entry.get("scratchpad_dir"):
                return Path(entry["scratchpad_dir"]) / name
    raise UsageError(
        "no scratch directory is recorded for this session, so this command has nowhere to "
        f"write {name}; name a file with --out"
    )


def _write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def _from_draft(args: Namespace, span: Span) -> dict[int, list[int]]:
    draft = getattr(args, "draft_file", None)
    if not draft:
        raise UsageError(
            "--preview and --pack take the person's trimmed draft with --from; write one "
            f"with `kyo excerpt {span.credit['id']} --draft`"
        )
    return read_selection(span, Path(draft).expanduser())


def cmd_excerpt(ctx: Context, args: Namespace) -> Output:
    mode = "pack" if args.pack else "preview" if args.preview else "draft"
    span, key = build_span(ctx, args.id)
    credit_id = span.credit["id"]
    terms = _terms(ctx)
    if mode == "draft":
        path = _scratch(ctx, f"excerpt-{credit_id}.draft.md", args.out)
        _write(path, render_draft(span, terms))
        hits = private_hits(span, terms)
        withheld = [m.number for m in span.messages if not m.shareable]
        lines = [
            f"excerpt draft of {credit_id}: {len(span.messages)} message(s), written to {path}"
        ]
        if hits:
            lines.append(f"{len(hits)} line(s) hold a private term; the person decides about each:")
            lines += [f"  [{marker}] {', '.join(kinds)}" for marker, kinds in hits]
        else:
            lines.append("no line holds a term from the private-words list")
        if withheld:
            lines.append("not shareable: message " + ", ".join(str(n) for n in withheld))
        return Output(
            lines=lines,
            data={
                "ok": True,
                "mode": mode,
                "credit_id": credit_id,
                "draft": str(path),
                "messages": len(span.messages),
                "private_hits": [{"marker": m, "kinds": k} for m, k in hits],
                "not_shareable": withheld,
            },
        )
    selection = _from_draft(args, span)
    approving: dict | None = None
    if mode == "pack":
        approving = _require_approval(ctx, span.credit, args.approved_by)
    pack = build_pack(span, key, selection, approving["id"] if approving else args.approved_by)
    shared_hits = sum(
        1
        for message in span.messages
        for i in selection.get(message.number, [])
        if terms.kinds_on((message.lines or [])[i])
    )
    quote_shown = _quote_shown(pack)
    summary = [
        f"{sum(len(v) for v in selection.values())} line(s) shared; {shared_hits} of them hold "
        "a private term",
        "the credit's quoted words are in a shared line of the person's"
        if quote_shown
        else "the credit's quoted words are NOT in a shared line of the person's, so a reader "
        "cannot confirm them",
    ]
    data = {"ok": True, "mode": mode, "credit_id": credit_id, "private_hits_shared": shared_hits,
            "quote_shown": quote_shown, "pack": pack}  # fmt: skip
    if mode == "preview":
        return Output(lines=[*render_pack(pack), "", *summary], data=data)
    encoded = json.dumps(pack, indent=2, sort_keys=True) + "\n"
    path = _scratch(ctx, f"excerpt-{credit_id}.pack.json", args.out)
    _write(path, encoded)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    data.update(path=str(path), sha256=digest)
    return Output(
        lines=[f"excerpt pack of {credit_id} written to {path} (sha256 {digest})", *summary],
        data=data,
    )


def _quote_shown(pack: dict) -> bool:
    quote = pack.get("quote")
    if not quote:
        return False
    pieces = transcript.quote_pieces(quote)
    runs: list[str] = []
    for message in pack["messages"]:
        if message["index"].get("role") != seal.ROLE_USER:
            continue
        run: list[str] = []
        previous = -2
        for line in message["shared"]:
            if line["i"] != previous + 1 and run:
                runs.append("\n".join(run))
                run = []
            run.append(line["text"])
            previous = line["i"]
        if run:
            runs.append("\n".join(run))
    return transcript.pieces_in(pieces, runs)
