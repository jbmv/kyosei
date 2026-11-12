"""`kyo excerpt` and the standalone verifier (KYO-660; decision KYO-654, DC7; sections 7.2 to
7.4 of the KYO-651 proposal).

The conversation is invented. A pack is written from a real turn index and transcript, so the
salts, proofs and index lines in it are the ones `kyo turns --verify` checks.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import seal

from .conftest import (
    CREDIT_ELSEWHERE,
    declare_person,
    drop_discussion_requirement,
    opt_in_transcripts,
)
from .test_kyo657_turn_index import (
    PROMPT_1,
    PROMPT_2,
    SESSION,
    append_transcript,
    person,
    prompt_hook,
    write_transcript,
)
from .test_kyo663_span_seal import reply_hook

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = REPO_ROOT / "tools" / "kyosei-verify-excerpt.py"

QUESTION = (
    "should the cache stay?\nit costs memory\nthe maintainer Zebrafish asked\n"
    "it is fast\nwhat do you think?"
)
REPLY = (
    "I recommend keeping it.\nIt is fast enough.\nThe alternative is slower.\nEviction is simple."
)
DECIDING = "keep the cache"
PRIVATE_LINE = "the maintainer Zebrafish asked"
WITHHELD_LINE = "it costs memory"


def verifier(pack: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(VERIFIER), str(pack), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def decision(kyo, project: Path, tmp_path: Path) -> dict:
    """A decision credited over a sealed discussion of three messages: the person's five
    lines, the agent's four, and the person's deciding words."""
    opt_in_transcripts(project)
    declare_person(project)
    drop_discussion_requirement(project)
    (project / "kyosei" / "private-words.txt").write_text("zebrafish\n", encoding="utf-8")
    write_transcript(
        [
            person("p-1", PROMPT_1, QUESTION, "2026-11-08T10:00:00.000Z"),
            {
                "type": "assistant",
                "uuid": "a-1",
                "timestamp": "2026-11-08T10:00:30.000Z",
                "sessionId": SESSION,
                "isSidechain": False,
                "message": {"role": "assistant", "content": [{"type": "text", "text": REPLY}]},
            },
        ]
    )
    prompt_hook(kyo, PROMPT_1, prompt=QUESTION)
    reply_hook(kyo, REPLY)
    append_transcript([person("p-2", PROMPT_2, DECIDING, "2026-11-08T10:01:00.000Z")])
    prompt_hook(kyo, PROMPT_2, prompt=DECIDING)
    words = tmp_path / "words.txt"
    words.write_text(DECIDING, encoding="utf-8")
    decided = tmp_path / "decision.yml"
    decided.write_text("recommended: keep\noutcome: accepted\n", encoding="utf-8")
    made = kyo("new", "decision", "--title", "Keep the cache", "--quote-file", str(words),
               "--decision-file", str(decided), "--session", SESSION,
               "--span-first", "p-1", "--span-last", "p-2", "--json")  # fmt: skip
    assert made.code == 0, made
    return {"credit": made.json["credit_id"], "record": made.json["record_id"], "tmp": tmp_path}


def approve(kyo, decision: dict) -> str:
    """The person's go-ahead, as an authorized credit on a gate of the decision's record.

    The record already holds its decided credit, and a record holds one current credit,
    so the go-ahead names a gate of the record, which is a subject an authorized credit may
    name (KYO-668); its id is on the same record."""
    tmp = decision["tmp"]
    basis = kyo("new", "decision", "--title", "Approve it", "--quote-file",
                str(_file(tmp, "approve.txt", "approved")), "--decision-file",
                str(_file(tmp, "approve.yml", "outcome: unprompted\n")), *CREDIT_ELSEWHERE,
                "--json")  # fmt: skip
    assert basis.code == 0, basis
    gate = kyo("gate", decision["record"], "awaiting:human-review", "--do",
               "the person approves sharing the excerpt", "--json")  # fmt: skip
    assert gate.code == 0, gate
    cleared = kyo("ungate", gate.json["gate_id"], "--withdrawn", "--why", "answered in the session")
    assert cleared.code == 0, cleared
    ran = kyo("credit", gate.json["gate_id"], "--role", "authorized", "--quote-file",
              str(_file(tmp, "go.txt", "share it")), *CREDIT_ELSEWHERE,
              "--basis", basis.json["credit_id"], "--json")  # fmt: skip
    assert ran.code == 0, ran
    return ran.json["credit_id"]


def _file(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def draft_of(kyo, decision: dict) -> Path:
    out = decision["tmp"] / "scratch" / "draft.md"
    ran = kyo("excerpt", decision["credit"], "--draft", "--out", str(out), "--json")
    assert ran.code == 0, ran
    return out


def trimmed(draft: Path, drop: list[str], to: Path) -> Path:
    """The draft with the lines holding any of ``drop`` deleted, as the person would."""
    kept = [
        line
        for line in draft.read_text(encoding="utf-8").split("\n")
        if not any(f"] {text}" in line for text in drop)
    ]
    to.write_text("\n".join(kept), encoding="utf-8")
    return to


def pack_of(kyo, decision: dict, drop: list[str], name: str = "pack.json") -> Path:
    tmp = decision["tmp"]
    cut = trimmed(draft_of(kyo, decision), drop, tmp / f"cut-{name}.md")
    approval = approve(kyo, decision)
    out = tmp / name
    ran = kyo("excerpt", decision["credit"], "--pack", "--from", str(cut),
              "--approved-by", approval, "--out", str(out))  # fmt: skip
    assert ran.code == 0, ran
    return out


# ------------------------------------------------------------------ KYO-660.c2


def test_the_draft_lists_every_line_and_the_lines_holding_a_private_word(
    kyo, decision: dict
) -> None:
    out = decision["tmp"] / "draft.md"
    ran = kyo("excerpt", decision["credit"], "--draft", "--out", str(out), "--json")
    assert ran.code == 0, ran
    assert ran.json["private_hits"] == [{"marker": "m1.l2", "kinds": ["listed word"]}]
    page = out.read_text(encoding="utf-8")
    # In the draft itself, by marker and kind: the term is not repeated.
    listing = page.split("## Message 1")[0]
    assert "- [m1.l2] listed word" in listing and "zebrafish" not in listing.lower()
    assert "Zebrafish" not in ran.out and "zebrafish" not in ran.out.lower()
    plain = kyo("excerpt", decision["credit"], "--draft", "--out", str(out))
    assert "  [m1.l2] listed word" in plain.out and "zebrafish" not in plain.out.lower()
    for number, text in enumerate(QUESTION.split("\n")):
        assert f"[m1.l{number}] {text}" in page
    for number, text in enumerate(REPLY.split("\n")):
        assert f"[m2.l{number}] {text}" in page
    assert f"[m3.l0] {DECIDING}" in page


def test_a_draft_with_no_private_hit_says_so(kyo, decision: dict, project: Path) -> None:
    (project / "kyosei" / "private-words.txt").write_text("quokka\n", encoding="utf-8")
    ran = kyo("excerpt", decision["credit"], "--draft", "--out", str(decision["tmp"] / "d.md"))
    assert ran.code == 0, ran
    assert "no line holds a term from the private-words list" in ran.out


# ------------------------------------------------------------------ KYO-660.c1


def test_a_pack_is_refused_without_the_persons_approval_and_the_remedy_clears_it(
    kyo, decision: dict
) -> None:
    cut = trimmed(draft_of(kyo, decision), [PRIVATE_LINE], decision["tmp"] / "cut.md")
    out = decision["tmp"] / "pack.json"
    base = ("excerpt", decision["credit"], "--pack", "--from", str(cut), "--out", str(out))
    refused = kyo(*base)
    assert refused.code == 2 and "excerpt-requires-approval" in refused.err, refused
    assert "authorized credit" in refused.err and "--approved-by" in refused.err
    assert not out.exists()
    # The remedy the message names: record the approval, then name that credit.
    approval = approve(kyo, decision)
    ok = kyo(*base, "--approved-by", approval, "--json")
    assert ok.code == 0, ok
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["approved_by"] == approval


def test_only_an_authorized_credit_on_the_same_record_approves(kyo, decision: dict) -> None:
    cut = trimmed(draft_of(kyo, decision), [], decision["tmp"] / "cut.md")
    base = ("excerpt", decision["credit"], "--pack", "--from", str(cut),
            "--out", str(decision["tmp"] / "pack.json"))  # fmt: skip
    # The decided credit itself has the wrong role.
    wrong_role = kyo(*base, "--approved-by", decision["credit"])
    assert wrong_role.code == 2 and "excerpt-requires-approval" in wrong_role.err, wrong_role
    assert "has the role decided" in wrong_role.err
    # An authorized credit on another record does not approve this one.
    other = kyo("new", "decision", "--title", "Another", "--quote-file",
                str(_file(decision["tmp"], "o.txt", "ok")), "--decision-file",
                str(_file(decision["tmp"], "o.yml", "outcome: unprompted\n")),
                *CREDIT_ELSEWHERE, "--json")  # fmt: skip
    assert other.code == 0, other
    elsewhere = kyo(*base, "--approved-by", other.json["credit_id"])
    assert elsewhere.code == 2 and "not a credit on" in elsewhere.err, elsewhere
    missing = kyo(*base, "--approved-by", f"{decision['record']}.p99")
    assert missing.code == 2 and "not a current credit" in missing.err, missing
    # Preview needs no approval: it writes nothing.
    assert kyo("excerpt", decision["credit"], "--preview", "--from", str(cut)).code == 0


# ------------------------------------------------------------------ the pack


def test_the_pack_holds_neither_the_withheld_text_nor_the_key_nor_the_transcript(
    kyo, decision: dict
) -> None:
    pack = pack_of(kyo, decision, [WITHHELD_LINE, PRIVATE_LINE, "Eviction is simple."])
    raw = pack.read_bytes()
    text = raw.decode("utf-8")
    assert WITHHELD_LINE not in text and PRIVATE_LINE not in text
    assert "Eviction is simple." not in text
    key = seal.load_key()
    assert key.hex().encode() not in raw and key not in raw
    transcript = next(Path(os.environ["CLAUDE_CONFIG_DIR"]).rglob(f"{SESSION}.jsonl"))
    for record in transcript.read_text(encoding="utf-8").splitlines():
        assert record not in text
    document = json.loads(text)
    assert document["object"] == "excerpt-pack"
    assert [m["index"]["role"] for m in document["messages"]] == ["user", "assistant", "user"]
    assert [line["i"] for line in document["messages"][0]["shared"]] == [0, 3, 4]
    assert [line["i"] for line in document["messages"][1]["shared"]] == [0, 1, 2]
    # The index lines are the ones the index holds, so `kyo turns --verify` and a pack agree.
    assert kyo("turns", "--verify").code == 0
    for message in document["messages"]:
        assert set(message["index"]) == {
            *("seal", "session", "prompt_id", "at", "role", "root", "lines", "prev", "origin")
        }


def test_a_preview_shows_the_positions_withheld(kyo, decision: dict) -> None:
    cut = trimmed(draft_of(kyo, decision), [WITHHELD_LINE, PRIVATE_LINE], decision["tmp"] / "c.md")
    shown = kyo("excerpt", decision["credit"], "--preview", "--from", str(cut))
    assert shown.code == 0, shown
    assert "message 1 (the person)" in shown.out and "3 of 5 line(s) shown" in shown.out
    assert "[2 line(s) withheld]" in shown.out or "[1 line(s) withheld]" in shown.out
    assert WITHHELD_LINE not in shown.out and PRIVATE_LINE not in shown.out
    assert "the credit's quoted words are in a shared line of the person's" in shown.out
    # Deleting a whole message shows it as withheld, with its time and line count.
    nothing = trimmed(draft_of(kyo, decision), [*REPLY.split("\n")], decision["tmp"] / "c2.md")
    shown = kyo("excerpt", decision["credit"], "--preview", "--from", str(nothing))
    assert "message 2 (the agent)" in shown.out and "4 line(s), withheld" in shown.out


def test_a_draft_whose_lines_are_not_the_spans_is_refused_and_a_new_draft_clears_it(
    kyo, decision: dict
) -> None:
    draft = draft_of(kyo, decision)
    page = draft.read_text(encoding="utf-8")
    base = ("excerpt", decision["credit"], "--preview", "--from")
    edited = _file(decision["tmp"], "edited.md", page.replace("it is fast", "it is slow"))
    refused = kyo(*base, str(edited))
    assert refused.code == 64 and "does not hold the text" in refused.err, refused
    marked = [n for n, line in enumerate(page.split("\n")) if line.startswith("[m1.l")]
    rows = page.split("\n")
    rows[marked[0]], rows[marked[1]] = rows[marked[1]], rows[marked[0]]
    reordered = _file(decision["tmp"], "reordered.md", "\n".join(rows))
    refused = kyo(*base, str(reordered))
    assert refused.code == 64 and "out of order" in refused.err, refused
    invented = _file(decision["tmp"], "invented.md", page + "[m9.l0] something never said\n")
    refused = kyo(*base, str(invented))
    assert refused.code == 64 and "not a shareable line of the span" in refused.err, refused
    # The remedy: a new draft, from which lines are only deleted.
    assert "--draft" in refused.err
    assert kyo(*base, str(draft_of(kyo, decision))).code == 0
    # And a preview or a pack without a draft names how to get one.
    bare = kyo("excerpt", decision["credit"], "--preview")
    assert bare.code == 64 and "--draft" in bare.err


def test_a_credit_with_an_empty_span_seal_cannot_be_packed(
    kyo, decision: dict, project: Path
) -> None:
    with sqlite3.connect(project / "kyosei" / "kyosei.db") as conn:
        conn.execute("UPDATE credits SET span_seal = NULL")
    refused = kyo("excerpt", decision["credit"], "--draft", "--out", str(decision["tmp"] / "d.md"))
    assert refused.code == 64 and "empty span_seal" in refused.err, refused
    assert "none can be packed" in refused.err


def test_a_message_whose_transcript_text_does_not_reproduce_its_root_is_not_shareable(
    kyo, decision: dict
) -> None:
    transcript = next(Path(os.environ["CLAUDE_CONFIG_DIR"]).rglob(f"{SESSION}.jsonl"))
    transcript.write_text(
        transcript.read_text(encoding="utf-8").replace(DECIDING, "keep the cache!"),
        encoding="utf-8",
    )
    out = decision["tmp"] / "d.md"
    ran = kyo("excerpt", decision["credit"], "--draft", "--out", str(out), "--json")
    assert ran.code == 0, ran
    assert ran.json["not_shareable"] == [3]
    page = out.read_text(encoding="utf-8")
    assert "keep the cache!" not in page and "does not reproduce the root" in page


def test_with_no_scratch_directory_the_draft_names_a_file_and_that_clears_it(
    kyo, decision: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    refused = kyo("excerpt", decision["credit"], "--draft")
    assert refused.code == 64 and "--out" in refused.err, refused
    assert (
        kyo("excerpt", decision["credit"], "--draft", "--out", str(decision["tmp"] / "d.md")).code
        == 0
    )


# ------------------------------------------------------------------ KYO-660.c3


def test_the_verifier_accepts_a_trimmed_pack_and_reports_exact_omissions(
    kyo, decision: dict
) -> None:
    pack = pack_of(kyo, decision, [WITHHELD_LINE, PRIVATE_LINE, *REPLY.split("\n")])
    ran = verifier(pack)
    assert ran.returncode == 0, ran
    assert "RESULT: verified" in ran.stdout
    assert (
        "message 1: the person" in ran.stdout
        and "partial (3 of 5 line(s) shown, 2 omitted)" in ran.stdout
    )
    assert (
        "message 2: the agent" in ran.stdout
        and "withheld (4 line(s) committed, none shown)" in ran.stdout
    )
    assert "message 3: the person" in ran.stdout and "whole (1 of 1 line(s) shown)" in ran.stdout
    assert "origin human" in ran.stdout
    assert "step 3: the quoted words occur in shared lines of a person's message" in ran.stdout
    # The two limits are stated as they are.
    assert "when the commitment was made" in ran.stdout and "who typed" in ran.stdout
    assert WITHHELD_LINE not in ran.stdout


def test_the_verifier_reports_a_quote_the_pack_does_not_show(kyo, decision: dict) -> None:
    pack = pack_of(kyo, decision, [DECIDING])
    ran = verifier(pack)
    assert ran.returncode == 0, ran
    assert "do NOT occur in any shared line" in ran.stdout


def _edited(pack: Path, to: Path, change) -> Path:
    document = json.loads(pack.read_text(encoding="utf-8"))
    change(document)
    to.write_text(json.dumps(document), encoding="utf-8")
    return to


@pytest.mark.parametrize(
    ("name", "change", "message"),
    [
        (
            "a shared line was edited",
            lambda d: d["messages"][0]["shared"][0].update(text="the maintainer agreed"),
            "do not give the message's root",
        ),
        (
            "a line was moved to another position",
            lambda d: d["messages"][0]["shared"][0].update(i=1),
            "not the proof of that position",
        ),
        (
            "a salt was changed",
            lambda d: d["messages"][0]["shared"][0].update(salt="00" * 32),
            "do not give the message's root",
        ),
        (
            "a proof node was changed",
            lambda d: d["messages"][0]["shared"][0]["proof"][0].__setitem__(1, "11" * 32),
            "do not give the message's root",
        ),
        (
            "the committed line count was raised",
            lambda d: d["messages"][0]["index"].update(lines=6),
            "",
        ),
        (
            "an index line's time was edited",
            lambda d: d["messages"][1]["index"].update(at="2026-11-08T11:00:00.000Z"),
            "does not check",
        ),
        (
            "an index line's root was replaced",
            lambda d: d["messages"][1]["index"].update(root="22" * 32),
            "",
        ),
        (
            "the span_seal was replaced",
            lambda d: d["credit"].update(span_seal="33" * 32),
            "not the credit's span_seal",
        ),
        (
            "a message of the span was removed",
            lambda d: d["messages"].pop(1),
            "prev is not the hash",
        ),
        (
            "a line was shared beyond the committed count",
            lambda d: d["messages"][2]["shared"].append(
                {"i": 1, "text": "extra", "salt": "00" * 32, "proof": []}
            ),
            "not one of the 1 committed lines",
        ),
    ],
)
def test_the_verifier_rejects_a_pack_that_was_altered(
    kyo, decision: dict, name: str, change, message: str
) -> None:
    pack = pack_of(kyo, decision, [WITHHELD_LINE])
    assert verifier(pack).returncode == 0
    altered = _edited(pack, decision["tmp"] / "altered.json", change)
    ran = verifier(altered)
    assert ran.returncode == 1, (name, ran)
    assert "RESULT: NOT verified" in ran.stdout, name
    assert message in ran.stdout, (name, ran.stdout)


def test_the_verifier_refuses_a_file_that_is_not_a_pack(tmp_path: Path) -> None:
    junk = tmp_path / "junk.json"
    junk.write_text("[1, 2]", encoding="utf-8")
    ran = verifier(junk)
    assert ran.returncode == 2 and "not an excerpt pack" in ran.stderr, ran
    missing = verifier(tmp_path / "nope.json")
    assert missing.returncode == 2 and "cannot be read" in missing.stderr


def test_the_verifier_imports_the_standard_library_only() -> None:
    tree = ast.parse(VERIFIER.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names), imported
    assert "kyosei" not in imported


def test_the_verifiers_commitments_are_the_seals() -> None:
    """The script repeats the seal's constants so that it needs nothing installed; this is
    what stops the two from drifting apart."""
    spec = importlib.util.spec_from_file_location("kyosei_verify_excerpt", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LINE_LABEL == seal._LINE_LABEL
    assert module.LEAF_PREFIX == seal._LEAF_PREFIX and module.NODE_PREFIX == seal._NODE_PREFIX
    assert module.HASHED_FIELDS == seal.HASHED_FIELDS and module.SEAL_VERSION == seal.SEAL_VERSION
    line = {"seal": 1, "session": "s", "prompt_id": "p", "at": "t", "role": "user",
            "root": "r", "lines": 1, "prev": None}  # fmt: skip
    assert module.line_hash(line) == seal.line_hash(line)
    key = bytes(range(32))
    for count in range(1, 13):
        text = "\n".join(f"line {n}" for n in range(count))
        leaves = seal.leaves_of(key, "s", "p", "user", None, text)
        for i in range(count):
            proof = seal.merkle_proof(leaves, i)
            assert [side for side, _ in proof] == module.expected_sides(i, count)
            one = seal.salt(key, "s", "p", "user", None, i)
            assert module.leaf(one, f"line {i}") == seal.leaf(one, f"line {i}")
            assert module.root_from_proof(leaves[i], proof) == seal.merkle_root(leaves)
