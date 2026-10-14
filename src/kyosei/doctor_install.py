"""Doctor checks on what a project installs from this build: schema version, hook
config, skill, the Claude Code plugin (KYO-596), procedures ledger, build lag, and setup
commands. Registered in
:data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import (
    cmd_check_command,
    install_hooks,
    install_procedures,
    install_skill,
    procedures,
    scaffold,
)
from . import config as config_module
from .config import CONFIG_NAME
from .schema import SCHEMA_VERSION
from .store import Store


def check_schema_version(store: Store) -> list[str]:
    stored = store.get_meta("schema_version")
    if stored is None:
        return ["meta table has no schema_version row"]
    if int(stored) != SCHEMA_VERSION:
        return [f"database schema_version is {stored}, this build speaks {SCHEMA_VERSION}"]
    return []


# ---------- KYO-596: the harness item, for a project on the Claude Code plugin
#
# A project on the plugin has no copied hooks, skill or CLAUDE.md section to compare, so
# the three items that compare them say nothing true about it. One item, `harness`,
# replaces them there; a project on the copies keeps the three as they are. Which of the
# two a project is on is read off the facts below, never recorded: a project whose
# settings enable `kyosei@kyosei` is on the plugin; one that does not, but carries
# Kyosei's copied hooks or skill, is on the copies; one that carries neither is on the
# plugin and `missing`, since the plugin is what `kyo onboard` sets up by default.

#: The harness item's states.
HARNESS_PRESENT = "present"
HARNESS_MISSING = "missing"
HARNESS_BEHIND = "behind"
HARNESS_COPIED = "copied"
HARNESS_DOUBLED = "doubled"
#: Not one of the states above: a settings file whose enablement or copies cannot be
#: read, so no state can be told. Its fix is the file's correction.
HARNESS_UNREADABLE = "unreadable"

#: How a run that asked `claude` names its source.
HARNESS_SOURCE_CLAUDE = "`claude plugin list --json`"

#: The settings files that can carry ``enabledPlugins``, highest precedence first (Claude
#: Code: local overrides project, project overrides user). Relative to the code root; the
#: user file is under the Claude Code config directory.
HARNESS_PROJECT_SETTINGS = (".claude/settings.local.json", ".claude/settings.json")


@dataclass(frozen=True)
class HarnessState:
    """What the harness item reads, for `kyo onboard --check` and `kyo doctor` alike."""

    #: False when the project is on the copies: the hooks, skill and CLAUDE.md-section
    #: items apply and this one is not shown.
    on_plugin: bool
    #: One of the ``HARNESS_*`` states; ``None`` when not :attr:`on_plugin`.
    status: str | None
    detail: str
    fix: str | None
    #: Whether any settings file enables ``kyosei@kyosei`` for this project.
    enabled: bool
    #: Where the plugin facts came from: :data:`HARNESS_SOURCE_CLAUDE`, Claude Code's own
    #: files, or ``None`` when no plugin fact was needed (nothing enables the plugin).
    source: str | None


def claude_config_dir(environ: Mapping[str, str]) -> Path:
    """Claude Code's configuration directory: ``$CLAUDE_CONFIG_DIR``, else ``~/.claude``."""
    configured = environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    home = environ.get("HOME", "").strip()
    return (Path(home) if home else Path.home()) / ".claude"


def _json_file(path: Path) -> tuple[object, str | None]:
    """``(document, None)``, ``(None, None)`` when absent, ``(None, why)`` when unreadable."""
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{path} is not readable JSON ({exc})"


def _main_checkout(code_root: Path) -> Path | None:
    """The main checkout when ``code_root`` is a linked git worktree, else ``None``.

    Local scope is recorded against the main checkout and covers its worktrees, so
    a dispatch worktree reads the main checkout's ``settings.local.json`` too.
    """
    if not (code_root / ".git").is_file():
        return None
    from .git_queries import run_git

    try:
        done = run_git(code_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except OSError:
        return None
    common = Path(done.stdout.strip()) if done.returncode == 0 else None
    if common is None or common.name != ".git":
        return None
    main = common.parent
    return None if main.resolve() == code_root.resolve() else main


def plugin_enablement(
    code_root: Path, environ: Mapping[str, str]
) -> tuple[bool | None, str | None, list[str]]:
    """``(enabled, file, unreadable)``: the ``enabledPlugins`` value for ``kyosei@kyosei``
    that wins for this project, the settings file it came from, and every settings file
    that could not be read. ``enabled`` is ``None`` when no file names the plugin."""
    from .cmd_harness import PLUGIN_ID

    candidates = [code_root / rel for rel in HARNESS_PROJECT_SETTINGS]
    main = _main_checkout(code_root)
    if main is not None:
        candidates.insert(1, main / HARNESS_PROJECT_SETTINGS[0])
    candidates.append(claude_config_dir(environ) / "settings.json")
    unreadable: list[str] = []
    for path in candidates:
        document, why = _json_file(path)
        if why is not None:
            unreadable.append(why)
            continue
        plugins = document.get("enabledPlugins") if isinstance(document, dict) else None
        if isinstance(plugins, dict) and PLUGIN_ID in plugins:
            return bool(plugins[PLUGIN_ID]), str(path), unreadable
    return None, None, unreadable


def repository_copies(code_root: Path) -> tuple[list[str], list[str], str | None]:
    """``(hook commands, skill files, unreadable)``: what `kyo install-hooks` and `kyo
    install-skill` copied into the repository, matched as `kyo harness install` matches
    them (:func:`kyosei.cmd_harness.copied_hook_commands`)."""
    from .cmd_harness import copied_hook_commands
    from .errors import KyoseiError

    try:
        hooks = copied_hook_commands(code_root)
        unreadable = None
    except KyoseiError as exc:
        hooks, unreadable = [], str(exc)
    skills = [
        str(asset.target)
        for asset in install_skill.SKILL_ASSETS
        if (code_root / asset.target).is_file()
    ]
    return hooks, skills, unreadable


def _plugin_from_claude(
    program: str, code_root: Path, environ: Mapping[str, str]
) -> tuple[dict | None, str | None]:
    """``(entry, None)`` from `claude plugin list --json` run in the project (``entry``
    ``None`` when the plugin is not installed), or ``(None, why)`` when it did not answer."""
    import subprocess

    from .cmd_harness import CLAUDE_TIMEOUT_SECONDS, PLUGIN_ID

    try:
        done = subprocess.run(
            [program, "plugin", "list", "--json"],
            cwd=code_root,
            env=dict(environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"`claude plugin list --json` did not run: {exc}"
    if done.returncode != 0:
        return None, (
            f"`claude plugin list --json` exited {done.returncode}: "
            f"{(done.stderr or done.stdout).strip() or 'no output'}"
        )
    try:
        listed = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        return None, f"`claude plugin list --json` printed no JSON list ({exc})"
    if not isinstance(listed, list):
        return None, "`claude plugin list --json` printed no JSON list"
    for entry in listed:
        if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
            return entry, None
    return {}, None


def _plugin_from_files(code_root: Path, environ: Mapping[str, str]) -> dict:
    """What `claude plugin list --json` would say, read from Claude Code's own files under
    ``<config dir>/plugins/``: ``{}`` when the plugin is not installed for this project,
    else an entry with ``readFromFolder`` where the marketplace is a local folder."""
    from .cmd_harness import PLUGIN_ID

    plugins_dir = claude_config_dir(environ) / "plugins"
    installed, _why = _json_file(plugins_dir / "installed_plugins.json")
    records = (
        installed.get("plugins", {}).get(PLUGIN_ID)
        if isinstance(installed, dict) and isinstance(installed.get("plugins"), dict)
        else None
    )
    here = {str(code_root.resolve())}
    main = _main_checkout(code_root)
    if main is not None:
        here.add(str(main.resolve()))

    def covers(record: object) -> bool:
        if not isinstance(record, dict):
            return False
        if record.get("scope") == "user":
            return True
        project = record.get("projectPath")
        return isinstance(project, str) and str(Path(project).resolve()) in here

    if not isinstance(records, list) or not any(covers(r) for r in records):
        return {}
    known, _why = _json_file(plugins_dir / "known_marketplaces.json")
    marketplace = known.get("kyosei") if isinstance(known, dict) else None
    source = marketplace.get("source") if isinstance(marketplace, dict) else None
    kind = source.get("source") if isinstance(source, dict) else None
    entry: dict = {"id": PLUGIN_ID, "enabled": True}
    if kind == "directory" and isinstance(source.get("path"), str):
        entry["readFromFolder"] = str(Path(source["path"]) / "plugin")
    elif kind == "file" and isinstance(marketplace.get("installLocation"), str):
        entry["readFromFolder"] = str(Path(marketplace["installLocation"]) / "plugin")
    return entry


def harness_state(
    code_root: Path, environ: Mapping[str, str] | None = None, *, ask_claude: bool = True
) -> HarnessState:
    """The harness item for the project at ``code_root``. Reads only.

    The enablement is the ``enabledPlugins`` key of the settings files (documented), read
    first and without running anything, so a project that enables nothing costs no
    subprocess. Only when a settings file enables the plugin is `claude plugin list
    --json` asked, in the project, for whether Claude Code loads it and from where; with
    ``claude`` not on PATH, or ``ask_claude`` false (`kyo doctor --hook`, on every stop),
    Claude Code's files under ``<config dir>/plugins/`` are read instead, and the detail
    says which source it used.
    """
    from .cmd_harness import (
        MIGRATE_COMMAND,
        PLUGIN_ID,
        claude_program,
        link_path,
        package_dir,
    )

    env = dict(os.environ if environ is None else environ)
    install = "`kyo harness install claude-code`"
    enabled, enabled_in, unreadable_settings = plugin_enablement(code_root, env)
    hooks, skills, unreadable_copies = repository_copies(code_root)
    copies = [f"hook `{command}` in .claude/settings.json" for command in hooks] + skills

    if not enabled:
        if unreadable_copies is not None and not hooks and not skills:
            return HarnessState(
                False, None, unreadable_copies, None, enabled=False, source=None
            )
        if hooks or skills:
            return HarnessState(False, None, "on the copies", None, enabled=False, source=None)
        why = (
            f"{PLUGIN_ID} is disabled for this project in {enabled_in}"
            if enabled is False
            else f"no settings file enables {PLUGIN_ID} for this project"
        )
        return HarnessState(
            True,
            HARNESS_MISSING,
            f"{why}, and none of Kyosei's hooks or skill files are copied into the "
            "repository, so no Claude Code session here runs Kyosei's hooks or loads its "
            "skill" + (f" ({'; '.join(unreadable_settings)})" if unreadable_settings else ""),
            f"{install} (registers the plugin on this machine and enables it here at local "
            "scope); the alternative is the copies, `kyo install-hooks --copies`, which writes "
            ".claude/settings.json: ask the operator first",
            enabled=False,
            source=None,
        )

    def state(status: str, detail: str, fix: str | None, source: str | None) -> HarnessState:
        said = f" (read from {source})" if source else ""
        return HarnessState(True, status, detail + said, fix, enabled=True, source=source)

    enabled_text = f"{PLUGIN_ID} is enabled in {enabled_in}"
    if unreadable_copies is not None:
        return state(
            HARNESS_UNREADABLE,
            f"{enabled_text}, but whether the repository still carries copied hooks cannot "
            f"be told: {unreadable_copies}",
            "correct .claude/settings.json and run `kyo onboard --check` again",
            None,
        )
    if copies:
        return state(
            HARNESS_DOUBLED,
            f"{enabled_text}, and the repository still carries Kyosei's copies, so every "
            "copied hook runs twice and the skill exists twice: " + "; ".join(copies),
            f"`{MIGRATE_COMMAND}` (removes the copies; the plugin stays enabled)",
            None,
        )

    link = link_path(env)
    target = package_dir()
    if link.is_symlink():
        pointed = Path(os.readlink(link))
        if not pointed.exists():
            return state(
                HARNESS_BEHIND,
                f"{enabled_text}, but the stable path {link} is broken: it points at "
                f"{pointed}, which no longer exists, so the plugin does not load",
                f"{install} (repoints the link at this build)",
                None,
            )
        if pointed.resolve() != target.resolve():
            return state(
                HARNESS_BEHIND,
                f"{enabled_text}, but the stable path {link} points at {pointed}, another "
                f"kyo's plugin, not this build's ({target})",
                f"{install} (repoints the link at this build)",
                None,
            )
    elif link.exists():
        return state(
            HARNESS_BEHIND,
            f"{enabled_text}, but {link} is not a symbolic link to this build's plugin",
            f"move {link} aside, then {install}",
            None,
        )

    program = claude_program(env) if ask_claude else None
    entry: dict | None = None
    source: str
    if program is not None:
        entry, failed = _plugin_from_claude(program, code_root, env)
        source = HARNESS_SOURCE_CLAUDE
        if failed is not None:
            entry = None
            source = (
                f"Claude Code's files under {claude_config_dir(env) / 'plugins'}, since "
                f"{failed}"
            )
    else:
        why = "`kyo doctor --hook` runs no claude" if not ask_claude else "claude is not on PATH"
        source = f"Claude Code's files under {claude_config_dir(env) / 'plugins'}, since {why}"
    if entry is None:
        entry = _plugin_from_files(code_root, env)

    stable = str(link / "plugin")
    if not entry:
        return state(
            HARNESS_MISSING,
            f"{enabled_text}, but Claude Code has no {PLUGIN_ID} installed for this project: "
            "the marketplace is not registered, or the plugin was never installed here",
            f"{install}, or `claude plugin install {PLUGIN_ID} --scope local` in the project "
            "once the marketplace is registered",
            source,
        )
    if not entry.get("enabled", False):
        return state(
            HARNESS_MISSING,
            f"{PLUGIN_ID} is installed, but Claude Code does not enable it for this project",
            f"{install}, or `claude plugin install {PLUGIN_ID} --scope local` in the project",
            source,
        )
    folder = entry.get("readFromFolder")
    re_register = (
        f"`claude plugin marketplace remove kyosei`, then {install} (it registers the "
        f"marketplace at {link} and enables the plugin here again; run it in every other "
        "project that had it enabled, since removing the marketplace uninstalls it there)"
    )
    if not isinstance(folder, str) or not folder:
        return state(
            HARNESS_COPIED,
            f"Claude Code loads {PLUGIN_ID} from a cached copy rather than from a folder, so "
            "it does not follow this build and can go stale",
            re_register,
            source,
        )
    if folder != stable:
        return state(
            HARNESS_COPIED,
            f"Claude Code loads {PLUGIN_ID} from {folder}, not from the stable path {stable}, "
            "so it does not follow this build when kyo is reinstalled",
            re_register,
            source,
        )
    return state(
        HARNESS_PRESENT,
        f"{enabled_text}, and Claude Code loads it from {stable}, which is this build's",
        None,
        source,
    )


def on_the_plugin(store: Store) -> bool:
    """Whether a settings file enables the plugin here, which retires the copy checks.

    Public because the session-start brief asks the same question before it names a copy
    remedy (KYO-608): brief and doctor must not disagree about which route a project is on.
    """
    enabled, _file, _unreadable = plugin_enablement(store.config.repo_root, os.environ)
    return bool(enabled)


def _harness_findings(store: Store, *, ask_claude: bool) -> list[str]:
    state = harness_state(store.config.repo_root, os.environ, ask_claude=ask_claude)
    # Silent unless the plugin is enabled: a project that enables it nowhere is either
    # on the copies (the hook and skill checks speak) or has set up neither, which doctor
    # has never called a problem (``check_hook_config`` is silent with no settings file).
    if not state.enabled or state.status == HARNESS_PRESENT:
        return []
    fix = f" Fix: {state.fix}." if state.fix else ""
    return [f"harness is {state.status}: {state.detail}.{fix}"]


def check_harness(store: Store) -> list[str]:
    """KYO-596: for a project on the Claude Code plugin, the one harness item, in place
    of ``hook_config``, ``skill`` and their notices, which say nothing for a project with
    no copies.

    A problem: each state it reports names the command that clears it, which is
    KYO-214's bar for staying in the failing tier. Silent where no settings file enables
    the plugin, and where it is ``present``.
    """
    return _harness_findings(store, ask_claude=True)


def check_harness_hook(store: Store, detail: dict | None = None) -> list[str]:
    """:func:`check_harness` for `kyo doctor --hook`: Claude Code's files rather than a
    `claude` subprocess on every stop (0.2 to 0.4 seconds a call). Named
    under ``hook_forms`` in `kyo doctor --hook --json`, as KYO-408 asks of every hook form."""
    if detail is not None:
        detail["harness"] = {
            "mode": "claude-code-files",
            "reason": "reads Claude Code's files rather than running `claude plugin list "
            "--json` on every stop",
        }
    return _harness_findings(store, ask_claude=False)


def check_hook_config(store: Store) -> list[str]:
    """KYO-20: is this project's ``.claude/settings.json`` current with the build?

    The only check that reads a file the tool does not own, and it earns that by
    covering the one gap every other check misses: code updates the moment the package
    does, and an installed hook configuration is a copy that never does
    (docs/design/doctor.md#kyo-20).

    Silent when no settings file exists: hooks are a convenience, not a
    requirement for driving `kyo` by hand.

    KYO-604: a hook whose command is exactly a line an earlier build packaged is
    :func:`check_hook_config_superseded`'s notice, not a finding here. What stays here is
    the broken half: unreadable settings, a packaged command no installed hook runs (an
    edited or deleted hook), a ``SessionEnd`` hook without its timeout.
    """
    if on_the_plugin(store):
        return []  # KYO-596: ``harness`` speaks for a project on the plugin
    problems, _superseded = install_hooks.hook_drift_by_tier(store.config.repo_root)
    return problems


def check_hook_config_superseded(store: Store) -> list[str]:
    """KYO-604: a hook an earlier Kyosei build installed, which this build supersedes. A
    NOTICE, declared in :data:`kyosei.doctor.NOTICE_CHECKS`.

    The per-finding split of ``hook_config`` that the tier being a property of a check
    name (KYO-214) requires: the same comparison, :func:`install_hooks.hook_drift_by_tier`,
    sorts each finding into one check or the other, so the two cannot disagree about which
    hook is which. Only a command that matches :data:`install_hooks.SUPERSEDED_HOOK_COMMANDS`
    EXACTLY lands here; anything a consumer edited matches nothing and stays a problem.

    It names a remedy (`kyo install-hooks`), so it argues the two things
    docs/design/doctor.md#kyo-267 asks of a remedy-naming notice. The condition is
    expected: every release that changes a packaged hook's text leaves every project that
    has not rerun install-hooks holding the earlier text, and that text still runs. And
    failing on it costs more than it is worth: `kyo doctor --hook` is the last step of the
    ``Stop`` and ``SessionEnd`` chain, so exit 3 here turned one release into a hook-error
    block, with the hook's whole command, on every turn of every session in every such
    project (observed in a consumer project after KYO-591's build). The notice
    still prints on every `kyo doctor`, and `kyo brief` and `kyo onboard --check` still
    name the hook and the remedy (docs/design/doctor.md#kyo-604).
    """
    if on_the_plugin(store):
        return []  # KYO-596: ``harness`` speaks for a project on the plugin
    _problems, superseded = install_hooks.hook_drift_by_tier(store.config.repo_root)
    return superseded


#: The directory `kyo html` used to own, kept here after the command was retired
#: (KYO-215) because :func:`check_retired_html_directory` is the only thing left that
#: needs the name. One string, one reader — the value is that the name a project's
#: filesystem still carries is written down once rather than typed into a message.
RETIRED_HTML_DIR_NAME = "html"


def check_retired_html_directory(store: Store) -> list[str]:
    """KYO-215: `kyosei/html/` is still on disk, and nothing writes it any more. A NOTICE.

    With `kyo html` retired the site cannot be behind, only *abandoned*, and a directory
    of pages frozen on an unknown date reads as authoritative
    (docs/design/doctor.md#kyo-215). Saying so once is the whole remedy: nothing here
    deletes anything, on ``check_hook_config``'s posture (tell, never do), and this is
    the human's own directory.

    Declared in :data:`kyosei.doctor.NOTICE_CHECKS`. It meets that bar the way
    ``procedures_rules_differ`` does — the condition is real and the reader may want
    it, and there is no command that clears it. `rm -rf` is the reader's decision about
    their own files, not a remedy the tool can name and run, and a project may keep the
    old pages on purpose as a frozen record of where the board stood.

    Silent when the directory is absent, which is every project that never generated a
    site and every project that has since removed one.
    """
    out_dir = store.config.kyosei_dir / RETIRED_HTML_DIR_NAME
    if not out_dir.is_dir():
        return []
    return [
        f"{out_dir} is left over from `kyo html`, which was retired in KYO-215 — "
        "nothing writes this directory any more, so its pages are frozen at whatever "
        "the board looked like when it was last generated. This is information, not a "
        "failure: the desktop app is the viewer now, and without it `kyo show`, "
        "`kyo list`, `kyo map` and `kyo brief` read the same store. Delete the "
        "directory when you no longer want the old pages — nothing here will delete "
        "it for you"
    ]


def check_ideas_scratchpad(store: Store) -> list[str]:
    """KYO-566.c3: a ``scratchpad.md`` under ``ideas/`` is invisible. A NOTICE.

    ``ideas.idea_files`` and the desktop app's ideas tree both skip a file by that name
    at any depth, so nothing in the CLI or the app lists, counts or opens it -- since
    KYO-114 the scratchpad is ``<per-project home>/scratchpad.md``, the one path
    :func:`kyosei.serve_files.scratchpad_target` resolves and the app's Scratchpad opens.
    Every store `kyo init` scaffolded before KYO-566 holds one, and the skill sent ideas
    there, so a thought written in it was found again only by somebody holding its path.

    Named here: each such file, where the scratchpad actually is (derived through
    :func:`kyosei.serve_files.project_paths`, the same derivation `kyo serve` uses, so
    the two cannot name different files), and that a thought to be found later belongs in
    its own ``ideas/<name>.md``. An untouched scaffold is said to hold nothing to move.

    Declared in :data:`kyosei.doctor.NOTICE_CHECKS`: every store scaffolded before this
    check has the file, so it is an expected state rather than a broken one, and moving
    a person's prose is a judgement about what each thought is -- no command can clear it.
    Silent when no such file exists.
    """
    import os

    from .serve_files import project_paths

    ideas_root = store.config.root / scaffold.IDEAS_DIR
    if not ideas_root.is_dir():
        return []
    found = sorted(
        path for path in ideas_root.rglob(scaffold.SCRATCHPAD_BASENAME) if path.is_file()
    )
    if not found:
        return []
    scratchpad = project_paths(str(store.config.root), dict(os.environ)).scratchpad
    findings = []
    for path in found:
        try:
            untouched = (
                path.read_text(encoding="utf-8") == scaffold.RETIRED_IDEAS_SCRATCHPAD_TEMPLATE
            )
        except (OSError, UnicodeDecodeError):
            untouched = False
        content = (
            "it holds only the template `kyo init` used to write, so there is nothing to "
            "move: delete it"
            if untouched
            else f"move what it holds to the scratchpad, {scratchpad} (what the desktop "
            "app's Scratchpad opens), or a thought to be found later into its own "
            f"{scaffold.IDEAS_DIR}/<name>.md file, which `kyo ideas review` lists and the "
            "app shows -- then delete it"
        )
        findings.append(
            f"{path} is invisible to the desktop app and to the CLI: `kyo ideas review`, "
            "the app's ideas tree and every idea count skip a file named "
            f"{scaffold.SCRATCHPAD_BASENAME} under {scaffold.IDEAS_DIR}/, because the "
            f"scratchpad moved to {scratchpad} (KYO-114). This is information, not a "
            f"failure: {content}"
        )
    return findings


def check_skill(store: Store) -> list[str]:
    """KYO-15: is this project's `/kyosei` skill the one this build ships?

    Third instance of the same shape, after `hook_config` and the packaged
    procedures: Kyosei ships consumers code that updates itself and *copies* that
    never do. The skill is the worst of the three to let drift, because it
    documents the CLI by name — a stale copy tells a controller to run commands
    and flags that may not exist in the build it is talking to.

    Silent when the project has no skill installed; reports, never rewrites.

    KYO-309: in the repository the packaged skill is built from, the comparison this
    makes is against that repository's own source for the skill (since KYO-593,
    ``src/kyosei/harness/claude-code/plugin/skills/kyosei/``) rather than
    against the running build's packaged copy, and the remedy it names is never `kyo
    install-skill`. The reasoning is on :func:`install_skill.skill_drift`; the age of the
    build itself moved to :func:`check_installed_build_lag`, which is a notice.

    KYO-328: the skill is two files — the workflow and the reference it cites in every
    section — and both are checked here, on identical terms and through the same
    :func:`install_skill.skill_drift`. A project holding one and not the other is reported
    too: the workflow names ``REFERENCE.md`` by path eight times, so a missing half is
    a document whose every pointer is dead, which is KYO-149's defect (a citation the
    reader cannot open) reached from the other side.

    KYO-604: a copy that is an earlier build's text, unedited since `kyo install-skill`
    wrote it, is :func:`check_skill_superseded`'s notice rather than a finding here.
    """
    if on_the_plugin(store):
        return []  # KYO-596: ``harness`` speaks for a project on the plugin
    # KYO-508: the CLAUDE.md section that makes the skill load is part of the install.
    root = store.config.repo_root
    problems, _superseded = install_skill.skill_drift_by_tier(root)
    return problems + install_skill.claude_md_drift(root)


def check_skill_superseded(store: Store) -> list[str]:
    """KYO-604: a `/kyosei` skill file an earlier build's `kyo install-skill` wrote, still
    unedited, which this build's packaged text supersedes. A NOTICE, declared in
    :data:`kyosei.doctor.NOTICE_CHECKS`.

    "Unedited" is read off the digest `kyo install-skill` records on the copy's last line
    (:data:`install_skill.SKILL_DIGEST_PREFIX`); a copy edited since, or one written
    before the digest existed, matches nothing and stays ``skill``'s problem. The two
    arguments a remedy-naming notice owes (docs/design/doctor.md#kyo-267) are the hooks'
    (``hook_config_superseded``): every release that changes the skill's text leaves every
    project that has not rerun install-skill on the earlier text, which is a complete
    skill of an earlier build rather than a broken one; and failing on it would turn one
    release into a hook-error block on every stop of every such project.
    """
    if on_the_plugin(store):
        return []  # KYO-596: ``harness`` speaks for a project on the plugin
    _problems, superseded = install_skill.skill_drift_by_tier(store.config.repo_root)
    return superseded


def check_installed_build_lag(store: Store) -> list[str]:
    """KYO-309: is the `kyo` running this older than the checkout it is running in?

    A NOTICE, declared in :data:`kyosei.doctor.NOTICE_CHECKS`, and it exists for exactly one
    project: the repository this package is built from. Everywhere else it is structurally
    silent — :func:`scaffold.repo_asset_source` finds no source file — so no consumer can
    ever read a line from it.

    The remedy is reinstalling kyo (uv tool upgrade or make install-tool), never
    `kyo install-skill`, which would
    write the installed build's older skill over this checkout's newer source. Nothing
    in the session clears it, which is why it is a notice
    (docs/design/doctor.md#kyo-309).
    """
    return install_skill.installed_build_lag(store.config.repo_root)


def check_path_build_lag(store: Store) -> list[str]:
    """KYO-335: can the `kyo` a hook would run still open this store?

    The controller writes ``kyosei/config.yml`` (or bumps ``schema_version``) from one
    build while every hook runs a *different* one; the moment the second refuses the
    store, every guard in the project exits without checking anything, and nothing
    says so (docs/design/doctor.md#kyo-335).

    Three properties, each answering something that made that window invisible:

    * **It asks the other build rather than comparing version strings.** `kyo where
      --json` exits 0 whether or not the store opens and carries the refusal verbatim
      (KYO-184), so what is reported here is that build's own sentence about its own
      store. An older build is not a finding — most of them read the store fine.
    * **It is silent when there is no second build.** `kyo doctor` run *by* a hook is
      the PATH build, and :func:`kyosei.config.path_program` returns ``None`` for the
      program already running, so the packaged `Stop` hook can never be painted red
      by this check. What reaches a reader is the working-tree run — which is the
      controller's, which is where the change that caused this was made.
    * **A probe that could not answer is not a pass.** ``can_open is None`` (a build
      too old for `kyo where`, one that timed out, one that is not executable) is
      reported as its own sentence and never as "fine", because "we asked and it
      said no" and "we could not ask" are different facts and collapsing them is the
      whole of what this record is about (law 6).

    A PROBLEM rather than a notice, and it clears the bar the other way round from
    :data:`kyosei.doctor.NOTICE_CHECKS`'s members: the condition is not expected, it is not
    something the reader must wait out, and the remedy is named and real. The cost
    that made the other remedy-naming checks into notices — a red `Stop` hook over a
    condition nobody woken can clear — cannot arise here, because the `Stop` hook's
    own `kyo` is the build this check falls silent for.
    """
    if not config_module.project_runs_kyo_from_path(store.config.repo_root):
        return []
    build = config_module.probe_path_build(store.config.root)
    if build is None or build.can_open is True:
        return []
    if build.can_open is None:
        return [
            f"the `kyo` on PATH ({build.executable}) could not be asked whether it can "
            f"open this store: {build.problem}. Every hook this project installs runs "
            "that build, so this is not a state to read as healthy — ask it by hand "
            f"(`{build.executable} where --json --root {store.config.root}`)"
        ]
    # One finding, not four: the sentences below are one argument (what refused, which
    # build, what it costs, what clears it) and `sort_into_tiers` tags a finding with
    # its check name, so four entries would put four `[path_build_lag]` bullets in
    # front of one thought.
    return ["\n".join(config_module.path_build_lag_lines(build, subject="this store"))]


def check_procedures(store: Store) -> list[str]:
    """KYO-27: every audience token in ``kyosei/procedures.md`` selects on something.

    Law 15 makes audience a first-class property, and law 2 says a field with no
    consumer is decoration — an audience nothing filters on is exactly that: a rule
    that reaches nobody while looking like it reaches someone
    (docs/design/doctor.md#kyo-27).
    """
    path = store.config.kyosei_dir / "procedures.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    known = ", ".join(sorted(procedures.KNOWN_AUDIENCES))
    # KYO-413.c4: a `###` section with no audience or no enforced-by bullet is skipped
    # by the parser, so it reached no brief and this check passed. Same finding as an
    # unknown audience, one step earlier: the rule reaches nobody.
    incomplete = [
        f"procedures.md rule {title!r} has no {' and no '.join(missing)} bullet, so the "
        "parser skips it and it currently reaches nobody — add the missing "
        f"`- **{missing[0]}**: ...` line under its `###` title (see the ledger's own "
        '"Rule format" section)'
        for title, missing in procedures.incomplete_rules(text)
    ]
    return incomplete + [
        f"procedures.md rule {title!r} names audience {token!r}, which nothing selects on "
        f"(known: {known}) — that rule currently reaches nobody"
        for title, token in procedures.unknown_audiences(text)
    ]


# ---------- KYO-212: this project's rule ledger against the one this build ships
#
# Fourth instance of the shape ``hook_config`` and ``skill`` already name: Kyosei ships
# consumers code that updates itself and *copies* that never do. ``kyosei/procedures.md``
# is copied once by `kyo init`, so without these checks a rule added to the packaged
# ledger reaches no existing project and no surface says so
# (docs/design/doctor.md#kyo-212).
#
# TWO CHECKS, NOT ONE, and the reason is the tier (KYO-214). A tier is a property of a
# check *name*, so two findings that differ in whether the reader can act on them cannot
# share one:
#
#   * a packaged rule the ledger LACKS is a problem — `kyo install-procedures` clears it
#     in one command, which is exactly the bar KYO-214 set for staying in the failing
#     tier ("a named remedy is precisely the case where the reader CAN act");
#   * a rule both copies carry whose TEXT differs is a notice — the ledger is
#     project-editable by design, so the difference may be the project's own considered
#     wording, and nothing in the tool will ever resolve it. Failing on it would ask a
#     consumer to undo a deliberate edit or live with a permanently red doctor, which is
#     how a doctor stops being read (KYO-33). Reported in full, exit 0.
#
# A rule only the project carries is never a finding in either. That is what a
# project-editable ledger is *for*, and it is the half of KYO-95.c2's decision that
# survives here: a store copy may go beyond the default freely; what it may not do is
# silently fall behind it.


def _ledger_source(store: Store) -> Path | None:
    """``src/kyosei/defaults/procedures.md`` in this checkout, or ``None`` (KYO-309).

    The one place the two ledger checks ask "which side of the package is this
    project on", so the comparison they make and the remedy they name cannot disagree
    about the answer (law 3). ``None`` for every consumer.
    """
    return scaffold.repo_asset_source(store.config.repo_root, scaffold.PROCEDURES_ASSET)


def _ledger_comparison(store: Store) -> tuple[list[procedures.LedgerDifference], list[str]]:
    """``(differences, errors)`` between this project's ledger and the default it copies.

    A pass-through to :func:`install_procedures.procedures_ledger_drift`, which `kyo
    brief`'s asset-drift block reads too, so the two cannot compare differently
    (docs/design/doctor.md#kyo-213).

    KYO-309 adds the code root beside the store root, which is what lets that function
    compare against this repository's own source when the project *is* the package's
    source; see its docstring for why the two roots are not the same directory.
    """
    return install_procedures.procedures_ledger_drift(store.config.root, store.config.repo_root)


def check_procedures_rules_missing(store: Store) -> list[str]:
    """KYO-212: is this project's ledger carrying every rule its default ships?

    A problem, not a notice: every finding here names a remedy that adds exactly the
    rules named and rewrites nothing the project wrote. The reader can clear it now,
    which is the whole of KYO-214's bar.

    KYO-309: *which* remedy depends on which copy is the source. For a consumer it is
    `kyo install-procedures`, which merges the build's packaged rules in. In the
    repository the package is built from, that command is the wrong way round — it
    reads the installed build's copy, which is this repository's OUTPUT and may predate
    the rule entirely — so the finding names the source file and the copy to make from
    it, and says why the command is not the answer here.
    """
    differences, errors = _ledger_comparison(store)
    source = _ledger_source(store)
    if source is None:
        remedy = (
            f"which {procedures.PACKAGED_LABEL} ships — run `kyo install-procedures` "
            "(it adds the rules you are missing and rewrites nothing you have)"
        )
    else:
        remedy = (
            f"which {procedures.PACKAGED_LEDGER_SOURCE_PATH} — this repository's own "
            "source for the packaged ledger — ships. Copy the rule across from there; "
            "`kyo install-procedures` reads the INSTALLED build's packaged copy, which "
            "this checkout is the source of rather than the other way round, so it may "
            "not carry this rule at all"
        )
    return errors + [
        f"kyosei/procedures.md is missing the rule {difference.title!r}, {remedy}"
        for difference in differences
        if difference.kind == procedures.LEDGER_MISSING
    ]


def check_procedures_rules_differ(store: Store) -> list[str]:
    """KYO-212: a rule both copies carry, whose text has come apart. A NOTICE.

    Declared in :data:`kyosei.doctor.NOTICE_CHECKS`, and the bar is met the way ``unresolved_stops``
    meets it: the condition is real and the reader may need it, and there is nothing
    this check itself can run that would clear it. `kyo install-procedures` will not
    touch a rule that is already there ON ITS OWN, and the packaged copy ships inside
    the installed package where no consumer can edit it — so the remedies are the
    project's own judgment: reword its copy back, keep its version deliberately (a
    legitimate answer), or run `kyo install-procedures --adopt` to take the packaged
    wording on purpose (KYO-151.c2). A failing doctor on a state a project chose is a
    doctor that gets switched off.

    So the finding says what both copies have, from where they part company, names the
    exact `--adopt` invocation beside the rule so the reader has a remedy and not only a
    finding, and says out loud that a deliberate rewording is a legitimate state and
    this is information rather than a failure.

    KYO-309: in the repository the package is built from, `--adopt` is the one thing
    this finding must not say. The other copy there is not a default the project drifted
    from but this repository's own source file, and `--adopt` takes the wording out of
    the *installed build*, which is this checkout's output and can only be older. The
    tier is right either way — nothing the tool runs resolves two wordings — so only the
    remedy sentence changes.

    KYO-515: a consumer rule whose text is exactly a wording an earlier build shipped
    (:func:`procedures.is_superseded_wording`) was not reworded by anyone, so its remedy
    is the plain `kyo install-procedures`, which moves it, and not `--adopt`. The tier
    stays a notice; after that rerun the rule agrees and this check says nothing.
    """
    differences, _errors = _ledger_comparison(store)
    source = _ledger_source(store)
    store_bodies = (
        procedures.rule_bodies(
            (store.config.root / procedures.STORE_LEDGER_PATH).read_text(encoding="utf-8")
        )
        if source is None and differences
        else {}
    )
    findings = []
    for difference in differences:
        if difference.kind != procedures.LEDGER_CHANGED:
            continue
        if source is None and procedures.is_superseded_wording(
            difference.title, store_bodies.get(difference.title, "")
        ):
            # KYO-515: nobody here wrote this text — it is a wording an earlier build
            # installed — so the plain rerun moves it, and offering `--adopt` (a
            # deliberate act) or calling it a legitimate rewording would be false.
            other, remedy = (
                procedures.PACKAGED_LABEL,
                "kyosei/procedures.md still carries the wording an earlier build "
                "installed for this rule, unedited since — run `kyo install-procedures`, "
                "which moves a rule on an earlier packaged wording to this build's and "
                "leaves every rule you reworded alone",
            )
        elif source is None:
            other, remedy = (
                procedures.PACKAGED_LABEL,
                "your ledger is yours to edit, a deliberate rewording is a legitimate "
                "state, and `kyo install-procedures` will not touch it on its own. "
                "Reword one copy, keep yours, or run `kyo install-procedures --adopt "
                f"{difference.title!r}` to take the packaged wording deliberately; "
                "either way it stays reported",
            )
        else:
            other, remedy = (
                procedures.PACKAGED_LEDGER_SOURCE_PATH,
                "both of these are yours: in this repository the second file is the "
                "source the next build's packaged ledger is made from, so this is two "
                "copies of your own text disagreeing rather than a project edit "
                "drifting from a default. Reword one to match the other. Never `kyo "
                "install-procedures --adopt` here — it takes the wording out of the "
                "installed build, which is a third text and the oldest of the three",
            )
        findings.append(
            f"kyosei/procedures.md and {other} word the rule {difference.title!r} "
            f"differently — this is information, not a failure: {remedy}:\n"
            f"    {other} has: {difference.packaged_excerpt}\n"
            f"    kyosei/procedures.md has: {difference.store_excerpt}"
        )
    return findings


def check_orchestrator_only_setup(store: Store) -> list[str]:
    """KYO-298.c5: a ``worktree_setup`` entry that is itself orchestrator-only. A NOTICE.

    **This is the exact contradiction the guard was built for, caught one layer
    earlier.** A brief that tells a worker to run a command is a stronger instruction
    than a rule telling it not to, so a worker follows ``worktree_setup``. `kyo
    check-command` refuses that at the moment it happens; this says so at the moment a
    maintainer reads their board, which is the moment the config can actually be
    changed (docs/design/doctor.md#kyo-298).

    A notice, never a failure, and it meets :data:`kyosei.doctor.NOTICE_CHECKS`'s bar the way the
    two remedy-naming members do. The condition is EXPECTED rather than broken: a
    project that provisions with `make venv` and tests with `make test` may have
    declared both on purpose, and which of the two sentences to change is an
    editorial judgment about that project's own config — Kyosei has no standing to
    call either line wrong. And failing would cost more than the finding is worth:
    `kyo doctor` runs from this project's ``Stop`` and ``SessionEnd`` hooks, so exit 3
    would paint every stop red until someone edited a file that is, on the facts,
    merely ambiguous.

    Silent in every project that declares no ``orchestrator_only_commands`` (or its
    pre-rename spelling ``controller_only_commands``), which is every project that has
    not opted in (KYO-298.c1).

    KYO-519 adds one more notice here: the key declared under its old
    spelling. Expected rather than broken (the old spelling is read permanently), and
    its remedy, a hand rename, is safe only once no older kyo reads the store -- so it
    names that precondition and the `path_build_lag` check that confirms it.
    """
    findings = []
    if store.config.orchestrator_only_key == config_module.CONTROLLER_ONLY_KEY:
        # KYO-519: the key under its pre-rename spelling. Read permanently,
        # so this is information: the rename is the operator's, by hand, and only safe
        # once no older kyo reads this store -- an older build refuses the new spelling
        # and every hook goes blind (KYO-335), which is why no command makes the edit.
        findings.append(
            f"{CONFIG_NAME} declares `{config_module.CONTROLLER_ONLY_KEY}`, the spelling "
            f"before the controller -> orchestrator rename (KYO-519); this build also reads "
            f"`{config_module.ORCHESTRATOR_ONLY_KEY}`, and both mean the same. To finish "
            f"the rename, rename the key by hand in {CONFIG_NAME} -- but only once every "
            "kyo that reads this store (each machine's kyo on PATH, which the hooks run) "
            "is at least KYO-519, because an older kyo refuses the new spelling and every "
            "hook goes blind. Run `kyo doctor` after the edit: no `path_build_lag` problem "
            "means the hooks' kyo still opens the store. Keeping the old spelling is also "
            "valid; nothing stops working"
        )
    declared = tuple(store.config.controller_only_commands)
    if not declared:
        return findings
    key = store.config.orchestrator_only_key or config_module.ORCHESTRATOR_ONLY_KEY
    for entry in store.config.worktree_setup:
        matches = cmd_check_command.controller_only_matches(str(entry), declared, cwd=None)
        for match in matches:
            findings.append(
                f"{CONFIG_NAME} tells every worker to run {str(entry)!r} in its worktree "
                f"(`worktree_setup`), and the same file declares {match.declared!r} to be "
                f"the orchestrator's alone (`{key}`) — so a brief "
                "generated here carries both sentences, and the one that names a command "
                "to run wins. This is information, not a failure: change the "
                "`worktree_setup` entry to the provisioning step alone (this project's "
                f"own `make venv`), or drop the command from `{key}` "
                "if the suite really is a worker's to run. `kyo check-command` refuses it "
                "in the meantime, which is a refusal aimed at a worker for doing what "
                "this file told it to"
            )
    return findings


def check_code_repo_identity(store: Store) -> list[str]:
    """KYO-559.c1: a split store that records no ``code_repo_id``, beside a code
    repository that now has a root commit, gets the identity recorded -- here, with one
    line saying so. A NOTICE, and the one check that writes.

    Writing is the point: the store was scaffolded before the repository had a commit
    (`git init` then `kyo init`, the order an agent takes), and filling in a missing
    identity has nothing to conflict with, so asking the operator to run a command whose
    documented purpose is overriding a safety check (`kyo init --adopt`) for it was the
    defect. A recorded identity is never touched (KYO-559.c7): a mismatch stays
    `kyo init --adopt`'s. The line is a notice because the condition is already cleared
    by the time it is read -- failing on it would paint a stop red for something done.
    """
    code_root = store.config.code_root
    if code_root is None:
        return []
    notes = scaffold.fill_in_code_repo_id(store.config.root, code_root)
    return ["; ".join(notes)] if notes else []


def check_code_repo_identity_mismatch(store: Store) -> list[str]:
    """KYO-635.c3: a split store whose recorded ``code_repo_id`` shares no root commit
    with the code repository it is linked from. A PROBLEM naming the one command that
    clears it, `kyo init --store <store> --adopt`.

    :func:`check_code_repo_identity` never writes over a recorded identity (KYO-559.c7),
    and before this check nothing reported one that differs, so a repository whose
    history was replaced (a new root commit) kept a store recording a root it no longer
    has, and every check passed. Whether the store really is this repository's is the
    operator's judgement, which is why this reports and the remedy is the override
    rather than an automatic rewrite.
    """
    code_root = store.config.code_root
    recorded = store.config.code_repo_id
    if code_root is None or recorded is None:
        return []
    store_root = store.config.root
    if Path(store_root).resolve() == Path(code_root).resolve():
        return []
    here = scaffold.code_repo_identity(Path(code_root))
    if here is None or scaffold.same_code_history(recorded, here):
        return []
    remedy = f"kyo init --store {shlex.quote(str(store_root))} --adopt"
    return [
        f"the store at {store_root} records code repository root commit {recorded}, but "
        f"the linked code repository {code_root} has root commit {here}: its history was "
        "replaced, or the store belongs to another repository. If this store is this "
        f"repository's, run `{remedy}` in {code_root} to record the new identity; if it "
        "is not, link this repository to its own store"
    ]
