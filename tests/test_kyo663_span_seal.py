"""Commitments in the turn index and ``span_seal`` on credits (KYO-663)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import cmd_turns, seal

from .conftest import declare_person, drop_discussion_requirement, opt_in_transcripts
from .test_kyo657_turn_index import (
    PROMPT_1,
    PROMPT_2,
    PROMPT_3,
    SESSION,
    append_transcript,
    index,
    person,
    prompt_hook,
    queued,
    stop_hook,
    transcript_path,
    write_transcript,
)

OTHER_SESSION = "7c1d2e3f-9999-4a2b-9c3d-0123456789ab"
THREE_LINES = "first line\nsecond line\nthird line"
KEY = bytes(range(32))


def reply_hook(kyo, reply: str, session: str = SESSION):
    payload = {
        "session_id": session,
        "hook_event_name": "Stop",
        "transcript_path": str(transcript_path(session)),
        "last_assistant_message": reply,
    }
    return kyo("turns", "--hook", stdin=json.dumps(payload))


def rewrite(project: Path, lines: list[dict]) -> None:
    cmd_turns.turns_path(project).write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8"
    )


def three_prompts(kyo, project: Path) -> list[dict]:
    opt_in_transcripts(project)
    for prompt_id, text in ((PROMPT_1, "yes"), (PROMPT_2, THREE_LINES), (PROMPT_3, "go on")):
        prompt_hook(kyo, prompt_id, prompt=text)
    return index(project)


# ------------------------------------------------------------------ KYO-663.c1


def test_an_edited_line_of_a_committed_message_fails_verification(kyo, project: Path) -> None:
    lines = three_prompts(kyo, project)
    key = seal.load_key()
    assert lines[1]["lines"] == 3
    assert seal.verify_message(key, lines[1], THREE_LINES)
    assert not seal.verify_message(key, lines[1], THREE_LINES.replace("second", "2nd"))
    assert not seal.verify_message(key, lines[1], "first line\nsecond line")
    assert not seal.verify_message(key, lines[1], THREE_LINES + "\n")
    # The same words under another prompt's line are a different commitment.
    assert not seal.verify_message(key, lines[2], THREE_LINES)


def test_an_edited_index_line_fails_verification(kyo, project: Path) -> None:
    lines = three_prompts(kyo, project)
    assert kyo("turns", "--verify").code == 0
    lines[1]["root"] = "0" * 64
    rewrite(project, lines)
    refused = kyo("turns", "--verify")
    assert refused.code == 1 and "line 3 does not follow" in refused.err, refused


@pytest.mark.parametrize("field", ["at", "lines", "prompt_id", "role", "session"])
def test_every_hashed_field_changes_the_line_hash(field: str) -> None:
    line = {"seal": 1, "session": "s", "prompt_id": "p", "at": "t", "role": "user",
            "root": "r", "lines": 1, "prev": None}  # fmt: skip
    assert seal.line_hash({**line, field: "changed"}) != seal.line_hash(line)
    # What the Stop hook settles later is outside the hash.
    assert seal.line_hash({**line, "origin": "human", "turn": "u", "pasted": True}) == (
        seal.line_hash(line)
    )


def test_a_reordered_message_fails_verification(kyo, project: Path) -> None:
    lines = three_prompts(kyo, project)
    rewrite(project, [lines[0], lines[2], lines[1]])
    refused = kyo("turns", "--verify")
    assert refused.code == 1 and "does not follow" in refused.err, refused


def test_an_inserted_message_fails_verification(kyo, project: Path) -> None:
    lines = three_prompts(kyo, project)
    forged = {**lines[1], "prompt_id": "aaaaaaaa-0000-4000-8000-0000000000ff", "root": "1" * 64}
    rewrite(project, [lines[0], lines[1], forged, lines[2]])
    refused = kyo("turns", "--verify")
    assert refused.code == 1 and "does not follow" in refused.err, refused
    # A forged line that names its predecessor correctly still breaks the line after it.
    forged["prev"] = seal.line_hash(lines[1])
    rewrite(project, [lines[0], lines[1], forged, lines[2]])
    assert kyo("turns", "--verify").code == 1
    # An unchained line placed inside the chain is named too.
    bare = {"session": SESSION, "prompt_id": "x", "at": lines[1]["at"], "origin": "human"}
    rewrite(project, [lines[0], bare, lines[1], lines[2]])
    refused = kyo("turns", "--verify")
    assert refused.code == 1 and "line 2 is not chained" in refused.err, refused


@pytest.mark.parametrize("count", range(1, 12))
def test_every_line_has_a_proof_to_its_root_and_only_its_own(count: int) -> None:
    text = "\n".join(f"line {n}" for n in range(count))
    leaves = seal.leaves_of(KEY, SESSION, PROMPT_1, seal.ROLE_USER, None, text)
    root = seal.merkle_root(leaves)
    assert seal.commit(KEY, SESSION, PROMPT_1, seal.ROLE_USER, None, text) == (root.hex(), count)
    for i, a_leaf in enumerate(leaves):
        proof = seal.merkle_proof(leaves, i)
        assert seal.root_from_proof(a_leaf, proof) == root
        changed = seal.leaf(seal.salt(KEY, SESSION, PROMPT_1, seal.ROLE_USER, None, i), "other")
        assert seal.root_from_proof(changed, proof) != root


def test_a_message_and_its_extension_have_different_roots() -> None:
    """No last leaf is duplicated to fill the tree, so three lines and those three with
    the third repeated do not share a root."""
    three = seal.commit(KEY, SESSION, PROMPT_1, seal.ROLE_USER, None, "a\nb\nc")
    four = seal.commit(KEY, SESSION, PROMPT_1, seal.ROLE_USER, None, "a\nb\nc\nc")
    assert three[0] != four[0]


# ------------------------------------------------------------------ KYO-663.c2


def test_a_one_word_root_cannot_be_found_without_the_key(kyo, project: Path) -> None:
    lines = three_prompts(kyo, project)
    line = lines[0]
    key = seal.load_key()
    assert line["lines"] == 1 and seal.verify_message(key, line, "yes")
    # Everything on the index line and every unkeyed hash of the word miss the root.
    public = [line["session"], line["prompt_id"], line["at"], "user", ""]
    guesses = {hashlib.sha256(b"yes").hexdigest()}
    for value in public:
        guesses.add(hashlib.sha256((value + "yes").encode()).hexdigest())
        guesses.add(hashlib.sha256(b"\x00" + value.encode() + b"yes").hexdigest())
    assert line["root"] not in guesses
    # A reader guessing the word with any other key computes another root.
    for guess in (bytes(32), KEY, os.urandom(32)):
        assert not seal.verify_message(guess, line, "yes")


def test_the_same_word_never_gives_the_same_leaf_twice() -> None:
    def root(session=SESSION, prompt=PROMPT_1, role=seal.ROLE_USER, prev=None, key=KEY):
        return seal.commit(key, session, prompt, role, prev, "yes")[0]

    roots = {
        root(),
        root(session=OTHER_SESSION),
        root(prompt=PROMPT_2),
        root(role=seal.ROLE_ASSISTANT),
        root(prev="ab" * 32),
        root(key=bytes(32)),
    }
    assert len(roots) == 6
    leaves = seal.leaves_of(KEY, SESSION, PROMPT_1, seal.ROLE_USER, None, "yes\nyes")
    assert leaves[0] != leaves[1]


def test_the_index_holds_no_words_and_no_key(kyo, project: Path) -> None:
    three_prompts(kyo, project)
    reply_hook(kyo, "a reply that must not be stored")
    raw = cmd_turns.turns_path(project).read_text(encoding="utf-8")
    key = seal.load_key()
    for secret in ("second line", "go on", "must not be stored", key.hex()):
        assert secret not in raw


# ------------------------------------------------------------------ KYO-663.c3


def test_the_chain_links_every_line_of_a_session(kyo, project: Path) -> None:
    """A prompt, a message sent mid-turn that the Stop hook adds from the transcript, the
    reply, and a second prompt: each names the hash of the line before it."""
    opt_in_transcripts(project)
    write_transcript([person("p-1", PROMPT_1, "start", "2026-11-08T10:00:00.000Z")])
    prompt_hook(kyo, PROMPT_1, prompt="start")
    append_transcript([queued("q-1", "and one more thing", "2026-11-08T10:00:30.000Z")])
    reply_hook(kyo, "done\nboth things")
    prompt_hook(kyo, PROMPT_2, prompt="thanks")
    prompt_hook(kyo, PROMPT_1, prompt="another session", session=OTHER_SESSION)

    mine = cmd_turns.session_lines(project, SESSION)
    assert [(line["role"], line["source"]) for line in mine] == [
        ("user", "hook"), ("user", "transcript"), ("assistant", "hook"), ("user", "hook")
    ]  # fmt: skip
    assert mine[0]["prev"] is None
    for before, after in zip(mine, mine[1:], strict=False):
        assert after["prev"] == seal.line_hash(before)
    assert seal.verify_chain(mine) == []
    # The mid-turn message was not committed when it was sent, so it carries no root.
    assert (mine[1]["root"], mine[1]["lines"]) == (None, None)
    key = seal.load_key()
    assert mine[2]["lines"] == 2 and mine[2]["prompt_id"] == PROMPT_1
    assert seal.verify_message(key, mine[2], "done\nboth things")
    # Another session's chain is its own.
    (other,) = cmd_turns.session_lines(project, OTHER_SESSION)
    assert other["prev"] is None
    assert kyo("turns", "--verify").code == 0


def test_a_second_stop_does_not_commit_the_same_reply_twice(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript([person("p-1", PROMPT_1, "start", "2026-11-08T10:00:00.000Z")])
    prompt_hook(kyo, PROMPT_1, prompt="start")
    reply_hook(kyo, "done")
    reply_hook(kyo, "done")
    stop_hook(kyo)
    lines = index(project)
    assert [line["role"] for line in lines] == ["user", "assistant"]
    # The Stop hook's fold confirms the prompt's line and leaves the reply's alone.
    assert [line["origin"] for line in lines] == ["human", "assistant"]
    reply_hook(kyo, "done, and more")
    assert [line["role"] for line in index(project)] == ["user", "assistant", "assistant"]
    assert kyo("turns", "--verify").code == 0


def test_lines_written_before_sealing_are_left_and_the_chain_begins_after_them(
    kyo, project: Path
) -> None:
    opt_in_transcripts(project)
    old = {"session": SESSION, "prompt_id": PROMPT_1, "at": "2026-11-08T10:00:00.000Z",
           "pasted": False, "origin": "human", "source": "hook", "turn": "p-1"}  # fmt: skip
    rewrite(project, [old])
    prompt_hook(kyo, PROMPT_2, prompt="after the upgrade")
    lines = index(project)
    assert lines[0] == old
    assert lines[1]["seal"] == seal.SEAL_VERSION and lines[1]["prev"] is None
    assert kyo("turns", "--verify").code == 0
    assert seal.span_seal(lines, "p-1") == (
        None, "the span's last turn was indexed before sealing began"
    )  # fmt: skip


def _decide(kyo, tmp_path: Path, first: str, last: str):
    drop_discussion_requirement(kyo.project)
    words = tmp_path / "w"
    words.write_text("keep the cache", encoding="utf-8")
    decision = tmp_path / "decision.yml"
    decision.write_text("recommended: keep\noutcome: accepted\n", encoding="utf-8")
    return kyo("new", "decision", "--title", "Keep the cache", "--quote-file", str(words),
               "--decision-file", str(decision), "--session", SESSION,
               "--span-first", first, "--span-last", last)  # fmt: skip


def _conversation(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    declare_person(project)
    write_transcript([person("p-1", PROMPT_1, "should we?", "2026-11-08T10:00:00.000Z")])
    prompt_hook(kyo, PROMPT_1, prompt="should we?")
    reply_hook(kyo, "I recommend keeping it")
    append_transcript([person("p-2", PROMPT_2, "keep the cache", "2026-11-08T10:01:00.000Z")])
    prompt_hook(kyo, PROMPT_2, prompt="keep the cache")


def test_a_credit_with_a_span_stores_span_seal(kyo, project: Path, tmp_path: Path) -> None:
    """The credit is written before the Stop that would give the last prompt's line its
    transcript record, so writing it folds the transcript in first."""
    _conversation(kyo, project)
    ok = _decide(kyo, tmp_path, "p-1", "p-2")
    assert ok.code == 0, ok
    last = next(line for line in index(project) if line.get("turn") == "p-2")
    with sqlite3.connect(project / "kyosei" / "kyosei.db") as conn:
        record, stored = conn.execute("SELECT record_id, span_seal FROM credits").fetchone()
    assert stored == seal.line_hash(last)
    assert f"span sealed: {stored}" in ok.out
    document = kyo("show", record, "--json", "--full").json
    assert document["credits"][0]["span_seal"] == stored

    assert kyo("turns", "--verify").code == 0
    # Changing a committed line of the span leaves the credit's seal matching nothing.
    lines = index(project)
    lines[0]["root"] = "0" * 64
    rewrite(project, lines)
    refused = kyo("turns", "--verify")
    assert refused.code == 1, refused
    assert "does not follow" in refused.err and "span_seal" in refused.err
    # So does removing the span's lines outright, which no chain link notices.
    rewrite(project, [])
    refused = kyo("turns", "--verify")
    assert refused.code == 1 and "span_seal" in refused.err, refused


def test_a_span_derived_from_a_discussion_is_sealed(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """The filing path a decision takes: its span comes from the discussion's turn
    references, and its seal from the index line of the span's last turn."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    _conversation(kyo, project)
    opened = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1, "--json")
    assert opened.code == 0, opened
    words = tmp_path / "w"
    words.write_text("keep the cache", encoding="utf-8")
    decision = tmp_path / "d.yml"
    decision.write_text("outcome: unprompted\n", encoding="utf-8")
    decided = kyo("spawn", opened.json["id"], "--kind", "decision", "--title", "Keep it",
                  "--quote-file", str(words), "--decision-file", str(decision),
                  "--json")  # fmt: skip
    assert decided.code == 0, decided
    credit = kyo("show", decided.json["record_id"], "--json", "--full").json["credits"][0]
    assert credit["span_last_turn"] == "p-2"
    last = next(line for line in index(project) if line.get("turn") == "p-2")
    assert credit["span_seal"] == seal.line_hash(last)
    assert kyo("turns", "--verify").code == 0


def test_a_span_whose_last_turn_was_never_indexed_is_not_sealed(
    kyo, project: Path, tmp_path: Path
) -> None:
    opt_in_transcripts(project)
    declare_person(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "should we?", "2026-11-08T10:00:00.000Z"),
            person("p-2", PROMPT_2, "keep the cache", "2026-11-08T10:01:00.000Z"),
        ]
    )
    old = [
        {"session": SESSION, "prompt_id": prompt, "at": at, "pasted": False,
         "origin": "human", "source": "hook", "turn": turn}
        for prompt, turn, at in (
            (PROMPT_1, "p-1", "2026-11-08T10:00:00.000Z"),
            (PROMPT_2, "p-2", "2026-11-08T10:01:00.000Z"),
        )
    ]  # fmt: skip
    rewrite(project, old)
    ok = _decide(kyo, tmp_path, "p-1", "p-2")
    assert ok.code == 0, ok
    with sqlite3.connect(project / "kyosei" / "kyosei.db") as conn:
        assert conn.execute("SELECT span_seal FROM credits").fetchone() == (None,)
    assert "span not sealed" in ok.out


# ------------------------------------------------------------------ KYO-663.c4


def test_the_key_is_a_local_file_until_another_store_is_chosen(kyo, project: Path) -> None:
    three_prompts(kyo, project)
    assert seal.recorded_choice() is None and seal.chosen_store() == seal.STORE_FILE
    path = seal.key_path()
    assert path.parent == Path(os.environ["KYOSEI_HOME"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(bytes.fromhex(path.read_text().strip())) == 32
    shown = kyo("turns", "--seal-key")
    assert shown.code == 0 and "file store" in shown.out, shown
    for store in seal.KEY_STORES:
        assert store in shown.out
    assert "leaves this machine" in shown.out


STUB = """#!/bin/sh
# Plays the Mac app's executable: the synchronizable item is the file $STUB_DIR/item.
[ "$1" = seal-key ] || exit 64
echo "$*" >> "$STUB_DIR/calls"
if [ -f "$STUB_DIR/refuse" ]; then echo "OSStatus -34018" >&2; exit 4; fi
case "$2" in
  read) [ -f "$STUB_DIR/item" ] || exit 3; cat "$STUB_DIR/item" ;;
  add) read key; [ -f "$STUB_DIR/item" ] && exit 5; echo "$key" > "$STUB_DIR/item" ;;
  delete) rm -f "$STUB_DIR/item" ;;
  *) exit 64 ;;
esac
"""


class Helper:
    """The stub executable and the item it holds."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.item = directory / "item"
        self.calls = directory / "calls"

    @property
    def held(self) -> bytes | None:
        return bytes.fromhex(self.item.read_text().strip()) if self.item.exists() else None

    def put(self, key: bytes) -> None:
        self.item.write_text(key.hex() + "\n")

    def refuse(self) -> None:
        (self.directory / "refuse").write_text("")

    @property
    def operations(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


@pytest.fixture
def helper(tmp_path: Path, monkeypatch) -> Helper:
    """A stub of the app's executable, named by $KYOSEI_SEAL_HELPER."""
    directory = tmp_path / "helper"
    directory.mkdir()
    script = directory / "Kyosei"
    script.write_text(STUB)
    script.chmod(0o755)
    monkeypatch.setenv("STUB_DIR", str(directory))
    monkeypatch.setenv(seal.HELPER_ENV, str(script))
    return Helper(directory)


CHOOSE = ("turns", "--seal-key", "icloud-keychain", "--key-leaves-this-machine")


def test_icloud_keychain_is_refused_until_the_disclosure_is_acknowledged(
    kyo, project: Path, helper: Helper
) -> None:
    """The refusal states the disclosure and names the flag, and the flag clears it."""
    three_prompts(kyo, project)
    key = seal.load_key()
    refused = kyo("turns", "--seal-key", "icloud-keychain")
    assert refused.code == 64, refused
    assert "leaves this machine, end-to-end encrypted by iCloud Keychain" in refused.err
    assert "copy also stays in a local store on this machine" in " ".join(refused.err.split())
    assert "--key-leaves-this-machine" in refused.err
    assert helper.held is None and not helper.operations and seal.recorded_choice() is None

    chosen = kyo(*CHOOSE)
    assert chosen.code == 0, chosen
    assert "leaves this machine, end-to-end encrypted by iCloud Keychain" in chosen.out
    assert "the key was added to iCloud Keychain" in chosen.out
    assert seal.recorded_choice() == seal.STORE_ICLOUD
    assert seal.local_store() == seal.STORE_FILE
    # The same key is the item and stays in the local file the hooks read.
    assert helper.held == key and seal.key_path().exists()
    assert seal.load_key() == key
    # The key went on standard input, never in an argument.
    assert helper.operations == ["seal-key read", "seal-key add", "seal-key read"]
    # Roots committed before the move still verify, and new lines commit with the key.
    prompt_hook(kyo, "aaaaaaaa-0000-4000-8000-000000000004", prompt="after the move")
    lines = index(project)
    assert seal.verify_message(key, lines[1], THREE_LINES)
    assert seal.verify_message(key, lines[-1], "after the move")
    assert json.loads(seal.choice_path().read_text()) == {
        "key_store": "icloud-keychain", "local": "file",
    }  # fmt: skip

    shown = kyo("turns", "--seal-key")
    assert "kept in iCloud Keychain, with a copy in the file store" in shown.out, shown

    # Choosing the file again keeps the local copy and leaves the item.
    back = kyo("turns", "--seal-key", "file")
    assert back.code == 0 and "the iCloud Keychain item is left" in back.out, back
    assert helper.held == key and "delete" not in " ".join(helper.operations)
    assert seal.load_key() == key and seal.chosen_store() == seal.STORE_FILE


def test_the_acknowledgement_flag_belongs_to_icloud_keychain_only(kyo, project: Path) -> None:
    assert kyo("turns", "--seal-key", "file", "--key-leaves-this-machine").code == 64
    assert kyo("turns", "--key-leaves-this-machine").code == 64


def test_a_store_that_does_not_keep_the_key_leaves_it_where_it_was(
    kyo, project: Path, helper: Helper
) -> None:
    three_prompts(kyo, project)
    key = seal.load_key()
    helper.refuse()
    refused = kyo(*CHOOSE)
    assert refused.code == 1 and "stays where it was" in refused.err, refused
    assert "OSStatus -34018" in refused.err and "iCloud Keychain refused" in refused.err
    assert seal.recorded_choice() is None and seal.load_key() == key and helper.held is None


def test_a_missing_helper_leaves_the_key_and_the_choice_as_they_were(
    kyo, project: Path, monkeypatch, tmp_path: Path
) -> None:
    three_prompts(kyo, project)
    key = seal.load_key()
    monkeypatch.setenv(seal.HELPER_ENV, str(tmp_path / "absent" / "Kyosei"))
    refused = kyo(*CHOOSE)
    assert refused.code == 1 and "stays where it was" in refused.err, refused
    assert "was not found" in refused.err and seal.HELPER_ENV in refused.err
    assert seal.recorded_choice() is None and seal.load_key() == key
    # The remedy: the executable named where it can be found clears the refusal.
    script = tmp_path / "Kyosei"
    script.write_text(STUB)
    script.chmod(0o755)
    (tmp_path / "item-dir").mkdir()
    monkeypatch.setenv("STUB_DIR", str(tmp_path / "item-dir"))
    monkeypatch.setenv(seal.HELPER_ENV, str(script))
    assert kyo(*CHOOSE).code == 0 and seal.recorded_choice() == seal.STORE_ICLOUD


def test_the_helper_is_looked_for_in_the_applications_folders(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(seal.HELPER_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    # The machine's own /Applications may hold the app; the first path is a stand-in.
    system = tmp_path / "system" / "Kyosei.app/Contents/MacOS/Kyosei"
    monkeypatch.setattr(seal, "HELPER_PATHS", (str(system), seal.HELPER_PATHS[1]))
    with pytest.raises(seal.SealKeyError) as err:
        seal._helper_path(None)
    assert str(system) in str(err.value)
    assert str(tmp_path / "Applications/Kyosei.app/Contents/MacOS/Kyosei") in str(err.value)
    found = tmp_path / "Applications/Kyosei.app/Contents/MacOS/Kyosei"
    found.parent.mkdir(parents=True)
    found.write_text("#!/bin/sh\n")
    found.chmod(0o755)
    assert seal._helper_path(None) == str(found)


def test_icloud_keychain_is_refused_off_macos(kyo, project: Path, monkeypatch) -> None:
    three_prompts(kyo, project)
    monkeypatch.delenv(seal.HELPER_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    refused = kyo(*CHOOSE)
    assert refused.code == 1 and "macOS only" in refused.err, refused
    assert seal.recorded_choice() is None


def test_a_machine_with_no_key_fetches_the_item_into_its_local_store(
    kyo, project: Path, helper: Helper
) -> None:
    helper.put(KEY)
    fetched = kyo(*CHOOSE)
    assert fetched.code == 0 and "fetched from iCloud Keychain" in fetched.out, fetched
    assert seal.key_path().read_text().strip() == KEY.hex()
    assert seal.load_key() == KEY and "add" not in " ".join(helper.operations)


def test_a_machine_with_no_key_and_no_item_makes_one_in_both(
    kyo, project: Path, helper: Helper
) -> None:
    made = kyo(*CHOOSE)
    assert made.code == 0 and "a new key was made" in made.out, made
    assert helper.held is not None and seal.load_key() == helper.held


def test_a_machine_whose_key_differs_from_the_item_is_refused(
    kyo, project: Path, helper: Helper
) -> None:
    three_prompts(kyo, project)
    key = seal.load_key()
    helper.put(KEY)
    refused = kyo(*CHOOSE)
    assert refused.code == 1 and "the two keys differ" in refused.err, refused
    assert seal.recorded_choice() is None and seal.load_key() == key and helper.held == KEY


def test_choosing_it_again_with_the_same_key_moves_nothing(
    kyo, project: Path, helper: Helper
) -> None:
    three_prompts(kyo, project)
    assert kyo(*CHOOSE).code == 0
    before = list(helper.operations)
    again = kyo(*CHOOSE)
    assert again.code == 0 and "already hold the same key" in again.out, again
    assert "add" not in " ".join(helper.operations[len(before):])


def test_the_copy_stays_in_the_login_keychain_when_that_held_the_key(
    kyo, project: Path, helper: Helper, temporary_keychain
) -> None:
    three_prompts(kyo, project)
    key = seal.load_key()
    assert kyo("turns", "--seal-key", "keychain").code == 0
    assert kyo(*CHOOSE).code == 0
    assert seal.local_store() == seal.STORE_KEYCHAIN and seal.load_key() == key
    assert helper.held == key and not seal.key_path().exists()
    assert json.loads(seal.choice_path().read_text())["local"] == "keychain"


def test_a_record_in_the_old_form_is_read_with_the_file_as_its_copy(
    kyo, project: Path, helper: Helper
) -> None:
    seal.choice_path().write_text(json.dumps({"key_store": "icloud-keychain"}) + "\n")
    assert seal.chosen_store() == seal.STORE_ICLOUD and seal.local_store() == seal.STORE_FILE
    seal._file_write(KEY, None)
    assert seal.load_key() == KEY and not helper.operations


def test_a_hook_on_a_machine_with_no_copy_chains_the_line_and_makes_no_key(
    kyo, project: Path, helper: Helper
) -> None:
    """The hook must not fail the prompt, and must not make a key that differs from the
    item; the error names the command that fetches the key, and running it clears it."""
    opt_in_transcripts(project)
    helper.put(KEY)
    seal.record_choice(seal.STORE_ICLOUD, None, seal.STORE_FILE)
    with pytest.raises(seal.SealKeyError, match="run `kyo turns --seal-key icloud-keychain"):
        seal.load_key()
    ran = prompt_hook(kyo, PROMPT_1, prompt="hello")
    assert ran.code == 0 and ran.out == "", ran
    (line,) = index(project)
    assert line["seal"] == seal.SEAL_VERSION and line["root"] is None
    assert not seal.key_path().exists() and not helper.operations

    assert kyo(*CHOOSE).code == 0
    assert seal.load_key() == KEY
    prompt_hook(kyo, PROMPT_2, prompt="again")
    assert seal.verify_message(KEY, index(project)[-1], "again")


def test_a_key_store_that_cannot_be_read_still_chains_the_line(
    kyo, project: Path, helper: Helper
) -> None:
    opt_in_transcripts(project)
    seal.record_choice(seal.STORE_ICLOUD, None, seal.STORE_KEYCHAIN)
    seal.key_path().unlink(missing_ok=True)
    helper.refuse()
    ran = prompt_hook(kyo, PROMPT_1, prompt="hello")
    assert ran.code == 0 and ran.out == "", ran
    (line,) = index(project)
    assert line["seal"] == seal.SEAL_VERSION and line["root"] is None


def test_an_item_that_appears_between_the_read_and_the_add_is_not_replaced(
    kyo, project: Path, helper: Helper, monkeypatch
) -> None:
    """Exit 5 from `add`: the item exists, so nothing is recorded."""
    three_prompts(kyo, project)
    key = seal.load_key()
    monkeypatch.setattr(seal, "_icloud_read", lambda environ: None)
    helper.put(KEY)
    with pytest.raises(seal.SealKeyError, match="already holds a seal key item"):
        seal.move_key(seal.STORE_ICLOUD)
    assert seal.recorded_choice() is None and seal.load_key() == key


@pytest.fixture
def temporary_keychain(tmp_path: Path, monkeypatch):
    """A keychain file of the test's own, never added to the account's search list."""
    if sys.platform != "darwin":
        pytest.skip("the keychain stores exist on macOS only")
    path = tmp_path / "seal-test.keychain-db"
    made = subprocess.run(
        ["/usr/bin/security", "create-keychain", "-p", "", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    if made.returncode != 0:
        pytest.skip(f"security create-keychain failed: {made.stderr.strip()}")
    monkeypatch.setenv(seal.KEYCHAIN_ENV, str(path))
    yield path
    subprocess.run(["/usr/bin/security", "delete-keychain", str(path)],
                   capture_output=True, timeout=30, check=False)  # fmt: skip


def test_the_key_moves_to_a_keychain_and_back(kyo, project: Path, temporary_keychain) -> None:
    three_prompts(kyo, project)
    key = seal.load_key()
    chosen = kyo("turns", "--seal-key", "keychain")
    assert chosen.code == 0 and "moved from the file store" in chosen.out, chosen
    assert not seal.key_path().exists()
    found = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", seal.KEYCHAIN_SERVICE,
         "-a", seal.KEYCHAIN_ACCOUNT, "-w", str(temporary_keychain)],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    assert found.stdout.strip() == key.hex()
    assert seal.chosen_store() == seal.STORE_KEYCHAIN and seal.load_key() == key
    prompt_hook(kyo, "aaaaaaaa-0000-4000-8000-000000000004", prompt="from the keychain")
    assert seal.verify_message(key, index(project)[-1], "from the keychain")

    back = kyo("turns", "--seal-key", "file")
    assert back.code == 0 and seal.load_key() == key, back
    assert seal.read_key(seal.STORE_KEYCHAIN) is None


def test_a_first_key_can_be_made_in_a_keychain(kyo, project: Path, temporary_keychain) -> None:
    chosen = kyo("turns", "--seal-key", "keychain")
    assert chosen.code == 0 and "a new key was made there" in chosen.out, chosen
    assert not seal.key_path().exists()
    assert len(seal.load_key()) == 32
