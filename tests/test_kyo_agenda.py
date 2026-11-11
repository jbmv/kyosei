"""KYO-770: a session's task list, kept by `kyo serve` and written by the person and the
orchestrator.

The routes go over a real socket; the verb, the hooks and the brief run in-process. Each
refusal that names a remedy is followed in the same test by that remedy.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import cmd_agenda, config, device_tokens

from .conftest import git_init, new_record, verify_criterion
from .test_kyo137_docs_obligations import (
    _commit_anything,
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
)
from .test_kyo329_accept import _docs_verdict, _met
from .test_kyo_serve import body_json
from .test_kyo_serve_send import issue
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
    stream,
)

AGENDA = f"{SESSIONS}/{OLD}/agenda"
SESSION = "55555555-5555-4555-8555-555555555555"
EARLIER = "66666666-6666-4666-8666-666666666666"


@pytest.fixture
def token(store_home: Path) -> str:
    return issue("ipad", device_tokens.PERMISSION_SEND)


@pytest.fixture
def in_session(monkeypatch) -> str:
    monkeypatch.setenv(cmd_agenda.SESSION_ENV, SESSION)
    return SESSION


def request(
    bed: Bed, method: str, document: dict | None, token: str | None, path: str = AGENDA, **headers
) -> tuple[int, dict, dict]:
    sent = {key.replace("_", "-"): value for key, value in headers.items()}
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    body = None if document is None else json.dumps(document).encode("utf-8")
    status, answered, raw = bed.client.request(path, method=method, headers=sent, body=body)
    return status, answered, body_json(raw)


def orchestrator(kyo, *args: str, session: str = OLD):
    result = kyo("agenda", *args, "--session", session)
    assert result.code == 0, result
    return result


def items_of(kyo, session: str = SESSION) -> list[dict]:
    return kyo("agenda", "--session", session, "--json").json["items"]


def agenda_file(store_home: Path, session: str = OLD) -> Path:
    return store_home / config.COMPOSER_DIRNAME / cmd_agenda.AGENDAS / f"{session}.json"


# ------------------------------------------------------------------ c1: the routes


def test_a_session_with_no_list_reads_as_an_empty_one_and_creates_nothing(
    bed: Bed, store_home: Path, token: str
) -> None:
    status, headers, read = request(bed, "GET", None, token)
    assert status == 200 and "ETag" not in headers
    assert read["items"] == [] and read["etag"] is None
    assert not (store_home / config.COMPOSER_DIRNAME).exists()


def test_a_list_is_created_read_and_replaced_with_each_item_s_facts(
    bed: Bed, store_home: Path, token: str
) -> None:
    status, headers, made = request(
        bed, "PUT", {"items": [{"text": "push before close"}]}, token, If_None_Match="*"
    )
    assert status == 201 and headers["ETag"] == made["etag"]
    (item,) = made["items"]
    assert item["text"] == "push before close" and item["author"] == "person"
    assert item["checked"] is False
    assert item["added_at"].endswith("Z") and item["changed_at"] == item["added_at"]

    status, headers, read = request(bed, "GET", None, token)
    assert status == 200 and headers["ETag"] == made["etag"] and read["items"] == made["items"]

    checked = {"items": [{"id": item["id"], "text": "push before close", "checked": True}]}
    status, _, saved = request(bed, "PUT", checked, token, If_Match=made["etag"])
    assert status == 200 and saved["etag"] != made["etag"]
    assert saved["items"][0]["checked"] is True
    assert saved["items"][0]["added_at"] == item["added_at"]
    assert saved["items"][0]["changed_at"] >= item["changed_at"]

    path = agenda_file(store_home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_list_needs_a_device_token_with_the_send_permission(bed: Bed, store_home) -> None:
    assert request(bed, "GET", None, None)[0] == 401
    reader = issue("reader", device_tokens.PERMISSION_NOTES)
    status, _, refused = request(bed, "GET", None, reader)
    assert status == 403 and "send" in refused["error"]
    assert request(bed, "PUT", {"items": []}, reader, If_None_Match="*")[0] == 403
    sender = issue("ipad", device_tokens.PERMISSION_SEND)  # the remedy: a token that may
    assert request(bed, "GET", None, sender)[0] == 200


def test_a_stale_writer_is_refused_and_reading_again_clears_it(bed: Bed, kyo, token: str) -> None:
    etag = request(bed, "PUT", {"items": [{"text": "mine"}]}, token, If_None_Match="*")[2]["etag"]
    orchestrator(kyo, "add", "--text", "the next wave")  # saved since, by the other side

    stale = {"items": [{"id": "1", "text": "mine, edited"}]}
    status, _, refused = request(bed, "PUT", stale, token, If_Match=etag)
    # Stale, and not "forbidden": the copy this device holds lacks the orchestrator's item,
    # so judging the items first would call an honest edit a removal.
    assert status == 412 and "GET it again" in refused["error"]

    read = request(bed, "GET", None, token)[2]
    assert [item["text"] for item in read["items"]] == ["mine", "the next wave"]
    merged = {"items": [{"id": "1", "text": "mine, edited"}, read["items"][1]]}
    status, _, saved = request(bed, "PUT", merged, token, If_Match=read["etag"])
    assert status == 200 and saved["items"][0]["text"] == "mine, edited"


def test_a_write_stating_no_precondition_is_refused_and_stating_one_clears_it(
    bed: Bed, token: str
) -> None:
    status, _, refused = request(bed, "PUT", {"items": []}, token)
    assert status == 428 and "If-None-Match: *" in refused["error"]
    assert request(bed, "PUT", {"items": []}, token, If_None_Match="*")[0] == 201


def test_a_body_that_is_not_a_list_of_items_is_refused(bed: Bed, token: str) -> None:
    for body in ({"boxes": []}, {"items": [{"text": "  "}]}, {"items": [{"text": "a", "id": "9"}]}):
        status, _, refused = request(bed, "PUT", body, token, If_None_Match="*")
        assert status == 400 and refused["reason"] == "bad-request", (body, refused)
    assert request(bed, "PUT", {"items": [{"text": "a"}]}, token, If_None_Match="*")[0] == 201


def test_a_change_by_either_side_is_announced_on_the_session_s_stream(
    bed: Bed, kyo, stream, token: str
) -> None:
    opened = stream()
    assert opened.event("ready")["agenda"] == {"etag": None}

    made = request(bed, "PUT", {"items": [{"text": "mine"}]}, token, If_None_Match="*")[2]
    event = opened.event("agenda")
    assert event == {"session_id": OLD, "etag": made["etag"]}  # the etag, and no item

    orchestrator(kyo, "add", "--text", "the next wave")
    after = opened.event("agenda")["etag"]
    assert after not in (None, made["etag"])
    assert after == request(bed, "GET", None, token)[2]["etag"]
    assert stream().event("ready")["agenda"] == {"etag": after}


# -------------------------------------------------------- c2: what a device may change


def _both_sides(bed: Bed, kyo, token: str) -> tuple[dict, dict, str]:
    """A list holding one item of the person's and one of the orchestrator's."""
    request(bed, "PUT", {"items": [{"text": "mine"}]}, token, If_None_Match="*")
    orchestrator(kyo, "add", "--text", "the next wave")
    read = request(bed, "GET", None, token)[2]
    mine, theirs = read["items"]
    assert (mine["author"], theirs["author"]) == ("person", "orchestrator")
    return mine, theirs, read["etag"]


def test_a_device_adds_edits_checks_and_removes_the_person_s_items(
    bed: Bed, kyo, token: str
) -> None:
    mine, theirs, etag = _both_sides(bed, kyo, token)

    added = {"items": [mine, theirs, {"text": "a second of mine", "author": "orchestrator"}]}
    status, _, saved = request(bed, "PUT", added, token, If_Match=etag)
    assert status == 200
    second = saved["items"][2]
    assert second["author"] == "person", "a device cannot state an item's author"

    edited = {"items": [{**mine, "text": "mine, edited", "checked": True}, theirs, second]}
    status, _, saved = request(bed, "PUT", edited, token, If_Match=saved["etag"])
    assert status == 200
    assert (saved["items"][0]["text"], saved["items"][0]["checked"]) == ("mine, edited", True)

    removed = {"items": [theirs, second]}
    status, _, saved = request(bed, "PUT", removed, token, If_Match=saved["etag"])
    assert status == 200 and [item["id"] for item in saved["items"]] == [theirs["id"], second["id"]]


@pytest.mark.parametrize("attempt", ["edit", "check", "remove"])
def test_the_same_request_against_the_orchestrator_s_item_is_refused(
    bed: Bed, kyo, token: str, attempt: str
) -> None:
    mine, theirs, etag = _both_sides(bed, kyo, token)
    sent = {
        "edit": [mine, {**theirs, "text": "reworded by a device"}],
        "check": [mine, {**theirs, "checked": True}],
        "remove": [mine],
    }[attempt]

    status, _, refused = request(bed, "PUT", {"items": sent}, token, If_Match=etag)

    assert status == 403 and refused["reason"] == "orchestrator-item"
    assert f"item {theirs['id']} is the orchestrator's" in refused["error"]
    read = request(bed, "GET", None, token)[2]
    assert read["etag"] == etag and read["items"] == [mine, theirs], "a refusal writes nothing"
    # The remedy the refusal names: the list as it was read, with that item unchanged.
    kept = {"items": [{**mine, "checked": True}, theirs]}
    assert request(bed, "PUT", kept, token, If_Match=etag)[0] == 200


def test_the_orchestrator_checks_and_edits_the_person_s_item(bed: Bed, kyo, token: str) -> None:
    mine, _, _ = _both_sides(bed, kyo, token)
    orchestrator(kyo, "edit", mine["id"], "--text", "mine, reworded")
    orchestrator(kyo, "check", mine["id"])
    item = request(bed, "GET", None, token)[2]["items"][0]
    assert (item["text"], item["checked"], item["author"]) == ("mine, reworded", True, "person")


# ------------------------------------------------------------------- c3: the verb


def test_the_verb_lists_adds_edits_checks_unchecks_removes_and_reorders(
    kyo, in_session: str
) -> None:
    empty = kyo("agenda")
    assert empty.code == 0 and "the list is empty" in empty.out

    assert kyo("agenda", "add", "--text", "dispatch wave 3").code == 0
    assert kyo("agenda", "add", "--text", "write the closing note").code == 0
    assert kyo("agenda", "add", "--text", "merge wave 2", "--to", "1").code == 0
    assert [(i["id"], i["text"]) for i in items_of(kyo)] == [
        ("3", "merge wave 2"),
        ("1", "dispatch wave 3"),
        ("2", "write the closing note"),
    ]
    assert all(item["author"] == "orchestrator" for item in items_of(kyo))

    assert kyo("agenda", "edit", "1", "--text", "dispatch wave 3 and 4").code == 0
    assert kyo("agenda", "check", "3", "1").code == 0
    assert kyo("agenda", "uncheck", "1").code == 0
    assert kyo("agenda", "move", "2", "--to", "1").code == 0
    listed = items_of(kyo)
    assert [(i["id"], i["checked"]) for i in listed] == [("2", False), ("3", True), ("1", False)]
    assert listed[2]["text"] == "dispatch wave 3 and 4"

    assert kyo("agenda", "remove", "3").code == 0
    assert [item["id"] for item in items_of(kyo)] == ["2", "1"]
    text = kyo("agenda").out
    assert f"session {SESSION}: 2 item(s), 2 unchecked" in text
    assert "[ ]   2  orchestrator  write the closing note" in text


def test_outside_a_session_the_verb_is_refused_and_naming_one_clears_it(kyo) -> None:
    refused = kyo("agenda", "add", "--text", "x")
    assert refused.code == 64 and "pass --session <id>" in refused.err
    assert kyo("agenda", "add", "--text", "x", "--session", SESSION).code == 0


def test_an_id_that_cannot_name_a_file_is_refused(kyo) -> None:
    refused = kyo("agenda", "--session", "../elsewhere")
    assert refused.code == 64 and "cannot name a session's list" in refused.err
    assert kyo("agenda", "--session", SESSION).code == 0


def test_an_unknown_item_is_refused_and_the_listing_it_names_gives_the_id(
    kyo, in_session: str
) -> None:
    kyo("agenda", "add", "--text", "one")
    refused = kyo("agenda", "check", "7")
    assert refused.code == 64 and f"`kyo agenda --session {SESSION}`" in refused.err
    listed = kyo("agenda", "--session", SESSION, "--json").json["items"]  # the remedy
    assert kyo("agenda", "check", listed[0]["id"]).code == 0


def test_each_action_missing_what_it_needs_is_refused_and_given_it_runs(
    kyo, in_session: str
) -> None:
    refused = kyo("agenda", "add")
    assert refused.code == 64 and "pass --text" in refused.err
    assert kyo("agenda", "add", "--text", "one").code == 0
    piped = kyo("agenda", "add", "--text-file", "-", stdin="words with `backticks`\nand a $sign\n")
    assert piped.code == 0 and items_of(kyo)[-1]["text"] == "words with `backticks` and a $sign"
    assert kyo("agenda", "remove", "2").code == 0
    refused = kyo("agenda", "add", "--text", "x", "--text-file", "-", stdin="y")
    assert refused.code == 64 and "--text or --text-file" in refused.err
    # What a shell leaves of "dead because `kyo rebuild` already handles it".
    eaten = kyo("agenda", "add", "--text", "dead because  already handles it")
    assert eaten.code == 64 and "--text-file" in eaten.err and len(items_of(kyo)) == 1
    lines = kyo("agenda", "add", "--text", "two\nlines")
    assert lines.code == 64 and "single line" in lines.err

    refused = kyo("agenda", "add", "1", "--text", "two")
    assert refused.code == 64 and "takes no item id" in refused.err

    refused = kyo("agenda", "check")
    assert refused.code == 64 and "`kyo agenda` lists them" in refused.err
    assert kyo("agenda", "check", "1").code == 0

    refused = kyo("agenda", "move", "1")
    assert refused.code == 64 and "pass --to POSITION" in refused.err
    refused = kyo("agenda", "move", "1", "--to", "0")
    assert refused.code == 64 and "`--to 1` is the top" in refused.err
    assert kyo("agenda", "move", "1", "--to", "1").code == 0

    refused = kyo("agenda", "edit", "1", "--text", "x" * (cmd_agenda.MAX_TEXT + 1))
    assert refused.code == 64 and "shorten it" in refused.err
    assert kyo("agenda", "edit", "1", "--text", "short").code == 0

    refused = kyo("agenda", "--text", "stray")
    assert refused.code == 64 and "name an action first" in refused.err


def test_a_full_list_refuses_another_item_and_removing_one_clears_it(
    kyo, in_session: str, monkeypatch
) -> None:
    monkeypatch.setattr(cmd_agenda, "MAX_ITEMS", 2)
    kyo("agenda", "add", "--text", "one")
    kyo("agenda", "add", "--text", "two")
    refused = kyo("agenda", "add", "--text", "three")
    assert refused.code == 64 and "`kyo agenda remove <id>`" in refused.err
    assert kyo("agenda", "remove", "1").code == 0
    assert kyo("agenda", "add", "--text", "three").code == 0


def test_a_file_that_is_not_a_list_is_refused_and_removing_it_clears_it(
    kyo, store_home: Path, in_session: str
) -> None:
    kyo("agenda", "add", "--text", "one")
    path = agenda_file(store_home, SESSION)
    for unusable in ("not json", '{"items": 3}'):
        path.write_text(unusable, encoding="utf-8")
        refused = kyo("agenda")
        assert refused.code == 1 and "remove the file" in refused.err, refused
        assert str(path) in refused.err
        path.unlink()
        assert kyo("agenda").code == 0
        assert kyo("agenda", "add", "--text", "again").code == 0


def test_two_processes_adding_at_once_lose_nothing(store_home: Path, tmp_path: Path) -> None:
    script = (
        "import sys; from kyosei import cmd_agenda\n"
        "env = {'KYOSEI_HOME': sys.argv[1]}\n"
        "for n in range(15):\n"
        "    cmd_agenda.write(env, 's1', lambda d, at: bool(d['items'].append("
        "cmd_agenda.new_item(d, f'{sys.argv[2]}-{n}', 'orchestrator', at)) or True))\n"
    )
    writers = [
        subprocess.Popen([sys.executable, "-c", script, str(store_home), name])
        for name in ("a", "b", "c")
    ]
    assert [writer.wait(timeout=60) for writer in writers] == [0, 0, 0]
    items = cmd_agenda.read({"KYOSEI_HOME": str(store_home)}, "s1")["items"]
    assert len(items) == 45 and len({item["id"] for item in items}) == 45


# ----------------------------------------- c4: dispatch, accept and close-wave's items


def test_dispatch_adds_an_item_that_accept_checks_and_the_wave_s_close_follows(
    kyo, git_project: Path, in_session: str
) -> None:
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    kyo("agenda", "add", "--text", "the plan for this session")

    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1")],
        docs_verdicts=[_docs_verdict()],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported
    made = items_of(kyo)[-1]
    assert made["text"].startswith(f"Accept {dispatch_id}: ") and made["checked"] is False
    assert made["author"] == "orchestrator" and made["for"] == f"dispatch:{dispatch_id}"

    assert kyo("accept", dispatch_id).code == 0
    listed = {item["for"]: item for item in items_of(kyo) if item["for"]}
    assert listed[f"dispatch:{dispatch_id}"]["checked"] is True
    wave = listed[cmd_agenda.FOR_WAVE]
    assert wave["checked"] is False and record in wave["text"] and "kyo close-wave" in wave["text"]

    assert kyo("accept", dispatch_id).code == 0  # a second acceptance adds no second item
    assert [item["for"] for item in items_of(kyo)].count(cmd_agenda.FOR_WAVE) == 1


def test_closing_a_dispatch_checks_its_item(kyo, git_project: Path, in_session: str) -> None:
    record = new_record(kyo, criteria=["it works"])
    kyo("agenda", "add", "--text", "the plan")
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    assert items_of(kyo)[-1]["checked"] is False

    closed = kyo("dispatch", "close", dispatch_id, "--withdrawn", "--why", "never handed out")
    assert closed.code == 0, closed
    assert items_of(kyo)[-1]["for"] == f"dispatch:{dispatch_id}"
    assert items_of(kyo)[-1]["checked"] is True


def test_close_wave_checks_the_wave_s_item(
    kyo, project: Path, store_home: Path, in_session: str
) -> None:
    config_file = project / "kyosei" / "config.yml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8") + 'close_wave:\n  suite:\n    - "true"\n',
        encoding="utf-8",
    )
    git_init(project)
    record = new_record(kyo, criteria=["it works"])
    verify_criterion(kyo, f"{record}.c1", method="machine-checked")
    kyo("agenda", "add", "--text", "the plan")
    environ = {"KYOSEI_HOME": str(store_home), cmd_agenda.SESSION_ENV: SESSION}
    cmd_agenda.note_wave_open(environ, [record])  # what `kyo accept` leaves
    assert items_of(kyo)[-1]["for"] == cmd_agenda.FOR_WAVE
    _commit_anything(project, "store: record filed and verified")
    assert kyo("close-wave", "--suite-passed", "true").code == 0

    dry = kyo("close-wave", record, "--dry-run")
    assert dry.code == 0 and items_of(kyo)[-1]["checked"] is False, "a dry run checks nothing"
    closed = kyo("close-wave", record)
    assert closed.code == 0, closed
    assert items_of(kyo)[-1]["checked"] is True


def test_with_no_list_or_outside_a_session_dispatch_changes_nothing_and_does_not_fail(
    kyo, git_project: Path, store_home: Path, monkeypatch
) -> None:
    record = new_record(kyo, criteria=["it works"])
    other = new_record(kyo, criteria=["it works"])

    # Outside a session, with another session's list on the machine.
    kyo("agenda", "add", "--text", "someone else's plan", "--session", EARLIER)
    before = agenda_file(store_home, EARLIER).read_bytes()
    assert kyo("dispatch", record, "--no-create-worktree").code == 0
    assert agenda_file(store_home, EARLIER).read_bytes() == before

    # In a session that has no list: none is made.
    monkeypatch.setenv(cmd_agenda.SESSION_ENV, SESSION)
    assert kyo("dispatch", other, "--no-create-worktree", "--force").code == 0
    assert not agenda_file(store_home, SESSION).exists()
    assert agenda_file(store_home, EARLIER).read_bytes() == before


def test_a_list_that_cannot_be_written_does_not_fail_the_dispatch(
    kyo, git_project: Path, store_home: Path, in_session: str
) -> None:
    record = new_record(kyo, criteria=["it works"])
    kyo("agenda", "add", "--text", "the plan")
    agenda_file(store_home, SESSION).write_text("not json", encoding="utf-8")
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--json")
    assert dispatched.code == 0 and dispatched.json["dispatch_id"]


# ------------------------------------------------------------ c5: the prompt hook


def _prompt_hook(kyo, session: str = SESSION):
    return kyo("check-build", "--hook", stdin=json.dumps({"session_id": session}))


def test_the_prompt_hook_prints_the_unchecked_items_with_who_wrote_each(
    bed: Bed, kyo, token: str
) -> None:
    request(bed, "PUT", {"items": [{"text": "push before close"}]}, token, If_None_Match="*")
    orchestrator(kyo, "add", "--text", "dispatch wave 3")
    orchestrator(kyo, "add", "--text", "already done")
    orchestrator(kyo, "check", "3")

    hook = _prompt_hook(kyo, OLD)

    assert hook.code == 0 and hook.err == ""
    assert hook.out.splitlines() == [
        "session list, unchecked (`kyo agenda`):",
        "  1 person: push before close",
        "  2 orchestrator: dispatch wave 3",
    ]


def test_the_prompt_hook_prints_nothing_when_nothing_is_unchecked(
    kyo, store_home: Path, in_session: str
) -> None:
    assert _prompt_hook(kyo).out == ""  # no list
    kyo("agenda", "add", "--text", "one")
    kyo("agenda", "check", "1")
    assert _prompt_hook(kyo).out == ""  # a list with every item checked
    kyo("agenda", "uncheck", "1")
    assert "1 orchestrator: one" in _prompt_hook(kyo).out
    agenda_file(store_home, SESSION).write_text("not json", encoding="utf-8")
    quiet = _prompt_hook(kyo)
    assert quiet.code == 0 and quiet.out == "", "a list that cannot be read is not a prompt's news"


# -------------------------------------------------------------- c6: the stop hook


def _stop_hook(kyo, session: str = SESSION, event: str = "Stop"):
    payload = {"session_id": session, "hook_event_name": event, "cwd": "/x"}
    result = kyo("sessions", "--hook", stdin=json.dumps(payload))
    assert result.code == 0, result
    return result


def test_the_stop_hook_announces_the_person_s_unchecked_items_and_never_blocks(
    bed: Bed, kyo, token: str
) -> None:
    sent = {"items": [{"text": "push before close"}, {"text": "email the notes"}]}
    request(bed, "PUT", sent, token, If_None_Match="*")
    orchestrator(kyo, "add", "--text", "the orchestrator's own, which is not announced")

    stop = _stop_hook(kyo, OLD)

    # One JSON object and nothing else on stdout, exit 0: Claude Code shows `systemMessage`
    # to the person and stops. No `decision`, no `additionalContext`, nothing on stderr.
    assert stop.err == ""
    announced = json.loads(stop.out)
    assert set(announced) == {"systemMessage"}
    assert announced["systemMessage"] == (
        "Unchecked on the session list, yours (2): push before close; email the notes"
    )

    orchestrator(kyo, "check", "1", "2")
    assert _stop_hook(kyo, OLD).out == ""
    assert _stop_hook(kyo, OLD, event="SessionStart").out == ""


def test_the_stop_hook_says_when_dispatches_run_and_the_list_holds_nothing_of_the_orchestrator_s(
    kyo, git_project: Path, in_session: str
) -> None:
    record = new_record(kyo, criteria=["it works"])
    assert _stop_hook(kyo).out == "", "nothing in flight, nothing to say"

    assert kyo("dispatch", record, "--no-create-worktree").code == 0
    said = json.loads(_stop_hook(kyo).out)["systemMessage"]
    assert said.startswith("1 dispatch(es) are running or unaccepted")
    assert 'kyo agenda add --text "..."' in said
    assert _stop_hook(kyo).out == "", "said once for this count (KYO-775)"

    assert kyo("agenda", "add", "--text", "watch the dispatch").code == 0  # what it names
    assert _stop_hook(kyo).out == ""


def test_the_in_flight_sentence_is_said_once_per_count_and_the_person_s_items_every_stop(
    kyo, git_project: Path, in_session: str, store_home: Path
) -> None:
    first = new_record(kyo, criteria=["it works"])
    second = new_record(kyo, criteria=["it works too"])
    sentence = "dispatch(es) are running or unaccepted"
    said_file = store_home / "composer" / "agenda-said" / f"{SESSION}.json"
    assert not said_file.exists()

    assert kyo("dispatch", first, "--no-create-worktree").code == 0
    assert sentence in json.loads(_stop_hook(kyo).out)["systemMessage"]  # no list file yet
    assert _stop_hook(kyo).out == ""
    assert _stop_hook(kyo).out == ""

    assert kyo("dispatch", second, "--no-create-worktree", "--force").code == 0
    assert json.loads(_stop_hook(kyo).out)["systemMessage"].startswith("2 dispatch(es)")
    assert _stop_hook(kyo).out == ""

    # The person's items keep their announcement at every stop, beside the limited sentence.
    listed = agenda_file(store_home, SESSION)
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text(
        json.dumps({"items": [{"id": "1", "text": "push", "author": "person",
                               "checked": False, "added_at": "2026-01-01T00:00:00Z",
                               "changed_at": "2026-01-01T00:00:00Z"}]}),
        encoding="utf-8",
    )
    for _ in range(2):
        assert json.loads(_stop_hook(kyo).out)["systemMessage"] == (
            "Unchecked on the session list, yours (1): push"
        )
    assert json.loads(said_file.read_text())["in_flight"] == 2


def test_a_falling_then_rising_count_says_the_sentence_again(
    kyo, git_project: Path, in_session: str, store_home: Path
) -> None:
    record = new_record(kyo, criteria=["it works"])
    assert kyo("dispatch", record, "--no-create-worktree").code == 0
    assert "dispatch(es)" in json.loads(_stop_hook(kyo).out)["systemMessage"]
    said_file = store_home / "composer" / "agenda-said" / f"{SESSION}.json"
    said_file.write_text('{"in_flight": 3}\n', encoding="utf-8")  # three were said before
    assert _stop_hook(kyo).out == "", "one in flight is fewer than said, so nothing"
    assert json.loads(said_file.read_text())["in_flight"] == 1, "the fall is remembered"
    said_file.write_text('{"in_flight": 0}\n', encoding="utf-8")
    assert "1 dispatch(es)" in json.loads(_stop_hook(kyo).out)["systemMessage"]


def test_a_reported_dispatch_counts_until_it_is_accepted(
    kyo, git_project: Path, in_session: str
) -> None:
    opened = kyo(
        "brief", "--compact", "--session-start", stdin=json.dumps({"session_id": SESSION})
    )
    assert opened.code == 0, opened
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1")],
        docs_verdicts=[_docs_verdict()],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported
    db = git_project / "kyosei" / "kyosei.db"
    assert cmd_agenda.dispatches_in_flight(db, SESSION) == 1
    assert cmd_agenda.dispatches_in_flight(db, EARLIER) == 0, "another session's is the brief's"
    assert kyo("accept", dispatch_id).code == 0
    assert cmd_agenda.dispatches_in_flight(db, SESSION) == 0


# ------------------------------------------------------- c7: the next session's brief


def _open_session(kyo, session: str):
    result = kyo("brief", "--compact", "--session-start", stdin=json.dumps({"session_id": session}))
    assert result.code == 0, result
    return result


def test_a_new_session_s_brief_prints_what_was_left_and_carries_the_person_s_once(
    bed: Bed, kyo, token: str
) -> None:
    _open_session(kyo, OLD)
    request(bed, "PUT", {"items": [{"text": "push before close"}]}, token, If_None_Match="*")
    orchestrator(kyo, "add", "--text", "dispatch wave 3")
    orchestrator(kyo, "add", "--text", "done already")
    orchestrator(kyo, "check", "3")

    brief = _open_session(kyo, SESSION)

    assert brief.out.rstrip().splitlines()[-4:] == [
        f"the previous session ({OLD[:8]}) left 2 unchecked on its list:",
        "  person: push before close (carried into this session's list)",
        "  orchestrator: dispatch wave 3",
        "  `kyo agenda` prints this session's list",
    ]
    carried = items_of(kyo)
    assert [(item["text"], item["author"], item["checked"]) for item in carried] == [
        ("push before close", "person", False)
    ]

    again = _open_session(kyo, SESSION)  # a resume, or a second brief: said once
    assert "left 2 unchecked" not in again.out
    third = _open_session(kyo, EARLIER)  # and the session after carries from the new list
    assert f"the previous session ({SESSION[:8]}) left 1 unchecked" in third.out
    assert len(items_of(kyo)) == 1 and len(items_of(kyo, EARLIER)) == 1


def test_a_brief_with_nothing_left_says_nothing_and_makes_no_list(
    kyo, project: Path, store_home: Path
) -> None:
    _open_session(kyo, EARLIER)
    kyo("agenda", "add", "--text", "done", "--session", EARLIER)
    kyo("agenda", "check", "1", "--session", EARLIER)
    brief = _open_session(kyo, SESSION)
    assert "the previous session" not in brief.out
    assert not agenda_file(store_home, SESSION).exists()
