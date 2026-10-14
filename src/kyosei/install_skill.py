"""``kyo install-skill``: the packaged ``/kyosei`` skill, its drift check, and the installed
build's lag behind a source checkout (KYO-15, KYO-309, KYO-328).
"""

from __future__ import annotations

from pathlib import Path

from .errors import KyoseiError
from .install_hooks import hook_fragments_behind
from .install_procedures import packaged_procedures_text
from .scaffold import (
    HOOKS_SOURCE_SUBDIR,
    PACKAGED_ASSET_SOURCE_DIR,
    PROCEDURES_ASSET,
    Asset,
    _reinstall_route,
    _source_repo_refusal,
    repo_asset_source,
)

#: KYO-15: the `/kyosei` controller workflow, installed alongside the hooks.
#:
#: Installed like the hooks because a project with the enforcement and not the workflow
#: has rules and no instructions; `kyo doctor`'s `skill` check is the other half
#: (docs/design/scaffold.md#kyo-15).
#:
#: KYO-593: the packaged file is the Claude Code plugin's own ``SKILL.md`` (moved there
#: from ``defaults/skill.md``), so the plugin and this command read one copy.
SKILL_PACKAGE_DIR = "harness/claude-code/plugin/skills/kyosei"
SKILL_ASSET = Asset("SKILL.md", ".claude/skills/kyosei/SKILL.md", SKILL_PACKAGE_DIR)

#: KYO-328: the skill's second tier — the incident histories the workflow used to
#: carry inline, moved to a file a controller reads on demand.
#:
#: A FILE beside the skill, not a command that prints it: it reuses the whole
#: drift/refusal machinery for one tuple entry, and it can be read one section at a time
#: instead of landing whole in the context the split exists to keep it out of
#: (docs/design/scaffold.md#kyo-328).
SKILL_REFERENCE_ASSET = Asset(
    "REFERENCE.md", ".claude/skills/kyosei/REFERENCE.md", SKILL_PACKAGE_DIR
)

#: Both halves of the `/kyosei` skill, in the order `kyo install-skill` writes them.
#: Every check and every install in this module iterates this rather than naming
#: ``SKILL_ASSET`` alone, so a third tier would join by being added here (law 3).
SKILL_ASSETS: tuple[Asset, ...] = (SKILL_ASSET, SKILL_REFERENCE_ASSET)

#: KYO-593.c3: how the packaged skill names the directory it sits in. Claude Code
#: substitutes it in a skill's text with the skill's own directory, which is the only
#: spelling true inside the plugin, where no ``.claude/skills/kyosei/`` exists. A copy
#: `kyo install-skill` writes is read by other harnesses and by people as plain text, so
#: the copy names its real directory instead (:func:`installed_skill_text`).
SKILL_DIR_TOKEN = "${CLAUDE_SKILL_DIR}"

#: The directory every copy `kyo install-skill` writes lands in; what
#: :data:`SKILL_DIR_TOKEN` becomes in those copies.
INSTALLED_SKILL_DIR = ".claude/skills/kyosei"


def installed_skill_text(packaged: str) -> str:
    """The text `kyo install-skill` writes for a packaged skill file (KYO-593.c3).

    Only ``${CLAUDE_SKILL_DIR}/`` followed by a path is rewritten. The packaged text
    never spells :data:`INSTALLED_SKILL_DIR` itself (a test holds that), so
    :func:`packaged_skill_text_from_installed` inverts this exactly.
    """
    return packaged.replace(SKILL_DIR_TOKEN + "/", INSTALLED_SKILL_DIR + "/")


def packaged_skill_text_from_installed(installed: str) -> str:
    """:func:`installed_skill_text` inverted: the packaged text an installed copy came
    from, for the source repository's mirror remedy."""
    return installed.replace(INSTALLED_SKILL_DIR + "/", SKILL_DIR_TOKEN + "/")


#: The two `sed` programs the mirror remedies print, one per direction. Basic regular
#: expressions: ``\$`` is a literal dollar, braces are literal, and the same text runs
#: under BSD and GNU sed.
_SED_TO_INSTALLED = (
    "sed 's|\\${CLAUDE_SKILL_DIR}/|" + INSTALLED_SKILL_DIR + "/|g'"
)
_SED_TO_PACKAGED = (
    "sed 's|" + INSTALLED_SKILL_DIR.replace(".", "\\.") + "/|${CLAUDE_SKILL_DIR}/|g'"
)


def _copy_command(source: Path, target: Path, *, to_installed: bool) -> str:
    """One shell line that writes ``target`` from ``source`` across the
    packaged/installed boundary (KYO-593.c3). A ``sed`` for both files, though only
    ``SKILL.md`` names its directory today: a ``cp`` that is right for one file and
    wrong for the other is the remedy a reader would copy to the wrong one."""
    program = _SED_TO_INSTALLED if to_installed else _SED_TO_PACKAGED
    return f"{program} {source} > {target}"

#: KYO-508: the marked section `kyo install-skill` keeps in the project's CLAUDE.md.
#: Written when the skill's description said it loads "whenever the project's CLAUDE.md
#: says this project uses Kyosei". KYO-593.c4 moved that trigger to the session-start
#: brief, which the plugin's hooks print with no CLAUDE.md at all; this section stays
#: for the copies mode, and `kyo harness migrate` removes it when a project moves to the
#: plugin (KYO-595, :mod:`kyosei.cmd_harness_migrate`).
CLAUDE_MD = "CLAUDE.md"
CLAUDE_MD_BEGIN = "<!-- kyosei:begin (managed by `kyo install-skill`; edit outside only) -->"
CLAUDE_MD_END = "<!-- kyosei:end -->"
CLAUDE_MD_BODY = (
    "This project uses Kyosei.\n"
    "\n"
    "The orchestrator's guide is the `kyosei` skill (`.claude/skills/kyosei/SKILL.md`); "
    "load it before\n"
    "driving the board.\n"
    "Dispatched workers never write under `kyosei/` or `ideas/`; `kyo check-branch` "
    "enforces it."
)
CLAUDE_MD_SECTION = f"{CLAUDE_MD_BEGIN}\n{CLAUDE_MD_BODY}\n{CLAUDE_MD_END}"

#: KYO-558: what `kyo init` writes between the same markers when the file has no Kyosei
#: section yet. A session opened in a project that `kyo init` made, and nothing else set
#: up, is told it uses Kyosei and where setup continues -- the hint the report behind
#: KYO-558 found missing. Inside the same markers, so the copies (`kyo install-skill`)
#: replace it with :data:`CLAUDE_MD_BODY` like any older section, and `kyo doctor`'s
#: :func:`claude_md_drift` stays silent on it while no skill is installed.
#:
#: KYO-613: it names both routes. On the plugin, `kyo harness install claude-code`
#: removes it once it has enabled the plugin (:func:`claude_md_without_init_section`);
#: on the copies, `kyo install-hooks --copies` or `kyo install-skill --copies` replaces
#: it. Before KYO-613 it named `kyo install-skill` alone, which the plugin route refuses.
CLAUDE_MD_SETUP_INCOMPLETE_BODY = (
    "This project uses Kyosei, and its setup is incomplete: `kyo init` made the store,\n"
    "but the `kyosei` skill and the hooks are not installed yet.\n"
    "\n"
    "Next step: run `kyo onboard --check`, which lists what is missing and the command\n"
    "that fixes each; `kyo onboard` is the guided setup. `kyo harness install claude-code`\n"
    "removes this section when it enables the Kyosei plugin; on the tracked copies,\n"
    "`kyo install-hooks --copies` (or `kyo install-skill --copies`) replaces it."
)
CLAUDE_MD_SETUP_INCOMPLETE_SECTION = (
    f"{CLAUDE_MD_BEGIN}\n{CLAUDE_MD_SETUP_INCOMPLETE_BODY}\n{CLAUDE_MD_END}"
)

#: KYO-613: the setup-incomplete bodies earlier builds wrote, still `kyo init`'s section
#: wherever a project carries one: KYO-558's, which named `kyo install-skill` alone.
SUPERSEDED_SETUP_INCOMPLETE_BODIES = (
    "This project uses Kyosei, and its setup is incomplete: `kyo init` made the store,\n"
    "but the `kyosei` skill and the hooks are not installed yet.\n"
    "\n"
    "Next step: run `kyo onboard --check`, which lists what is missing and the command\n"
    "that fixes each; `kyo onboard` is the guided setup. `kyo install-skill` replaces\n"
    "this section.",
)
SETUP_INCOMPLETE_SECTIONS = tuple(
    f"{CLAUDE_MD_BEGIN}\n{body}\n{CLAUDE_MD_END}"
    for body in (CLAUDE_MD_SETUP_INCOMPLETE_BODY, *SUPERSEDED_SETUP_INCOMPLETE_BODIES)
)


def claude_md_with_section(existing: str | None, *, section: str = CLAUDE_MD_SECTION) -> str:
    """``existing`` CLAUDE.md text with the Kyosei section current (KYO-508).

    Only the text from the begin marker through the end marker is ever replaced; every
    byte outside is returned as given. With no section the block is appended after a
    blank line; with no file the file is just the block. ``section`` is the whole
    marked block to put there: `kyo install-skill`'s by default, `kyo init`'s
    :data:`CLAUDE_MD_SETUP_INCOMPLETE_SECTION` from :func:`claude_md_for_init`.
    """
    if existing is None:
        return section + "\n"
    begin = existing.find(CLAUDE_MD_BEGIN)
    if begin != -1:
        end = existing.find(CLAUDE_MD_END, begin)
        if end != -1:
            return existing[:begin] + section + existing[end + len(CLAUDE_MD_END) :]
    if not existing.strip():
        return section + "\n"
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + section + "\n"


def _init_section_span(text: str) -> tuple[int, int] | None:
    """Where ``text``'s marked section is, when that section is one `kyo init` wrote
    (this build's or an earlier build's, :data:`SETUP_INCOMPLETE_SECTIONS`), else
    ``None``. Only the first marked section counts: it is the one every writer replaces."""
    begin = text.find(CLAUDE_MD_BEGIN)
    if begin == -1:
        return None
    end = text.find(CLAUDE_MD_END, begin)
    if end == -1:
        return None
    end += len(CLAUDE_MD_END)
    return (begin, end) if text[begin:end] in SETUP_INCOMPLETE_SECTIONS else None


def claude_md_carries_init_section(text: str | None) -> bool:
    """Whether ``text`` carries `kyo init`'s setup-incomplete section (KYO-558), in this
    build's wording or an earlier one's (KYO-613)."""
    return text is not None and _init_section_span(text) is not None


def claude_md_without_init_section(code_root: Path) -> str | None:
    """KYO-613: remove `kyo init`'s setup-incomplete section from ``code_root``'s
    CLAUDE.md (and the file itself when the section was all it held). Returns what was
    done, or ``None`` when the file carries no such section.

    Only a section whose text is exactly one `kyo init` wrote is removed: the copies'
    section, or one somebody edited, is theirs and stays. Every byte outside the
    markers is kept (:func:`kyosei.cmd_harness_migrate.claude_md_without_section`).
    """
    from .cmd_harness_migrate import claude_md_without_section

    path = code_root / CLAUDE_MD
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if _init_section_span(text) is None:
        return None
    remaining = claude_md_without_section(text)
    if remaining is None:  # pragma: no cover -- the span above found both markers
        return None
    if remaining:
        path.write_text(remaining, encoding="utf-8")
        return f"removed the setup-incomplete section `kyo init` wrote into {path}"
    path.unlink()
    return (
        f"removed {path}, which held only the setup-incomplete section `kyo init` wrote"
    )


def claude_md_for_init(code_root: Path) -> Path | None:
    """KYO-558.c4: give ``code_root``'s CLAUDE.md `kyo init`'s setup-incomplete section,
    when it has no Kyosei section at all. Returns the path written, or ``None``.

    Never replaces a marked section that is already there: a second checkout of a
    project whose CLAUDE.md carries `kyo install-skill`'s section must keep it, and a
    section `kyo init` wrote before is already the one this would write.

    KYO-634: nor is anything written where the Kyosei plugin is already enabled, since
    the section's advice (setup incomplete, skill and hooks not installed) would be
    false there, and `kyo harness install claude-code` exists to remove it (KYO-613).
    "Enabled" is :func:`kyosei.cmd_harness_migrate.plugin_enabled`, the predicate the
    harness commands already use, so the commands cannot disagree about it.
    """
    from .cmd_harness_migrate import plugin_enabled

    if plugin_enabled(code_root):
        return None
    path = code_root / CLAUDE_MD
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current is not None and CLAUDE_MD_BEGIN in current:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        claude_md_with_section(current, section=CLAUDE_MD_SETUP_INCOMPLETE_SECTION),
        encoding="utf-8",
    )
    return path


def claude_md_drift(root: Path) -> list[str]:
    """Is the Kyosei section in CLAUDE.md missing or older than this build's? (KYO-508)

    Silent when no skill is installed (a project driving `kyo` by hand needs no line),
    and in the repository the package is built from, where `kyo install-skill` is
    refused and so cannot be the remedy.
    """
    if not any((root / a.target).is_file() for a in SKILL_ASSETS):
        return []
    if any(repo_asset_source(root, a) is not None for a in SKILL_ASSETS):
        return []
    path = root / CLAUDE_MD
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    if text is not None and claude_md_with_section(text) == text:
        return []
    what = "has no" if text is None or CLAUDE_MD_BEGIN not in text else "carries an older"
    where = f"{CLAUDE_MD} is absent, so it has no" if text is None else f"{CLAUDE_MD} {what}"
    return [
        f"{where} Kyosei section — the /kyosei skill is installed, but nothing tells a "
        "session this project uses Kyosei, so it may never load. Run `kyo install-skill`, "
        "which writes only the marked section"
    ]


def packaged_skill_asset_text(asset: Asset) -> str:
    """One packaged `/kyosei` file's text, or raise if this build ships none.

    KYO-328: the skill is two files, and every caller that used to read the one
    reads whichever of :data:`SKILL_ASSETS` it is working on. The error names the
    missing file rather than "the skill", because a build that shipped SKILL.md and
    not REFERENCE.md is a real and confusing state to land in.
    """
    path = asset.packaged_path()
    if not path.is_file():
        raise KyoseiError(f"packaged {asset.source} is missing from {path}; reinstall kyosei")
    return path.read_text(encoding="utf-8")


def packaged_skill_text() -> str:
    """The packaged ``/kyosei`` workflow's text (``SKILL.md``'s half)."""
    return packaged_skill_asset_text(SKILL_ASSET)


def packaged_skill_reference_text() -> str:
    """The packaged ``/kyosei`` reference's text (``REFERENCE.md``'s half, KYO-328)."""
    return packaged_skill_asset_text(SKILL_REFERENCE_ASSET)


def skill_drift(root: Path) -> list[str]:
    """Whether this project's ``/kyosei`` skill matches its source (KYO-15, KYO-309).

    Silent when the project has no skill installed — a project driving `kyo` by
    hand does not need one. Reports, never rewrites: the same posture as
    ``hook_drift``, and for the same reason a consumer gave us directly, that a
    check which TELLS is one a consumer session can act on without a peer
    reaching into their configuration.

    *Which* copy is the source depends on the side of the package the project is on.
    For a **consumer** the packaged copy is the source and a difference means `kyo
    install-skill`. For the **repository the package is built from** (it carries
    :data:`PACKAGED_ASSET_SOURCE_DIR`) the direction is reversed: the comparison is
    against that repository's own source, a mirror up to one path substitution (KYO-593.c3),
    and the build's age is
    :func:`installed_build_lag`'s notice -- never a remedy that would overwrite the newer
    skill with the older one (docs/design/scaffold.md#kyo-309).

    KYO-328: the skill is two files and both are checked, on identical terms. A
    project that has the workflow and not the reference is drifted — the workflow
    sends a reader to REFERENCE.md by name in every section, so a missing half is a
    document full of dead pointers, which is the KYO-149 failure in a new place.
    """
    findings: list[str] = []
    for asset in SKILL_ASSETS:
        findings += _one_skill_asset_drift(root, asset)
    return findings


def _one_skill_asset_drift(root: Path, asset: Asset) -> list[str]:
    """:func:`skill_drift` for one of :data:`SKILL_ASSETS`."""
    problems, superseded = _one_skill_asset_drift_by_tier(root, asset)
    return superseded + problems


#: KYO-604: the line `kyo install-skill` ends each file it writes with, recording the
#: SHA-256 of everything above it. It lives in the copy itself, as a Markdown comment
#: that renders as nothing, so it travels with the file through every clone and commit
#: and needs no new file beside it: a sidecar in the repository would be one more file
#: the consumer has to commit, and one outside it (the store, the per-project home)
#: would be absent on every other machine and in a split store's code checkout. What it
#: answers is the one thing nothing else can: whether this copy is still exactly the
#: text some build of `kyo install-skill` wrote, or has been edited since.
SKILL_DIGEST_PREFIX = "<!-- kyo install-skill wrote this file; sha256 of the text above this line: "
SKILL_DIGEST_SUFFIX = " -->"


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ends_in_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def stamp_skill_text(body: str) -> str:
    """``body`` as `kyo install-skill` writes it to a consumer: ending in a newline,
    then the line recording that text's digest (KYO-604)."""
    body = _ends_in_newline(body)
    return f"{body}{SKILL_DIGEST_PREFIX}{_sha256(body)}{SKILL_DIGEST_SUFFIX}\n"


def split_skill_digest(text: str) -> tuple[str, str | None]:
    """``(body, recorded_digest)`` for an installed skill file, or ``(text, None)`` when
    its last line is not a digest line (every copy written before KYO-604)."""
    head, sep, last = text.rstrip("\n").rpartition("\n")
    if last.startswith(SKILL_DIGEST_PREFIX) and last.endswith(SKILL_DIGEST_SUFFIX):
        return head + sep, last[len(SKILL_DIGEST_PREFIX) : -len(SKILL_DIGEST_SUFFIX)]
    return text, None


def _one_skill_asset_drift_by_tier(root: Path, asset: Asset) -> tuple[list[str], list[str]]:
    """``(problems, superseded)`` for one of :data:`SKILL_ASSETS` (KYO-604).

    ``superseded`` is a copy that still matches the digest `kyo install-skill` recorded
    when it wrote it (:data:`SKILL_DIGEST_PREFIX`) but is not this build's packaged text:
    an earlier build's skill, unedited, which `kyo doctor` reports as the notice
    ``skill_superseded``. Everything else that differs is a problem: a copy edited since
    it was written, one with no recorded digest (every copy written before KYO-604, which
    one `kyo install-skill` makes eligible), a missing half, and the source repository's
    byte mirror.
    """
    target = root / asset.target
    source = repo_asset_source(root, asset)
    if not target.is_file():
        # Absent is not drift for a project that installed no skill at all. It IS
        # drift when the other half is there: `kyo install-skill` writes both, so one
        # without the other is a half-applied install or a hand-deleted file.
        if not any((root / other.target).is_file() for other in SKILL_ASSETS):
            return [], []
        return [
            f"{asset.target} is missing while the rest of the /kyosei skill is "
            "installed — the workflow cites it by name in every section, so those "
            "pointers resolve to nothing. Run `kyo install-skill`, which writes both "
            "files"
            if source is None
            else f"{asset.target} is missing while the rest of the /kyosei skill is "
            f"installed; this repository's own source for it is "
            f"{asset.source_path}, so copy it across:\n"
            "    "
            + _copy_command(asset.source_path, Path(asset.target), to_installed=True)
        ], []
    installed = target.read_text(encoding="utf-8")
    if source is not None:
        # KYO-593.c3: a mirror up to the one substitution `kyo install-skill` makes.
        if installed == installed_skill_text(source.read_text(encoding="utf-8")):
            return [], []
        source_path = asset.source_path
        keep_skill = _copy_command(Path(asset.target), source_path, to_installed=False)
        keep_source = _copy_command(source_path, Path(asset.target), to_installed=True)
        return [
            f"{asset.target} differs from {source_path}, this repository's own "
            "source for the packaged /kyosei skill — the two are mirrors by "
            "construction (the copy names its directory where the source says "
            f"{SKILL_DIR_TOKEN}), so one has been edited without the other. Do NOT run "
            "`kyo install-skill` here: it writes the installed build's copy over both "
            "and loses whichever text is newer. Copy the one you edited over the "
            "other, whole, in the same commit:\n"
            f"    {keep_skill}   # keeps the skill's text\n"
            f"    {keep_source}   # keeps the packaged source's text"
        ], []
    try:
        packaged = installed_skill_text(packaged_skill_asset_text(asset))
    except KyoseiError as exc:  # pragma: no cover - only on a broken install
        return [str(exc)], []
    body, recorded = split_skill_digest(installed)
    if installed == packaged or body == _ends_in_newline(packaged):
        return [], []
    if recorded is not None and _sha256(body) == recorded:
        return [], [
            f"{asset.target} is an earlier build's /kyosei skill, unedited since `kyo "
            "install-skill` wrote it (its recorded digest still matches) — run `kyo "
            "install-skill` to bring it to this build's text, which describes the CLI you "
            "are running"
        ]
    why = (
        "it has been edited since `kyo install-skill` wrote it"
        if recorded is not None
        else "it carries no digest from `kyo install-skill`, so nothing says it is "
        "unedited (a copy written before KYO-604 needs one `kyo install-skill`)"
    )
    return [
        f"{asset.target} differs from this build's packaged /kyosei skill, and {why} — "
        "run `kyo install-skill` (it describes the CLI you are running, so a stale "
        "copy documents commands and flags that may not exist here; it overwrites the "
        "file, so carry any edit of your own across first)"
    ], []


def skill_drift_by_tier(root: Path) -> tuple[list[str], list[str]]:
    """:func:`skill_drift` as ``(problems, superseded)`` — what `kyo doctor`'s ``skill``
    and ``skill_superseded`` checks each report (KYO-604)."""
    problems: list[str] = []
    superseded: list[str] = []
    for asset in SKILL_ASSETS:
        p, n = _one_skill_asset_drift_by_tier(root, asset)
        problems += p
        superseded += n
    return problems, superseded


def installed_build_lag(root: Path) -> list[str]:
    """Packaged assets whose in-repo source has moved on past the running build.

    KYO-309, and it exists only for the repository the package is built from —
    :func:`repo_asset_source` returns ``None`` everywhere else, so a consumer project
    never sees a line from here. What it answers is the question the drift checks used
    to answer wrongly there: not "is this project's copy stale" but "is the `kyo`
    running this check older than the checkout it is running in".

    A notice, not a failure: nobody can clear it from the session -- the remedy is to
    move the installed build to this checkout (KYO-214's bar for the notice tier). It must
    never name `kyo install-skill` / `kyo install-procedures`, which would write the
    *older* packaged text over the newer source (docs/design/scaffold.md#kyo-309).

    KYO-450 (left by KYO-446.d1): the remedy is :func:`buildinfo.upgrade_route` for THIS
    running build, which passes its own release flag -- the re-snapshot (`make
    install-tool`) for an unreleased uv tool, `uv tool upgrade kyosei` for a release --
    and no longer the desktop app's re-pack, which ships no `kyo` any more.
    """
    from .buildinfo import upgrade_route

    remedy = upgrade_route()
    findings = []
    for asset, packaged_text, label, install in (
        (SKILL_ASSET, packaged_skill_text, "/kyosei skill", "kyo install-skill"),
        (
            SKILL_REFERENCE_ASSET,
            packaged_skill_reference_text,
            "/kyosei skill reference",
            "kyo install-skill",
        ),
        (
            PROCEDURES_ASSET,
            packaged_procedures_text,
            "standing-rules ledger",
            "kyo install-procedures",
        ),
    ):
        source = repo_asset_source(root, asset)
        if source is None:
            continue
        try:
            packaged = packaged_text()
        except KyoseiError:  # pragma: no cover - a broken install; reported elsewhere
            continue
        if source.read_text(encoding="utf-8") == packaged:
            continue
        source_path = asset.source_path
        findings.append(
            f"the `kyo` running this check is an installed build whose packaged {label} "
            f"is older than {source_path}, this repository's own source for it — this "
            "checkout is where the next build's copy comes from, so nothing here is "
            "stale except the build. This is information, not a failure: the remedy is "
            f"to move the installed build to this checkout -- {remedy}. Never "
            f"`{install}` — that writes this build's older text over the newer source, "
            "which is the edit you just made"
        )
    # KYO-318.c2: the hook fragments are the third packaged thing with a source in this
    # checkout, and the only one that is a directory rather than one file — so it is
    # reported here rather than in the loop above, in the same words and for the same
    # reason. `hook_drift` now compares against the source, which means an edit to a
    # fragment stops showing up as the project's drift; the build's age is the fact that
    # is actually true, and this is where it is said.
    behind = hook_fragments_behind(root)
    if behind:
        source_path = PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR
        findings.append(
            "the `kyo` running this check is an installed build whose packaged hook "
            f"templates ({', '.join(sorted(behind))}) are older than {source_path}, this "
            "repository's own source for them — this checkout is where the next build's "
            "copies come from, so nothing here is stale except the build. This is "
            "information, not a failure: the remedy is to move the installed build to "
            f"this checkout -- {remedy}. Never `kyo install-hooks` — that merges this build's "
            "older fragments into .claude/settings.json, which is how a hook group you "
            "deleted comes back"
        )
    return findings


def install_skill_refusal(root: Path) -> str | None:
    """Why `kyo install-skill` must not write here, or ``None`` (KYO-318.c1).

    KYO-328: the command writes two files, so it is refused when EITHER is behind its
    source here — a run that wrote only the half that happened to agree would leave the
    two tiers of one document at different ages, which is the half-done outcome a
    refusal exists to prevent (law 6). The message names every behind file and a `cp`
    per file, so the hand-copy remedy is complete as printed.
    """
    behind = []
    for asset in SKILL_ASSETS:
        source = repo_asset_source(root, asset)
        if source is None:
            # This checkout is not the source of THIS file. Asked per file rather than
            # per skill, on `repo_asset_source`'s own reasoning: a project is the
            # package's source repository for whatever source files it actually
            # carries, and a consumer carries none, so not one line of this is
            # reachable for them however many files the skill grows.
            continue
        try:
            packaged = packaged_skill_asset_text(asset)
        except KyoseiError:  # pragma: no cover - a broken install; reported by doctor
            return None
        if source.read_text(encoding="utf-8") != packaged:
            behind.append(asset)
    if not behind:
        return None
    copies = "\n".join(
        "        "
        + _copy_command(asset.source_path, Path(asset.target), to_installed=True)
        for asset in behind
    )
    named = ", ".join(str(asset.source_path) for asset in behind)
    return _source_repo_refusal(
        root,
        command="kyo install-skill",
        what="/kyosei skill",
        source_path=Path(named),
        instead=[
            f"move this copy to a build made from this checkout ({_reinstall_route()}); "
            "this command then has nothing to do, because the two copies agree",
            "or, if what you wanted is this project's own skill brought up to the "
            "source, copy it across yourself — that is the same write without the "
            f"older build in the middle:\n{copies}",
        ],
    )


def install_skill(root: Path, *, print_only: bool = False) -> list[tuple[Path, bool]]:
    """Write the packaged ``/kyosei`` skill into ``root``: one ``(path, changed)`` per
    file of :data:`SKILL_ASSETS`, in that order (KYO-15, KYO-328).

    Refuses to write (never to preview) in the repository this package is built from
    when the copies differ — :func:`install_skill_refusal` carries the reasoning. The
    refusal is raised once, before anything is written, so the two tiers of the skill
    cannot end up at different ages.
    """
    if not print_only:
        refusal = install_skill_refusal(root)
        if refusal is not None:
            raise KyoseiError(refusal)
    written: list[tuple[Path, bool]] = []
    for asset in SKILL_ASSETS:
        target = root / asset.target
        packaged = installed_skill_text(packaged_skill_asset_text(asset))
        # KYO-604: a consumer's copy ends with the digest of what was written, which is
        # how `kyo doctor` later tells an earlier build's unedited text (a notice) from an
        # edited copy (a problem). Never in the repository the package is built from,
        # whose copy is a byte mirror of its source.
        if repo_asset_source(root, asset) is None:
            packaged = stamp_skill_text(packaged)
        if target.is_file() and target.read_text(encoding="utf-8") == packaged:
            written.append((target, False))
            continue
        if not print_only:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(packaged, encoding="utf-8")
        written.append((target, True))
    # KYO-508: the marked CLAUDE.md section, last so `written[0]` stays the workflow.
    md = root / CLAUDE_MD
    current = md.read_text(encoding="utf-8") if md.is_file() else None
    updated = claude_md_with_section(current)
    if updated == current:
        written.append((md, False))
    else:
        if not print_only:
            md.write_text(updated, encoding="utf-8")
        written.append((md, True))
    return written
