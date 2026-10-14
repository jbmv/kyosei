"""``kyo harness migrate`` — from the tracked copies to the Claude Code plugin (KYO-595).

The migration half of moving a project onto the plugin, which is the default way to give
a project Kyosei's skill and hooks. A plugin hook and a settings hook with the same
command both run (this was tried), so enabling the plugin and removing the
copies happen in one command, in this order:

1. the link and the marketplace registration, as `kyo harness install claude-code` makes
   them (:mod:`kyosei.cmd_harness`);
2. the plugin enabled in this project at local scope;
3. the copies removed: each hook entry in ``.claude/settings.json`` whose command is,
   exactly, one the packaged fragments install or one an earlier release shipped
   (:data:`~kyosei.install_hooks.SUPERSEDED_HOOK_COMMANDS`); each skill file that is
   unedited (its KYO-604 digest still matches, or it is this build's text); and the
   ``CLAUDE.md`` section between :data:`~kyosei.install_skill.CLAUDE_MD_BEGIN` and
   :data:`~kyosei.install_skill.CLAUDE_MD_END`;
4. a report of what was removed and what was left, with the reason for each.

An entry or file the operator edited is left in place and named. The git ``pre-push`` hook
stays: it is untracked and serves a push typed by hand, which no plugin sees; one that is
absent is written, as `kyo harness install claude-code` writes it (KYO-612). Nothing is
committed and no history is rewritten; the removal is an ordinary working-tree change for
the operator to commit.

KYO-610: in the repository the package is built from (KYO-309, KYO-318), the copies are
mirrors of this checkout's own source rather than text an installed build wrote, so "unedited"
is read against that source: a skill file is removed when it equals the installed form of its
source (:func:`~kyosei.install_skill.installed_skill_text`), and a hook entry when its command
is one this checkout's own fragments install, beside the running build's and the superseded
ones. KYO-595.c6 refused here; KYO-610 moves this repository like a consumer instead,
so its sessions load the installed build's plugin and the command says how far that
build stands from the checkout and how to turn the plugin off if a build breaks a hook.

Also here, because both read the same facts: :func:`harness_mode`, which says whether a
project is on the copies, on the plugin, or on neither. `kyo install-skill` and `kyo
install-hooks` read it to decide whether they write without ``--copies`` (KYO-595.c3), and
`kyo version`'s upgrade line reads it to name the per-project commands (KYO-595.c4).
"""

from __future__ import annotations

import json
from pathlib import Path

from .commands import Context, Output
from .errors import KyoseiError

#: The flag `kyo install-skill` and `kyo install-hooks` take to write the tracked copies
#: in a project not already on them, since the plugin is the default.
COPIES_FLAG = "--copies"

#: :func:`harness_mode`'s answers.
MODE_COPIES = "copies"
MODE_PLUGIN = "plugin"
MODE_NONE = "none"

#: The settings files whose ``enabledPlugins`` can enable the plugin for a project:
#: local scope (what `kyo harness install` writes) and project scope.
PLUGIN_SETTINGS_RELS: tuple[str, ...] = (".claude/settings.local.json", ".claude/settings.json")


# ---------------------------------------------------------------- which mode a project is on


def plugin_enabled(code_root: Path) -> bool:
    """Whether ``kyosei@kyosei`` is enabled for this project in its local- or project-scope
    settings. A file that is not a readable JSON object enables nothing."""
    from .cmd_harness import PLUGIN_ID

    for rel in PLUGIN_SETTINGS_RELS:
        path = code_root / rel
        if not path.is_file():
            continue
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
        if isinstance(enabled, dict) and enabled.get(PLUGIN_ID) is True:
            return True
    return False


def copied_skill_paths(code_root: Path) -> list[Path]:
    """The skill copies `kyo install-skill` writes that exist in this project."""
    from .install_skill import SKILL_ASSETS

    return [code_root / a.target for a in SKILL_ASSETS if (code_root / a.target).is_file()]


def on_the_copies(code_root: Path) -> bool:
    """Whether the project carries the copied skill or Kyosei's copied hooks.

    A settings file whose hooks cannot be read counts as carrying them: whether it does
    cannot be told, and the conservative answer keeps `kyo install-hooks` behaving as it
    did before KYO-595, where its own parse names the problem.
    """
    from .cmd_harness import copied_hook_commands

    if copied_skill_paths(code_root):
        return True
    try:
        return bool(copied_hook_commands(code_root))
    except KyoseiError:
        return True


def harness_mode(code_root: Path) -> str:
    """:data:`MODE_COPIES` when the project carries any copy (even beside the plugin,
    the doubled state `kyo doctor` reports), else :data:`MODE_PLUGIN` when the plugin is
    enabled here, else :data:`MODE_NONE`."""
    if on_the_copies(code_root):
        return MODE_COPIES
    if plugin_enabled(code_root):
        return MODE_PLUGIN
    return MODE_NONE


def copies_refusal(code_root: Path, command: str) -> str | None:
    """Why ``command`` (`kyo install-skill` or `kyo install-hooks`) writes nothing here
    without :data:`COPIES_FLAG`, or ``None`` when it writes as before (KYO-595.c3).

    A project already on the copies keeps working with no new flag: other projects run
    these commands after every upgrade. Anywhere else the plugin is the default, and the
    copies are written only when asked for by name.
    """
    mode = harness_mode(code_root)
    if mode == MODE_COPIES:
        return None
    where = (
        "has the Kyosei plugin enabled"
        if mode == MODE_PLUGIN
        else "carries neither the copied /kyosei skill nor Kyosei's copied hooks"
    )
    return (
        f"{code_root} {where}, so `{command}` wrote nothing: the Claude Code plugin is the "
        "default way to give a project Kyosei's skill and hooks, and it is installed with "
        "`kyo harness install claude-code`. To write the tracked copies instead (for an "
        "operator without plugins, or a cloud session that cannot load one), run "
        f"`{command} {COPIES_FLAG}`"
        + (
            "; with the plugin enabled as well, every hook would then run twice"
            if mode == MODE_PLUGIN
            else ""
        )
    )


# --------------------------------------------------------------------------- the plan


def _packaged_hook_commands(code_root: Path | None = None) -> set[str]:
    """Every whole command string the packaged fragments install, under any event.

    KYO-610: with ``code_root`` the repository the package is built from, the commands
    its own fragment source installs (:func:`~kyosei.scaffold.repo_hooks_source`) count
    too, since that is what its settings file mirrors, whichever build is running.
    """
    from .install_hooks import HOOK_INSTALL_TARGETS, _load_hook_fragment
    from .scaffold import repo_hooks_source

    sources: list[Path | None] = [None]
    source_dir = repo_hooks_source(code_root) if code_root is not None else None
    if source_dir is not None:
        sources.append(source_dir)
    commands: set[str] = set()
    for hooks_dir in sources:
        for _event, name in HOOK_INSTALL_TARGETS:
            for group in _load_hook_fragment(name, hooks_dir):
                for hook in group.get("hooks", []):
                    if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                        commands.add(hook["command"])
    return commands


def _read_settings(code_root: Path) -> dict | None:
    """``.claude/settings.json`` as a dict, ``None`` when absent; refuses one that is not
    a JSON object, before anything is written."""
    from .install_hooks import CLAUDE_SETTINGS_REL

    path = code_root / CLAUDE_SETTINGS_REL
    if not path.is_file():
        return None
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        settings = exc
    if not isinstance(settings, dict):
        raise KyoseiError(
            f"{path} is not a readable JSON object, so which of its hooks are Kyosei's "
            "copies cannot be told and nothing was changed; correct the file and run "
            "`kyo harness migrate` again"
        )
    return settings


def _plan_hooks(settings: dict | None, code_root: Path) -> tuple[dict | None, list, list]:
    """``(new_settings, removed, left)``. ``new_settings`` is ``None`` when nothing is
    removed. Each of ``removed`` and ``left`` is ``(event, command, reason)``."""
    from .cmd_harness import copied_hook_commands
    from .install_hooks import SUPERSEDED_HOOK_COMMANDS
    from .scaffold import repo_hooks_source

    if settings is None or not isinstance(settings.get("hooks"), dict):
        return None, [], []
    exact = _packaged_hook_commands(code_root)
    installs = (
        "a command this build's fragments or this checkout's own fragment source install"
        if repo_hooks_source(code_root) is not None
        else "a command this build's fragments install"
    )
    copies = set(copied_hook_commands(code_root))
    removed: list[tuple[str, str, str]] = []
    left: list[tuple[str, str, str]] = []
    new = json.loads(json.dumps(settings))
    hooks = new["hooks"]
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        for group in list(groups):
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                continue
            kept = []
            for hook in entries:
                command = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(command, str) and command in exact:
                    removed.append((event, command, installs))
                    continue
                if isinstance(command, str) and command in SUPERSEDED_HOOK_COMMANDS:
                    removed.append((event, command, "a command an earlier release installed"))
                    continue
                if isinstance(command, str) and command in copies:
                    left.append(
                        (
                            event,
                            command,
                            "it runs a Kyosei command but matches no line any release "
                            "installed, so it was edited; remove it by hand, or the plugin "
                            "and this line will both run that command",
                        )
                    )
                kept.append(hook)
            if len(kept) != len(entries):
                if kept:
                    group["hooks"] = kept
                else:
                    groups.remove(group)
        if not groups:
            del hooks[event]
    if not removed:
        return None, removed, left
    if not hooks:
        del new["hooks"]
    return new, removed, left


def _skill_copy_is_unedited(path: Path, asset, code_root: Path) -> bool:
    """KYO-604's digest still matches, or the file is this build's installed text.

    KYO-610: in the repository the package is built from, the copy is a mirror of that
    checkout's own source, and it is unedited exactly when it equals the installed form of
    that source -- the test ``skill_drift`` applies there (KYO-593.c3).
    """
    from .install_skill import (
        _ends_in_newline,
        _sha256,
        installed_skill_text,
        packaged_skill_asset_text,
        split_skill_digest,
    )
    from .scaffold import repo_asset_source

    text = path.read_text(encoding="utf-8")
    source = repo_asset_source(code_root, asset)
    if source is not None:
        return text == installed_skill_text(source.read_text(encoding="utf-8"))
    body, recorded = split_skill_digest(text)
    if recorded is not None and _sha256(body) == recorded:
        return True
    packaged = installed_skill_text(packaged_skill_asset_text(asset))
    return text == packaged or body == _ends_in_newline(packaged)


def _plan_skill(code_root: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    from .install_skill import SKILL_ASSETS
    from .scaffold import repo_asset_source

    remove: list[Path] = []
    left: list[tuple[Path, str]] = []
    for asset in SKILL_ASSETS:
        path = code_root / asset.target
        if not path.is_file():
            continue
        if _skill_copy_is_unedited(path, asset, code_root):
            remove.append(path)
        elif repo_asset_source(code_root, asset) is not None:
            left.append(
                (
                    path,
                    f"it differs from {asset.source_path}, this repository's own source "
                    "for it, so one of the two was edited without the other; carry any edit "
                    "of the copy into the source and delete the copy, since the plugin "
                    "carries the skill",
                )
            )
        else:
            left.append(
                (
                    path,
                    "edited since `kyo install-skill` wrote it (its digest no longer "
                    "matches and it is not this build's text); carry anything of your own "
                    "elsewhere and delete it, since the plugin carries the skill",
                )
            )
    return remove, left


def claude_md_without_section(text: str) -> str | None:
    """``text`` with the Kyosei section and the blank line before it removed, or ``None``
    when it has no complete section. Every byte outside is kept."""
    from .install_skill import CLAUDE_MD_BEGIN, CLAUDE_MD_END

    begin = text.find(CLAUDE_MD_BEGIN)
    if begin == -1:
        return None
    end = text.find(CLAUDE_MD_END, begin)
    if end == -1:
        return None
    before = text[:begin]
    after = text[end + len(CLAUDE_MD_END) :]
    if after.startswith("\n"):
        after = after[1:]
    if not after.strip():
        return before.rstrip("\n") + "\n" if before.strip() else ""
    if before.endswith("\n\n") and after.startswith("\n"):
        after = after[1:]
    return before + after


def source_checkout_note(code_root: Path) -> str | None:
    """KYO-610: what moving the repository the package is built from onto the plugin
    means, or ``None`` anywhere else. Its sessions load the installed build's skill and
    hooks, so they lag this checkout until the next reinstall, and a build whose hook
    breaks sessions here breaks the session that would fix it too."""
    from .buildinfo import upgrade_route
    from .cmd_harness import PLUGIN_ID
    from .install_skill import SKILL_ASSETS
    from .scaffold import build_age_against, repo_asset_source

    if not any(repo_asset_source(code_root, a) is not None for a in SKILL_ASSETS):
        return None
    return (
        f"source checkout: {code_root} is the repository the Kyosei package is built from, "
        "and its sessions now load the skill and hooks from the installed build, which is "
        f"{build_age_against(code_root)} this checkout; an edit to the source reaches them "
        f"after {upgrade_route()}. If an installed build's hook breaks sessions here, "
        f"`claude plugin disable {PLUGIN_ID}` in this repository turns the plugin off until "
        "a fixed build is installed"
    )


# ------------------------------------------------------------------------- the command


def migrate_claude_code(ctx: Context) -> Output:
    """``kyo harness migrate``: enable the plugin here, then remove the copies."""
    from .cmd_harness import (
        LOCAL_SETTINGS_REL,
        PLUGIN_ID,
        _run_claude,
        claude_program,
        ensure_local_settings_ignored,
        link_path,
        package_dir,
        write_link,
    )
    from .config import refuse_real_home_from_a_dispatch_worktree
    from .install_hooks import (
        CLAUDE_SETTINGS_REL,
        PRE_PUSH_MARKER,
        git_hooks_dir,
        install_git_pre_push_hook,
    )
    from .install_skill import CLAUDE_MD

    layout = ctx.layout
    if layout is None or not layout.configured:
        raise KyoseiError(
            "`kyo harness migrate` runs in a Kyosei project, and none resolves here; run it "
            "from the project's directory, or pass --root <project>"
        )
    code_root = layout.code_root
    environ = ctx.environ

    # Everything is read and decided before anything is written.
    settings = _read_settings(code_root)
    new_settings, hooks_removed, hooks_left = _plan_hooks(settings, code_root)
    skill_remove, skill_left = _plan_skill(code_root)
    md_path = code_root / CLAUDE_MD
    md_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else None
    md_new = claude_md_without_section(md_text) if md_text is not None else None

    refuse_real_home_from_a_dispatch_worktree(
        environ,
        command="kyo harness migrate",
        what="point the Claude Code plugin link at this build",
    )
    program = claude_program(environ)
    if program is None:
        raise KyoseiError(
            "`claude` is not on PATH, so the plugin cannot be enabled here, and the copies "
            "were not removed: removing them first would leave this project with neither. "
            "Nothing was changed; run `kyo harness migrate` again where `claude` is on PATH"
        )

    # 1-2: link, register, enable (the order the module docstring gives).
    link = link_path(environ)
    target = package_dir()
    previous = write_link(link, target)
    lines = [
        f"link: {link} -> {target}"
        + (f" (repointed from {previous})" if previous is not None else "")
    ]
    _run_claude(program, ["plugin", "marketplace", "add", str(link)], link.parent, environ)
    lines.append(f"marketplace: registered {link} with Claude Code as `kyosei`")
    ignored = ensure_local_settings_ignored(code_root)
    if ignored is not None:
        lines.append(ignored)
    _run_claude(program, ["plugin", "install", PLUGIN_ID, "--scope", "local"], code_root, environ)
    lines.append(
        f"project: enabled {PLUGIN_ID} in {code_root} at local scope ({LOCAL_SETTINGS_REL})"
    )

    # 3: remove the copies.
    removed: list[dict] = []
    left: list[dict] = []
    if new_settings is not None:
        (code_root / CLAUDE_SETTINGS_REL).write_text(
            json.dumps(new_settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    for event, command, why in hooks_removed:
        removed.append(
            {"path": str(CLAUDE_SETTINGS_REL), "what": f"{event} hook: {command}", "why": why}
        )
    for event, command, why in hooks_left:
        left.append(
            {"path": str(CLAUDE_SETTINGS_REL), "what": f"{event} hook: {command}", "why": why}
        )
    for path in skill_remove:
        path.unlink()
        removed.append(
            {
                "path": str(path.relative_to(code_root)),
                "what": "the copied /kyosei skill file",
                "why": "unedited since `kyo install-skill` wrote it; the plugin carries it now",
            }
        )
    for path, why in skill_left:
        left.append(
            {"path": str(path.relative_to(code_root)), "what": "a /kyosei skill file", "why": why}
        )
    skill_dir = code_root / ".claude" / "skills" / "kyosei"
    for directory in (skill_dir, skill_dir.parent):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if md_new is not None:
        if md_new:
            md_path.write_text(md_new, encoding="utf-8")
            what = "the Kyosei section between its markers"
        else:
            md_path.unlink()
            what = "the Kyosei section between its markers, which was the whole file"
        removed.append(
            {
                "path": CLAUDE_MD,
                "what": what,
                "why": "the plugin's session-start brief says the project uses Kyosei",
            }
        )
    hooks_dir = git_hooks_dir(code_root)
    pre_push = hooks_dir / "pre-push" if hooks_dir is not None else None
    if (
        pre_push is not None
        and pre_push.is_file()
        and PRE_PUSH_MARKER in pre_push.read_text(encoding="utf-8", errors="replace")
    ):
        left.append(
            {
                "path": str(pre_push),
                "what": "the advisory git pre-push hook",
                "why": "it is untracked and serves a push typed by hand, which no plugin sees",
            }
        )
    # KYO-612: the plugin route keeps KYO-60's human half, so a project on the copies that
    # never had the hook gets it here, as `kyo harness install claude-code` writes it; an
    # outdated one of ours is brought current, and an operator's own is never touched.
    pre_push_changes, pre_push_notes = install_git_pre_push_hook(code_root)

    # 4: say what happened.
    if removed:
        lines.append("removed:")
        lines.extend(f"  - {r['path']}: {r['what']} ({r['why']})" for r in removed)
    else:
        lines.append("removed: nothing (no copies were found)")
    if left:
        lines.append("left:")
        lines.extend(f"  ! {r['path']}: {r['what']} ({r['why']})" for r in left)
    lines.extend(pre_push_changes)
    lines.extend(f"! {note}" for note in pre_push_notes)
    if removed:
        lines.append(
            "nothing was committed: these are working-tree changes to commit yourself; a "
            "copy already pushed stays in history"
        )
    note = source_checkout_note(code_root)
    if note is not None:
        lines.append(note)
    return Output(
        lines,
        {
            "ok": True,
            "command": "harness",
            "action": "migrate",
            "harness": "claude-code",
            "project": str(code_root),
            "stable_link": str(link),
            "target": str(target),
            "repointed_from": previous,
            "plugin_enabled": True,
            "gitignore_changed": ignored is not None,
            "removed": removed,
            "left": left,
            "pre_push": {"changes": pre_push_changes, "notes": pre_push_notes},
            "source_checkout": note,
        },
    )
