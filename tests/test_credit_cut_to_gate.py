"""A credit from a turn that answers several gates stores only its gate's part (KYO-934).

The Mac app sends one prompt holding an answer to each of several gates, each under the line
``kyo serve`` writes to say what it answers. The transcripts here hold such a prompt for two
records created by the test, so the ids in its lines are real; the words are invented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kyosei import credits, message_lines, serve_notes

from .conftest import declare_person, new_record, opt_in_transcripts
from .test_kyo658_discussions import (
    PROMPT_1,
    SESSION,
    _file,
    person,
    prompt_hook,
    write_transcript,
)

T1 = "2026-11-06T10:00:00.000Z"
T3 = "2026-11-06T10:01:00.000Z"
ATTACHED = f"attached /home/composer/attachments/proj/{SESSION}/{'ab' * 32}.png"
REPLY = 'on your reply 8c1f0000-0000-4000-8000-0000000000e2 "the count was blank because..."'
FIRST_ANSWER = "the button is where I asked, approved"
FIRST_MORE = "and the colour is right too"
SECOND_ANSWER = "this one still shows the old label"
REMARK = "why was that blank at all?"
OPENING = "notes from the signed build:"


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


@pytest.fixture
def setup(kyo, project: Path, session: str) -> Path:
    opt_in_transcripts(project)
    declare_person(project)
    return project


def _record(kyo, title: str) -> tuple[str, str, str]:
    """A record with one criterion and one open gate that waits on a person."""
    record = new_record(kyo, kind="task", title=title, criteria=["it is right"])
    done = kyo("gate", record, "awaiting:human-review", "--do", "look at it", "--json")
    assert done.code == 0, done
    return record, f"{record}.c1", done.json["gate_id"]


def _say(kyo, text: str) -> None:
    """The person's one prompt of the session, sent and indexed."""
    write_transcript([person("p-1", PROMPT_1, text, T1)])
    prompt_hook(kyo, PROMPT_1)


def _report(first_gate: str, second_gate: str) -> str:
    """One prompt answering two gates and remarking on a reply, as `kyo serve` sends it."""
    return "\n".join(
        [
            OPENING,
            f'on record {first_gate} "look at it"',
            ATTACHED,
            FIRST_ANSWER,
            "",
            ATTACHED,
            FIRST_MORE,
            f'on record {second_gate} "look at it"',
            SECOND_ANSWER,
            REPLY,
            REMARK,
        ]
    )


def _credit(kyo, record: str, index: int = -1) -> dict:
    return kyo("show", record, "--json").json["credits"][index]


def _words(kyo, record: str, quote_id: str) -> str:
    shown = kyo("show", record, "--json").json
    return next(e["text"] for e in shown["evidence_artifacts"] if e["id"] == quote_id)


FIRST_PART = f"[...]\n{FIRST_ANSWER}\n[...]\n{FIRST_MORE}\n[...]"
SECOND_PART = f"[...]\n{SECOND_ANSWER}\n[...]"


# --------------------------------------------------------------- c1: the part is stored


def test_ungate_stores_the_part_headed_for_its_gate_and_names_the_turn(kyo, setup: Path) -> None:
    first, _, first_gate = _record(kyo, "The button")
    second, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    for record, gate, part in ((first, first_gate, FIRST_PART), (second, second_gate, SECOND_PART)):
        done = kyo("ungate", gate)
        assert done.code == 0, done
        credit = _credit(kyo, record)
        assert _words(kyo, record, credit["quote_id"]) == part
        assert credit["source_turn"] == "p-1" and credit["source_session"] == SESSION
        (turn,) = credit["turns"]
        assert turn["turn"] == "p-1" and turn["prompt_id"] == PROMPT_1
        assert turn["role"] == "approval" and turn["quote_id"] == credit["quote_id"]
        assert credit["transcript_check"] in ("matched", "matched-paste-unknown")


def test_verify_takes_the_part_headed_for_another_element_of_its_record(kyo, setup: Path) -> None:
    """The person answered under the gate's line; the verdict is on the criterion, which no
    line names, so the parts naming anything of the criterion's record are taken."""
    first, cid, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    done = kyo("verify", cid, "--verdict", "met", "--method", "human-observed")
    assert done.code == 0, done
    credit = _credit(kyo, first)
    assert _words(kyo, first, credit["quote_id"]) == FIRST_PART
    shown = kyo("show", first, "--json").json
    evidence = {e["id"]: e["text"] for e in shown["evidence_artifacts"]}
    assert FIRST_PART in evidence.values() and SECOND_ANSWER not in "".join(evidence.values())


def test_confirm_stores_the_part_on_the_verdict_and_the_gate(kyo, setup: Path) -> None:
    """`kyo confirm` composes `kyo verify` and `kyo ungate`, handing each the words it read
    and the turn: the part passes their check of a quote against the named turn."""
    first, cid, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    done = kyo("confirm", first, "--criterion", cid, "--no-close")
    assert done.code == 0, done
    credit = _credit(kyo, first)
    assert _words(kyo, first, credit["quote_id"]) == FIRST_PART
    assert credit["source_turn"] == "p-1"
    assert kyo("show", first, "--json").json["criteria"][0]["state"] == "met"
    assert f"stored of prompt {PROMPT_1}" in done.out and FIRST_ANSWER in done.out


def test_an_exact_header_is_preferred_to_the_rest_of_its_record(kyo, setup: Path) -> None:
    record, cid, gate = _record(kyo, "The button")
    _say(
        kyo,
        f'on record {gate} "look at it"\n{FIRST_ANSWER}\n'
        f'on record {cid} "it is right"\n{SECOND_ANSWER}',
    )
    assert kyo("ungate", gate).code == 0
    credit = _credit(kyo, record)
    assert _words(kyo, record, credit["quote_id"]) == f"[...]\n{FIRST_ANSWER}\n[...]"


def test_a_header_whose_quoted_words_name_another_record_is_not_that_records_part(
    kyo, setup: Path
) -> None:
    """KYO-917.g1's text named KYO-903.c8: only the id the line is for counts."""
    first, _, first_gate = _record(kyo, "The button")
    second, _, second_gate = _record(kyo, "The label")
    text = f'on record {first_gate} "see {second}.c1 and {second_gate}"\n{FIRST_ANSWER}'
    _say(kyo, text)
    done = kyo("ungate", second_gate)
    assert done.code == 0, done
    credit = _credit(kyo, second)
    assert _words(kyo, second, credit["quote_id"]) == text
    assert "is stored whole" in done.out


# --------------------------------------------------------------- c2: the whole turn


def test_a_turn_with_no_part_for_the_gate_is_stored_whole(kyo, setup: Path) -> None:
    first, _, first_gate = _record(kyo, "The button")
    third, _, third_gate = _record(kyo, "The third")
    _, _, second_gate = _record(kyo, "The label")
    text = _report(first_gate, second_gate)
    _say(kyo, text)
    done = kyo("ungate", third_gate)
    assert done.code == 0, done
    credit = _credit(kyo, third)
    assert _words(kyo, third, credit["quote_id"]) == text
    assert f"prompt {PROMPT_1} is stored whole: no part of it is headed for {third_gate}" in (
        done.out
    )


def test_a_turn_with_no_server_line_is_stored_whole(kyo, setup: Path) -> None:
    record, _, gate = _record(kyo, "The button")
    text = "on record, this looks right\nattached is what I saw\non the other hand, fine"
    _say(kyo, text)
    done = kyo("ungate", gate)
    assert done.code == 0, done
    assert _words(kyo, record, _credit(kyo, record)["quote_id"]) == text
    assert "is stored whole" in done.out


def test_a_quote_file_still_decides_what_is_stored(kyo, setup: Path, tmp_path: Path) -> None:
    first, _, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    done = kyo(
        "ungate", first_gate, "--turn", "p-1", "--quote-file", _file(tmp_path, "q.txt", OPENING)
    )
    assert done.code == 0, done
    assert _words(kyo, first, _credit(kyo, first)["quote_id"]).strip() == OPENING
    assert "stored of" not in done.out and "stored whole" not in done.out


def test_a_part_that_fails_the_check_falls_back_to_the_whole_turn(
    kyo, setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the check of the part against the turn's typed words fails this test."""
    record, _, gate = _record(kyo, "The button")
    text = f'on record {gate} "look at it"\n{FIRST_ANSWER}'
    _say(kyo, text)
    monkeypatch.setattr(
        message_lines,
        "cut_for",
        lambda segments, targets: message_lines.Cut("words nobody typed", ("on record X",)),
    )
    done = kyo("ungate", gate)
    assert done.code == 0, done
    assert _words(kyo, record, _credit(kyo, record)["quote_id"]) == text
    assert "did not check against the turn's typed words" in done.out


# --------------------------------------------------------------- c3: the receipt


def test_the_receipt_names_the_header_and_prints_the_parts_first_line(kyo, setup: Path) -> None:
    first, _, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    done = kyo("ungate", second_gate)
    assert done.code == 0, done
    (line,) = [line for line in done.out.splitlines() if "stored of" in line]
    assert f'its part under on record {second_gate} "look at it"' in line
    assert f'which begins "{SECOND_ANSWER}"' in line
    assert f"{len(SECOND_PART)} of the turn's" in line
    assert "stored whole" not in done.out


# --------------------------------------------------------------- c4: verification


def test_turns_verify_passes_on_a_store_holding_a_cut_credit(kyo, setup: Path) -> None:
    first, _, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    assert kyo("ungate", first_gate).code == 0
    (turn,) = _credit(kyo, first)["turns"]
    assert turn["root"], "the cut credit copied the index line's commitment"
    done = kyo("turns", "--verify", "--json")
    assert done.code == 0, done
    assert done.json["ok"] and done.json["credit_turns_checked"] == 1


def test_a_cut_credits_words_are_found_in_the_turn_as_a_quote_is(kyo, setup: Path) -> None:
    first, _, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    assert kyo("ungate", first_gate).code == 0
    credit = _credit(kyo, first)
    stored = _words(kyo, first, credit["quote_id"])
    pieces = credits.transcript.quote_pieces(stored)
    assert len(pieces) == 2
    assert credits.transcript.pieces_in(pieces, [_report(first_gate, second_gate)])


def test_a_part_that_does_not_occur_in_the_named_turn_is_refused(
    kyo, setup: Path, tmp_path: Path
) -> None:
    """A part is given to a command as words beside the turn it was cut from, which is how
    `kyo confirm` hands it to the steps it composes; words the turn does not hold are
    refused, and the turn's own part clears the refusal."""
    first, _, first_gate = _record(kyo, "The button")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    line = ("ungate", first_gate, "--turn", "p-1", "--quote-file")
    foreign = "[...]\nthe button is where I asked, rejected\n[...]"
    refused = kyo(*line, _file(tmp_path, "foreign.txt", foreign))
    assert refused.code != 0 and "credit-quote-not-found" in refused.err
    assert not kyo("show", first, "--json").json["credits"]
    assert kyo(*line, _file(tmp_path, "part.txt", FIRST_PART)).code == 0


# --------------------------------------------------------------- c5: kyo show


def test_show_prints_the_parts_words_and_says_they_are_part_of_a_longer_turn(
    kyo, setup: Path
) -> None:
    first, _, first_gate = _record(kyo, "The button")
    third, _, third_gate = _record(kyo, "The third")
    _, _, second_gate = _record(kyo, "The label")
    _say(kyo, _report(first_gate, second_gate))
    assert kyo("ungate", first_gate).code == 0
    assert kyo("ungate", third_gate).code == 0
    shown = kyo("show", first).out
    lines = [line for line in shown.splitlines() if FIRST_ANSWER in line]
    assert lines and all("(part of a longer turn)" in line for line in lines)
    assert all(f'"{FIRST_ANSWER}"' in line for line in lines)
    assert '"[...]"' not in shown
    assert "part of a longer turn" not in kyo("show", third).out


# --------------------------------------------------------------- the form, in one place


def test_parts_end_at_the_next_anchor_line_and_not_at_an_attachment() -> None:
    text = _report("KYO-1.g1", "KYO-2.g1")
    first, second, third = message_lines.parts([text])
    assert first.ids == ("KYO-1.g1",) and second.ids == ("KYO-2.g1",) and third.ids == ()
    lines = text.split("\n")
    assert [lines[i] for i in first.at] == [FIRST_ANSWER, FIRST_MORE]
    assert [lines[i] for i in second.at] == [SECOND_ANSWER]
    assert [lines[i] for i in third.at] == [REMARK]


def test_a_notes_message_is_cut_at_its_note_headings() -> None:
    text = (
        "[kyo-message, 2 notes]\n\nNote 1 (KYO-N41)\n"
        'on record KYO-12.g1 "the iPad build is checked by..."\n'
        "this looks right on the iPad, approved\n\nNote 2 (KYO-N42)\nan unanchored remark"
    )
    cut = message_lines.cut_for([text], ["KYO-12"])
    assert cut is not None
    assert cut.text == "[...]\nthis looks right on the iPad, approved\n[...]"
    assert message_lines.cut_for([text], ["KYO-13.g1"]) is None


def test_a_part_runs_across_a_paste_and_marks_its_place() -> None:
    segments = ['on record KYO-1.g1 "look"\nsee this:', "and so, approved\non note KYO-N2\nno"]
    cut = message_lines.cut_for(segments, ["KYO-1.g1"])
    assert cut is not None and cut.text == "[...]\nsee this:\n[...]\nand so, approved\n[...]"


def test_a_long_part_keeps_its_head_and_tail_and_still_checks() -> None:
    words = " ".join(f"word{n}" for n in range(1200))
    kept = credits._part_text(words)
    assert len(kept) < len(words) and f"\n{credits.OMISSION}\n" in kept
    pieces = credits.transcript.quote_pieces(kept)
    assert credits.transcript.pieces_in(pieces, [words])


def test_every_line_serve_notes_documents_is_read_as_the_servers() -> None:
    """The module docstring of `serve_notes` states the form; each `on ...` and `attached`
    example in it is recognised here, so a new form of line cannot go unread."""
    examples = [
        line.strip()
        for line in (serve_notes.__doc__ or "").splitlines()
        if re.match(r"^    (on |attached )", line)
    ]
    assert len(examples) >= 10
    unread = [
        line
        for line in examples
        if not (message_lines.is_anchor_line(line) or message_lines.is_attached_line(line))
        # The docstring abbreviates a uuid and an attachment's digest, which the form does not.
        and "..." not in line.split('"')[0]
        and "<sha256>" not in line
    ]
    assert not unread, unread
    assert message_lines.is_anchor_line(REPLY) and message_lines.is_attached_line(ATTACHED)


def test_the_writers_and_the_reader_agree() -> None:
    assert message_lines.record_named(message_lines.record_line("KYO-7.g2", '"a b..."')) == (
        "KYO-7.g2"
    )
    assert message_lines.record_named(message_lines.record_line("KYO-7", "")) == "KYO-7"
    assert message_lines.record_named(message_lines.record_line("KYO-7.r1.x1", None)) == (
        "KYO-7.r1.x1"
    )
    uuid = "8c1f0000-0000-4000-8000-0000000000e2"
    for agent in (True, False):
        head = message_lines.turn_line_head(uuid, agent=agent, session=uuid)
        assert message_lines.is_anchor_line(f'{head} "words"')
    assert message_lines.is_anchor_line(message_lines.note_line("KYO-N41"))
    assert message_lines.is_attached_line(message_lines.attached_line(f"/a/b/{'0' * 64}.pdf"))
    assert not message_lines.is_attached_line("attached is the screenshot")
    assert not message_lines.is_anchor_line("on record, it is fine")
    assert not message_lines.is_anchor_line("on file systems I have no view")


def test_the_mac_apps_record_line_reads_the_same_ids() -> None:
    """The client keeps its own pattern, in TypeScript; it holds the same id form."""
    source = Path(__file__).parent.parent / "apps/apple/web/src/marks.ts"
    assert message_lines.RECORD_ID in source.read_text(encoding="utf-8")
