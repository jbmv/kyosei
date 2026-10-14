"""``kyo harness install claude-code`` — the stable path to this build's plugin (KYO-594).

The plugin's files ship inside the package (:data:`kyosei.harness.CLAUDE_CODE_DIR`,
KYO-593), and this command registers them with Claude Code. Claude Code loads a
relative-path plugin from a marketplace added by local path in place, so an edit to the
files reaches the next session with no `claude plugin` command, and it records a
marketplace added through a symbolic link under the link's path rather than the resolved
one. This command therefore:

1. points ``$KYOSEI_HOME/harness/claude-code`` at the running package's
   ``harness/claude-code`` directory, repointing a link that is already there;
2. registers that path with ``claude plugin marketplace add`` (once per machine; a repeat is
   harmless, measured under a temporary ``CLAUDE_CONFIG_DIR``);
3. inside a Kyosei project, enables the plugin there at local scope, after making
   sure git ignores ``.claude/settings.local.json``, the file that scope writes.

**This is the one writer of the link.** Several `kyo` builds can coexist on one machine (the
uv tool snapshot and a development venv), and each would point the link at itself; a `kyo`
that merely runs must never repoint it. ``tests/test_harness_install.py`` fails when any
other module in the package creates a symbolic link. `kyo doctor` reporting a link that
points at another build is later work in plan KYO-590.

**Copied hooks block the enablement, not the registration.** A plugin hook and the same
hook copied into ``.claude/settings.json`` by `kyo install-hooks` both run (observed), so in
a project that still carries the copies the plugin is not enabled, and the output names `kyo
harness migrate` (:mod:`kyosei.cmd_harness_migrate`, KYO-595), which removes them and
enables it in one step. The copies are recognised with
`install_hooks.py`'s own matching, :func:`copied_hook_commands`.

**Without `claude` on PATH** the link is still written and the `claude` commands are printed
for the operator to run, with exit 0: the link is this command's own work, and the rest is a
step the operator can take later.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

from .commands import Context, Output
from .errors import KyoseiError
from .git_queries import run_git

#: The harnesses this command can install into; the argparse choices.
HARNESSES: tuple[str, ...] = ("claude-code",)

#: The plugin's id as Claude Code names it: plugin ``kyosei`` in marketplace ``kyosei``
#: (``harness/claude-code/.claude-plugin/marketplace.json``).
PLUGIN_ID = "kyosei@kyosei"

#: The settings file local scope writes, relative to the project.
LOCAL_SETTINGS_REL = ".claude/settings.local.json"

#: How long one `claude plugin` command may take. Measured at about a second each.
CLAUDE_TIMEOUT_SECONDS = 120

#: The command that removes copied hooks and enables the plugin (KYO-595).
MIGRATE_COMMAND = "kyo harness migrate"


def link_path(environ: dict[str, str] | None = None) -> Path:
    """``$KYOSEI_HOME/harness/claude-code``: the path Claude Code knows the marketplace by."""
    from .config import store_home

    return store_home(environ) / "harness" / "claude-code"


def package_dir() -> Path:
    """The running package's marketplace directory, the link's target."""
    from .harness import CLAUDE_CODE_DIR

    return CLAUDE_CODE_DIR


def write_link(link: Path, target: Path) -> str | None:
    """Point ``link`` at ``target``; returns the previous target when it was repointed,
    else ``None``. A link already pointing there is left as it is.

    Refuses a path that exists and is not a symbolic link: something an operator put there
    is not this command's to replace. The new link is made beside the old one and renamed
    over it, so no reader sees the path missing.
    """
    previous: str | None = None
    if link.is_symlink():
        previous = os.readlink(link)
        if previous == str(target):
            return None
    elif link.exists():
        raise KyoseiError(
            f"{link} exists and is not a symbolic link, so kyo harness install will not "
            f"replace it; move it aside (`mv {shlex.quote(str(link))} "
            f"{shlex.quote(str(link) + '.old')}`) and run `kyo harness install claude-code` "
            "again"
        )
    link.parent.mkdir(parents=True, exist_ok=True)
    staging = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    if staging.is_symlink() or staging.exists():
        staging.unlink()
    os.symlink(target, staging)
    os.replace(staging, link)
    return previous


def _settings_hook_commands(settings: object) -> list[str]:
    commands: list[str] = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return commands
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else []:
            entries = group.get("hooks") if isinstance(group, dict) else None
            for hook in entries if isinstance(entries, list) else []:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    commands.append(hook["command"])
    return commands


def copied_hook_commands(code_root: Path) -> list[str]:
    """The hook commands in ``<code_root>/.claude/settings.json`` that are Kyosei's copies.

    The matching is `install_hooks.py`'s: a command is a copy when, with the PATH guard
    stripped (:func:`~kyosei.install_hooks.hook_action`), one of its ``&&`` elements
    (:func:`~kyosei.install_hooks.command_elements`) is one the packaged fragments install
    under any event (:func:`~kyosei.install_hooks.packaged_elements`), or when the whole
    command is one a previous release shipped
    (:data:`~kyosei.install_hooks.SUPERSEDED_HOOK_COMMANDS`). Any event, not only the one a
    fragment names, since a handler under another event would run beside the plugin's all
    the same. A line a consumer extended with a step of their own still counts: its Kyosei
    element would run twice.

    Refuses a settings file that is not a JSON object, since whether it carries the copies
    cannot then be told.
    """
    from .install_hooks import (
        CLAUDE_SETTINGS_REL,
        HOOK_INSTALL_TARGETS,
        SUPERSEDED_HOOK_COMMANDS,
        command_elements,
        hook_action,
        packaged_elements,
    )

    path = code_root / CLAUDE_SETTINGS_REL
    if not path.is_file():
        return []
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KyoseiError(
            f"{path} is not valid JSON ({exc}), so whether it still carries Kyosei's copied "
            "hooks cannot be told and nothing was changed; correct the file and run `kyo "
            "harness install claude-code` again"
        ) from exc
    if not isinstance(settings, dict):
        raise KyoseiError(
            f"{path} does not hold a JSON object, so whether it still carries Kyosei's copied "
            "hooks cannot be told and nothing was changed; correct the file and run `kyo "
            "harness install claude-code` again"
        )
    packaged: set[str] = set()
    for event in {event for event, _fragment in HOOK_INSTALL_TARGETS}:
        packaged |= packaged_elements(event)
    found: list[str] = []
    for command in _settings_hook_commands(settings):
        superseded = command in SUPERSEDED_HOOK_COMMANDS or (
            hook_action(command) in SUPERSEDED_HOOK_COMMANDS
        )
        if (superseded or command_elements(command) & packaged) and command not in found:
            found.append(command)
    return found


def _git(code_root: Path, *args: str) -> subprocess.CompletedProcess:
    # Every git subprocess goes through run_git (pinned config, bounded time).
    return run_git(code_root, *args)


def ensure_local_settings_ignored(code_root: Path) -> str | None:
    """Make git ignore ``.claude/settings.local.json`` in the project before local scope
    writes it. Returns the line to print when the repository's ``.gitignore`` was changed,
    else ``None``.

    Asked of git (`git check-ignore`) rather than of the ``.gitignore`` text, since a global
    excludes file can already cover it. Appended to ``<code_root>/.gitignore``, the
    file `kyo init` and `kyo install-hooks` already add the worker report to. Refuses when
    the path is still not ignored afterwards: a tracked file, or a later pattern that
    re-includes it.
    """
    check = _git(code_root, "check-ignore", "-q", LOCAL_SETTINGS_REL)
    if check.returncode == 0:
        return None
    if check.returncode != 1:
        # Not a git repository (128), or git missing: nothing can commit the file.
        return None
    target = code_root / ".gitignore"
    existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    listed = any(
        line.strip() in (LOCAL_SETTINGS_REL, f"/{LOCAL_SETTINGS_REL}")
        for line in existing.splitlines()
    )
    changed = None
    if not listed:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        target.write_text(
            existing
            + prefix
            + "# Claude Code's local-scope settings: one operator's, never committed "
            "(kyo harness install, KYO-594).\n" + LOCAL_SETTINGS_REL + "\n",
            encoding="utf-8",
        )
        changed = (
            f"gitignore: added {LOCAL_SETTINGS_REL} to {target}, since git did not ignore "
            "it and enabling the plugin at local scope writes it"
        )
    if _git(code_root, "check-ignore", "-q", LOCAL_SETTINGS_REL).returncode == 0:
        return changed
    tracked = _git(code_root, "ls-files", "--error-unmatch", LOCAL_SETTINGS_REL).returncode == 0
    if tracked:
        why = (
            f"git tracks {LOCAL_SETTINGS_REL}, and a tracked file is never ignored; untrack it "
            f"(`git rm --cached {LOCAL_SETTINGS_REL}`) and run `kyo harness install "
            "claude-code` again"
        )
    else:
        rule = _git(code_root, "check-ignore", "-v", "--no-index", "-n", LOCAL_SETTINGS_REL)
        why = (
            f"git still does not ignore {LOCAL_SETTINGS_REL} although {target} lists it "
            f"({rule.stdout.strip() or 'git check-ignore -v names no rule'}); remove the "
            "pattern that re-includes it and run `kyo harness install claude-code` again"
        )
    raise KyoseiError(
        f"the plugin was not enabled in {code_root}: {why}. The link and the marketplace "
        "registration are done"
    )


def claude_program(environ: dict[str, str]) -> str | None:
    """The `claude` executable on this invocation's ``PATH``, or ``None``."""
    return shutil.which("claude", path=environ.get("PATH", os.defpath))


def _run_claude(program: str, args: list[str], cwd: Path, environ: dict[str, str]) -> str:
    """Run one `claude` command; its output on success, a refusal naming it otherwise."""
    shown = " ".join(shlex.quote(word) for word in ["claude", *args])
    try:
        done = subprocess.run(
            [program, *args],
            cwd=cwd,
            env=environ,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KyoseiError(f"`{shown}` (in {cwd}) did not run: {exc}") from exc
    output = "\n".join(part.strip() for part in (done.stdout, done.stderr) if part.strip())
    if done.returncode != 0:
        raise KyoseiError(f"`{shown}` (in {cwd}) exited {done.returncode}: {output or 'no output'}")
    return output


def install_claude_code(ctx: Context) -> Output:
    """The ``claude-code`` harness: link, register, then enable in the project here.

    In a project not on the copies it also does the two things the plugin cannot carry.
    KYO-612: it writes the advisory git ``pre-push`` hook (KYO-60's human half, which
    `kyo install-hooks` writes on the copies), since a push typed by hand passes through
    git and no Claude Code hook; an operator's own ``pre-push`` is never overwritten, and
    the line to add to it is named. KYO-613: once the plugin is enabled, it removes the
    setup-incomplete section `kyo init` wrote into the project's CLAUDE.md; nothing else on
    this route replaces it.
    """
    from .config import refuse_real_home_from_a_dispatch_worktree
    from .install_hooks import install_git_pre_push_hook
    from .install_skill import (
        CLAUDE_MD,
        claude_md_carries_init_section,
        claude_md_without_init_section,
    )

    environ = ctx.environ
    link = link_path(environ)
    target = package_dir()
    # KYO-535.c2: the link lives in the machine's Kyosei home, which a dispatched worker
    # must not repoint at its own unmerged worktree.
    refuse_real_home_from_a_dispatch_worktree(
        environ,
        command="kyo harness install claude-code",
        what="point the Claude Code plugin link at this build",
    )
    layout = ctx.layout
    project = layout.code_root if layout is not None and layout.configured else None
    # Before anything is written: an unreadable settings file refuses here.
    copied = copied_hook_commands(project) if project is not None else []

    previous = write_link(link, target)
    lines = [
        f"link: {link} -> {target}"
        + (f" (repointed from {previous})" if previous is not None else "")
    ]
    program = claude_program(environ)
    marketplace_args = ["plugin", "marketplace", "add", str(link)]
    install_args = ["plugin", "install", PLUGIN_ID, "--scope", "local"]
    to_run: list[str] = []
    data: dict = {
        "ok": True,
        "command": "harness",
        "action": "install",
        "harness": "claude-code",
        "stable_link": str(link),
        "target": str(target),
        "repointed_from": previous,
        "claude": program,
        "project": str(project) if project is not None else None,
        "copied_hooks": copied,
        "marketplace_registered": False,
        "plugin_enabled": False,
        "gitignore_changed": False,
        "pre_push": None,
        "claude_md_section_removed": False,
        "commands_to_run": to_run,
    }

    def shown(args: list[str], cwd: Path | None = None) -> str:
        words = " ".join(shlex.quote(word) for word in ["claude", *args])
        return f"cd {shlex.quote(str(cwd))} && {words}" if cwd is not None else words

    if program is not None:
        _run_claude(program, marketplace_args, link.parent, environ)
        data["marketplace_registered"] = True
        lines.append(f"marketplace: registered {link} with Claude Code as `kyosei`")
    else:
        to_run.append(shown(marketplace_args))

    if project is None:
        lines.append(
            "project: none here, so the plugin is not enabled anywhere yet; run `kyo harness "
            "install claude-code` in each Kyosei project to enable it there"
        )
    elif copied:
        lines.append(
            f"project: {project} still carries Kyosei's copied hooks in .claude/settings.json, "
            "so the plugin was NOT enabled there: both would run every hook twice"
        )
        lines.extend(f"  {command}" for command in copied)
        lines.append(
            f"next: run `{MIGRATE_COMMAND}` in {project}: it enables the plugin there and "
            "removes the copied hooks, the copied skill and the CLAUDE.md section in the "
            "same step, and names anything you edited, which it leaves in place"
        )
    else:
        ignored = ensure_local_settings_ignored(project)
        if ignored is not None:
            data["gitignore_changed"] = True
            lines.append(ignored)
        if program is not None:
            _run_claude(program, install_args, project, environ)
            data["plugin_enabled"] = True
            lines.append(
                f"project: enabled {PLUGIN_ID} in {project} at local scope ({LOCAL_SETTINGS_REL})"
            )
            # KYO-613: the plugin's session-start brief now names the project, so the
            # section saying setup is incomplete is wrong from here on.
            removed = claude_md_without_init_section(project)
            if removed is not None:
                data["claude_md_section_removed"] = True
                lines.append(
                    f"CLAUDE.md: {removed}; the plugin's session-start brief names this "
                    "project now. A working-tree change, not committed"
                )
        else:
            to_run.append(shown(install_args, project))
            md_path = project / CLAUDE_MD
            if md_path.is_file() and claude_md_carries_init_section(
                md_path.read_text(encoding="utf-8")
            ):
                lines.append(
                    "CLAUDE.md keeps the setup-incomplete section `kyo init` wrote, since "
                    "the plugin is not enabled yet; run `kyo harness install claude-code` "
                    "again where `claude` is on PATH, which enables it and removes the section"
                )
        # KYO-612: the human half of KYO-60. Independent of `claude`, so written either way.
        pre_push_changes, pre_push_notes = install_git_pre_push_hook(project)
        data["pre_push"] = {"changes": pre_push_changes, "notes": pre_push_notes}
        lines.extend(pre_push_changes)
        lines.extend(f"! {note}" for note in pre_push_notes)

    if to_run:
        lines.append(
            "claude is not on PATH, so nothing was registered with Claude Code; run these "
            "where it is:"
        )
        lines.extend(f"  {command}" for command in to_run)
    return Output(lines, data)


def cmd_harness(ctx: Context, args: Namespace) -> Output:
    """``kyo harness install claude-code`` and ``kyo harness migrate [claude-code]``;
    argparse holds both words to their choices."""
    if args.action == "migrate":
        from .cmd_harness_migrate import migrate_claude_code

        return migrate_claude_code(ctx)
    if args.harness is None:
        raise KyoseiError(
            "`kyo harness install` needs the harness to install into; run `kyo harness "
            f"install {HARNESSES[0]}`"
        )
    return install_claude_code(ctx)
