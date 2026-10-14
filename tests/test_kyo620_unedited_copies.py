"""KYO-620.c4: the scrub of the shipped assets leaves a consumer's unedited copy upgradable.

KYO-620 rewrote text that consumers hold copies of. Two of those copies have installers
that tell an unedited copy from an edited one, and both must still recognise a copy that
matches the text as it stood before the scrub:

* ``kyosei/procedures.md``: `kyo install-procedures` refreshes a rule (and the preamble)
  only when its text is a wording an earlier build shipped, by digest
  (:data:`kyosei.procedures.SUPERSEDED_RULE_WORDINGS`, :data:`SUPERSEDED_PREAMBLES`). The
  digests of every pre-scrub wording are pinned below, so dropping one from the tables
  fails here even after the repository's history no longer holds the old text.
* The `/kyosei` skill's two files: `kyo install-skill` stamps each copy with the digest of
  what it wrote (KYO-604), so an unedited earlier copy is recognised whatever its text.

The registries (``kinds.yml``, ``documents.yml`` and the rest) have no such installer:
`kyo init` copies them once and nothing compares a project's copy with the default's text.
KYO-620 changed only their comments and one status summary, so the one comparison that does
exist, `kyo doctor`'s ``kind_grants_missing`` over ``accepts_from``, is unaffected; the
last test below holds that.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from kyosei import procedures
from kyosei.cmd_harness_migrate import _skill_copy_is_unedited
from kyosei.install_procedures import packaged_procedures_text
from kyosei.install_skill import (
    SKILL_ASSETS,
    installed_skill_text,
    packaged_skill_asset_text,
    skill_drift_by_tier,
    stamp_skill_text,
)

REPO = Path(__file__).resolve().parents[1]

#: The commit KYO-620's dispatch branched from: the shipped text before the scrub.
PRE_SCRUB_SHA = "3f175c17c2c8e2add6b7637443cba3794bfe447c"

#: Every packaged rule KYO-620 reworded, with the digest of its pre-scrub body
#: (:func:`kyosei.procedures.wording_digest` of :func:`~kyosei.procedures.rule_bodies`).
PRE_SCRUB_RULE_DIGESTS = {
    "Prose enters the store only via stdin or a file; the one-line text flags are the "
    "exception": "5dd866fae127ac7968e2f666930074d6993a56ab332b4974cc0aa1d1856cf4a2",
    "Never write to the structured store directly": (
        "a49caf2041098a9e7f0fcedc3615ef8e2ce2b82a40695c2e1bbd822b1fd412f9"
    ),
    "Never state a timestamp, line count, or other tool-derivable fact from memory": (
        "b42dc5828880de48e64f0deeae7fe9634f24dff8d5b8ae64da5f929d52a36982"
    ),
    "Write a long run to a file and read the file; never pipe a suite through `tail`": (
        "a297ace2754f9d9ace69d29230770deefed5bc9d9b321b7982c2a059a78298ee"
    ),
    "Never deploy, and never mutate a production system outside the declared "
    "`write_scope`": "676d7016fddc098ab5f012bbbad87d1cfb0e10d398b2be6ecf64f2161ad13f5f",
    "Run the tests that cover your change, not the whole suite": (
        "5aad2740f30442cf815c22711dc252ebeced516f579061beec47669aa66362d1"
    ),
    "Keep every foreground command under about four minutes": (
        "e8b1eaedad0c33d0a51a8001f86b7a5fdccef4b4e7dcf80174057f8121741f4a"
    ),
    "Read each governed section your branch raises, and propose its verdict in "
    "`docs_verdicts[]`": "159400722bb974d372205dfce12fffb81f3c284b077798b65de81c5d7963a226",
    "Review a report's `premise_false[]` and `disagreements[]` before its "
    "`criteria_verdicts`": "6bcaa7b18f8aafd7305601cc36f950f6de59983e2c987c986120a5dea9011b22",
    "Never claim absence from a phrase search; search one distinctive word and read the "
    "context": "7c609501b878510b38e6de3ce6b46bf3d034d9fc0135b7bd3d3f83371f3659c5",
    "Re-verify a record's premises immediately before every dispatch": (
        "c9074eea55a29288bd94a442b211d17836b44b01dfdb77ed76701e4537149cdd"
    ),
    "Declare the worker model in the store, not in your head": (
        "37e7ad6bc778c80e55316066a81246be1c222caf58ce8e8a3358d3f342d78743"
    ),
    "Run the full suite yourself, once, on the merged tree": (
        "947648a05d1e27d061324ecb44c4fc53022eb5025ed348aad6b9cc0488a841d8"
    ),
    "Group records into one dispatch when a worker would otherwise ingest the same "
    "context twice": "eba19cab73f18725fedfe532e6261683b45aff11096a0734deff81fc1ec7ae14",
    "Cite every record to the maintainer as a markdown link with a `kyosei://` target": (
        "24a85ecea8b4fc8797f8990f6b14e67b0cdcdf80100f80aec2ca4483dc5c8cc8"
    ),
}

#: The digest of the pre-scrub preamble (:func:`kyosei.procedures.preamble`).
PRE_SCRUB_PREAMBLE_DIGEST = "5abf686686d5cf5353cb4aa0493977ad7fe8175fbcf9ff6c890256242347418e"


def _pre_scrub_text(rel: str) -> str | None:
    """``rel`` as it stood before the scrub, or ``None`` where this checkout's history
    does not hold that commit (a shallow clone, or the published fresh history)."""
    result = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{PRE_SCRUB_SHA}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


# --------------------------------------------------------------- procedures.md


def test_every_reworded_rule_is_still_a_packaged_rule_under_the_same_title() -> None:
    """A renamed rule is out of reach of the tables by construction, so the scrub
    reworded bodies and kept every title."""
    titles = set(procedures.rule_bodies(packaged_procedures_text()))
    assert set(PRE_SCRUB_RULE_DIGESTS) <= titles


@pytest.mark.parametrize("title", sorted(PRE_SCRUB_RULE_DIGESTS))
def test_each_pre_scrub_rule_wording_is_in_the_superseded_table(title: str) -> None:
    assert PRE_SCRUB_RULE_DIGESTS[title] in procedures.SUPERSEDED_RULE_WORDINGS.get(
        title, frozenset()
    ), f"{title!r}: kyo install-procedures would treat the pre-KYO-620 wording as edited"


def test_the_pre_scrub_preamble_is_in_the_superseded_table() -> None:
    assert PRE_SCRUB_PREAMBLE_DIGEST in procedures.SUPERSEDED_PREAMBLES


def test_the_pinned_digests_are_the_pre_scrub_text_where_history_has_it() -> None:
    """The pins above are the digests of the real pre-scrub ledger, not of a guess."""
    old = _pre_scrub_text(procedures.PACKAGED_LEDGER_SOURCE_PATH)
    if old is None:
        pytest.skip(f"{PRE_SCRUB_SHA} is not in this checkout's history")
    bodies = procedures.rule_bodies(old)
    for title, digest in PRE_SCRUB_RULE_DIGESTS.items():
        assert procedures.wording_digest(bodies[title]) == digest, title
    assert procedures.wording_digest(procedures.preamble(old)) == PRE_SCRUB_PREAMBLE_DIGEST


def test_install_procedures_moves_an_unedited_pre_scrub_ledger_to_the_new_text(
    kyo, project: Path
) -> None:
    """The behaviour the digests buy: a project holding the pre-scrub ledger, untouched,
    ends a plain rerun on this build's text with nothing reported as a deliberate edit."""
    old = _pre_scrub_text(procedures.PACKAGED_LEDGER_SOURCE_PATH)
    if old is None:
        pytest.skip(f"{PRE_SCRUB_SHA} is not in this checkout's history")
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(old, encoding="utf-8")

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["differing"] == []
    refreshed = {item["title"] for item in result.json["refreshed"]}
    assert set(PRE_SCRUB_RULE_DIGESTS) <= refreshed

    upgraded = ledger.read_text(encoding="utf-8")
    packaged = packaged_procedures_text()
    assert procedures.rule_bodies(upgraded) == procedures.rule_bodies(packaged)
    assert procedures.preamble(upgraded) == procedures.preamble(packaged)


# --------------------------------------------------------------- the skill


def _install_copy(root: Path, asset, packaged_text: str) -> Path:
    target = root / asset.target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stamp_skill_text(installed_skill_text(packaged_text)), encoding="utf-8")
    return target


def _assert_recognised_as_unedited(root: Path, earlier: dict) -> None:
    problems, superseded = skill_drift_by_tier(root)
    assert problems == [], problems
    assert len(superseded) == len(SKILL_ASSETS), superseded
    for asset in SKILL_ASSETS:
        assert _skill_copy_is_unedited(root / asset.target, asset, root), asset.target
        assert earlier[asset.target] != packaged_skill_asset_text(asset)


def test_an_unedited_earlier_skill_copy_is_superseded_not_edited(tmp_path: Path) -> None:
    """The mechanism needs no table: any text `kyo install-skill` wrote and nobody
    edited is recognised, so a copy of the pre-scrub skill is too."""
    earlier = {
        asset.target: packaged_skill_asset_text(asset) + "\nA paragraph a later build dropped.\n"
        for asset in SKILL_ASSETS
    }
    for asset in SKILL_ASSETS:
        _install_copy(tmp_path, asset, earlier[asset.target])
    _assert_recognised_as_unedited(tmp_path, earlier)


def test_an_unedited_pre_scrub_skill_copy_is_superseded_not_edited(tmp_path: Path) -> None:
    earlier = {}
    for asset in SKILL_ASSETS:
        old = _pre_scrub_text(asset.source_path.as_posix())
        if old is None:
            pytest.skip(f"{PRE_SCRUB_SHA} is not in this checkout's history")
        earlier[asset.target] = old
        _install_copy(tmp_path, asset, old)
    _assert_recognised_as_unedited(tmp_path, earlier)


# --------------------------------------------------------------- the registries


@pytest.mark.parametrize(
    "name", ["kinds.yml", "scopes.yml", "gate-tags.yml", "bases.yml", "documents.yml"]
)
def test_the_scrub_left_each_registry_s_values_unchanged(name: str) -> None:
    """Only comments changed, so a project's copy compares exactly as it did."""
    old = _pre_scrub_text(f"src/kyosei/defaults/{name}")
    if old is None:
        pytest.skip(f"{PRE_SCRUB_SHA} is not in this checkout's history")
    new = (REPO / "src" / "kyosei" / "defaults" / name).read_text(encoding="utf-8")
    assert yaml.safe_load(new) == yaml.safe_load(old)
