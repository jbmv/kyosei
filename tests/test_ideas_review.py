"""``kyo ideas review`` — unpromoted ideas by age, with advisory broken-mention flags,
and ``kyo ideas migrate-stamps`` — the one-time move of the promotion stamp from a
trailing block to top-of-file YAML frontmatter (KYO-30).

Law 12/13: the ideas layer is never enforced against — everything ``review`` does is
*listed*, never fixed, never blocked. ``migrate-stamps`` is the single exception that
proves it: it is the only command that rewrites a file under ``ideas/``, it is a dry run
unless asked twice, and it is never run by a hook.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

#: The pre-KYO-30 stamp, exactly as `kyo promote` used to append it. Written out
#: literally rather than produced by the code under test: a migration test whose input
#: came from the current writer could never see the format it is supposed to migrate.
LEGACY_STAMP = "\n---\npromoted: KYO-9\npromoted_at: 2026-01-01T00:00:00.000Z\n---\n"


def _write_idea(project: Path, name: str, body: str) -> Path:
    path = project / "ideas" / name
    path.write_text(body, encoding="utf-8")
    return path


def _frontmatter(text: str) -> dict:
    """The file's YAML frontmatter, read by a real YAML parser rather than by the
    module under test — the point of the migration is that *Obsidian* can read it."""
    assert text.startswith("---\n"), text
    return yaml.safe_load(text.split("\n---\n")[0].removeprefix("---\n"))


def test_scratchpad_is_never_listed(kyo, project: Path) -> None:
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"] == []


def test_an_idea_file_is_listed(kyo, project: Path) -> None:
    _write_idea(project, "one.md", "# One\n\nA thought.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    paths = [item["path"] for item in result.json["ideas"]]
    assert paths == ["ideas/one.md"]


def test_a_promoted_idea_drops_out_of_the_listing(kyo, project: Path, write_file) -> None:
    idea = _write_idea(project, "one.md", "# One\n\nA thought.\n")
    kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"] == []


def test_ideas_without_a_git_repo_fall_back_to_mtime(kyo, project: Path) -> None:
    """Law 16: never trust a hand-typed date. A tmp project is not a git repo, so
    the tool must derive the timestamp from the filesystem, not fabricate one."""
    _write_idea(project, "one.md", "# One\n\nA thought.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    item = result.json["ideas"][0]
    assert item["last_activity_source"] == "mtime"
    assert item["age_days"] >= 0
    assert item["last_activity"].endswith("Z")


def test_ideas_are_sorted_oldest_first(kyo, project: Path) -> None:
    older = _write_idea(project, "older.md", "# Older\n")
    time.sleep(0.05)
    newer = _write_idea(project, "newer.md", "# Newer\n")
    now = time.time()
    os.utime(older, (now - 100000, now - 100000))
    os.utime(newer, (now - 10, now - 10))
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert [item["path"] for item in result.json["ideas"]] == ["ideas/older.md", "ideas/newer.md"]


def test_a_broken_backticked_path_mention_is_flagged(kyo, project: Path) -> None:
    _write_idea(
        project,
        "one.md",
        "# One\n\nSee `kyosei/kinds.yml` for the registry, and fix `src/ghost.py` too.\n",
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    item = result.json["ideas"][0]
    assert item["flags"] == ["src/ghost.py"]


def test_a_resolving_mention_is_not_flagged(kyo, project: Path) -> None:
    _write_idea(project, "one.md", "# One\n\nSee `kyosei/kinds.yml` for the registry.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_mention_of_a_directory_that_exists_is_not_flagged(kyo, project: Path) -> None:
    """KYO-33.c1: `kyo ideas review` used to test `is_file()`, so a backticked mention
    of a directory that is really there was reported as broken — on notation this
    project's own docs and briefs use constantly. An advisory purge list is only worth
    anything if a human trusts it, and routine false positives are what stop that."""
    _write_idea(
        project,
        "one.md",
        "# One\n\nExport lives in `kyosei/export/`, intake in `ideas/`,"
        " and `kyosei/export` names the same directory without the slash.\n",
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_mention_of_a_directory_that_does_not_exist_is_still_flagged(
    kyo, project: Path
) -> None:
    """KYO-33.c3, the other direction: `is_file()` -> `exists()` must not turn the
    check into one that flags nothing. A check that flags nothing is indistinguishable
    from one that finds nothing."""
    _write_idea(project, "one.md", "# One\n\nSee `docs/ghost/` for the rest.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == ["docs/ghost/"]


def test_a_mention_with_a_line_suffix_resolves_against_the_file(kyo, project: Path) -> None:
    """KYO-33.c2: `path.py:105` notation resolves against the file, the same way the
    `#symbol` suffix already did."""
    _write_idea(
        project,
        "one.md",
        "# One\n\nThe registry is `kyosei/kinds.yml:12`, and the column form"
        " `kyosei/kinds.yml:12:4` is the same file.\n",
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_symbol_suffix_still_resolves_against_the_file(kyo, project: Path) -> None:
    """The pre-existing `#symbol` behaviour the line suffix was modelled on — pinned
    so the KYO-33 rework of the resolution step cannot quietly drop it."""
    _write_idea(project, "one.md", "# One\n\nSee `kyosei/kinds.yml#work`.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_line_suffix_on_a_nonexistent_path_is_still_flagged(kyo, project: Path) -> None:
    """KYO-33.c3: stripping the line suffix must not become a way to silence a mention
    whose path is absent with or without it. Flagged under the token as written, so the
    human can find the text to purge."""
    _write_idea(project, "one.md", "# One\n\nBroken: `src/ghost.py:105`.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == ["src/ghost.py:105"]


def test_a_real_filename_ending_in_digits_after_a_colon_is_not_stripped(
    kyo, project: Path
) -> None:
    """KYO-33: the line-suffix strip could in principle eat the digits off a filename
    that legitimately ends in `:<digits>`. It cannot, because the literal path is tried
    first and only a *failure* falls back to the stripped form — a file that answers for
    itself is never reinterpreted as a line reference."""
    (project / "kyosei" / "snapshot:105").write_text("x", encoding="utf-8")
    _write_idea(project, "one.md", "# One\n\nSee `kyosei/snapshot:105`.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_broken_mention_that_hard_wrapped_is_still_flagged(kyo, project: Path) -> None:
    """KYO-66. The code-span pattern was ``` `([^`\\n]+)` ``` — a mention had to sit
    entirely on one physical line — so the longest mentions, which are the ones most
    likely to be real paths, were invisible to the scan. `kyo ideas review` then
    reported no broken mentions, which reads exactly like there being none. Ideas are
    prose and prose here is hard-wrapped, so this is the ordinary case, not the edge."""
    _write_idea(
        project,
        "one.md",
        "# One\n\nThe rule that matters lives in `docs/design/ghost.md#Law\n"
        "15` and nowhere else.\n",
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == ["docs/design/ghost.md#Law 15"], (
        "flagged under the flattened token: where it wrapped is not information the "
        "human purging it needs, and a raw line break in a flag is unreadable"
    )


def test_a_resolving_mention_that_hard_wrapped_is_not_flagged(kyo, project: Path) -> None:
    """The other direction, and the one that decides whether the list is trusted: a
    scan that can now see wrapped mentions must not start flagging the real ones."""
    _write_idea(
        project,
        "one.md",
        "# One\n\nThe registry that every kind is declared in is `kyosei/kinds.yml#work\n"
        "archetype`, as ever.\n",
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_mention_broken_across_a_blank_line_is_not_treated_as_one_token(
    kyo, project: Path
) -> None:
    """The bound on the widening. Exactly one non-blank continuation line is allowed,
    so an unbalanced backtick can reach the next line and no further — a code span
    that swallowed the rest of the file would flag paragraphs as paths."""
    _write_idea(
        project,
        "one.md",
        "# One\n\nAn unclosed `span here\n\nand a later one` closes it.\n",
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_a_backticked_non_path_token_is_not_flagged(kyo, project: Path) -> None:
    _write_idea(project, "one.md", "# One\n\nRun `pytest` to check.\n")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"][0]["flags"] == []


def test_human_text_output_shows_age_and_flags(kyo, project: Path) -> None:
    _write_idea(project, "one.md", "# One\n\nBroken: `src/ghost.py`.\n")
    result = kyo("ideas", "review")
    assert result.code == 0, result
    assert "ideas/one.md" in result.out
    assert "age:" in result.out
    assert "src/ghost.py" in result.out


def test_nested_idea_files_are_found(kyo, project: Path) -> None:
    nested_dir = project / "ideas" / "sub"
    nested_dir.mkdir()
    (nested_dir / "nested.md").write_text("# Nested\n", encoding="utf-8")
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert [item["path"] for item in result.json["ideas"]] == ["ideas/sub/nested.md"]


def test_ideas_review_is_empty_when_there_is_no_ideas_directory(tmp_path: Path) -> None:
    """`kyo ideas review` never opens the db (unlike `list`/`map`), so an explicit
    ``--root`` with no ``ideas/`` under it degrades to an empty listing rather than
    a runtime error — consistent with the ideas layer never being enforced against
    (law 12)."""
    from .conftest import invoke

    result = invoke(["--root", str(tmp_path), "ideas", "review", "--json"])
    assert result.code == 0, result
    assert result.json["ideas"] == []


# --------------------------------------------------- KYO-30: the frontmatter stamp


def test_a_frontmatter_stamped_idea_drops_out_of_the_listing(kyo, project: Path) -> None:
    """KYO-30.c4: review reads the stamp from top-of-file frontmatter. Written by hand
    here rather than via `kyo promote`, so this fails if the reader silently kept
    depending on the trailing block the writer no longer produces."""
    _write_idea(
        project,
        "one.md",
        '---\npromoted:\n  - KYO-9\npromoted_at:\n  - "2026-01-01T00:00:00.000Z"\n---\n\n# One\n',
    )
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"] == []
    assert result.json["legacy_stamped"] == []


def test_a_bottom_stamped_idea_is_held_back_and_named_for_migration(
    kyo, project: Path
) -> None:
    """It must not reappear as unpromoted (that would offer it for a second promotion),
    and it must not pass silently either — a stamp Obsidian cannot see is the defect
    KYO-30 exists to fix, so review says so and names the remedy (law 6)."""
    _write_idea(project, "one.md", "# One\n\nA thought.\n" + LEGACY_STAMP)
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"] == []
    assert result.json["legacy_stamped"] == ["ideas/one.md"]
    assert "kyo ideas migrate-stamps" in kyo("ideas", "review").out


def test_migrate_stamps_writes_nothing_without_apply(kyo, project: Path) -> None:
    """Law 11, dry-run by default. ``ideas/`` is the human's own directory and the
    least defended surface in the tool; the list comes before the edit."""
    body = "# One\n\nA thought.\n"
    idea = _write_idea(project, "one.md", body + LEGACY_STAMP)
    result = kyo("ideas", "migrate-stamps", "--json")
    assert result.code == 0, result
    assert result.json["applied"] is False
    assert result.json["count"] == 1
    assert result.json["files"][0]["path"] == "ideas/one.md"
    assert result.json["files"][0]["promoted"] == ["KYO-9"]
    assert idea.read_text(encoding="utf-8") == body + LEGACY_STAMP


def test_migrate_stamps_apply_moves_the_stamp_to_frontmatter(kyo, project: Path) -> None:
    """KYO-30.c4. The record id and its timestamp survive the move — a migration that
    lost the timestamp would quietly rewrite history the tool stamped (law 16)."""
    idea = _write_idea(project, "one.md", "# One\n\nA thought.\n" + LEGACY_STAMP)
    result = kyo("ideas", "migrate-stamps", "--apply", "--json")
    assert result.code == 0, result
    assert result.json["applied"] is True
    assert result.json["count"] == 1

    text = idea.read_text(encoding="utf-8")
    front = _frontmatter(text)
    assert front["promoted"] == ["KYO-9"]
    assert front["promoted_at"] == ["2026-01-01T00:00:00.000Z"]
    assert text.endswith("# One\n\nA thought.\n")
    assert "promoted: KYO-9" not in text


def test_migrating_twice_is_a_no_op(kyo, project: Path) -> None:
    """KYO-30.c5, half one. Idempotence is structural, not bookkept: a migrated file has
    no trailing block left to find, so the second run reports zero without consulting
    any record of the first — which is what makes it safe to re-run on a directory that
    is half migrated, or migrated by someone else, or restored from a backup."""
    idea = _write_idea(project, "one.md", "# One\n\nA thought.\n" + LEGACY_STAMP)
    kyo("ideas", "migrate-stamps", "--apply")
    after_first = idea.read_text(encoding="utf-8")

    result = kyo("ideas", "migrate-stamps", "--apply", "--json")
    assert result.code == 0, result
    assert result.json["count"] == 0
    assert result.json["files"] == []
    assert idea.read_text(encoding="utf-8") == after_first
    assert _frontmatter(after_first)["promoted"] == ["KYO-9"]


def test_migration_does_not_corrupt_a_file_a_human_has_edited_around(
    kyo, project: Path
) -> None:
    """KYO-30.c5, half two. The realistic file: Obsidian properties the human set, a
    YAML comment, a wikilink, a code fence containing a line that looks like a stamp,
    and prose added *after* the promotion. Everything except the trailing block must
    come back byte-for-byte — a migration that mangles someone's notes is worse than no
    migration, and ``ideas/`` has no other defence (law 12: never validated).
    """
    body = (
        "---\n"
        "tags:\n"
        "  - inbox\n"
        "# my own note to self\n"
        "aliases: [One, Uno]\n"
        "status: thinking\n"
        "---\n"
        "\n"
        "# One\n"
        "\n"
        "A thought, with a [[wikilink]] and a `path/like.md` mention.\n"
        "\n"
        "```\n"
        "promoted: NOT-A-STAMP\n"
        "```\n"
        "\n"
        "Something I wrote after it was promoted.\n"
    )
    idea = _write_idea(project, "one.md", body + LEGACY_STAMP)
    result = kyo("ideas", "migrate-stamps", "--apply", "--json")
    assert result.code == 0, result
    assert result.json["count"] == 1

    text = idea.read_text(encoding="utf-8")
    front = _frontmatter(text)
    assert front["tags"] == ["inbox"]
    assert front["aliases"] == ["One", "Uno"]
    assert front["status"] == "thinking"
    assert front["promoted"] == ["KYO-9"]
    assert front["promoted_at"] == ["2026-01-01T00:00:00.000Z"]
    assert "# my own note to self" in text
    assert text.endswith(
        "\n# One\n"
        "\n"
        "A thought, with a [[wikilink]] and a `path/like.md` mention.\n"
        "\n"
        "```\n"
        "promoted: NOT-A-STAMP\n"
        "```\n"
        "\n"
        "Something I wrote after it was promoted.\n"
    )


def test_an_unstamped_idea_is_never_touched_by_the_migration(kyo, project: Path) -> None:
    """The blast radius is exactly the bottom-stamped files. An unpromoted idea has no
    stamp to move, and acquiring an empty ``promoted:`` property would make it look
    promoted to an Obsidian Base query over the ideas directory."""
    body = "# One\n\nA thought.\n"
    idea = _write_idea(project, "one.md", body)
    result = kyo("ideas", "migrate-stamps", "--apply", "--json")
    assert result.code == 0, result
    assert result.json["count"] == 0
    assert idea.read_text(encoding="utf-8") == body
    assert kyo("ideas", "review", "--json").json["ideas"][0]["path"] == "ideas/one.md"


# ------------------------------ KYO-106: the stub is written forward, never backward
#
#: A file promoted under v1: stamped in frontmatter, and still holding the whole text the
#: record now also holds. Modelled on files promoted under v1, and written out
#: literally rather than produced by `kyo promote`, which no longer makes this shape at all
#: — a fixture built by the current writer could not represent the thing being protected.
LEGACY_STAMPED = (
    "---\n"
    "promoted:\n"
    "  - KYO-102\n"
    'promoted_at:\n  - "2026-08-27T13:40:35.070Z"\n'
    "---\n"
    "\n"
    "# Gates gain a tag registry; the payload stops being search-proof free text\n"
    "\n"
    "KYO-72 step 1, one of three records.\n"
    "\n"
    "## What this task builds\n"
    "\n"
    "- **A per-project gate-tag registry**; an unknown tag is refused at write time,\n"
    "  loudly, naming the registry (law 6).\n"
    "- **Gates stay orthogonal to status.** No `gated` status appears anywhere.\n"
)


def test_a_file_stamped_before_the_stub_is_never_rewritten(kyo, project: Path, write_file) -> None:
    """KYO-106.c3, and the boundary the record draws in as many words: "no retroactive
    rewriting". Files promoted under v1 that still hold their text are v1's cost, not a
    mess a later run tidies — a promotion that stubbed old files would rewrite a human's
    vault on the strength of a stamp somebody else's tool may have written.

    The surface is exactly two commands: `kyo promote` and `kyo ideas migrate-stamps
    --apply` are the only things in the package that write under ``ideas/`` at all (see
    ``kyosei/ideas.py``'s module docstring), so running both over this file, plus the read
    surface that walks the same directory, is the whole of "any command".
    """
    legacy = _write_idea(project, "legacy.md", LEGACY_STAMPED)
    other = _write_idea(project, "fresh.md", "# Fresh\n\nAn unpromoted thought.\n")

    assert kyo("ideas", "review", "--json").code == 0
    assert kyo("ideas", "migrate-stamps", "--apply", "--json").code == 0
    refused = kyo(
        "promote", str(legacy), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert refused.code == 1
    assert "KYO-102" in refused.err
    promoted = kyo(
        "promote", str(other), "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "x\n"),
        "--touches-file", write_file("t2.txt", "kyosei/kinds.yml\n"),
        "--json",
    )
    assert promoted.code == 0, promoted

    assert legacy.read_text(encoding="utf-8") == LEGACY_STAMPED, (
        "the file kept its stamp and its full text, byte for byte"
    )


def test_the_new_behaviour_applies_at_the_moment_of_promoting_and_only_then(
    kyo, project: Path
) -> None:
    """The other direction of KYO-106.c3, which the equality above cannot show: the legacy
    file is not left alone because nothing *looked* at it. Review reads its stamp — it is
    held out of the unpromoted listing and out of the migration backlog — and still does not
    touch its body. Recognised and left alone is the whole of the boundary; unnoticed would
    be an accident that a future reader is free to "fix"."""
    _write_idea(project, "legacy.md", LEGACY_STAMPED)
    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"] == []
    assert result.json["legacy_stamped"] == []


def test_a_stubbed_file_drops_out_of_the_review_listing(kyo, project: Path, write_file) -> None:
    """KYO-106.c4 against the read surface `kyo promote` shares its stamp reader with: the
    file a promotion leaves behind is one ``review`` recognises as promoted, so it is not
    offered for a second promotion — and it is not reported as needing the KYO-30 migration
    either, which a body-shaped stub could plausibly have tripped.

    ``--keep``: KYO-228 made this the opt-in path. The default (no stub, no stamp) drops
    the file out of the listing for the blunter reason that it is gone —
    ``test_a_promoted_idea_drops_out_of_the_listing`` above already covers that, since it
    promotes with no ``--keep``.
    """
    idea = _write_idea(project, "one.md", "# One\n\nA thought.\n")
    kyo(
        "promote", str(idea), "--kind", "chore", "--keep",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    text = idea.read_text(encoding="utf-8")
    assert "A thought." not in text
    assert "Text now lives in the record." in text

    result = kyo("ideas", "review", "--json")
    assert result.code == 0, result
    assert result.json["ideas"] == []
    assert result.json["legacy_stamped"] == []


def test_a_promoted_file_migrates_and_is_then_still_refused_promotion(
    kyo, project: Path, write_file
) -> None:
    """The two halves of KYO-30 meeting: after the migration the refusal still fires,
    reading the stamp from its new home. This is the regression that would ship a
    duplicate record for every idea in a consumer's vault."""
    idea = _write_idea(project, "one.md", "# One\n\nA thought.\n" + LEGACY_STAMP)
    kyo("ideas", "migrate-stamps", "--apply")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 1
    assert "already carries a promotion stamp" in result.err
    assert "KYO-9" in result.err


# ------------------- KYO-107: what section promotion does to the unpromoted listing


def test_a_partly_promoted_file_drops_out_of_the_listing_like_any_stamped_file(
    kyo, project: Path, write_file
) -> None:
    """The consequence of section promotion for this surface, pinned rather than papered
    over. ``review`` asks one question of a file — does it carry a stamp — and a file with
    one promoted section carries one, so it leaves the "unpromoted ideas" listing even
    though it still holds ideas nobody has promoted.

    That is deliberate here, and it is not a judgment this record was free to reverse: the
    same rule is what keeps KYO-106's ``LEGACY_STAMPED`` fixture out of the listing, and
    ``test_the_new_behaviour_applies_at_the_moment_of_promoting_and_only_then`` asserts
    exactly that about a stamped file that still holds its whole text under headings. A
    per-section listing would put every v1-stamped file in a consumer's vault back into the
    review queue, which is the retroactive rewriting KYO-106.c3 rules out one surface over.
    So: recognised, listed as promoted, and the remaining sections are named by `kyo
    promote` itself at the moment it writes the stub (see ``cmd_promote``'s write-echo)
    rather than by this listing.

    ``--keep``: KYO-228 made the stub (and so this whole mechanism) opt-in.
    ``test_a_default_section_promotion_leaves_the_file_in_the_unpromoted_listing`` below
    covers the now-default behaviour, which is the opposite: no stamp, no drop-out.
    """
    idea = _write_idea(
        project,
        "two.md",
        "# Two ideas\n\n## First\n\nPromote me.\n\n## Second\n\nNot yet.\n",
    )
    result = kyo(
        "promote", str(idea), "--section", "First", "--kind", "chore", "--keep",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 0, result
    assert "1 section(s) still unpromoted: 'Second'" in result.out

    assert "Not yet." in idea.read_text(encoding="utf-8")
    review = kyo("ideas", "review", "--json")
    assert review.code == 0, review
    assert review.json["ideas"] == []
    assert review.json["legacy_stamped"] == []


def test_a_default_section_promotion_leaves_the_file_in_the_unpromoted_listing(
    kyo, project: Path, write_file
) -> None:
    """KYO-228: the default writes no stamp, so ``review`` -- which asks only "does this
    file carry a stamp" -- has nothing to recognise. The file, still holding "Second",
    correctly stays in the unpromoted listing rather than review depending on a stub
    (or a stamp) that promotion no longer leaves behind.
    """
    idea = _write_idea(
        project,
        "two.md",
        "# Two ideas\n\n## First\n\nPromote me.\n\n## Second\n\nNot yet.\n",
    )
    result = kyo(
        "promote", str(idea), "--section", "First", "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 0, result
    assert "Promote me." not in idea.read_text(encoding="utf-8")
    assert "Not yet." in idea.read_text(encoding="utf-8")

    review = kyo("ideas", "review", "--json")
    assert review.code == 0, review
    assert [item["path"] for item in review.json["ideas"]] == ["ideas/two.md"]
    assert review.json["legacy_stamped"] == []


def test_the_migration_leaves_a_file_holding_section_stubs_alone(kyo, project: Path) -> None:
    """KYO-106.c3's no-retroactivity rule, checked against the shape KYO-107 introduces:
    the stub is a blockquote in the body, and `kyo ideas migrate-stamps` looks for a
    trailing ``---`` block. A file whose sections are stubbed has nothing to migrate, and a
    scan that mistook one for a legacy stamp would rewrite a human's vault."""
    _write_idea(
        project,
        "two.md",
        "---\npromoted:\n  - KYO-9\npromoted_at:\n  - \"2026-01-01T00:00:00.000Z\"\n---\n"
        "\n# Two ideas\n\n## First\n\n> **Promoted -> KYO-9** · 2026-01-01 · #promoted #KYO-9\n"
        "> Text now lives in the record.\n\n## Second\n\nNot yet.\n",
    )
    result = kyo("ideas", "migrate-stamps", "--json")
    assert result.code == 0, result
    assert result.json["count"] == 0


# ------------------------------------------------------------------------ KYO-109
# The frontmatter block, read as properties. Obsidian Bases query frontmatter
# properties, and KYO-30 moved the stamp into that block for exactly this reason. What a
# Base over ideas can select on is what comes back here.


def test_a_file_with_no_frontmatter_has_no_properties_and_is_not_an_error(
    project: Path,
) -> None:
    """Law 13: the pool has zero required ceremony, so the shape most of an ideas tree
    is in has to be a legitimate answer rather than a parse failure."""
    from kyosei.ideas import frontmatter_properties

    assert frontmatter_properties("# Just a heading\n\nand prose\n") == {}
    assert frontmatter_properties("") == {}
    # An opening fence that is never closed is a horizontal rule or an unfinished edit,
    # which `split_frontmatter` already refuses to read as a property block.
    assert frontmatter_properties("---\nauthor: ann\n\n# no closing fence\n") == {}


def test_every_frontmatter_key_is_a_property_including_the_stamp(project: Path) -> None:
    """No key list anywhere decides which properties are legitimate — the human's own
    keys and the two the promotion stamp owns come back the same way."""
    from kyosei.ideas import frontmatter_properties

    properties = frontmatter_properties(
        "---\n"
        "author: ann\n"
        "area: the query layer\n"
        "tags: [design, v2]\n"
        "promoted:\n  - KYO-9\n"
        'promoted_at:\n  - "2026-01-01T00:00:00.000Z"\n'
        "empty:\n"
        "---\n\n# An idea\n\nprose\n"
    )
    assert properties == {
        "author": "ann",
        "area": "the query layer",
        "tags": ["design", "v2"],
        "promoted": ["KYO-9"],
        "promoted_at": ["2026-01-01T00:00:00.000Z"],
        # A key declared with no value is a real state (Obsidian writes it for an empty
        # property) and a different fact from the key being absent.
        "empty": None,
    }


def test_a_list_property_reads_as_a_list_in_both_shapes_a_human_may_write(
    project: Path,
) -> None:
    """The tool writes block lists and understands three shapes (``_values_in``); this
    is that guarantee at the property level, where a query depends on it — ``tags`` must
    not be a string in one file and a list in the next because of how it was typed."""
    from kyosei.ideas import frontmatter_properties

    block = frontmatter_properties("---\ntags:\n  - design\n  - v2\n---\n\nprose\n")
    flow = frontmatter_properties("---\ntags: [design, v2]\n---\n\nprose\n")
    assert block == flow == {"tags": ["design", "v2"]}
    # And a single scalar stays a scalar: `author: ann` is not a one-item list.
    assert frontmatter_properties("---\nauthor: ann\n---\n\nprose\n") == {
        "author": "ann"
    }


def test_reading_properties_never_rewrites_the_file(kyo, project: Path) -> None:
    """Law 12 in the one place a read could quietly stop honouring it. The reader takes
    text and returns a mapping; this pins that the command surface built on it leaves
    the human's directory byte-identical."""
    path = _write_idea(project, "one.md", "---\nauthor: ann\n---\n\n# One\n\nprose\n")
    before = path.read_bytes()
    assert kyo("ideas", "review", "--json").code == 0
    assert path.read_bytes() == before
