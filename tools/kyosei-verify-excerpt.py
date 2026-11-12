#!/usr/bin/env python3
"""Verify a Kyosei excerpt pack: ``kyosei-verify-excerpt.py pack.json``.

A pack is written by ``kyo excerpt --pack`` (KYO-660). It holds a credit, for each message of
the discussion the credit cites the message's line of the turn index, and for each line the
person chose to share its text, its salt and its Merkle proof. This script uses the Python
standard library only and imports nothing from Kyosei, so a reader can run it on a machine
that has never had Kyosei installed.

WHAT IT CHECKS, in four steps.

1. For each shared line it recomputes the leaf H(0x00 || salt || line), folds the proof, and
   compares the result with the root on the message's index line. The proof's sides must
   also be the ones the line's position and the committed line count give, so a line cannot
   be presented at another position, and the number of omitted lines is exact.
2. It recomputes each index line's hash from its committed fields and its ``prev``, from the
   first message to the last. Each ``prev`` must be the hash of the line before it, and the
   final hash must equal the credit's ``span_seal``.
3. It checks that the credit's quoted words occur in the shared lines of a person's message.
4. It reports which messages are whole, partial (lines shown of lines committed), withheld or
   never committed, and whether each person's message is stated to be theirs.

WHAT IT PROVES. Each shared line was in the message committed when that message was sent; the
lines are in their original positions, and the number omitted is exact; nothing was inserted
between the first and last message of the span.

WHAT IT DOES NOT PROVE.
- That the commitment was made at the stated time. The index is a local file that the same
  account can rewrite, consistently, from a given line onward. Time evidence needs the head of
  the chain timestamped outside the machine, which Kyosei does not yet do.
- Who typed the person's lines. The ``origin`` of an index line is not covered by its hash,
  and neither the hash nor the seal says who was at the keyboard.

Exit status: 0 when steps 1 and 2 hold, 1 when a shared line, salt, proof, index line or
``span_seal`` does not check, 2 when the file cannot be read as a pack. Step 3 is reported and
does not change the status: a person may withhold the line that holds their words.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata

PACK_OBJECT = "excerpt-pack"
PACK_FORMAT = 1
SEAL_VERSION = 1

# These constants are Kyosei's own, from src/kyosei/seal.py. A test compares them.
LINE_LABEL = b"kyosei-seal-line-v1\n"
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
HASHED_FIELDS = ("seal", "session", "prompt_id", "at", "role", "root", "lines", "prev")

OMISSION = re.compile(r"\s*\[(?:\.\.\.|…)\]\s*")

LIMITS = (
    "This proves each shared line was in the message committed when it was sent, that the "
    "lines are in their original positions with an exact count omitted, and that nothing "
    "was inserted. It does not prove when the commitment was made (the index is a local file "
    "its owner can rewrite from a line onward, and is not yet timestamped outside the "
    "machine), or who typed the person's lines."
)


class NotAPack(Exception):
    """The file is not a pack this script can read."""


def line_hash(line: dict) -> str:
    body = json.dumps(
        {name: line.get(name) for name in HASHED_FIELDS}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(LINE_LABEL + body).hexdigest()


def leaf(salt: bytes, text: str) -> bytes:
    return hashlib.sha256(LEAF_PREFIX + salt + text.encode("utf-8")).digest()


def _split(count: int) -> int:
    k = 1
    while k * 2 < count:
        k *= 2
    return k


def expected_sides(i: int, count: int) -> list[str]:
    """The sides, from the leaf upward, of the siblings in line ``i``'s proof in a tree of
    ``count`` leaves, split as RFC 6962 splits it."""
    if count == 1:
        return []
    k = _split(count)
    if i < k:
        return [*expected_sides(i, k), "right"]
    return [*expected_sides(i - k, count - k), "left"]


def root_from_proof(start: bytes, proof: list) -> bytes:
    node = start
    for side, sibling in proof:
        pair = sibling + node if side == "left" else node + sibling
        node = hashlib.sha256(NODE_PREFIX + pair).digest()
    return node


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _find_whole(haystack: str, needle: str, start: int) -> int:
    pos = start
    while True:
        at = haystack.find(needle, pos)
        if at < 0:
            return -1
        before = haystack[at - 1] if at > 0 else ""
        end = at + len(needle)
        after = haystack[end] if end < len(haystack) else ""
        if not (needle[0].isalnum() and before.isalnum()) and not (
            needle[-1].isalnum() and after.isalnum()
        ):
            return at
        pos = at + 1


def pieces_in(pieces: list[str], runs: list[str]) -> bool:
    """Every piece occurs, in order, each inside one run of consecutive shared lines."""
    normalised = [normalise(run) for run in runs]
    index, pos = 0, 0
    for piece in pieces:
        while index < len(normalised):
            at = _find_whole(normalised[index], piece, pos)
            if at >= 0:
                pos = at + len(piece)
                break
            index, pos = index + 1, 0
        else:
            return False
    return True


def _hex(value: object, what: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{what} is not a hex string")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{what} is not hex") from exc


def check_message(number: int, message: dict) -> tuple[list[str], str]:
    """Step 1 for one message: ``(problems, state)``; the state is ``whole``, ``partial``,
    ``withheld`` or ``uncommitted``, with the counts."""
    index, shared = message["index"], message["shared"]
    root, count = index.get("root"), index.get("lines")
    if root is None or count is None:
        if shared:
            return [f"message {number}: lines are shown of a message that was never committed"], ""
        return [], "never committed when it was sent, so none of it can be shown"
    problems: list[str] = []
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return [f"message {number}: the committed line count is not a positive integer"], ""
    try:
        committed = _hex(root, "the root")
    except ValueError as exc:
        return [f"message {number}: {exc}"], ""
    seen = -1
    for line in shared:
        i = line.get("i")
        where = f"message {number}, line {i}"
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < count:
            problems.append(f"{where}: the position is not one of the {count} committed lines")
            continue
        if i <= seen:
            problems.append(f"{where}: shared out of order or twice")
            continue
        seen = i
        try:
            salt = _hex(line.get("salt"), "the salt")
            proof = [(side, _hex(node, "a proof node")) for side, node in line.get("proof", [])]
        except (ValueError, TypeError) as exc:
            problems.append(f"{where}: {exc}")
            continue
        text = line.get("text")
        if not isinstance(text, str):
            problems.append(f"{where}: the text is not a string")
            continue
        if [side for side, _ in proof] != expected_sides(i, count):
            problems.append(f"{where}: the proof is not the proof of that position")
        elif root_from_proof(leaf(salt, text), proof) != committed:
            problems.append(f"{where}: the line, salt and proof do not give the message's root")
    shown = len(shared)
    if shown == 0:
        return problems, f"withheld ({count} line(s) committed, none shown)"
    if shown == count:
        return problems, f"whole ({count} of {count} line(s) shown)"
    return problems, f"partial ({shown} of {count} line(s) shown, {count - shown} omitted)"


def check_chain(messages: list[dict], span_seal: object) -> list[str]:
    """Step 2: the index lines chain from the first message to the last, and end at the
    credit's ``span_seal``."""
    problems: list[str] = []
    if not span_seal:
        return ["the credit has no span_seal, so the pack is not sealed to anything"]
    previous: str | None = None
    for number, message in enumerate(messages, 1):
        index = message["index"]
        if index.get("seal") != SEAL_VERSION:
            problems.append(
                f"message {number}: its index line is not a sealed line this "
                f"script reads (seal {index.get('seal')!r})"
            )
            return problems
        if number > 1 and index.get("prev") != previous:
            problems.append(
                f"message {number}: its prev is not the hash of the line before it, so a "
                "line of the index was changed, removed or inserted"
            )
        if number > 1 and index.get("session") != messages[0]["index"].get("session"):
            problems.append(f"message {number}: it is of another session than message 1")
        previous = line_hash(index)
    if not problems and previous != span_seal:
        problems.append(
            "the hash of the last index line is not the credit's span_seal, so the pack's "
            "chain is not the chain the credit sealed"
        )
    return problems


def quote_found(pack: dict) -> bool | None:
    """Step 3: whether the quoted words occur in shared lines of a person's message, or None
    when the credit quotes nothing the pack can check (a choice, not typed words)."""
    quote = pack.get("quote")
    form = (pack.get("credit") or {}).get("words_form")
    if not quote or form in ("choice", "accepted-suggestion"):
        return None
    pieces = [p for p in (normalise(x) for x in OMISSION.split(quote)) if p]
    runs: list[str] = []
    for message in pack["messages"]:
        if message["index"].get("role") != "user":
            continue
        run: list[str] = []
        previous = -2
        for line in message["shared"]:
            if line.get("i") != previous + 1 and run:
                runs.append("\n".join(run))
                run = []
            run.append(line.get("text", ""))
            previous = line.get("i")
        if run:
            runs.append("\n".join(run))
    return pieces_in(pieces, runs)


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            pack = json.load(handle)
    except (OSError, ValueError) as exc:
        raise NotAPack(f"{path} cannot be read as JSON: {exc}") from exc
    if not isinstance(pack, dict) or pack.get("object") != PACK_OBJECT:
        raise NotAPack(f"{path} is not an excerpt pack (its object is not {PACK_OBJECT!r})")
    if pack.get("pack_format") != PACK_FORMAT:
        raise NotAPack(
            f"the pack's format is {pack.get('pack_format')!r}; this reads {PACK_FORMAT}"
        )
    messages = pack.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(pack.get("credit"), dict):
        raise NotAPack("the pack holds no credit or no messages")
    for message in messages:
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("index"), dict)
            or not isinstance(message.get("shared"), list)
            or not all(isinstance(line, dict) for line in message["shared"])
        ):
            raise NotAPack("a message of the pack is not an index line with a list of shared lines")
    return pack


def verify(pack: dict) -> tuple[bool, list[str]]:
    """Run the four steps; ``(whether steps 1 and 2 hold, the report)``."""
    credit = pack["credit"]
    out = [
        f"credit {credit.get('id')}, session {credit.get('span_session')}, "
        f"approved by {pack.get('approved_by') or 'nobody named'}"
    ]
    problems: list[str] = []
    states: list[str] = []
    for number, message in enumerate(pack["messages"], 1):
        found, state = check_message(number, message)
        problems += found
        states.append(state)
    shown = sum(len(m["shared"]) for m in pack["messages"])
    out.append(f"step 1: {shown} shared line(s) in {len(pack['messages'])} message(s) checked")
    chain = check_chain(pack["messages"], credit.get("span_seal"))
    problems += chain
    out.append(
        "step 2: the index lines chain to the credit's span_seal"
        if not chain
        else "step 2: the chain does not check"
    )
    found = quote_found(pack)
    out.append(
        "step 3: the credit quotes no typed words to look for"
        if found is None
        else "step 3: the quoted words occur in shared lines of a person's message"
        if found
        else "step 3: the quoted words do NOT occur in any shared line of a person's message"
    )
    out.append("step 4: the messages")
    for number, (message, state) in enumerate(zip(pack["messages"], states, strict=True), 1):
        index = message["index"]
        who = "the agent" if index.get("role") == "assistant" else "the person"
        origin = ""
        if index.get("role") == "user":
            origin = (
                ", stated to be the person's own (origin human)"
                if index.get("origin") == "human"
                else f", not confirmed as the person's (origin {index.get('origin')})"
            )
        out.append(
            f"  message {number}: {who}, {index.get('at')}: {state or 'does not check'}{origin}"
        )
    out += [f"problem: {problem}" for problem in problems]
    out.append("RESULT: " + ("verified" if not problems else "NOT verified"))
    out.append(LIMITS)
    return not problems, out


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(
            "usage: kyosei-verify-excerpt.py PACK.json\n\n"
            + (__doc__ or "").strip().split("\n\n")[0]
        )
        return 0 if len(argv) == 2 else 2
    try:
        pack = load(argv[1])
        ok, report = verify(pack)
    except NotAPack as exc:
        print(f"not verified: {exc}", file=sys.stderr)
        return 2
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
