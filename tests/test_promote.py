"""``kyo promote`` — the ceremony boundary (law 13; KYO-1).

Every mechanism this milestone adds gets a test that fails if the mechanism is
removed: flow validation against the kind registry, touch validation against the
repo *now*, the promotion stamp (and the refusal to re-promote a stamped file), the
frozen description, and the stamp/record write behaving as one transaction.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kyosei import ideas
from kyosei.contracts import CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA
from kyosei.errors import EXIT_CONTRACT
from kyosei.prose import fingerprint

from .conftest import events_text, new_record


def _write_idea(project: Path, name: str, body: str) -> Path:
    path = project / "ideas" / name
    path.write_text(body, encoding="utf-8")
    return path


def _frontmatter(text: str) -> dict:
    """The idea file's YAML frontmatter, parsed by a real YAML reader.

    Deliberately not `kyosei.ideas`' own parser: a test that read the stamp back with
    the same code that wrote it would pass on a block only Kyosei can understand, and
    the point of KYO-30 is that *Obsidian* can read it.
    """
    assert text.startswith("---\n"), text
    return yaml.safe_load(text.split("\n---\n")[0].removeprefix("---\n"))


#: The promotion stub, written out literally rather than produced by the code under test —
#: the same reason ``test_ideas_review.LEGACY_STAMP`` is a literal. A test that built the
#: expected block by calling `ideas.promotion_stub` would pass on whatever shape that
#: function happens to produce, including a shape the decision record does not describe,
#: and the whole content of KYO-106.c1 is that the block matches the decision.
STUB_TEMPLATE = (
    "> **Promoted -> {record_id}** · {date} · #promoted #{record_id}\n"
    "> Text now lives in the record.\n"
)


def _expected_stub(record_id: str, promoted_at: str) -> str:
    """The stub the file must end with, dated from the stamp the *file* carries.

    The date is read back out of ``promoted_at`` rather than taken from a clock this
    test read: the two must agree, and a test that fetched its own ``date`` would pass
    on a stub dated a day off whenever a promotion straddled midnight UTC.
    """
    return STUB_TEMPLATE.format(record_id=record_id, date=promoted_at[:10])


# ---------------------------------------------------------------- flow validation


def test_promote_creates_a_record_of_an_accepting_kind(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "widget.md", "# Fix the widget\n\nIt explodes on cold cache.\n")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "chore",
        "--criteria-file",
        write_file("c.txt", "it no longer explodes\n"),
        "--touches-file",
        write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "chore"
    assert result.json["record"]["title"] == "Fix the widget"
    assert result.json["record"]["id"] == "TST-1"


def test_promote_refuses_a_kind_that_does_not_accept_idea(kyo, project: Path, write_file) -> None:
    """``decision``'s accepts_from is ``[proposal, investigation]`` — no ``idea``.

    KYO-68.c3: exit **2**, not 1. This is a named transition contract, the same class
    of rejection `kyo new` and `kyo spawn` raise when the registry's flow rules turn a
    kind away — and until KYO-68 this one alone exited 1, so a consumer scripting
    against "a creation verb refused my kind" had to handle two codes for one
    situation, with nothing visible deciding which it got. The three now agree; the
    agreement itself is pinned in ``tests/test_kyo5_error_remedies.py``.
    """
    idea = _write_idea(project, "arch.md", "# A decision\n\nShould we do X?\n")
    result = kyo("promote", str(idea), "--kind", "decision")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA in result.err
    assert "does not accept promotion from idea" in result.err
    assert "proposal" in result.err and "investigation" in result.err


def test_promote_refuses_an_unknown_kind(kyo, project: Path) -> None:
    idea = _write_idea(project, "x.md", "# X\n")
    result = kyo("promote", str(idea), "--kind", "ghost-kind")
    assert result.code == 1
    # KYO-104: the refusal is `registry.unknown_name_refusal`'s, shared by all four
    # registry dimensions, rather than a wording `kinds.py` kept of its own. It says
    # strictly more than the sentence it replaced — which file, and what that file does
    # declare — so the assertion asks for the name AND the list.
    assert "kind 'ghost-kind' is not declared" in result.err
    assert "declared kinds:" in result.err


def test_promote_refuses_an_idea_file_outside_ideas_dir(kyo, project: Path, write_file) -> None:
    outside = project / "notes.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    result = kyo(
        "promote", str(outside), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
    )
    assert result.code == 1
    assert "ideas/" in result.err


# --------------------------------------------------------------------------- title


def test_title_defaults_to_the_first_heading(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A Great Idea\n\nSome body text.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["record"]["title"] == "A Great Idea"


def test_title_flag_overrides_the_heading(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A Great Idea\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore", "--title", "A Better Title",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["record"]["title"] == "A Better Title"


def test_no_title_and_no_heading_fails_loudly(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "Just some prose, no heading at all.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "no top-level" in result.err


# ------------------------------------------------------------------------ criteria


def test_criteria_file_is_required_when_the_kind_requires_it(
    kyo, project: Path, write_file
) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "requires_criteria" in result.err


# ------------------------------------------------------------------------- touches


def test_touches_file_is_required_for_work_archetype_kinds(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
    )
    assert result.code == 1
    assert "archetype 'work'" in result.err


def test_a_touch_naming_a_missing_file_is_refused(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/does-not-exist.py\n"),
    )
    assert result.code == 1
    assert "no such file" in result.err
    assert "kyosei/does-not-exist.py" in result.err


def test_a_touch_naming_a_missing_symbol_is_refused(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml#not_a_real_symbol_xyz\n"),
    )
    assert result.code == 1
    assert "not found" in result.err
    assert "not_a_real_symbol_xyz" in result.err


def test_a_valid_symbol_touch_is_stored(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo("--full",
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml#bug\n"),
        "--json",
    )
    assert result.code == 0, result
    touches = result.json["record"]["touches"]
    assert len(touches) == 1
    assert touches[0]["path"] == "kyosei/kinds.yml"
    assert touches[0]["symbol"] == "bug"


def test_touch_validation_collects_every_problem_at_once(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file(
            "t.txt", "kyosei/nope-one.py\nkyosei/nope-two.py\n"
        ),
    )
    assert result.code == 1
    assert "nope-one" in result.err
    assert "nope-two" in result.err


# ---------------------------------------------------------------------- the stamp


def _promote(kyo, idea: Path, write_file, suffix: str = "") -> str:
    """Promotes with ``--keep``, so every caller below -- almost all written before
    KYO-228 flipped the default to consuming -- still exercises exactly the pre-KYO-228
    stub behaviour they assert on. The default (consuming) path has its own tests,
    further down, that call `kyo promote` directly without this helper.
    """
    result = kyo(
        "promote", str(idea), "--kind", "chore", "--keep",
        "--criteria-file", write_file(f"c{suffix}.txt", "x\n"),
        "--touches-file", write_file(f"t{suffix}.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_the_stamp_is_top_of_file_frontmatter_and_promoted_is_a_list(
    kyo, project: Path, write_file
) -> None:
    """KYO-30.c3. Two properties of the stamp, both load-bearing and both invisible to
    a substring check: it is at the TOP (Obsidian reads frontmatter nowhere else, and
    the pre-KYO-30 stamp was at the bottom, which is the entire defect), and ``promoted``
    parses as a LIST, so a file that later carries several section promotions widens
    without a second stamp convention appearing beside this one.
    """
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    record_id = _promote(kyo, idea, write_file)

    text = idea.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    front = _frontmatter(text)
    assert front["promoted"] == [record_id]
    assert isinstance(front["promoted_at"], list)
    assert len(front["promoted_at"]) == 1
    assert front["promoted_at"][0].endswith("Z"), front["promoted_at"]
    assert text.endswith(_expected_stub(record_id, front["promoted_at"][0]))


def test_the_stamp_merges_with_frontmatter_the_human_already_wrote(
    kyo, project: Path, write_file
) -> None:
    """KYO-30.c3: *merged with*, not written over. ``ideas/`` is an Obsidian vault as
    far as its owner is concerned; a promotion that dropped their tags and aliases would
    be a data-loss bug dressed as a feature."""
    idea = _write_idea(
        project,
        "a.md",
        "---\ntags:\n  - inbox\n  - design\naliases: [Widget]\n# a comment of mine\n"
        "status: thinking\n---\n\n# A\n\nBody.\n",
    )
    record_id = _promote(kyo, idea, write_file)

    text = idea.read_text(encoding="utf-8")
    front = _frontmatter(text)
    assert front["tags"] == ["inbox", "design"]
    assert front["aliases"] == ["Widget"]
    assert front["status"] == "thinking"
    assert front["promoted"] == [record_id]
    assert "# a comment of mine" in text
    assert text.endswith(_expected_stub(record_id, front["promoted_at"][0]))
    assert "Body." not in text, (
        "KYO-106: the prose moved into the record; the frontmatter the human wrote is "
        "properties, not prose, and is the part that stays"
    )


def test_apply_stamp_appends_rather_than_replacing_an_existing_promotion() -> None:
    """The list is a list in practice, not just in shape. `kyo promote` is still one
    file / one record, so this is the unit that proves the second promotion of a file
    (a section promotion) will land beside the first instead of
    overwriting it — and that the two lists stay index-aligned."""
    first = ideas.apply_stamp("# A\n\nBody.\n", "KYO-1", "2026-01-01T00:00:00.000Z")
    second = ideas.apply_stamp(first, "KYO-2", "2026-02-02T00:00:00.000Z")

    front = _frontmatter(second)
    assert front["promoted"] == ["KYO-1", "KYO-2"]
    assert front["promoted_at"] == ["2026-01-01T00:00:00.000Z", "2026-02-02T00:00:00.000Z"]
    assert ideas.promotion_stamps(second) == [
        ideas.Stamp("KYO-1", "2026-01-01T00:00:00.000Z"),
        ideas.Stamp("KYO-2", "2026-02-02T00:00:00.000Z"),
    ]
    assert second.endswith("# A\n\nBody.\n")


def test_the_stamp_timestamp_is_stored_as_text_not_a_yaml_date() -> None:
    """Law 16 in the small: an unquoted ``2026-01-01T00:00:00.000Z`` is a YAML
    *timestamp*, so every proper parser would hand back a datetime whose text form is
    not the one the store holds. The stamp must read back as the characters written."""
    stamped = ideas.apply_stamp("# A\n", "KYO-1", "2026-01-01T00:00:00.000Z")
    front = _frontmatter(stamped)
    assert front["promoted_at"] == ["2026-01-01T00:00:00.000Z"]
    assert isinstance(front["promoted_at"][0], str)


def test_a_stamped_file_cannot_be_promoted_again(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    record_id = _promote(kyo, idea, write_file)
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "already carries a promotion stamp" in result.err
    assert record_id in result.err


def test_the_re_promotion_refusal_names_every_record_the_file_lists(
    kyo, project: Path, write_file
) -> None:
    """KYO-30.c3's consequence for law 6: once ``promoted`` is a list, a refusal that
    named one record would understate the file. The controller reading this error has to
    be able to find every record already holding this file's text."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    one = ideas.apply_stamp("# A\n\nBody.\n", "KYO-1", "2026-01-01T00:00:00.000Z")
    idea.write_text(
        ideas.apply_stamp(one, "KYO-2", "2026-02-02T00:00:00.000Z"), encoding="utf-8"
    )
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "KYO-1 at 2026-01-01T00:00:00.000Z" in result.err
    assert "KYO-2 at 2026-02-02T00:00:00.000Z" in result.err


def test_a_file_still_carrying_the_old_bottom_stamp_is_refused_promotion(
    kyo, project: Path, write_file
) -> None:
    """The migration is a separate, human-initiated act, so between this release and
    that run every promoted idea in a consumer's vault carries the pre-KYO-30 stamp.
    Reading only frontmatter would offer all of them for promotion a second time."""
    idea = _write_idea(
        project,
        "a.md",
        "# A\n\nBody.\n\n---\npromoted: KYO-9\npromoted_at: 2026-01-01T00:00:00.000Z\n---\n",
    )
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "already carries a promotion stamp" in result.err
    assert "KYO-9" in result.err


# --------------------------------------------------------------------- description


def test_description_is_a_frozen_copy_of_the_idea_plus_a_pointer(
    kyo, project: Path, write_file
) -> None:
    body = "# A\n\nThe idea's own words, verbatim.\n"
    idea = _write_idea(project, "a.md", body)
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    description = result.json["record"]["description"]
    assert "The idea's own words, verbatim." in description
    assert "Promoted from: ideas/a.md" in description


def test_editing_the_idea_file_after_promotion_does_not_change_the_frozen_description(
    kyo, project: Path, write_file
) -> None:
    """``--keep``: this is specifically about a file that still exists to edit. Under
    the KYO-228 default the file is gone, which makes the same point more bluntly --
    see ``test_a_default_whole_file_promotion_deletes_the_idea_file`` below."""
    idea = _write_idea(project, "a.md", "# A\n\nOriginal text.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore", "--keep",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    record_id = result.json["record"]["id"]
    idea.write_text(
        idea.read_text(encoding="utf-8") + "\n\nAdded after promotion.\n", encoding="utf-8"
    )
    doc = kyo("show", record_id, "--json").json
    assert "Added after promotion." not in doc["description"]
    assert "Original text." in doc["description"]


# ------------------------------------------------------------------------ atomicity


def test_a_stamp_that_cannot_be_written_leaves_no_record(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """Law 6 / promotion's single-transaction contract: if the stamp write fails,
    the record must not persist. ``--keep``, because this is specifically the stub
    write path -- the default (consuming) path's own atomicity is
    ``test_a_deletion_that_cannot_happen_leaves_no_record`` below, which patches
    ``Path.unlink`` rather than ``Path.write_text``."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, *args, **kwargs):
        if self == idea:
            raise OSError("simulated disk failure")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)
    result = kyo(
        "promote", str(idea), "--kind", "chore", "--keep",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "could not write promotion stamp" in result.err
    assert kyo("list", "--json").json["records"] == []
    assert idea.read_text(encoding="utf-8") == "# A\n\nBody.\n"


def test_a_deletion_that_cannot_happen_leaves_no_record(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """The default (consuming) path's own version of the test above: if the file
    cannot be removed, the record must not persist either, and the file is untouched."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    original_unlink = Path.unlink

    def flaky_unlink(self: Path, *args, **kwargs):
        if self == idea:
            raise OSError("simulated disk failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "could not remove" in result.err
    assert kyo("list", "--json").json["records"] == []
    assert idea.read_text(encoding="utf-8") == "# A\n\nBody.\n"


def test_a_failed_commit_after_the_stamp_write_restores_the_original_idea_file(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """The reverse direction: if the db side then fails, the idea file's mutation must
    not persist either. Exercises the KYO-228 default (consuming) path -- the file is
    deleted, the commit then fails, and ``original_text`` comes back exactly, which is
    the same rollback whether what preceded it was a delete or a stub write (KYO-228:
    "restoring `original_text` recreates a deleted file exactly as well as it restores
    a stamped one")."""
    from kyosei import commands

    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    original_text = idea.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(commands, "_commit_and_export", boom)
    # KYO-421: reported by `cli.main` on one line, exit 1, rather than escaping.
    died = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert died.code == 1 and "RuntimeError: simulated commit failure" in died.err, died
    assert idea.read_text(encoding="utf-8") == original_text
    assert kyo("list", "--json").json["records"] == []


# --------------------------------------------------------------------------- misc


def test_promote_writes_an_event_and_an_export(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    record_id = result.json["record"]["id"]
    assert (project / "kyosei" / "export" / f"{record_id}.json").is_file()
    events = events_text(project)
    assert record_id in events


def test_promote_leaves_other_records_untouched(kyo, project: Path, write_file) -> None:
    other = new_record(kyo, title="unrelated")
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert kyo("show", other, "--json").json["status"] == "open"


# ------------------------------------------------------------------- KYO-24: bug


def test_a_bug_can_be_promoted_from_an_idea(kyo, project: Path, write_file) -> None:
    """KYO-24. `bug.accepts_from` was `[intake]` alone, so a defect written up as an
    idea — which is how every consumer report arrives — could not become a bug. Two
    records in this repo (KYO-17, KYO-23) are `task`s carrying a note that says only
    "this is really a bug, but the registry would not take it"."""
    idea = _write_idea(project, "defect.md", "# The widget renders twice\n\nOn cold cache.\n")
    result = kyo("--full",
        "promote",
        str(idea),
        "--kind",
        "bug",
        "--criteria-file", write_file("bug-criteria.txt", "it renders once\n"),
        "--touches-file", write_file("bug-touches.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "bug"
    assert [t["path"] for t in result.json["record"]["touches"]] == ["kyosei/kinds.yml"]


def test_filing_a_bug_directly_still_works(kyo) -> None:
    """Additive, not a replacement: `intake` stays, so the human's Bugs surface is
    untouched (CLAUDE.md, the three human surfaces)."""
    result = kyo("new", "bug", "--title", "Filed by a human", "--json")
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "bug"


# ------------------------------ KYO-106: promotion moves the text and leaves a stub
#
# The stub decision rejected three alternatives (locking the region in place,
# leaving the full text beside the record, deleting it outright). What the tests below
# hold down is the one it chose: after a whole-file promotion the prose is in the store
# and a pointer is in the file, and the pointer carries no claim that can go stale.


def test_the_promoted_text_leaves_the_idea_file_for_a_stub(kyo, project: Path, write_file) -> None:
    """KYO-106.c1. Until this, `kyo promote` copied the text and left it — two artifacts
    holding one claim, with nothing linking them and nothing keeping them equal (law 3).
    Files stamped under v1 were found carrying a body that was a stale pre-amendment copy
    of a live record.
    """
    idea = _write_idea(project, "widget.md", "# Fix the widget\n\nIt explodes on cold cache.\n")
    record_id = _promote(kyo, idea, write_file)

    text = idea.read_text(encoding="utf-8")
    front = _frontmatter(text)
    assert front["promoted"] == [record_id]
    assert text.endswith(_expected_stub(record_id, front["promoted_at"][0]))
    assert "It explodes on cold cache." not in text
    assert kyo("show", record_id, "--json").json["description"].startswith(
        "# Fix the widget\n\nIt explodes on cold cache."
    )


def test_no_file_under_ideas_still_holds_a_copy_of_the_promoted_text(
    kyo, project: Path, write_file
) -> None:
    """KYO-106.c1, the half the file-level assertion cannot make. "The record is the single
    home of the prose" is a claim about ``ideas/`` as a whole, not about one path — a
    promotion that stubbed the file and dropped a backup beside it would satisfy every
    assertion above and none of the decision.
    """
    idea = _write_idea(project, "widget.md", "# Fix the widget\n\nIt explodes on cold cache.\n")
    _promote(kyo, idea, write_file)

    holders = [
        path.relative_to(project).as_posix()
        for path in sorted((project / "ideas").rglob("*"))
        if path.is_file() and "It explodes on cold cache." in path.read_text(encoding="utf-8")
    ]
    assert holders == []


def test_the_stub_is_a_pointer_and_carries_no_copy_of_the_claim(
    kyo, project: Path, write_file
) -> None:
    """The reason for a stub rather than a deletion *or* a summary: a pointer cannot
    go stale, and a summary can. The stub names the record twice — once readably and once
    as an Obsidian tag — and says nothing about what the idea was.
    """
    idea = _write_idea(project, "widget.md", "# Fix the widget\n\nIt explodes on cold cache.\n")
    record_id = _promote(kyo, idea, write_file)

    _, _, stub = idea.read_text(encoding="utf-8").partition("\n---\n")
    assert stub.strip().split("\n") == [
        f"> **Promoted -> {record_id}** · "
        f"{_frontmatter(idea.read_text(encoding='utf-8'))['promoted_at'][0][:10]} · "
        f"#promoted #{record_id}",
        "> Text now lives in the record.",
    ]
    assert "widget" not in stub, "a stub that described the idea would be a second copy"


def test_the_stub_is_dated_from_the_stamp_it_was_written_with(
    kyo, project: Path, write_file
) -> None:
    """Law 16 in the small: the date in the stub is sliced from the timestamp the same
    write put in ``promoted_at``, not formatted from a second clock reading. Two clock
    reads either side of midnight UTC would disagree, and the disagreement would be
    invisible on every day but one."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    record_id = _promote(kyo, idea, write_file)

    text = idea.read_text(encoding="utf-8")
    stamped_at = _frontmatter(text)["promoted_at"][0]
    assert f"**Promoted -> {record_id}** · {stamped_at[:10]} ·" in text


VERBATIM_BODY = (
    "# Widget\n"
    "\n"
    "Prose with a [[wikilink]], a `kyosei/kinds.yml` mention, and an em dash — plus\n"
    "a hard-wrapped\n"
    "sentence.\n"
    "\n"
    "```python\n"
    "def f():\n"
    "\treturn 1  \n"
    "```\n"
    "\n"
    "Ends with 共生 and two  spaces.\n"
)


def test_the_promoted_text_lands_byte_verbatim(kyo, project: Path, write_file) -> None:
    """KYO-106.c2. Equality, not ``in``: the prose is now in exactly one place, so anything
    promotion silently normalised — a tab, a trailing space, a wrap, a non-ASCII character,
    a fenced block — would be lost rather than merely reformatted. The only difference from
    the file's bytes is the trailing newline ``prose.normalise`` strips from every stored
    field, and the provenance pointer appended after it.
    """
    idea = _write_idea(project, "verbatim.md", VERBATIM_BODY)
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["record"]["description"] == (
        VERBATIM_BODY.rstrip("\n") + "\n\n---\nPromoted from: ideas/verbatim.md\n"
    )


def test_the_promoted_text_is_echoed_back_with_its_sha(kyo, project: Path, write_file) -> None:
    """KYO-106.c2, the law-5 half. Promotion is a store write like any other, so what it
    stored is read back to the caller measured — a dropped word changes the digest even
    when the sentence still reads fine. The sha is recomputed here from the description the
    store hands back, so an echo of the *right shape* over the wrong text still fails.
    """
    idea = _write_idea(project, "verbatim.md", VERBATIM_BODY)
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 0, result
    stored = kyo("list", "--json").json["records"][0]["id"]
    description = kyo("show", stored, "--json").json["description"]
    assert f"stored description ({len(description)} chars, sha256:{fingerprint(description)}):" in (
        result.out
    )


def test_the_frontmatter_the_human_wrote_stays_in_the_file_not_in_the_record(
    kyo, project: Path, write_file
) -> None:
    """KYO-106.c2's "modulo the frontmatter block". Properties are not prose: ``tags`` and
    ``aliases`` are how the vault finds the file, they belong to the file that survives, and
    a record description opening with somebody's YAML would be neither the idea's words nor
    a thing `kyo redact` should ever have to reach into.
    """
    idea = _write_idea(
        project,
        "a.md",
        "---\ntags:\n  - inbox\naliases: [Widget]\n---\n\n# A\n\nThe idea's own words.\n",
    )
    record_id = _promote(kyo, idea, write_file)

    description = kyo("show", record_id, "--json").json["description"]
    assert description.startswith("# A\n\nThe idea's own words.")
    assert "tags:" not in description and "aliases:" not in description
    assert _frontmatter(idea.read_text(encoding="utf-8"))["tags"] == ["inbox"]


def test_a_stubbed_file_still_reports_its_promotion_and_is_refused_a_second_one(
    kyo, project: Path, write_file
) -> None:
    """KYO-106.c4. The stamp machinery is unchanged by the stub — the file that comes back
    from a promotion is one the existing reader recognises, which is what keeps it out of
    `kyo ideas review` and out of a second promotion. The refusal has to say more than it
    used to: before the stub, re-promoting made a duplicate record; now the prose is not in
    the file at all, so a caller who cannot find it needs the id that has it.
    """
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    record_id = _promote(kyo, idea, write_file)

    text = idea.read_text(encoding="utf-8")
    stamps = ideas.promotion_stamps(text)
    assert [stamp.record_id for stamp in stamps] == [record_id]
    assert stamps[0].at == _frontmatter(text)["promoted_at"][0]

    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert record_id in result.err
    assert "its text lives in" in result.err
    assert idea.read_text(encoding="utf-8") == text, "a refused promotion writes nothing"


def test_the_readme_quick_start_command_is_accepted(kyo, project: Path, write_file) -> None:
    """The README showed `kyo promote ... --kind bug` while the registry refused it.
    It is true again now, and this test is what keeps the two from drifting apart a
    second time."""
    idea = _write_idea(project, "some-idea.md", "# Some idea\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "bug",
        "--criteria-file", write_file("c.txt", "it works\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 0, result


# --------------------------- KYO-228: promotion consumes a whole file by default
#
# A whole-file promotion used to leave a stub; KYO-228 reversed that, because a stub
# kept in the vault serves no purpose once the record holds the text. The tests above
# this marker (via `_promote`, which passes `--keep`) hold down
# the old behaviour exactly as it was; these hold down the new default, and
# `--keep`'s own contract: that it is, byte for byte, the behaviour those older tests
# still exercise.


def test_a_default_whole_file_promotion_deletes_the_idea_file(
    kyo, project: Path, write_file
) -> None:
    idea = _write_idea(project, "widget.md", "# Fix the widget\n\nIt explodes on cold cache.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert not idea.exists(), "KYO-228: the default leaves no file, not a stub"
    assert result.json["record"]["description"].startswith(
        "# Fix the widget\n\nIt explodes on cold cache."
    )
    assert "Promoted from: ideas/widget.md" in result.json["record"]["description"]


def test_the_default_promotion_json_states_kept_false_and_stamped_at_null(
    kyo, project: Path, write_file
) -> None:
    """Law 6: present and null, not absent -- a consumer reads "no stamp was written"
    as a stated fact rather than inferring it from a missing key."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["kept"] is False
    assert result.json["stamped_at"] is None


def test_promoting_the_same_path_again_after_a_default_promotion_fails_cleanly(
    kyo, project: Path, write_file
) -> None:
    """No stub means no "already carries a promotion stamp" refusal to give — the file
    is simply not there for a second `kyo promote` to find, and it is refused the same
    way any other missing path would be."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    first = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert first.code == 0, first
    second = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
    )
    assert second.code == 1
    assert "is not a file" in second.err
    assert len(kyo("list", "--json").json["records"]) == 1


def test_keep_retains_the_stub_for_a_whole_file_promotion(kyo, project: Path, write_file) -> None:
    """The explicit, direct demonstration of the flag: pass ``--keep`` with nothing
    else changed, and the file survives holding exactly the promotion stub -- the pre-KYO-228
    behaviour, opted back into rather than lost."""
    idea = _write_idea(project, "widget.md", "# Fix the widget\n\nIt explodes on cold cache.\n")
    result = kyo(
        "promote", str(idea), "--kind", "chore", "--keep",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["kept"] is True
    record_id = result.json["record"]["id"]
    assert result.json["stamped_at"] is not None
    text = idea.read_text(encoding="utf-8")
    front = _frontmatter(text)
    assert front["promoted"] == [record_id]
    assert text.endswith(_expected_stub(record_id, front["promoted_at"][0]))


# ------------------------- KYO-107: one section becomes a record, the file survives
#
# The section half of the stub decision. The whole-file tests above hold down "the
# record is the single home of the prose"; these hold down the same sentence at section
# scale, plus the thing only the section case can get wrong — that promoting one idea
# out of a file of several leaves every other byte of that file exactly where it was.

#: A file holding two ideas, of the shape ``ideas/`` actually fills up with. No
#: frontmatter, so the expected text below can state the whole file rather than a suffix
#: of it: a promotion that reordered, re-indented or re-fenced anything would fail on the
#: equality rather than survive an ``in``.
TWO_IDEAS = (
    "# Two ideas in one file\n"
    "\n"
    "## Ship the widget\n"
    "\n"
    "It explodes on cold cache, and the retry path double-counts.\n"
    "\n"
    "```python\n"
    "def f():\n"
    "\treturn 1  \n"
    "```\n"
    "\n"
    "## Rename the thing\n"
    "\n"
    "`kyosei/kinds.yml` calls it a chore and the README calls it a task.\n"
)

#: The prose under ``## Ship the widget`` — the bytes a promotion of that section moves.
WIDGET_TEXT = (
    "It explodes on cold cache, and the retry path double-counts.\n"
    "\n"
    "```python\n"
    "def f():\n"
    "\treturn 1  \n"
    "```"
)

#: The prose under ``## Rename the thing``.
RENAME_TEXT = "`kyosei/kinds.yml` calls it a chore and the README calls it a task."


def _promote_section(kyo, idea: Path, section: str, write_file, suffix: str = "") -> dict:
    """Also ``--keep`` for the same reason ``_promote`` above is -- see its docstring."""
    result = kyo(
        "promote", str(idea), "--section", section, "--kind", "chore", "--keep",
        "--criteria-file", write_file(f"cs{suffix}.txt", "x\n"),
        "--touches-file", write_file(f"ts{suffix}.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json


def _promote_section_default(kyo, idea: Path, section: str, write_file, suffix: str = "") -> dict:
    """The KYO-228 default: no ``--keep``, so the section is removed rather than
    stubbed."""
    result = kyo(
        "promote", str(idea), "--section", section, "--kind", "chore",
        "--criteria-file", write_file(f"cs{suffix}.txt", "x\n"),
        "--touches-file", write_file(f"ts{suffix}.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json


def test_a_named_section_becomes_the_record_and_the_rest_of_the_file_survives(
    kyo, project: Path, write_file
) -> None:
    """KYO-107.c1, stated as one equality over the whole file.

    ``in``/``not in`` cannot say "the rest of the file survives": a promotion that rewrote
    the second section's fence, dropped its trailing spaces, or moved the sections around
    would pass every substring check and fail the decision. So the expected text is written
    out in full, with exactly one region swapped for the stub — and the stub itself is the
    literal ``STUB_TEMPLATE``, not a call to the writer under test.
    """
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    document = _promote_section(kyo, idea, "Ship the widget", write_file)
    record_id = document["record"]["id"]
    assert document["section"] == "Ship the widget"

    text = idea.read_text(encoding="utf-8")
    at = _frontmatter(text)["promoted_at"][0]
    assert text == (
        "---\n"
        "promoted:\n"
        f"  - {record_id}\n"
        "promoted_at:\n"
        f'  - "{at}"\n'
        "---\n"
        "\n"
        "# Two ideas in one file\n"
        "\n"
        "## Ship the widget\n"
        "\n"
        + _expected_stub(record_id, at)
        + "\n"
        "## Rename the thing\n"
        "\n"
        "`kyosei/kinds.yml` calls it a chore and the README calls it a task.\n"
    )
    assert kyo("show", record_id, "--json").json["description"] == (
        WIDGET_TEXT + "\n\n---\nPromoted from: ideas/two.md#Ship the widget\n"
    )


def test_the_section_title_defaults_to_its_heading(kyo, project: Path, write_file) -> None:
    """`--section` alone is enough to promote: the record comes out called what the vault
    calls it. The pre-KYO-107 default — the file's first ``# heading`` — would have named
    every section of a file after the file."""
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    document = _promote_section(kyo, idea, "Rename the thing", write_file)
    assert document["record"]["title"] == "Rename the thing"


def test_two_sections_promoted_at_different_times_leave_two_stubs_and_two_records(
    kyo, project: Path, write_file
) -> None:
    """KYO-107.c2 and .c4 together. ``promoted`` has been a list since KYO-30 for exactly
    this; what the second promotion proves is that it is one *appended to*, that the first
    stub is not disturbed by the second write, and that each stub points at its own record
    rather than both at the last one.
    """
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    first = _promote_section(kyo, idea, "Ship the widget", write_file)["record"]["id"]
    second = _promote_section(kyo, idea, "Rename the thing", write_file, suffix="2")["record"]["id"]
    assert first != second

    text = idea.read_text(encoding="utf-8")
    front = _frontmatter(text)
    assert front["promoted"] == [first, second]
    assert len(front["promoted_at"]) == 2

    widget, rename = (
        ideas.sections_named(text, "Ship the widget")[0],
        ideas.sections_named(text, "Rename the thing")[0],
    )
    assert widget.text == _expected_stub(first, front["promoted_at"][0]).rstrip("\n")
    assert rename.text == _expected_stub(second, front["promoted_at"][1]).rstrip("\n")

    assert kyo("show", first, "--json").json["description"].startswith(WIDGET_TEXT)
    assert kyo("show", second, "--json").json["description"].startswith(RENAME_TEXT)


def test_no_file_under_ideas_holds_a_copy_of_either_promoted_section(
    kyo, project: Path, write_file
) -> None:
    """KYO-107.c4's second half, and the one the file-level assertions cannot make: "no
    promoted text is duplicated anywhere under ideas/" is a claim about the directory. The
    heading lines stay behind on purpose — they are the anchor, not the prose — so what is
    searched for is the text each record now holds.
    """
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    _promote_section(kyo, idea, "Ship the widget", write_file)
    _promote_section(kyo, idea, "Rename the thing", write_file, suffix="2")

    for promoted in ("It explodes on cold cache", "calls it a chore"):
        holders = [
            path.relative_to(project).as_posix()
            for path in sorted((project / "ideas").rglob("*"))
            if path.is_file() and promoted in path.read_text(encoding="utf-8")
        ]
        assert holders == [], promoted


def test_the_heading_stays_in_the_file_as_the_anchor_under_keep(
    kyo, project: Path, write_file
) -> None:
    """The design decision KYO-107.c3 forced, and now specifically a ``--keep`` property
    (``_promote_section`` passes it): if the heading went with the prose, the section
    would stop existing, ``[[two.md#Ship the widget]]`` would stop resolving, and the
    refusal below — "that section is already promoted, here is the record" — could
    never fire, because there would be nothing left to name. KYO-228's default gives up
    exactly this anchor on purpose; see
    ``test_a_default_section_promotion_removes_the_heading_too`` below.
    """
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    _promote_section(kyo, idea, "Ship the widget", write_file)

    headings = [section.heading for section in ideas.sections(idea.read_text(encoding="utf-8"))]
    assert headings == ["Two ideas in one file", "Ship the widget", "Rename the thing"]


# --------------------------- KYO-228: promotion consumes a section by default
#
# The section half of the same reversal. A section promotion now removes the section outright —
# heading included — rather than leaving KYO-107's stub under it; the rest of the file
# is exactly as untouched as it always was.


def test_a_default_section_promotion_removes_the_heading_too(
    kyo, project: Path, write_file
) -> None:
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    document = _promote_section_default(kyo, idea, "Ship the widget", write_file)
    record_id = document["record"]["id"]
    assert document["kept"] is False
    assert document["stamped_at"] is None

    text = idea.read_text(encoding="utf-8")
    assert text == (
        "# Two ideas in one file\n"
        "\n"
        "## Rename the thing\n"
        "\n"
        "`kyosei/kinds.yml` calls it a chore and the README calls it a task.\n"
    )
    headings = [section.heading for section in ideas.sections(text)]
    assert headings == ["Two ideas in one file", "Rename the thing"]
    assert kyo("show", record_id, "--json").json["description"] == (
        WIDGET_TEXT + "\n\n---\nPromoted from: ideas/two.md#Ship the widget\n"
    )


def test_a_default_section_promotion_leaves_the_frontmatter_untouched(
    kyo, project: Path, write_file
) -> None:
    """KYO-228: the store's ``promoted`` list on the idea is superseded by the record's
    own provenance, so a default (consuming) section promotion writes no
    ``promoted``/``promoted_at`` key at all -- the surviving frontmatter is exactly what
    the human wrote, byte for byte."""
    idea = _write_idea(
        project,
        "two.md",
        "---\ntags:\n  - inbox\n---\n" + TWO_IDEAS,
    )
    _promote_section_default(kyo, idea, "Ship the widget", write_file)
    text = idea.read_text(encoding="utf-8")
    assert text.startswith("---\ntags:\n  - inbox\n---\n")
    assert "promoted" not in _frontmatter(text)


def test_promoting_the_removed_section_again_finds_no_such_heading(
    kyo, project: Path, write_file
) -> None:
    """No stub means no "already promoted" refusal for a section either -- the heading
    is gone, so a second attempt reads exactly like the heading was never there."""
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    _promote_section_default(kyo, idea, "Ship the widget", write_file)
    result = kyo(
        "promote", str(idea), "--section", "Ship the widget", "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "no section titled 'Ship the widget'" in result.err
    assert "'Rename the thing'" in result.err


def test_keep_retains_the_stub_for_a_section_promotion(kyo, project: Path, write_file) -> None:
    """The section-scale twin of ``test_keep_retains_the_stub_for_a_whole_file_promotion``:
    ``--keep`` reproduces KYO-107's exact stub, heading and all."""
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    document = _promote_section(kyo, idea, "Ship the widget", write_file)
    record_id = document["record"]["id"]
    assert document["kept"] is True

    text = idea.read_text(encoding="utf-8")
    at = _frontmatter(text)["promoted_at"][0]
    assert text == (
        "---\n"
        "promoted:\n"
        f"  - {record_id}\n"
        "promoted_at:\n"
        f'  - "{at}"\n'
        "---\n"
        "\n"
        "# Two ideas in one file\n"
        "\n"
        "## Ship the widget\n"
        "\n"
        + _expected_stub(record_id, at)
        + "\n"
        "## Rename the thing\n"
        "\n"
        "`kyosei/kinds.yml` calls it a chore and the README calls it a task.\n"
    )


# ------------------------------------------------- KYO-107.c3: the refusals


def test_an_unknown_section_is_refused_and_the_real_sections_are_named(
    kyo, project: Path, write_file
) -> None:
    """Law 6: a refusal that only said "no such section" would leave the caller guessing at
    a heading they cannot see from the error. The list is the remedy, which is why the
    message names no command to run."""
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    result = kyo(
        "promote", str(idea), "--section", "Ship the widgets", "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "no section titled 'Ship the widgets'" in result.err
    assert "'Two ideas in one file'" in result.err
    assert "'Ship the widget'" in result.err
    assert "'Rename the thing'" in result.err
    assert idea.read_text(encoding="utf-8") == TWO_IDEAS, "a refused promotion writes nothing"


def test_the_section_listing_says_which_sections_are_already_promoted(
    kyo, project: Path, write_file
) -> None:
    """The listing is what the caller chooses from, so a section whose text has already
    moved has to say so there — otherwise they pick it and meet a second refusal, one at a
    time, for as many promoted sections as the file holds."""
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    record_id = _promote_section(kyo, idea, "Ship the widget", write_file)["record"]["id"]
    result = kyo(
        "promote", str(idea), "--section", "Nope", "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert f"'Ship the widget' (promoted -> {record_id})" in result.err


def test_an_already_stubbed_section_is_refused_and_names_the_record_holding_its_text(
    kyo, project: Path, write_file
) -> None:
    """KYO-107.c3. Same sentence KYO-106's whole-file refusal makes, one level down: the
    prose is not in this file, so a second promotion would copy a stub into a new record
    and the text would be nowhere the caller was looking."""
    idea = _write_idea(project, "two.md", TWO_IDEAS)
    record_id = _promote_section(kyo, idea, "Ship the widget", write_file)["record"]["id"]
    before = idea.read_text(encoding="utf-8")

    result = kyo(
        "promote", str(idea), "--section", "Ship the widget", "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "section 'Ship the widget' is already promoted" in result.err
    assert f"its text lives in {record_id}" in result.err
    assert idea.read_text(encoding="utf-8") == before, "a refused promotion writes nothing"


def test_two_sections_sharing_a_heading_are_refused_rather_than_guessed_between(
    kyo, project: Path, write_file
) -> None:
    """Law 6 on the addressing itself. A heading is what an Obsidian user can see and type,
    and nothing stops them typing it twice — at which point the flag has been handed a name
    that does not identify a section, and promoting "the first one" would move prose the
    caller never looked at. The lines are named because that is what tells the two apart in
    the editor the file is open in.
    """
    idea = _write_idea(
        project,
        "dupes.md",
        "# Notes\n\n## Notes\n\nThe first block.\n\n## Notes\n\nThe second block.\n",
    )
    result = kyo(
        "promote", str(idea), "--section", "Notes", "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "3 sections titled 'Notes'" in result.err
    assert "at lines 1, 3, 7" in result.err
    assert "The first block." in idea.read_text(encoding="utf-8")


def test_a_section_with_no_text_under_it_is_refused(kyo, project: Path, write_file) -> None:
    """A heading with nothing beneath it is a placeholder, not an idea. Promoting it would
    create a record whose description is empty and stamp the file for it — a promotion that
    moved nothing, recorded as one that did."""
    idea = _write_idea(project, "empty.md", "# Ideas\n\n## Later\n\n## Now\n\nSomething.\n")
    result = kyo(
        "promote", str(idea), "--section", "Later", "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "holds no text under its heading" in result.err
    assert kyo("list", "--json").json["records"] == []


def test_a_whole_file_stub_has_no_sections_left_and_the_refusal_says_so(
    kyo, project: Path, write_file
) -> None:
    """The two scales meeting. After a whole-file promotion the body is a stub and there is
    nothing left to address, so a `--section` call against it must not read as "you typed
    the heading wrong" — it says the file holds no headings and where the text went."""
    idea = _write_idea(project, "a.md", "# A\n\nBody.\n")
    record_id = _promote(kyo, idea, write_file)
    result = kyo(
        "promote", str(idea), "--section", "A", "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "no markdown headings at all" in result.err
    assert record_id in result.err


# ------------------------------------------------- KYO-107: the section scan itself


def test_the_stub_reader_recognises_exactly_what_the_stub_writer_writes() -> None:
    """The two halves of the stub — written by `promotion_stub`, read back by the scan that
    decides a section is already promoted — are one convention held in two places, so a
    change to the block's shape that left the reader behind would silently make every
    promoted section look unpromoted and re-promotable."""
    assert ideas.stub_record_ids(ideas.promotion_stub("KYO-1", "2026-01-01T00:00:00.000Z")) == [
        "KYO-1"
    ]
    assert ideas.stub_record_ids(STUB_TEMPLATE.format(record_id="KYO-2", date="2026-01-01")) == [
        "KYO-2"
    ]
    assert ideas.stub_record_ids("> just an ordinary blockquote\n") == []


def test_a_hash_inside_a_code_fence_is_not_a_section() -> None:
    """``ideas/`` is full of fenced shell and Python, and every ``# comment`` in one starts
    a line with a hash. A scan without a fence to hide behind would offer them as sections
    and, worse, end the real section above at the first comment line in its own example."""
    text = "# Real\n\nProse.\n\n```sh\n# not a heading\nkyo doctor\n```\n\nMore prose.\n"
    assert [section.heading for section in ideas.sections(text)] == ["Real"]
    assert ideas.sections(text)[0].text.endswith("More prose.")


def test_a_yaml_comment_in_frontmatter_is_not_the_files_first_heading() -> None:
    """The defect the shared scan removed. ``first_heading`` used to search the whole file
    with a bare ``^# `` regex, so a human's ``# a comment of mine`` in their Obsidian
    properties block became the title of the record their idea was promoted into."""
    text = "---\ntags:\n  - inbox\n# a comment of mine\n---\n\n# The real title\n\nBody.\n"
    assert ideas.first_heading(text) == "The real title"


def test_a_hashtag_without_a_space_is_not_a_heading() -> None:
    """Obsidian's own tag syntax. ``#promoted`` at the start of a line is a tag, and
    CommonMark agrees it is not an ATX heading."""
    assert [section.heading for section in ideas.sections("#promoted\n\nBody.\n")] == []


def test_a_subsection_travels_with_the_section_it_belongs_to() -> None:
    """A section ends at the next heading of the same or higher level, which is what a
    human means by "this idea": promoting ``## An idea`` takes the ``###`` parts of that
    idea and stops at the next ``##``."""
    text = "## An idea\n\nOne.\n\n### A detail\n\nTwo.\n\n## Another\n\nThree.\n"
    first = ideas.sections_named(text, "An idea")[0]
    assert first.text == "One.\n\n### A detail\n\nTwo."


def test_a_failed_commit_after_a_section_stub_write_restores_the_whole_file(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """The rollback KYO-106 gave the prose, at section scale, now exercised over the
    KYO-228 default (the section is removed, not stubbed). What is at risk here is larger
    than what was promoted: the write replaces one region of a file that holds other
    people's unpromoted ideas, so a half-applied promotion would leave those sections in a
    file with one of them silently missing and no record to account for it."""
    from kyosei import commands

    idea = _write_idea(project, "two.md", TWO_IDEAS)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(commands, "_commit_and_export", boom)
    # KYO-421: reported by `cli.main` on one line, exit 1, rather than escaping.
    died = kyo(
        "promote", str(idea), "--section", "Ship the widget", "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert died.code == 1 and "RuntimeError: simulated commit failure" in died.err, died
    assert idea.read_text(encoding="utf-8") == TWO_IDEAS
    assert kyo("list", "--json").json["records"] == []
