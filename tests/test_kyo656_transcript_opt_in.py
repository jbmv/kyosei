"""Reading transcripts is opt-in per project, and credits apply only where people are
declared (KYO-656, decision KYO-654 DC12)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

import kyosei
from kyosei import cmd_onboard
from kyosei.config import (
    READ_TRANSCRIPTS_KEY,
    load_config,
    render_config,
    transcripts_opted_in,
)
from kyosei.errors import KyoseiError
from kyosei.harness import claude_code_transcript as transcript
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

from .conftest import (
    TEST_PERSON,
    declare_person,
    invoke,
    new_record,
    opt_in_transcripts,
)

SESSION = "7c1d2e3f-1111-4a2b-9c3d-0123456789ab"
WORDS = "it works on the laptop"
PERSON_TURN = "11111111-1111-4a2b-9c3d-000000000001"
HUMAN_OBSERVED = ("--verdict", "met", "--method", "human-observed")


def _config_path(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def _write_key(project: Path, value: object) -> None:
    raw = yaml.safe_load(_config_path(project).read_text(encoding="utf-8"))
    raw[READ_TRANSCRIPTS_KEY] = value
    _config_path(project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _file(at: Path, name: str, text: str = WORDS) -> str:
    path = at / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _claude_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "claude-config"
    (home / "projects" / "-a-project").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return home


@pytest.fixture
def transcript_on_disk(_claude_home: Path) -> Path:
    """A transcript that would match, so a credit reading it would say ``matched``."""
    record = {
        "type": "user",
        "uuid": PERSON_TURN,
        "timestamp": "2026-11-06T10:00:00.000Z",
        "sessionId": SESSION,
        "isSidechain": False,
        "message": {"role": "user", "content": WORDS},
        "origin": {"kind": "human"},
        "promptSource": "typed",
    }
    path = _claude_home / "projects" / "-a-project" / f"{SESSION}.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def no_reads(monkeypatch) -> None:
    """Any opening of a transcript or the prompt history fails the test."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a project that has not opted in read a transcript")

    for name in (
        "candidate_transcripts",
        "read_transcripts",
        "read_history",
        "check_quote",
        "find_person_turns",
        "measure_span",
    ):
        monkeypatch.setattr(transcript, name, refuse)


# --------------------------------------------------------------------------- the key


def test_only_an_explicit_true_opts_in(project: Path) -> None:
    assert READ_TRANSCRIPTS_KEY in CONFIG_CONSUMERS
    assert READ_TRANSCRIPTS_KEY in OPTIONAL_CONFIG_KEYS
    assert transcripts_opted_in(load_config(project)) is False
    _write_key(project, False)
    assert transcripts_opted_in(load_config(project)) is False
    _write_key(project, True)
    assert transcripts_opted_in(load_config(project)) is True


def test_a_read_transcripts_value_that_is_not_a_boolean_is_refused(project: Path) -> None:
    """The remedy: write a boolean, or delete the line."""
    _write_key(project, "yes please")
    with pytest.raises(KyoseiError, match="must be true or false"):
        load_config(project)
    _write_key(project, True)
    load_config(project)
    raw = yaml.safe_load(_config_path(project).read_text(encoding="utf-8"))
    del raw[READ_TRANSCRIPTS_KEY]
    _config_path(project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert transcripts_opted_in(load_config(project)) is False


def test_every_module_that_imports_the_reader_asks_the_one_question() -> None:
    """KYO-656: a new reader that skips the check fails here (and KYO-657 inherits it)."""
    src = Path(kyosei.__file__).parent
    readers = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "claude_code_transcript.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [f"{node.module or ''}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("claude_code_transcript" in name for name in names):
                readers.append(path)
    assert {p.name for p in readers} == {
        "cmd_discuss.py",  # KYO-658: the stamp folds the transcript into the index
        "cmd_notes.py",
        "cmd_turns.py",
        "credits.py",
        "doctor_install.py",
        "doctor_records.py",
        "session_layer.py",
    }
    for path in readers:
        assert "transcripts_opted_in(" in path.read_text(encoding="utf-8"), path


# ------------------------------------------------- c2: no transcript read until opted in


def test_a_credit_in_a_project_that_has_not_opted_in_reads_nothing(
    kyo, project: Path, tmp_path: Path, transcript_on_disk: Path, no_reads: None
) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    ran = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--evidence-file",
              _file(tmp_path, "w"), "--session", SESSION, "--turn", PERSON_TURN,
              "--json")  # fmt: skip
    assert ran.code == 0, ran
    (credit,) = kyo("show", record, "--json").json["credits"]
    assert credit["transcript_check"] == "not-applicable"
    assert credit["source_session"] == SESSION
    assert credit["source_kind"] == "claude-code"
    # Kept as the person's pointer, and never confirmed: no form and no timestamp.
    assert credit["source_turn"] == PERSON_TURN
    assert credit["observed_at"] is None and credit["words_form"] is None
    human = kyo("show", record)
    assert human.code == 0


def test_declaring_false_reads_nothing_either(
    kyo, project: Path, tmp_path: Path, transcript_on_disk: Path, no_reads: None
) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    _write_key(project, False)
    ran = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--evidence-file",
              _file(tmp_path, "w"), "--session", SESSION)  # fmt: skip
    assert ran.code == 0, ran
    assert "no transcript was read" in ran.out
    assert kyo("show", record, "--json").json["credits"][0]["transcript_check"] == "not-applicable"


def test_opting_in_is_what_turns_the_read_on(
    kyo, project: Path, tmp_path: Path, transcript_on_disk: Path
) -> None:
    """The positive half: the same line matches once the key is written, so the test above
    is passing for the right reason."""
    record = new_record(kyo, criteria=["a", "b"])
    declare_person(project)
    line = ("verify", "{}", *HUMAN_OBSERVED, "--evidence-file", _file(tmp_path, "w"),
            "--session", SESSION)  # fmt: skip
    assert kyo(*[a.format(f"{record}.c1") for a in line]).code == 0
    opt_in_transcripts(project)
    assert kyo(*[a.format(f"{record}.c2") for a in line]).code == 0
    checks = sorted(c["transcript_check"] for c in kyo("show", record, "--json").json["credits"])
    assert checks[1] == "not-applicable" and checks[0].startswith("matched")


def test_a_span_and_a_refining_turn_read_nothing(
    kyo, project: Path, tmp_path: Path, transcript_on_disk: Path, no_reads: None
) -> None:
    declare_person(project)
    decision_file = _file(
        tmp_path,
        "d.yml",
        f"recommended: keep it\noutcome: refined\nrefining_turn: {PERSON_TURN}\n"
        "alternatives:\n  - {text: keep it, chosen: true}\n",
    )
    ran = kyo("new", "decision", "--title", "Keep the cache", "--quote-file", _file(tmp_path, "q"),
              "--decision-file", decision_file, "--session", SESSION,
              "--span-first", PERSON_TURN, "--span-last", PERSON_TURN, "--opened-late",
              "--json")  # fmt: skip
    assert ran.code == 0, ran
    shown = kyo("show", ran.json["record_id"], "--json")
    (credit,) = shown.json["credits"]
    assert credit["transcript_check"] == "not-applicable"
    assert credit["span_first_turn"] == PERSON_TURN and credit["span_person_turns"] is None
    # The human view renders an unmeasured discussion (it raised on one, KYO-656).
    assert "not measured" in kyo("show", ran.json["record_id"]).out


def test_doctor_does_not_look_for_a_transcript_until_opted_in(
    kyo, project: Path, tmp_path: Path, transcript_on_disk: Path, monkeypatch
) -> None:
    from .test_credit_coverage import _credit, _human_verdict

    declare_person(project)
    record, verdict = _human_verdict(kyo)
    _credit(project, record, [("verification", verdict)], transcript_check="transcript-absent")

    def refuse(*_args, **_kwargs):
        raise AssertionError("doctor looked for a transcript")

    monkeypatch.setattr(transcript, "candidate_transcripts", refuse)
    result = kyo("doctor", "--json")
    assert "credit_transcripts_appeared" not in str(result.json["notices"])


def test_a_recheck_is_refused_until_the_project_opts_in(
    kyo, project: Path, tmp_path: Path, transcript_on_disk: Path
) -> None:
    from .test_credit_coverage import _credit, _human_verdict

    declare_person(project)
    record, verdict = _human_verdict(kyo)
    # _credit quotes the verdict's own evidence, so the transcript must hold those words.
    transcript_on_disk.write_text(
        transcript_on_disk.read_text(encoding="utf-8")
        .replace(WORDS, "the person's words")
        .replace(PERSON_TURN, "turn-1"),
        encoding="utf-8",
    )
    credit = _credit(
        project,
        record,
        [("verification", verdict)],
        transcript_check="transcript-absent",
        source_session=SESSION,
    )
    refused = kyo("credit", "--recheck", credit)
    assert refused.code == 2 and "credit-transcripts-not-opted-in" in refused.err, refused
    assert f"{READ_TRANSCRIPTS_KEY}: true" in refused.err
    assert transcript_on_disk.exists()
    opt_in_transcripts(project)
    done = kyo("credit", "--recheck", credit, "--json")
    assert done.code == 0, done
    assert done.json["transcript_check"].startswith("matched")


# -------------------------------------------- c3: nothing refused where nobody is declared


def _gate(kyo, record: str, gate_type: str = "awaiting:human-review") -> str:
    made = kyo("gate", record, gate_type, "--do", "look at the banner", "--json")
    return made.json["gate_id"]


def test_a_human_observed_verdict_needs_no_credit_with_nobody_declared(
    kyo, project: Path, tmp_path: Path, no_reads: None
) -> None:
    record = new_record(kyo, criteria=["a"])
    ran = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--evidence-file", _file(tmp_path, "w"))
    assert ran.code == 0, ran
    assert kyo("show", record, "--json").json["credits"] == []


def test_kyo_confirm_with_nobody_declared_is_not_refused(
    kyo, project: Path, tmp_path: Path, no_reads: None
) -> None:
    record = new_record(kyo, criteria=["a"])
    gate = _gate(kyo, record)
    ran = kyo("confirm", record, "--quote-file", _file(tmp_path, "w"), "--gate", gate,
              "--criterion", f"{record}.c1", "--no-close", "--json")  # fmt: skip
    assert ran.code == 0, ran
    assert ran.json["credit_id"] is None
    shown = kyo("show", record, "--json").json
    assert shown["gates"][0]["cleared_at"]
    assert all(c["state"] == "met" for c in shown["criteria"])
    assert shown["credits"] == []


def test_kyo_confirm_of_a_gate_alone_with_nobody_declared_keeps_the_words(
    kyo, project: Path, tmp_path: Path
) -> None:
    """With no credit to hold them, the words are attached on their own."""
    record = new_record(kyo, criteria=[])
    gate = _gate(kyo, record, "awaiting:human-run")
    ran = kyo("confirm", record, "--quote-file", _file(tmp_path, "w"), "--gate", gate, "--no-close",
              "--json")  # fmt: skip
    assert ran.code == 0, ran
    assert ran.json["credit_id"] is None
    shown = kyo("show", record, "--json").json
    assert shown["gates"][0]["cleared_at"]
    assert any(
        kyo("show", e["id"], "--json").code == 0 and e["sha256"]
        for e in shown["evidence_artifacts"]
    )
    assert "attached" in shown["gates"][0]["cleared_why"]


def test_a_person_gate_clears_with_nobody_declared(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    gate = _gate(kyo, record)
    ran = kyo("ungate", gate, "--why", "the banner shows", "--json")
    assert ran.code == 0, ran
    assert kyo("show", record, "--json").json["gates"][0]["cleared_at"]


def test_a_carried_settle_clears_a_person_gate_with_nobody_declared(kyo, project: Path) -> None:
    made = kyo("new", "reminder", "--title", "ask", "--json").json["record_id"]
    kyo("gate", made, "awaiting:human-review", "--do", "answer it")
    ran = kyo("wont-do", made, "--why", "dropped")
    assert ran.code == 0, ran


def test_filing_a_decision_needs_no_credit_with_nobody_declared(
    kyo, project: Path, tmp_path: Path
) -> None:
    ran = kyo("new", "decision", "--title", "Keep it", "--evidence-file", _file(tmp_path, "e"),
              "--json")  # fmt: skip
    assert ran.code == 0, ran
    assert kyo("show", ran.json["record_id"], "--json").json["credits"] == []


def test_declaring_someone_turns_each_enforcement_point_back_on(
    kyo, project: Path, tmp_path: Path
) -> None:
    declare_person(project)
    record = new_record(kyo, criteria=["a"])
    gate = _gate(kyo, record)
    refused = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--evidence-file",
                  _file(tmp_path, "w"))  # fmt: skip
    assert refused.code == 2 and "human-observed-requires-credit" in refused.err
    cleared = kyo("ungate", gate, "--why", "x")
    assert cleared.code == 2 and "person-gate-clear-requires-credit" in cleared.err
    assert kyo("confirm", record, "--quote-file", _file(tmp_path, "w"), "--gate", gate).code == 2


def test_a_credit_flag_with_nobody_declared_is_still_refused(
    kyo, project: Path, tmp_path: Path
) -> None:
    """Asking for a credit nobody could hold is refused, not dropped."""
    record = new_record(kyo, criteria=["a"])
    ran = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--evidence-file", _file(tmp_path, "w"),
              "--person", TEST_PERSON, "--source-other", "a call")  # fmt: skip
    assert ran.code == 2 and "credit-person-undeclared" in ran.err, ran


def _people_notices(result) -> list[str]:
    return [n for n in result.json["notices"] if "credit_people_undeclared" in n]


def test_doctor_gives_one_notice_naming_the_key_and_the_question(
    kyo, project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--evidence-file",
               _file(tmp_path, "w")).code == 0  # fmt: skip
    gate = _gate(kyo, record)
    assert kyo("ungate", gate, "--why", "x").code == 0
    result = kyo("doctor", "--json")
    (notice,) = _people_notices(result)
    assert "`people:`" in notice and "D13" in notice and "D14" in notice
    # The one notice: neither coverage check adds to it, and it is no problem.
    assert "credit_coverage" not in str(result.json["notices"])
    assert not [p for p in result.json["problems"] if "credit_coverage" in p]


def test_declaring_a_person_clears_the_notice(kyo, project: Path) -> None:
    """The first remedy the notice names."""
    assert _people_notices(kyo("doctor", "--json"))
    declare_person(project)
    assert not _people_notices(kyo("doctor", "--json"))


def test_recording_d13_declined_clears_the_notice(kyo, project: Path) -> None:
    """The second remedy the notice names."""
    assert _people_notices(kyo("doctor", "--json"))
    raw = yaml.safe_load(_config_path(project).read_text(encoding="utf-8"))
    raw["onboarding"] = {"D13": "declined"}
    _config_path(project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert not _people_notices(kyo("doctor", "--json"))


# ---------------------------------------------------------------- c1: asked at onboarding


def _prompt_entry(ident: str) -> str:
    text = cmd_onboard.packaged_prompt_text()
    return re.search(rf"\*\*\[{ident}\].*?(?=\n- \*\*\[|\n\n)", text, re.S).group(0)


def test_the_prompt_asks_the_transcript_question_with_its_disclosure() -> None:
    entry = " ".join(_prompt_entry("D14").split())
    assert "Ask" in entry and "?" in entry
    assert "What is read:" in entry and "~/.claude/history.jsonl" in entry
    assert "What is stored:" in entry and "No text or path of a transcript" in entry
    # KYO-663: the commitments, the key, and that one key store sends the key off the machine.
    assert "a salted hash and a line count" in entry and "seal.key" in entry
    assert "the key leaves this machine, end-to-end encrypted by iCloud Keychain" in entry
    assert "What never leaves this machine:" in entry
    assert "read_transcripts: true" in entry and "read_transcripts: false" in entry


def test_the_setup_decisions_list_carries_the_question_and_the_people_key() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "docs" / "guide" / "setup-decisions.md").read_text(encoding="utf-8")
    assert "### D14. May Kyosei read this project's transcripts?" in text
    assert "### D13. People credited" in text
    assert "`read_transcripts: true`" in text and "`people:`" in text


def test_the_config_kyo_init_writes_offers_the_key(project: Path) -> None:
    """KYO-686: the fixture's `kyo init` answered, so the key is set; the commented offer
    is what `render_config` writes for a project with no answer, as one from before."""
    text = _config_path(project).read_text(encoding="utf-8")
    assert yaml.safe_load(text)[READ_TRANSCRIPTS_KEY] is False
    raw = yaml.safe_load(text)
    del raw[READ_TRANSCRIPTS_KEY]
    rendered = render_config(raw)
    assert f"# {READ_TRANSCRIPTS_KEY}: true" in rendered
    assert READ_TRANSCRIPTS_KEY not in yaml.safe_load(rendered)


def test_onboard_check_reads_the_answer_back(project: Path) -> None:
    def item() -> dict:
        ran = invoke(["--root", str(project), "onboard", "--check", "--json"])
        items = {i["label"]: i for i in ran.json["projects"][0]["items"]}
        return items[READ_TRANSCRIPTS_KEY]

    raw = yaml.safe_load(_config_path(project).read_text(encoding="utf-8"))
    del raw[READ_TRANSCRIPTS_KEY]
    _config_path(project).write_text(yaml.safe_dump(raw), encoding="utf-8")
    missing = item()
    assert missing["status"] == "missing" and "D14" in missing["fix"]
    _write_key(project, False)
    assert item()["status"] == "present" and item()["detail"].startswith("false")
    _write_key(project, True)
    assert item()["status"] == "present" and item()["detail"].startswith("true")


def test_the_transcript_module_says_who_asks() -> None:
    assert "transcripts_opted_in" in (transcript.__doc__ or "")
