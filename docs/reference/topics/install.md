# Install and upgrading

Installing `kyo`, checking which copy is running, and moving a project to a newer build.

## Install

**One `kyo` per machine, installed as a uv tool, and `kyo version` says which kind a
running copy is (KYO-317).** Every hook in every project, every
Terminal.app session, the desktop app and the app's terminal panes run that one
executable, found on your login shell's PATH. The desktop app ships no Python and no
`kyo` of its own (KYO-450): it reads your projects through the `kyo` you install
here, and says so plainly when there is none.

```sh
uv tool install kyosei   # or: pipx install kyosei -- once the package is on PyPI
uv tool install .        # from a Kyosei checkout: a snapshot of that source
```

`kyo version`'s "installed as" line (*a uv tool install*, *a working tree*, or —
when the path a copy runs from does not say — *an install this copy cannot place*)
is how you tell which copy answered a given command, and its "to move to a newer
build" line names the remedy for that kind.

Both `kyosei` and `kyo` are installed and are the same program.

**The Claude Code plugin comes from the installed `kyo`, in this order (KYO-594).**

1. Install `kyo`, as above.
2. Once per machine, run `kyo harness install claude-code`. It points
   `$KYOSEI_HOME/harness/claude-code` (`~/kyosei/harness/claude-code` by default) at the
   plugin directory inside the running package, and registers that path with
   `claude plugin marketplace add`. Claude Code loads the plugin from there in place, so a
   later upgrade of `kyo` reaches the next session without a `claude plugin` command. Run
   it again after installing `kyo` somewhere else: it repoints the link, and it is the
   only command that writes the link.
3. In each project, run `kyo harness install claude-code` again. Inside a Kyosei project
   it also enables the plugin there at local scope (`claude plugin install kyosei@kyosei
   --scope local`), which writes `.claude/settings.local.json`. If git does not already
   ignore that file, it adds it to the project's `.gitignore` first and says so. It also
   does the two things a plugin cannot carry (KYO-612, KYO-613): it writes the advisory
   git `pre-push` hook, which warns a person pushing by hand that the push publishes an
   in-repo store (see "Before your first push" in [store.md](store.md)), and once the
   plugin is enabled it removes the setup-incomplete section `kyo init` wrote into
   CLAUDE.md. A `pre-push` hook of your own is never overwritten; the command names the
   line to add to it instead.

The plugin is not offered by any hosted marketplace; the installed package is its only
source, which keeps its skill and hooks the same version as the `kyo` that runs them. With
`claude` not on PATH the command writes the link, prints the `claude` commands to run, and
exits 0. In a project whose `.claude/settings.json` still holds the hooks `kyo
install-hooks` copied there, it registers the plugin but does not enable it, since both
copies of every hook would run; it names `kyo harness migrate` as the next step.

**Moving a project from the copies to the plugin (KYO-595).** `kyo harness migrate`, run
in the project, registers and enables the plugin as above and then removes the copies: each
hook entry in `.claude/settings.json` whose command is exactly one that this or an earlier
release installed, each file under `.claude/skills/kyosei/` that is unedited (its recorded
digest still matches, or it is this build's text), and the CLAUDE.md section between the
Kyosei markers. An entry or file that was edited is left in place and named. The git
`pre-push` hook stays, because it serves a push typed by hand, and is written if the
project lacked it, as on a fresh clone (KYO-612). The command commits nothing;
the removals are working-tree changes to commit. It refuses without `claude` on PATH, before
changing anything. In Kyosei's own source checkout it migrates as anywhere else, removing a
file under `.claude/skills/kyosei/` when it equals the installed form of its packaged source
(KYO-610). The copies remain a supported mode:
`kyo install-hooks` and `kyo install-skill` keep writing them in a project that already has
them, and elsewhere write them only with `--copies`.

**In a new project, the first thing to run is `kyo onboard` (KYO-514).** It prints this
build's setup prompt: hand it to the agent working in the project (or paste it into a Claude
Code session there), and the agent works out whether it is in an empty folder, an existing
repository or an existing store, runs `kyo init` and the install commands itself, and puts
the choices that are yours (where the store lives, the id prefix, consent to set up the
plugin or the copies, worker models, and so on) to you in two rounds. It ends with a fixed readback of what was
set up. The prompt ships inside the package, so it always describes the `kyo` that printed
it. For a project set up long ago, `kyo onboard --check` reads it back against the same
checklist (each item present, missing or behind this build, with the command that fixes
it) and writes nothing; `kyo onboard --check --all` does that for every project in
`$KYOSEI_HOME/projects.json`.

**Prior-art search is an optional extra (KYO-586).**
`uv tool install 'kyosei[recx]'` adds recx-cli, with which Kyosei searches recx's index of
published repositories for existing work that matches a record's title and description.
Installing it turns nothing on: each project stays off until it records a choice with
`kyo prior-art-setting on` or `kyo prior-art-setting off`, which writes `recx.prior_art.state`
in its `kyosei/config.yml`. `kyo onboard` asks the question when the extra is installed,
and in a project that has not chosen, the session-start brief prints one line naming the
command until either answer is recorded. Installing the extra may not change the build, so
the brief's BUILD CHANGED notice cannot be relied on to say it happened. Once on, a
proposal created by `kyo new`, `kyo promote` or `kyo spawn` is searched for, and nothing is
searched on an edit, a move or a status change; the trigger is `recx.prior_art.on`, edited
by hand. `kyo prior-art <record-id>` retries a failed search or runs a one-off search for
any record. The results are text written by strangers, which an agent treats as data and
never follows or runs. "Prior-art search" in [operating.md](operating.md) has the detail.

**Install a snapshot, not the working tree.** `uv tool install -e .` (or any
editable install) puts a `.pth` pointing at this repo's `src/` into the tool
environment, so every consuming project silently tracks whatever is in the
checkout *right now* — not a release, not even a commit, but uncommitted edits
mid-save, schema changes included, and nothing reports it. Install without `-e`, and
upgrade deliberately:

```sh
uv tool install --force .    # re-snapshot after pulling or committing
```

**In Kyosei's own checkout, re-snapshot with `make install-tool`** (KYO-446). It
runs `uv tool install --force .` with `VIRTUAL_ENV` unset, so an orchestrator's or an
activated venv cannot steer the installer (KYO-297). A machine on which Kyosei is
developed typically runs one `kyo`, a snapshot of `main`, for every project. Run the target on
`main` after any merge that writes a config key or bumps `schema_version`. Until you do,
the PATH build refuses a store that carries the newer key or schema, and every hook in
that project checks nothing (KYO-335). `kyo doctor` and `kyo rebuild` name this
target as the remedy for an unreleased uv-tool build. A published release installed
from PyPI moves with `uv tool upgrade kyosei` instead, and that is the remedy they
name for a release build. Do not reach for it to move a snapshot: it would carry the
snapshot to the latest release, which is behind the source.

To check which one you have:

```sh
ls "$(dirname "$(readlink -f "$(command -v kyo)")")"/../lib/python*/site-packages | grep editable
```

A hit means editable. Nothing means a snapshot, which is what a consuming project
should have. Note that an *activated* development venv (`VIRTUAL_ENV` set) puts
`.venv/bin` ahead of the installed tool on `PATH` — correct while working on
Kyosei itself, worth knowing if a consuming project shares that shell.

**Upgrading a consumer across a `schema_version` bump.** `kyo` refuses to run
against a database an older or newer build wrote, naming the remedy: `kyo rebuild
--force`, which reconstructs `kyosei.db` from `kyosei/export/` (git-canonical, so
nothing is lost — allocations included). That rebuild reads every export document
exactly as it was written, so it leaves `kyosei/export/` stamped at the schema
version you just upgraded past — `kyo doctor`'s `exports` / `export_roundtrip`
checks report that on the very next run. The full sequence is four steps, in
order: re-snapshot the tool, `kyo rebuild --force`, `kyo export --refresh` (rewrites
the drifted export documents from the rebuilt database), then `kyo doctor` (KYO-216).

For development:

```sh
make test    # pytest
make lint    # ruff
```

`make` bootstraps a Python 3.12 virtualenv in `.venv/` on first use.

## Upgrading

The section above says how to reinstall. This one says **when it is worth doing,
and what you get**. Without it, a project can report a defect that its installed build
already fixes, because nothing says which build is running (KYO-226).

`kyo version` prints one line — the package version, the commit the wheel was
built from, that commit's date (in UTC, the clock every other date the store prints
is on — KYO-398; `--json` carries the full `commit_time`), whether the tree it was built from was dirty, and
now which kind of install this copy is (KYO-317): a uv tool install, a working
tree, or "cannot tell" when the path it runs from does not say -- and whether this build is a published release: a wheel
built from the commit tagged `v<version>` with a clean tree reads `(release)`,
anything else `unreleased build <commit>`, followed by the last release it came
after when one is reachable (KYO-445). `--json` carries `release`, `last_release`
and the `schema_version` the build speaks. It is the same line `kyo brief` now opens a session with,
so you never have to ask. Both the commit and the changelog are **build-time
products**: the commit is stamped into the package when the wheel is built (`uv
tool install .`, `pipx install .` and `make install-tool` each build one from the
working tree at that instant), and the changelog entries are regenerated
from Kyosei's store by the same hook — so neither can be older than the build
carrying it, and neither needs a `.git` or this repository to read.

**Which reinstall, and whose job it is, follows the kind `kyo version` just
named.** A working tree needs no reinstall at all — `git pull` or a newer commit
is the whole remedy, and `kyo version` says so rather than naming a command that
does not apply. A uv tool is moved by `make install-tool` from a Kyosei checkout
when it is an unreleased snapshot, and by `uv tool upgrade kyosei` when it is a
published release; `kyo version` names which. Swapping or re-packing the desktop
app does not move `kyo`, because the app ships none (KYO-450). **A running `kyo serve`
keeps serving the build it started as** ("Serving a store", in [serve-and-desktop.md](serve-and-desktop.md)): after the upgrade,

```sh
kyo serve --restart
```

A uv tool install is the four steps below, and they are yours if that is what
`kyo version` named.

**An upgrade of the machine's one `kyo` upgrades every project on it at once**
(KYO-398). Every project on a machine runs the one installed `kyo` (KYO-335), so
a `make install-tool` or `uv tool upgrade kyosei` changes the tool under every
consumer project — mid-session, if one is running — and none of them asked for it or
ran anything.
Two places tell a session so. **The session-start brief**: each brief records the
build that produced it, and a session-start brief whose build differs from the one
the project's previous session-start brief recorded opens with a `BUILD CHANGED`
notice naming both builds and listing the records the new build carries that the
old one did not (from the new build's own changelog, completed after the old
build's commit time and at or before the new one's). When the previous brief
predates recorded builds, or either build cannot say which it is, the brief says it
cannot tell rather than staying silent. **The `UserPromptSubmit` hook**
(`kyo check-build --hook`, installed with the other hooks by the plugin or by `kyo
install-hooks`) covers an upgrade that
lands mid-session: on each prompt it compares the `kyo` on PATH with the build this
session's opening brief recorded, and prints one line — both builds and the `kyo
changelog --since` that lists what changed — only when they differ. It reads the
build stamp and one row, read-only, and prints nothing otherwise. A copy that runs
live from a working tree changes build with every commit, so there the brief's
notice is one line pointing at `git log` instead of a record list.

```sh
kyo version                     # which build you are running, and which kind of install
kyo changelog --since <date>    # records completed since then, in the build you run
uv tool install --force .       # re-snapshot from a Kyosei checkout
kyo install-procedures          # in each project, on either route
kyo install-hooks && kyo install-skill   # in each project on the copies, too
```

`kyo changelog` with no argument defaults to your own build's snapshot date,
which makes a bare `kyo changelog` after a reinstall the answer to "what did I
just get". **Every record it lists is IN the build printing it** — the list is
packaged with the build and can name nothing newer, and the command says so; what a
newer build adds is in that build's own `kyo changelog`. `--since` takes a date
(UTC), a UTC timestamp such as a build's `commit_time` (strictly after it), or a
commit; `--all` prints the whole history, and `--output FILE` writes the same entries
as Markdown.

The last two lines are the per-project half of an upgrade, and which of them a project
needs depends on how it is wired into Claude Code. On the plugin, the hooks and the
skill are read in place from the build the stable link names, so the upgrade itself
updates them, and only the standing rules, a copy on every route, need `kyo
install-procedures`. On the copies, the hooks and the skill are copies too, and only a
rerun updates them, whichever route reinstalled the CLI itself. `kyo version` names
the commands for the project it runs in. `kyo install-procedures` **merges** — see
"After every upgrade, three commands" in [operating.md](operating.md) for exactly what
it will and will not rewrite.

**Until those copies are refreshed, `kyo doctor` reports an earlier build's text as a
notice rather than a problem (KYO-604)**, so an upgrade does not fail the `Stop` hook
that `kyo doctor --hook` ends. A hook whose command is exactly a line an earlier build
installed is reported under `hook_config_superseded`, naming `kyo install-hooks`. A
`/kyosei` skill file still matching the digest `kyo install-skill` recorded when it
wrote it is reported under `skill_superseded`, naming `kyo install-skill`; the digest is
the file's last line, a Markdown comment, so it travels with the file and needs no
separate file committed. Both notices exit 0. What stays a problem, and fails the
hook: a packaged hook that is missing, a Kyosei hook command that matches neither the
current nor any earlier packaged text, and a skill file that differs from the packaged
text and has either been edited since it was written or carries no digest. Every skill
copy written before KYO-604 has no digest, so it needs one `kyo install-skill` before a
later change of the skill's text can be reported as a notice. `kyo install-skill`
overwrites the file, so carry any edit of your own across first. `kyo brief` and
`kyo onboard --check` name both kinds of finding, with the remedy, as before.

**In Kyosei's own repository those three run the other way round, and they now
refuse rather than run (KYO-318).** A checkout carrying `src/kyosei/defaults/` is
where the *next* build's packaged copies come from, so an older installed `kyo`
told to install them writes its own older text over the newer source — and
reports success. All three check, before writing anything, whether the two copies
differ, and refuse with the direction stated (the build is older than, newer than,
or simply a different build from this checkout — read off git's own ancestry, or
said to be unknown rather than guessed) plus two remedies: move this copy to a
build made from the checkout, or copy the file across by hand, which is the same
write with the older build taken out of the middle. Where the two copies agree —
every consumer, and every run from a build made out of this checkout, an editable
install included — nothing above applies and the commands behave exactly as they
always have. `kyo version`'s own upgrade line asks the same question, so it stops
naming the destructive two here (KYO-318.n1).

**Desktop builds that carried their own `kyo` (KYO-450).** Earlier builds of the desktop
app shipped a CPython runtime and could link `kyo` and `kyosei` onto PATH from Kyosei →
**Install Command Line Tools…**. Current builds do neither. At every launch the app removes
any `kyo` or `kyosei` link in `~/.local/bin`, `~/bin` or `/usr/local/bin` that points into
its **own** `userData/cli/`, which only it ever wrote, along with its old shim directories,
and says once, in a dialog, which links it removed. Anything else at those names (a uv
tool's `kyo`, a link somewhere else) is left as it was. Install `kyo` as above, then run
`kyo doctor` and, on the copies, `kyo install-hooks` in each project.

Crossing a `schema_version` bump is a longer sequence, and it is in "Upgrading a
consumer across a `schema_version` bump" just above: re-snapshot, `kyo rebuild
--force`, `kyo export --refresh`, `kyo doctor`.

### Upgrading from schema 25 to 26

This subsection is what a project's orchestrator has to **do** differently after moving to a
build at schema 26 from one at schema 25, in the order it will meet it. `kyo changelog`
lists every record the newer build carries.

1. **Migrate the store (schema 25 -> 26, KYO-498).** The first `kyo` command after the
   upgrade is refused with a schema mismatch naming these steps: `kyo rebuild --force`,
   `kyo export --refresh` (every record document gains `"since_note": null`), `kyo
   doctor`. Commit the refreshed export. Then refresh the per-project copies:
   `kyo install-skill` and `kyo install-procedures` (the skill now teaches `kyo
   confirm` and the `reminder` kind; a procedures rule names `--quote`).
2. **`kyo dispatch --branch` creates the worktree (KYO-485).** Stop running the printed
   `git worktree add` by hand: the dispatch already ran it, and a second one fails with
   "already exists". `--no-create-worktree` restores the old print-the-command behaviour;
   `--base <ref>` starts the worktree (and records the base sha) at a predecessor's
   branch (KYO-485), and a re-dispatch onto an existing branch records that branch's tip
   (KYO-487).
3. **A record cannot settle past an open gate (KYO-490).** `kyo move` into a complete or
   retired status, `kyo wont-do`, `kyo void` and `kyo accept --close` are refused while
   the record carries an open gate, naming it. Clear it with `kyo ungate <gate-id> --why`
   once its condition is met, or pass `--force` to settle with the gate left open.
4. **`kyo confirm` closes a person's look in one act (KYO-494).** `kyo confirm <id>
   --quote-file q.txt` verifies the gate's criterion `human-observed` with the
   maintainer's words as evidence, clears the answered human gate, and walks the record
   to completion; it also approves a proposal's decision criterion (`--criterion`).
5. **Workers can check a report before handing it back (KYO-477).** Every brief now
   carries `kyo report <dispatch> --file .kyosei-report.json --check` (writes nothing)
   and a filled example report.
6. **Shorter acknowledgements (KYO-488, KYO-489).** Write commands print each
   explanation once, in a short form; the full text is under "Output notes" in
   `kyo reference <command>`. Refusals, remedies and report review content are unchanged.
7. **Opt-ins, each off until a project declares it:**
   - *Merged is not live*: declare a `deploy` tag in `kyosei/gate-tags.yml`, gate a
     record `awaiting:event --tag deploy` when it merges, and after a deploy clear them
     with `kyo ungate --tag deploy --why "deployed <sha>" --apply` (KYO-479, KYO-492).
     With rule 3, done then means live.
   - *Reminders*: the default registry gains a `reminder` kind with `policy.carried`
     (KYO-497; named `carry` until KYO-521). A project whose `kinds.yml` predates it
     copies the `reminder` entry from the packaged default, and adds `reminder` to the
     `accepts_from` of each kind it produces (the registry's symmetry check refuses one
     half without the other); `kyo brief` then lists open reminders with computed ages, and
     `kyo edit <id> --since-note PREFIX-S<n>` dates one from the shift note that first
     carried it (KYO-498). The brief also shows each open gate's age (KYO-496).
   - *Numbered documents*: `numbered.amends: {field, status}` in `documents.yml` lets a
     fragment's first line, `<!-- kyosei-fragment amends: 178, 186 -->`, have
     `kyo docs assemble` write the back-link and status (KYO-493). A fragment now holds
     exactly one numbered section (KYO-486).
   - *Section status*: `section_status:` on a document set makes `kyo docs check` hold
     each section's status and amended-by/superseded-by links to agree, raises a docs
     obligation when a branch changes code pointing at a section (KYO-499, KYO-500), and
     `index:` adds a generated index (`kyo docs index`, KYO-502). The pairs-agree rule
     applies to every project regardless.

8. **The `carry` kind is now `reminder`, and a decision can be filed directly (KYO-521).**
   A project whose `kinds.yml` has no `carry` entry has nothing to rename (item 7 covers
   adopting `reminder`). One that has it renames it, in this order -- each step leaves a
   registry `kyo` loads, which is why the new entry goes in before the old one comes out:
   1. In `kyosei/kinds.yml`, add the `reminder` entry, copied from the packaged
      `kinds.yml`, beside `carry`. Add `reminder` to the `accepts_from` of each kind it
      produces -- `task`, `bug`, `investigation`, `proposal` and `decision` -- and, to
      file decisions directly, `intake` to `decision`'s `accepts_from` (the packaged
      `decision` entry reads `accepts_from: [intake, proposal, investigation, reminder]`).
      `kyo doctor` passes its `kind_registry` check with both kinds present.
   2. `kyo list --kind carry` lists every carry record, settled ones included (do not
      pass `--unsettled`). Run `kyo edit <id> --kind reminder` for every one of them,
      including the settled ones: a record whose kind leaves the registry is a
      `kind_registry` problem whatever its status. A record carrying a `--since-note`
      keeps it, since `reminder` is a carried kind. Each edit is an ordinary edit
      event; nothing about the record but its kind changes.
   3. Remove the `carry` entry and every edge naming it: `carry` from the `accepts_from`
      of `task`, `bug`, `investigation` and `proposal`.
   4. `kyo doctor`: the `kind_registry` check reports no problem. Commit `kinds.yml` and
      the refreshed export together.

   A decision the maintainer made in conversation is now filed where it was made:
   `kyo new decision --title "..." --evidence-file words.md`, the file holding their
   words (who, when, where, then the words verbatim). `kyo new` refuses a `decision`
   without `--evidence-file` (`filing-requires-evidence`), because the kind's policy sets
   `requires_evidence_links`. A reminder that was waiting on the maintainer becomes one
   with `kyo spawn <reminder-id> --kind decision --evidence-file words.md`; the proposal route remains for a
   decision that needs design work first. Then `kyo install-skill`: section 6 of the
   skill now says what is a reminder and what is not -- a defect is a bug, a settled
   rule is written into the project's docs, buildable work is a task.

**Where a release comes from: `make release`** (KYO-447). A published release is one
command, run from a clean `main` whose `make test` and `make lint` passed at that sha:

```sh
make release DRY_RUN=1                                # everything but the publish
make release TOKEN_FILE=path/to/pypi-token           # the real one
```

It refuses, naming which, when `pyproject.toml` sets no version, when that version and
`kyosei.__version__` differ (the tag reads one, the stamp's release check the other),
when the tree is dirty, when `v<version>` is already tagged, or when no token file is
given. Then it tags `v<version>`, builds the **wheel only** with `VIRTUAL_ENV` unset,
reads the stamp back out of the wheel and refuses unless it names HEAD, clean, a
release, that version, and publishes with `uv publish`. The token goes from the file
into `UV_PUBLISH_TOKEN`, never onto a command line or into the output. The tag is
deleted on every exit that published nothing: a failed build, a stamp refusal, a failed
publish, and a dry run. Nothing is pushed.

**Kyosei ships no sdist, and `uv build` with no flags says so.** The wheel's stamp and
changelog can only be written in a checkout with its `.git` and store, and a wheel
built from an sdist has neither. So the build backend refuses an sdist with a message
that names `uv build --wheel` and `make release`, instead of the unexplained
``backend-path entry `build_backend` does not exist`` it used to end in. The wheel is
pure Python (`py3-none-any`), so wheel-only is a complete PyPI release, and every
install route (`uv tool install .`, `make install-tool`, `uv pip install <checkout>`)
builds the wheel directly and never asks for an sdist.
