"""KYO-786: a decided credit's alternatives may name who proposed each, and a record may
be linked to the decision it carries out, so that `kyo dossier` prints both.

The decisions here are filed with ``--source-other``, so no transcript is read; the words
are invented.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kyosei import cmd_dossier
from kyosei.contracts import LINK_TYPES

from .conftest import TEST_PERSON, declare_person, new_record

WORDS = "keep the cache, and warm it at start"
ALTERNATIVES = (
    "recommended: keep the cache\n"
    "outcome: accepted\n"
    "alternatives:\n"
    "  - {text: keep the cache, chosen: true, proposed_by: person}\n"
    "  - {text: drop the cache, proposed_by: orchestrator}\n"
    "  - {text: shard the cache, proposed_by: worker}\n"
    "  - {text: leave it alone}\n"
)


def _file(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _decide(kyo, project: Path, tmp_path: Path, decision_file: str = ALTERNATIVES):
    declare_person(project)
    return kyo(
        "new", "decision", "--title", "Decision: the cache stays",
        "--quote-file", _file(tmp_path, "q.txt", WORDS),
        "--decision-file", _file(tmp_path, "d.yml", decision_file),
        "--person", TEST_PERSON, "--source-other", "a call", "--opened-late", "--json",
    )  # fmt: skip


def _decision(kyo, project: Path, tmp_path: Path, decision_file: str = ALTERNATIVES) -> str:
    filed = _decide(kyo, project, tmp_path, decision_file)
    assert filed.code == 0, filed
    return filed.json["record_id"]


def _next(page: str) -> str:
    return page.split("## What happened next")[1].split("\n## ")[0]


# ------------------------------------------------------------------ KYO-786.c1


def test_an_alternative_stores_its_proposer_and_only_when_the_file_names_one(
    kyo, project: Path, tmp_path: Path
) -> None:
    decision = _decision(kyo, project, tmp_path)
    credit = kyo("show", decision, "--json").json["credits"][0]
    assert credit["alternatives"] == [
        # `person` is stored as the key of the person the credit names.
        {"text": "keep the cache", "chosen": True, "proposed_by": TEST_PERSON},
        {"text": "drop the cache", "chosen": False, "proposed_by": "orchestrator"},
        {"text": "shard the cache", "chosen": False, "proposed_by": "worker"},
        # No proposer named: none is stored, and a reader credits the agent.
        {"text": "leave it alone", "chosen": False},
    ]


def test_the_dossier_names_the_proposer_of_each_option(kyo, project: Path, tmp_path: Path) -> None:
    page = kyo("dossier", _decision(kyo, project, tmp_path)).out
    assert (
        f"- keep the cache (chosen), proposed by {TEST_PERSON}\n"
        "- drop the cache, proposed by the agent\n"
        "- shard the cache, proposed by an agent the work was handed to\n"
        "- leave it alone, proposed by the agent\n"
    ) in page
    # An option credited by default is said to be: the page states the rule it applied.
    assert cmd_dossier.PROPOSER_DEFAULTED in page
    assert "does not say who first proposed" not in page


def test_a_page_whose_options_all_name_a_proposer_states_no_default(
    kyo, project: Path, tmp_path: Path
) -> None:
    named = ALTERNATIVES.replace("  - {text: leave it alone}\n", "")
    page = kyo("dossier", _decision(kyo, project, tmp_path, named)).out
    assert "- drop the cache, proposed by the agent\n" in page
    assert cmd_dossier.PROPOSER_DEFAULTED not in page


def test_a_person_is_named_by_key_and_an_undeclared_proposer_is_refused(
    kyo, project: Path, tmp_path: Path
) -> None:
    declare_person(project, "second")
    by_key = ALTERNATIVES.replace("proposed_by: worker", "proposed_by: second")
    decision = _decision(kyo, project, tmp_path, by_key)
    assert "- shard the cache, proposed by second\n" in kyo("dossier", decision).out

    refused = _decide(kyo, project, tmp_path, ALTERNATIVES.replace("worker", "somebody"))
    assert refused.code != 0
    assert "proposed_by 'somebody'" in refused.err and "orchestrator, worker" in refused.err
    # A key the alternative does not read is refused, as a key of the file itself is.
    misspelt = _decide(kyo, project, tmp_path, ALTERNATIVES.replace("proposed_by: worker", "by: x"))
    assert misspelt.code != 0 and "an alternative reads chosen, proposed_by, text" in misspelt.err


def test_a_credit_written_before_proposers_reads_as_the_agents(
    kyo, project: Path, tmp_path: Path
) -> None:
    """The stored form of an earlier credit is ``{text, chosen}`` and nothing else."""
    decision = _decision(kyo, project, tmp_path)
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db", isolation_level=None)
    conn.execute(
        "UPDATE credits SET alternatives_json = ?",
        (json.dumps([{"text": "keep the cache", "chosen": True}, {"text": "drop the cache"}]),),
    )
    conn.close()
    page = kyo("dossier", decision).out
    assert "- keep the cache (chosen), proposed by the agent\n- drop the cache, proposed by" in page
    assert cmd_dossier.PROPOSER_DEFAULTED in page


# ------------------------------------------------------------------ KYO-786.c2


def test_work_filed_with_implements_is_listed_under_what_happened_next(
    kyo, project: Path, tmp_path: Path
) -> None:
    decision = _decision(kyo, project, tmp_path)
    assert "The store links no work to this decision." in kyo("dossier", decision).out
    filed = kyo(
        "new", "task", "--title", "Warm the cache at start", "--implements", decision,
        "--criteria-file", "-", "--json", stdin="it is warm at start\n",
    )  # fmt: skip
    assert filed.code == 0, filed
    task = filed.json["record_id"]
    link = filed.json["implements_link"]
    assert (link["src"], link["type"], link["dst"]) == (task, "implements", decision)
    # The link is the one `kyo link` writes: both records show it, each from its side.
    assert {"id": link["id"], "type": "implements", "direction": "outgoing", "other": decision} in [
        {k: entry[k] for k in ("id", "type", "direction", "other")}
        for entry in kyo("show", task, "--json").json["links"]
    ]
    listed = _next(kyo("dossier", decision).out)
    assert "The store links no work" not in listed
    assert '"Warm the cache at start"' in listed
    assert cmd_dossier.IMPLEMENTS_IT in listed
    # Its status, in the words `kyo list` uses, and where its criteria stand.
    assert "task, open" in listed and "0 of 1 criterion met" in listed

    assert kyo("move", task, "working").code == 0
    assert "task, working" in _next(kyo("dossier", decision).out)


def test_work_already_filed_is_linked_with_kyo_link(kyo, project: Path, tmp_path: Path) -> None:
    assert "implements" in LINK_TYPES
    decision = _decision(kyo, project, tmp_path)
    task = new_record(kyo, kind="task", title="Measure the warm start", criteria=["measured"])
    assert '"Measure the warm start"' not in kyo("dossier", decision).out
    linked = kyo("link", task, "implements", decision)
    assert linked.code == 0, linked
    assert "carries out the decision on" in linked.out.splitlines()[-1]
    listed = _next(kyo("dossier", decision).out)
    assert '"Measure the warm start"' in listed and cmd_dossier.IMPLEMENTS_IT in listed
    # Retracting the link takes the record off the page again.
    link_id = kyo("show", task, "--json").json["links"][0]["id"]
    assert kyo("unlink", link_id, "--why", "linked to the wrong decision").code == 0
    assert "The store links no work to this decision." in kyo("dossier", decision).out


def test_spawn_takes_implements_beside_its_own_link(kyo, project: Path, tmp_path: Path) -> None:
    decision = _decision(kyo, project, tmp_path)
    source = new_record(kyo, kind="investigation", title="Why the start is cold")
    spawned = kyo(
        "spawn", source, "--kind", "task", "--title", "Preload the cache",
        "--criteria-file", _file(tmp_path, "c.txt", "it is preloaded\n"),
        "--touches-file", _file(tmp_path, "t.txt", "+preload.py\n"),
        "--implements", decision, "--json",
    )  # fmt: skip
    assert spawned.code == 0, spawned
    task = spawned.json["record_id"]
    assert spawned.json["link"]["type"] == "produced-by"
    assert spawned.json["implements_link"]["dst"] == decision
    links = kyo("show", task, "--json").json["links"]
    assert {(entry["type"], entry["other"]) for entry in links} == {
        ("produced-by", source),
        ("implements", decision),
    }
    assert '"Preload the cache"' in _next(kyo("dossier", decision).out)
    assert task in kyo("dossier", decision).out.split("\n## References\n")[1]


def test_implements_is_refused_for_a_record_no_decision_is_on(
    kyo, project: Path, tmp_path: Path
) -> None:
    other = new_record(kyo, kind="task", title="Not a decision", criteria=["done"])
    count = "SELECT COUNT(*) FROM records"
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db", isolation_level=None)
    before = conn.execute(count).fetchone()[0]
    refused = kyo("new", "task", "--title", "Follows nothing", "--implements", other)
    assert refused.code != 0
    assert f"--implements names {other}, a 'task' that no credit records a decision on" in (
        refused.err
    )
    assert conn.execute(count).fetchone()[0] == before
    conn.close()
