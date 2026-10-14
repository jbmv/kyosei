"""Per-harness integration files that ship inside the package (KYO-593).

``claude-code/`` is a Claude Code marketplace holding one plugin: the marketplace
manifest at ``claude-code/.claude-plugin/marketplace.json``, and the plugin under
``claude-code/plugin/`` with its manifest, the `/kyosei` skill's two files, and
``hooks/hooks.json``. Registering the marketplace with Claude Code is
`kyo harness install` (KYO-594).

The skill's two files are the single packaged copy: `kyo install-skill` reads them from
here too (:data:`kyosei.install_skill.SKILL_ASSETS`). ``hooks.json`` is derived from the
hook fragments `kyo install-hooks` merges (:data:`kyosei.install_hooks.HOOK_INSTALL_TARGETS`)
and committed; ``tests/test_harness_plugin.py`` fails when the two disagree, and
``python -m kyosei.harness`` rewrites it.

KYO-605: Claude Code prints a failing hook's whole command, and every packaged hook command
begins with the PATH guard (KYO-316, lengthened by KYO-591's marker walk). The plugin
therefore carries that guard once, in ``hooks/kyo-hook``, and each ``hooks.json`` command
names that script through ``${CLAUDE_PLUGIN_ROOT}`` followed by the `kyo` arguments. Both
files are generated from :data:`kyosei.install_hooks.PATH_GUARD_PREFIX` and the fragments;
the copies `kyo install-hooks` writes keep the inline guard, since a project's
``settings.json`` cannot name a plugin directory.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The packaged Claude Code marketplace directory.
CLAUDE_CODE_DIR = Path(__file__).resolve().parent / "claude-code"

#: The one plugin inside it, named by the marketplace entry's ``source``.
CLAUDE_CODE_PLUGIN_DIR = CLAUDE_CODE_DIR / "plugin"

#: The plugin's hooks file, derived from the hook fragments.
CLAUDE_CODE_HOOKS_PATH = CLAUDE_CODE_PLUGIN_DIR / "hooks" / "hooks.json"

#: The plugin's one hook script: the PATH guard, then `kyo` with the script's arguments.
CLAUDE_CODE_HOOK_SCRIPT_PATH = CLAUDE_CODE_PLUGIN_DIR / "hooks" / "kyo-hook"

#: How every ``hooks.json`` command names the script. Run through ``sh`` rather than by
#: its executable bit, which a wheel build, an installer and Claude Code's plugin cache
#: would each have to preserve; a lost bit would make every hook fail with exit 126 in
#: every session, Kyosei project or not. Quoted, since the plugin root can hold a space.
HOOK_SCRIPT_INVOCATION = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/kyo-hook"'

#: What the plugin's hooks file says about itself; Claude Code shows it beside the hooks.
HOOKS_DESCRIPTION = (
    "Kyosei's session hooks: the session-start brief, the stop-time sweep and doctor, the "
    "dispatched worker's clean-tree and branch checks, and the push and command guards. "
    "Generated from the packaged hook fragments; every command exits 0 silently outside "
    "a Kyosei project."
)


def plugin_hooks_document() -> dict:
    """The plugin's ``hooks.json`` document: the ``hooks`` section `kyo install-hooks`
    writes into an empty ``.claude/settings.json``, under the plugin's own top-level key.

    Derived through :func:`kyosei.install_hooks.merge_hooks` itself rather than by a
    second reading of the fragments, so the per-event choices that function makes (which
    events a fragment installs under, the ``SessionEnd``-only ``timeout``, the stripped
    ``comment`` fields) cannot differ between the two install routes.

    Each command is then rewritten by :func:`plugin_hook_command`, so the plugin runs the
    same `kyo` invocations behind the script rather than behind the inline guard (KYO-605).
    """
    from ..install_hooks import merge_hooks

    settings, _changes = merge_hooks({})
    hooks = settings["hooks"]
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                hook["command"] = plugin_hook_command(hook["command"])
    return {"description": HOOKS_DESCRIPTION, "hooks": hooks}


def kyo_invocations(action: str) -> list[str]:
    """The `kyo` argument strings of a guard-free hook command: ``kyo a && kyo b`` gives
    ``["a", "b"]``. Refuses any other shell structure, which the script form could not
    carry without changing what the hook does."""
    invocations = []
    for part in action.split(" && "):
        if not part.startswith("kyo ") or any(c in part for c in ";|&<>`$(){}'\"\n"):
            raise ValueError(f"not a chain of plain `kyo` invocations: {action!r}")
        invocations.append(part[len("kyo ") :])
    return invocations


def plugin_hook_command(command: str) -> str:
    """A packaged fragment's command (guard, then `kyo ...` joined by ``&&``) as the
    plugin's: the hook script once per `kyo` invocation, joined by the same ``&&``. With
    `kyo` missing inside a project the first call exits 127 and ``&&`` stops the chain,
    as the inline guard's ``exit 127`` does; outside one each call exits 0 silently."""
    from ..install_hooks import PATH_GUARD_PREFIX

    if not command.startswith(PATH_GUARD_PREFIX):
        raise ValueError(f"a packaged hook command without the current PATH guard: {command!r}")
    return " && ".join(
        f"{HOOK_SCRIPT_INVOCATION} {args}"
        for args in kyo_invocations(command[len(PATH_GUARD_PREFIX) :])
    )


def render_hook_script() -> str:
    """The plugin's ``hooks/kyo-hook``: the fragments' PATH guard, character for
    character, then ``exec kyo "$@"``. Its ``exit 0`` and ``exit 127`` end the script
    exactly where they end the inline command."""
    from ..install_hooks import PATH_GUARD_PREFIX

    return (
        "#!/bin/sh\n"
        "# Generated by `python -m kyosei.harness` from kyosei.install_hooks.PATH_GUARD_PREFIX\n"
        "# (KYO-605). The PATH guard the packaged hook fragments carry inline: with `kyo`\n"
        "# missing, silent exit 0 outside a Kyosei project (KYO-591) and one line with\n"
        "# exit 127 inside one (KYO-316). Otherwise it runs `kyo` with this script's\n"
        "# arguments and keeps its exit status.\n"
        f'{PATH_GUARD_PREFIX}exec kyo "$@"\n'
    )


def render_plugin_hooks() -> str:
    """:func:`plugin_hooks_document` as the exact text the committed file holds."""
    return json.dumps(plugin_hooks_document(), indent=2, ensure_ascii=False) + "\n"


def _write(path: Path, text: str, mode: int | None = None) -> bool:
    changed = not (path.is_file() and path.read_text(encoding="utf-8") == text)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if mode is not None and (path.stat().st_mode & 0o777) != mode:
        path.chmod(mode)
        changed = True
    return changed


def write_plugin_hooks(path: Path = CLAUDE_CODE_HOOKS_PATH) -> bool:
    """Rewrite the plugin's ``hooks.json`` from the fragments; ``True`` when it changed."""
    return _write(path, render_plugin_hooks())


def write_hook_script(path: Path = CLAUDE_CODE_HOOK_SCRIPT_PATH) -> bool:
    """Rewrite the plugin's hook script, executable; ``True`` when it changed."""
    return _write(path, render_hook_script(), 0o755)
