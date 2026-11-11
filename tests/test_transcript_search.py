"""KYO-812: `kyo transcripts`, the search of what the person and the assistant said.

Every transcript here is written by the test, one record per line in the shapes Claude Code
writes, into a Claude Code home of the test's own. The session ids reach the project the
three ways a real one's do: ``sessions.json``, the turn index and the transcript archive.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from kyosei import cmd_sessions, cmd_turns, transcript_search
from kyosei.config import load_config
from kyosei.transcript_archive import archive_dir, archive_session

from .conftest import invoke, new_record, opt_in_transcripts

LIVE = "11111111-1111-4111-8111-111111111111"
INDEXED = "22222222-2222-4222-8222-222222222222"
ARCHIVED = "33333333-3333-4333-8333-333333333333"
STRANGER = "99999999-9999-4999-8999-999999999999"

REPLY_WORDS = "the heron stands in the shallow water"
RESULT_WORDS = "pelican output from the tool"
THINKING_WORDS = "private reasoning about cormorants"
CALL_WORDS = "grep for kingfisher"
SIDECHAIN_WORDS = "a subagent mentions the albatross"
SUBAGENT_FILE_WORDS = "the subagent file mentions a petrel"


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def person(number: int, at: str, text: str) -> dict:
    return {
        "type": "user",
        "uuid": _uuid(number),
        "timestamp": at,
        "origin": {"kind": "human"},
        "promptId": f"prompt-{number}",
        "message": {"role": "user", "content": text},
    }


def assistant(
    number: int, at: str, message_id: str, *blocks: dict, sidechain: bool = False
) -> dict:
    return {
        "type": "assistant",
        "uuid": _uuid(number),
        "timestamp": at,
        "isSidechain": sidechain,
        "message": {"id": message_id, "role": "assistant", "content": list(blocks)},
    }


def text(words: str) -> dict:
    return {"type": "text", "text": words}


def tool_result(number: int, at: str, call_id: str, words: str) -> dict:
    return {
        "type": "user",
        "uuid": _uuid(number),
        "timestamp": at,
        "toolUseResult": {"stdout": words},
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": words}],
        },
    }


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def live_rows() -> list[dict]:
    return [
        person(1, "2026-11-10T10:00:00.000Z", "Where does the Heron stand\nin the evening?"),
        assistant(
            2, "2026-11-10T10:00:01.000Z", "msg_a", {"type": "thinking", "thinking": THINKING_WORDS}
        ),
        assistant(
            3,
            "2026-11-10T10:00:02.000Z",
            "msg_a",
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": CALL_WORDS}},
        ),
        tool_result(4, "2026-11-10T10:00:03.000Z", "toolu_1", RESULT_WORDS),
        assistant(5, "2026-11-10T10:00:04.000Z", "msg_b", text(f"I looked: {REPLY_WORDS}.")),
        assistant(6, "2026-11-10T10:00:05.000Z", "msg_c", text(SIDECHAIN_WORDS), sidechain=True),
    ]


class World:
    """An opted-in project and a Claude Code home holding three of its sessions."""

    def __init__(self, project: Path, claude: Path) -> None:
        self.project = project
        self.claude = claude
        self.transcripts = claude / "projects" / "-work"

    @property
    def config(self):
        return load_config(self.project)

    @property
    def index_dir(self) -> Path:
        return transcript_search.index_dir(self.config)

    def search(self, query: str, **kwargs) -> dict:
        return transcript_search.search(self.config, self.project, "proj", query, **kwargs)

    def found(self, query: str, **kwargs) -> list[tuple[str, str]]:
        return [(r["session_id"], r["role"]) for r in self.search(query, **kwargs)["results"]]


@pytest.fixture
def world(project: Path, tmp_path: Path, monkeypatch) -> World:
    opt_in_transcripts(project)
    claude = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    made = World(project, claude)
    # One session `sessions.json` records, its transcript where Claude Code keeps it.
    live = made.transcripts / f"{LIVE}.jsonl"
    write_rows(live, live_rows())
    write_rows(
        made.transcripts / LIVE / "subagents" / "agent-a1.jsonl",
        [
            assistant(
                7, "2026-11-10T10:00:06.000Z", "msg_s", text(SUBAGENT_FILE_WORDS), sidechain=True
            )
        ],
    )
    entry = {
        "session_id": LIVE,
        "state": "running",
        "first_seen": "2026-11-10T10:00:00.000Z",
        "last_seen": "2026-11-10T10:00:00.000Z",
        "transcript_path": str(live),
    }
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(project), [entry]) is None
    # One only the turn index names.
    write_rows(
        made.transcripts / f"{INDEXED}.jsonl",
        [
            person(11, "2026-11-08T09:00:00.000Z", "a note about the wren and the robin"),
            assistant(
                12, "2026-11-08T09:00:01.000Z", "msg_d", text("The robin sings; the wren hides.")
            ),
        ],
    )
    cmd_turns.turns_path(project).write_text(
        json.dumps({"session": INDEXED, "prompt_id": "prompt-11"}) + "\n", encoding="utf-8"
    )
    # One whose transcript only the archive still holds.
    gone = made.transcripts / f"{ARCHIVED}.jsonl"
    write_rows(
        gone,
        [
            person(21, "2026-11-05T08:00:00.000Z", "tell me about the heron of last week"),
            assistant(
                22, "2026-11-05T08:00:01.000Z", "msg_e", text("Last week the heron flew north.")
            ),
        ],
    )
    archive_session(gone, archive_dir(made.config))
    gone.unlink()
    # And one no file of this project's names, which is therefore another project's.
    write_rows(
        made.transcripts / f"{STRANGER}.jsonl",
        [person(31, "2026-11-11T08:00:00.000Z", "the heron of another project")],
    )
    return made


# ------------------------------------------------------------------ c1: what is searched


def test_a_replys_words_are_found_and_a_tool_results_are_not(world: World) -> None:
    assert world.found(REPLY_WORDS) == [(LIVE, "assistant")]
    for unsearched in (
        RESULT_WORDS,
        THINKING_WORDS,
        CALL_WORDS,
        SIDECHAIN_WORDS,
        SUBAGENT_FILE_WORDS,
    ):
        assert world.found(unsearched) == [], unsearched


def test_the_fixture_holds_what_the_search_leaves_out(world: World) -> None:
    """Non-vacuous: each phrase that is not found is in a transcript the search read or
    stood beside, so it is the reader and not the fixture that leaves it out."""
    held = (world.transcripts / f"{LIVE}.jsonl").read_text(encoding="utf-8")
    for unsearched in (RESULT_WORDS, THINKING_WORDS, CALL_WORDS, SIDECHAIN_WORDS):
        assert unsearched in held
    subagent = world.transcripts / LIVE / "subagents" / "agent-a1.jsonl"
    assert SUBAGENT_FILE_WORDS in subagent.read_text(encoding="utf-8")


def test_prompts_and_replies_are_found_in_live_and_archived_sessions_alike(world: World) -> None:
    assert world.found("heron") == [
        (LIVE, "assistant"),
        (LIVE, "person"),
        (ARCHIVED, "assistant"),
        (ARCHIVED, "person"),
    ]
    assert world.found("the wren") == [(INDEXED, "assistant"), (INDEXED, "person")]


def test_a_session_no_file_of_the_project_names_is_not_searched(world: World) -> None:
    assert world.found("another project") == []
    assert STRANGER not in transcript_search.project_session_ids(world.config, world.project)


def test_the_command_prints_the_results(world: World) -> None:
    result = invoke(["--root", str(world.project), "transcripts", "search", "shallow", "water"])
    assert result.code == 0, result
    assert f"session {LIVE}" in result.out and f"message {_uuid(5)}" in result.out
    assert "in the [shallow water]." in result.out

    as_json = invoke(["--root", str(world.project), "transcripts", "search", "heron", "--json"])
    document = json.loads(as_json.out)
    assert [r["session_id"] for r in document["results"]] == [LIVE, LIVE, ARCHIVED, ARCHIVED]


# ------------------------------------------------------------------- c3: a result's shape


def test_a_result_names_its_place_and_marks_the_matched_words(world: World) -> None:
    (reply,) = world.search(REPLY_WORDS)["results"]
    assert reply == {
        "project": "proj",
        "session_id": LIVE,
        # The record of the reply's first text block, which the chat's anchors resolve, and
        # the message id its replies are keyed by.
        "message": _uuid(5),
        "message_id": "msg_b",
        "role": "assistant",
        "at": "2026-11-10T10:00:04.000Z",
        "matches": 1,
        "excerpt": [
            {"text": "I looked: ", "match": False},
            {"text": REPLY_WORDS, "match": True},
            {"text": ".", "match": False},
        ],
    }
    prompt = world.search("heron stand in")["results"][0]
    assert (prompt["role"], prompt["message"], prompt["message_id"]) == ("person", _uuid(1), None)
    # Case is ignored and the line's end matches the query's space, as in the chat's find.
    assert {"text": "Heron stand in", "match": True} in prompt["excerpt"]


def test_a_phrase_does_not_match_its_words_apart(world: World) -> None:
    # The prompt and the reply each hold "wren" and "robin", never side by side.
    assert world.found("robin") == [(INDEXED, "assistant"), (INDEXED, "person")]
    assert world.found("wren") == [(INDEXED, "assistant"), (INDEXED, "person")]
    assert world.found("wren robin") == []
    assert world.found("robin wren") == []
    # The words in the order and the company they were written in are found.
    assert world.found("wren and the robin") == [(INDEXED, "person")]
    assert world.found("robin sings") == [(INDEXED, "assistant")]


def test_no_character_is_an_operator_and_a_part_of_a_word_is_found(world: World) -> None:
    assert world.found('"heron"') == []
    assert world.found("heron OR wren") == []
    assert world.found("hero*") == []
    assert world.found("allow wat") == [(LIVE, "assistant")]
    # Shorter than the tokenizer's three characters: every row is read instead.
    assert world.found("s;") == [(INDEXED, "assistant")]


def test_results_come_newest_first_and_page(world: World) -> None:
    first = world.search("heron", limit=3)
    assert [r["at"] for r in first["results"]] == sorted(
        (r["at"] for r in first["results"]), reverse=True
    )
    assert first["more"] and first["cursor"]
    rest = world.search("heron", limit=3, cursor=first["cursor"])
    assert [(r["session_id"], r["role"]) for r in rest["results"]] == [(ARCHIVED, "person")]
    assert rest["more"] is False and rest["cursor"] is None
    every = world.search("heron")["results"]
    assert first["results"] + rest["results"] == every


def run(world: World, *args: str):
    return invoke(["--root", str(world.project), "transcripts", *args])


def test_a_cursor_the_search_did_not_return_is_refused_and_none_clears_it(world: World) -> None:
    refused = run(world, "search", "heron", "--cursor", "not-a-cursor")
    assert refused.code != 0, refused
    assert "pass the `cursor` of the previous response unchanged, or none" in refused.err
    assert run(world, "search", "heron").code == 0  # none, for the first page
    first = json.loads(run(world, "search", "heron", "--limit", "1", "--json").out)
    assert run(world, "search", "heron", "--cursor", first["cursor"]).code == 0  # unchanged


def test_a_blank_phrase_is_refused_and_giving_the_words_clears_it(world: World) -> None:
    for blank in (["search"], ["search", "  "]):
        refused = run(world, *blank)
        assert refused.code != 0, refused
        assert '`kyo transcripts search "the words"`' in refused.err
    assert run(world, "search", "the words").code == 0  # the remedy, as it is written


def test_a_page_size_out_of_range_is_refused_and_one_in_range_clears_it(world: World) -> None:
    for size in ("0", "101"):
        refused = run(world, "search", "heron", "--limit", size)
        assert refused.code != 0 and "between 1 and 100" in refused.err, refused
    assert run(world, "search", "heron", "--limit", "100").code == 0


def test_rebuild_takes_no_argument_and_the_bare_form_clears_it(world: World) -> None:
    for extra in (["heron"], ["--limit", "5"], ["--cursor", "x"]):
        refused = run(world, "rebuild", *extra)
        assert refused.code != 0, refused
        assert "run `kyo transcripts rebuild`" in refused.err
    assert not world.index_dir.exists()
    assert run(world, "rebuild").code == 0  # the remedy, as it is written


# -------------------------------------------------------------------- c2: derived, local


def test_the_index_is_local_private_and_outside_the_store(world: World, store_home: Path) -> None:
    world.search("heron")
    index = world.index_dir / transcript_search.INDEX_NAME
    assert index.is_file()
    # A sibling of the transcript archive under $KYOSEI_HOME, named as the archive is.
    assert world.index_dir == store_home / "transcript-search" / "proj"
    assert archive_dir(world.config) == store_home / "transcript-archive" / "proj"
    assert world.project not in index.parents
    assert index.stat().st_mode & 0o777 == 0o600
    assert world.index_dir.stat().st_mode & 0o777 == 0o700


def test_an_index_inside_a_git_repository_is_refused_and_moving_the_home_clears_it(
    world: World, tmp_path: Path, monkeypatch
) -> None:
    inside = tmp_path / "a-repository" / "home"
    inside.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path / "a-repository")], check=True)
    monkeypatch.setenv("KYOSEI_HOME", str(inside))
    for action in (["search", "heron"], ["rebuild"]):
        refused = run(world, *action)
        assert refused.code != 0, refused
        assert "where the text of a transcript could be committed" in refused.err
        assert "point $KYOSEI_HOME at a directory outside every git repository" in refused.err
    assert not (inside / "transcript-search").exists()  # nothing was written there

    outside = tmp_path / "outside-home"
    monkeypatch.setenv("KYOSEI_HOME", str(outside))  # the remedy, as it is written
    assert run(world, "search", "heron").code == 0
    assert (outside / "transcript-search" / "proj" / transcript_search.INDEX_NAME).is_file()


def test_deleting_the_index_and_rebuilding_gives_the_same_results(world: World) -> None:
    queries = ("heron", "the wren", "robin", REPLY_WORDS, "s;", RESULT_WORDS)
    before = {query: world.search(query)["results"] for query in queries}
    assert before["heron"]
    shutil.rmtree(world.index_dir)

    result = invoke(["--root", str(world.project), "transcripts", "rebuild", "--json"])
    assert result.code == 0, result
    assert json.loads(result.out)["index"]["read"] == 3
    assert (world.index_dir / transcript_search.INDEX_NAME).is_file()
    assert {query: world.search(query)["results"] for query in queries} == before


def test_a_search_reads_again_only_the_sessions_that_changed(world: World, monkeypatch) -> None:
    read: list[str] = []
    real = transcript_search._rows_of

    def counted(session_id: str, path: Path):
        read.append(session_id)
        return real(session_id, path)

    monkeypatch.setattr(transcript_search, "_rows_of", counted)
    assert world.search("heron")["index"]["read"] == 3
    assert sorted(read) == sorted([LIVE, INDEXED, ARCHIVED])

    read.clear()
    assert world.search("heron")["index"]["read"] == 0
    assert read == []

    live = world.transcripts / f"{LIVE}.jsonl"
    with live.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(person(8, "2026-11-10T11:00:00.000Z", "and the egret?")) + "\n")
    assert world.found("egret") == [(LIVE, "person")]
    assert read == [LIVE]


def test_a_session_whose_transcript_is_gone_everywhere_leaves_the_index(world: World) -> None:
    assert world.found("wren") != []
    (world.transcripts / f"{INDEXED}.jsonl").unlink()
    found = world.search("wren")
    assert found["results"] == [] and found["index"]["removed"] == 1


def test_an_index_of_another_layout_or_a_damaged_one_is_written_again(world: World) -> None:
    expected = world.found("heron")
    index = world.index_dir / transcript_search.INDEX_NAME
    index.write_bytes(b"this is not a database, and it is long enough to be read as one" * 40)
    assert world.found("heron") == expected

    conn = sqlite3.connect(index)
    conn.execute("PRAGMA user_version = 999")
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()
    assert world.found("heron") == expected


def test_without_the_trigram_tokenizer_the_answers_are_the_same(world: World, monkeypatch) -> None:
    queries = ("heron", "the wren", "allow wat", "s;", "robin wren")
    with_fts = {query: world.found(query) for query in queries}
    shutil.rmtree(world.index_dir)
    monkeypatch.setattr(
        transcript_search,
        "_FTS_TABLE",
        "CREATE VIRTUAL TABLE messages USING fts5(text, tokenize='no-such-tokenizer')",
    )
    assert {query: world.found(query) for query in queries} == with_fts
    assert with_fts["heron"]


# ---------------------------------------------------------- the text stays out of the store


def test_the_export_and_every_file_of_the_project_hold_none_of_a_replys_text(
    world: World, kyo
) -> None:
    new_record(kyo, title="a record written beside the index")
    world.search("heron")
    assert invoke(["--root", str(world.project), "export", "--refresh"]).code == 0
    needle = REPLY_WORDS.encode("utf-8")
    # Every file under the project, tracked or ignored, the database and the export included.
    files = [path for path in world.project.rglob("*") if path.is_file()]
    names = {str(path.relative_to(world.project)) for path in files}
    assert "kyosei/kyosei.db" in names
    assert any(name.startswith("kyosei/export/") and name.endswith(".json") for name in names)
    for path in files:
        assert needle not in path.read_bytes(), path
    # Non-vacuous: the index does hold it, so the walk above would have found a copy.
    held = b"".join(path.read_bytes() for path in world.index_dir.iterdir())
    assert needle in held


# ------------------------------------------------------------------------ c5: the opt-in


def test_a_project_not_opted_in_is_refused_and_the_remedy_clears_it(
    project: Path, tmp_path: Path, monkeypatch
) -> None:
    claude = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    live = claude / "projects" / "-work" / f"{LIVE}.jsonl"
    write_rows(live, live_rows())
    entry = {"session_id": LIVE, "state": "running", "transcript_path": str(live)}
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(project), [entry]) is None

    for action in (["search", "heron"], ["rebuild"]):
        refused = invoke(["--root", str(project), "transcripts", *action])
        assert refused.code != 0, action
        assert "has not opted in to reading transcripts" in refused.err
        assert "set `read_transcripts: true` in kyosei/config.yml" in refused.err
    index = transcript_search.index_dir(load_config(project))
    assert not index.exists()  # nothing was read or written

    opt_in_transcripts(project)  # the remedy, as it is written
    cleared = invoke(["--root", str(project), "transcripts", "search", "heron", "--json"])
    assert cleared.code == 0, cleared
    assert [r["role"] for r in json.loads(cleared.out)["results"]] == ["assistant", "person"]
