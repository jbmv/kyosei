"""``python -m kyosei.harness``: rewrite the Claude Code plugin's ``hooks.json`` and its
hook script ``hooks/kyo-hook`` from the packaged hook fragments and PATH guard (KYO-593.c2,
KYO-605). Run it in the source checkout after editing a fragment under
``src/kyosei/defaults/hooks/`` or the guard in ``kyosei.install_hooks``;
``tests/test_harness_plugin.py`` fails until it has been run."""

from __future__ import annotations

from . import (
    CLAUDE_CODE_HOOK_SCRIPT_PATH,
    CLAUDE_CODE_HOOKS_PATH,
    write_hook_script,
    write_plugin_hooks,
)

if __name__ == "__main__":
    for path, changed in (
        (CLAUDE_CODE_HOOK_SCRIPT_PATH, write_hook_script()),
        (CLAUDE_CODE_HOOKS_PATH, write_plugin_hooks()),
    ):
        print(f"{'wrote' if changed else 'unchanged'}: {path}")
