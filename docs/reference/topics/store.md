# The store and its layout

Where the store lives, what `kyo init` creates, the store's database and export, and reading it back.

## Where the store lives

governs: src/kyosei/config.py, src/kyosei/scaffold.py, src/kyosei/install_prose.py

`kyo init` has one question that is hard to reverse later: **is this project's
store committed to the same git repository as its code?** In a folder with no store,
bare `kyo init` does not answer it for you (KYO-558). It writes nothing, exits
nonzero, and names the routes, the same on a terminal and off one: `kyo onboard`
(the guided setup, recommended), `kyo init --defaults` with the store path and
prefix it would use, and `kyo init --in-repo`. `--store DIR` and `--adopt` choose
too. `kyo init --explain` prints, for the folder it is run in, what each layout keeps
in or out of the code repository and where dispatch worktrees go, and writes
nothing; that explanation is no longer printed after a scaffold, where it came after
the choice. A `kyo init` that makes or links a store ends with the items
`kyo onboard --check` reads as missing or behind, each with its fix, then
`kyo onboard`.

The default (`--defaults`) is **not**. The store goes to `~/kyosei/<project>/` — a visible
directory you control the audience of — and is initialised there as **its own git
repository**, one per project. The code repository ends up carrying no store at
all: one pointer file, `.kyosei`, naming where the store is, and one
`.gitignore` entry for `.kyosei-report.json` (KYO-206 — the path a dispatched
worker writes its report to, which belongs to the code repository under both
layouts because that is the repository a dispatch worktree is a checkout of).
`kyo init` writes both (and a pointer in CLAUDE.md, described below) and commits
none of them. `.kyosei` may stay untracked, which suits a public repository whose
link names a local path: a worktree checks out tracked files only, so a worktree
cut without the link has none, and `kyo` there reads the main checkout's link at
the same relative position, keeping the worktree as its code root (KYO-580). A
link present in the worktree itself, committed or copied, takes precedence. The
link reads:

```yaml
store: ~/kyosei/myproject
```

That path is written home-relative when it can be, so the pointer survives being
cloned onto another machine. `$KYOSEI_HOME` moves the parent directory;
`kyo init --store DIR` places one project's store anywhere you like. The first
line `kyo init` prints is the store path it chose and what chose it, including
that `$KYOSEI_HOME` moves it — worth reading before a trial run, because a store
it creates under the real `~/kyosei` stays there until you remove it. `kyo init
--in-repo` creates nothing under `$KYOSEI_HOME`; the worktree directory it names
there is made by the first `kyo dispatch --branch`.

**From inside a running dispatch's worktree, the real home is refused** (KYO-535).
`kyo init` and `kyo projects add` refuse before creating or registering anything when
they run from a worktree a running dispatch declared — seen from the cwd, from where
the running `kyosei` package was imported (a worker's own `.venv`), or from the path
given — and the home they would write is the real one: `$KYOSEI_HOME` unset (or
naming `~/kyosei`), the dispatch's worktree inside it, or its `projects.json`
registering the project. Set `$KYOSEI_HOME` to a temporary directory for a scratch
project, or `KYOSEI_ALLOW_REAL_HOME=1` when the real home is meant. The main checkout
is never a linked worktree, so the orchestrator's own commands there are unaffected.

**A store that already exists at that path is adopted, and checked first
(KYO-416).** The default path is `$KYOSEI_HOME/<directory name>`, so `~/oss/api`
and `~/work/api` both default to `~/kyosei/api`: a store found there is evidence
of a shared directory name and nothing more. So a split store records which code
repository it was made for — `code_repo_id` in its `config.yml`, that
repository's root-commit sha(s), the same identity a sweep records — and `kyo
init` compares before it links:

- a store recording the same history (a second checkout, a clone) is linked and
  the output says it was verified;
- a store recording a **different** history is refused, naming both root commits
  and the store path; `--adopt` links it anyway (a fork, a re-created
  repository) and records this repository's root commit in its place, with one line
  naming both (KYO-635), and `--store DIR` gives this repository a store of its own;
- a store that **cannot be compared** — it predates `code_repo_id`, or this
  repository has no commits yet — is refused on the default path, where the
  shared name is the only evidence, and linked with an `UNVERIFIED` line on an
  explicit `--store DIR` (naming the path is the evidence; the migration below
  ends that way) or with `--adopt`;
- `--prefix` that differs from the adopted store's prefix is refused: the store's
  records already carry its prefix, and adoption cannot change it.

A repository with no commits at `kyo init` gets no `code_repo_id`, and the output
says so. Adoption writes one into an existing store only when `--adopt` overrides a
recorded identity that differs.

**Why the default is the split store.** Committing the store to the code
repository commits, to the same place as the code and visible to everyone that
repository is visible to: `ideas/` (unfiltered by construction — that is its
purpose), every record description verbatim, shift notes (where an orchestrator
writes what it suspects and what it thinks is going wrong), evidence artifacts
stored verbatim (law 5 — whatever was in the terminal: logs, stack traces, query
output, customer identifiers), the frozen dispatch briefs quoting all of it, and
the events ledger, which has already copied every version of every one of them —
by reference for evidence artifacts since KYO-268, verbatim for everything else.
None of that is misuse; it is the tool working as designed. `kyo redact` removes
that prose from the store and the ledgers going forward and explicitly cannot
reach a commit you have already pushed.

**Gitignoring the store is not the alternative** — `kyosei.db` is already
gitignored, so ignoring `kyosei/export/` too would leave the store untracked in
its entirety: destroyed by a fresh clone, a `git clean`, or a lost laptop, with
no recovery path, since `kyo rebuild` reconstructs the database *from* the
export. The split store gives up none of that. It is still committed, still
diffable, still rebuildable — to a different remote, not to no remote.

**The in-repo layout stays first-class**, and `kyo init --in-repo` chooses it. It
is the right answer when the records are not sensitive and you want a record
change reviewed in the same pull request as the code change that motivated it —
which is real value, and is exactly what the default gives up.

**One consequence runs in the helpful direction.** `kyo check-branch` enforces
"a worker branch must not diff `kyosei/` or `ideas/`". Under the split layout a
worker branch cannot express that diff at all, because neither directory is in
the code repository: worker isolation stops being a check that can fail and
becomes a physical property. Both guards say which of the two guarantees they
are making on every run — including saying, in a repository that has configured
no store at all, that they are guarding nothing and that exit 0 proves nothing.

**One consequence does not.** A split store is *pulled*, so its `kyosei.db` —
gitignored, and rebuilt locally — can be legitimately **behind** its export, a
direction that could not arise while one process owned both files. Reads say so
and stay useful: `kyo list` and the session brief warn that what you just read is
incomplete, and name `kyo rebuild --force`. Writes refuse. Every mutating command
allocates at least one id from the database's counters and then rewrites
`kyosei/export/_allocations.json` with them, so over a behind database it would
lower the git-canonical counters and hand out an id the export has already
issued — two different records under one identifier in a committed file, which is
what law 4 exists to prevent (KYO-74, KYO-75). The refusal sits on the shared
transaction, so it covers every write including ones not yet written; `kyo
rebuild` is exempt by construction (it never opens a `Store`), which matters
because it is the remedy every one of those messages names. **After a pull, run
`kyo rebuild --force` before working.**

**In a dispatch worktree, an in-repo store is read through the main checkout.**
`kyosei.db` is gitignored and `git worktree add` checks out tracked files and
nothing else, so a worker's worktree carries a complete `kyosei/` — `config.yml`,
`export/` — and no database at all. `kyo` resolves that through git's common dir
to the same store in the main checkout and reads it there, so `kyo show
<dispatch-id>` answers in a worktree the way it answers anywhere else. Three
things follow, and they are the whole of the behaviour. What you read is the
**main checkout's live database**, not this branch's `kyosei/export/`, which is a
checkout of the branch and may be behind it — `kyo where` says which store
answered and that it was reached that way. **Writes are refused**, naming the
checkout the write would have landed in: a write from here lands outside this
branch's diff, where `kyo check-branch` could never see it. And a worktree that
has a database of its own is left alone, so nothing changes for a second checkout
somebody rebuilt in deliberately. A split store needs none of this — it is
outside the repository already, so a worktree of the code repository reaches the
one database by the ordinary route and may write to it (KYO-342).

**Dispatch worktrees of a split project sit inside the store directory** —
`~/kyosei/<project>/dispatch worktrees/<branch>`, because the store *is*
`~/kyosei/<project>/` (KYO-70). Three things keep that nesting harmless (KYO-403).
`kyo`'s upward search for a project stops at the git toplevel it started in, so a
worktree never climbs out of itself into the store and answers as an in-repo
project on the store's own `main` — outside any git repository the search is
unchanged, and `--root` never searches. The first `kyo dispatch --branch` adds
`/dispatch worktrees/` to the store repository's root `.gitignore`, so the
worktrees stay out of the store's `git status`. And `kyo check-branch` guards
`.kyosei` itself as an exact path: a worker branch that adds, deletes or
re-points the link is refused, since after a merge every guard here would answer
about the wrong store or none.

### Moving an existing store out of the code repository

There is no `kyo` command for this: it is three ordinary git operations, and the
history caveat below is the reason it is worth doing deliberately rather than
automatically.

```sh
mkdir -p ~/kyosei/<project>
mv kyosei ideas ~/kyosei/<project>/          # the whole store, both layers
git add -A && git commit -m "move the Kyosei store out of this repository"
cd ~/kyosei/<project> && git init -b main && git add -A \
  && git commit -m "Kyosei store for <project>"
cd -                                          # back in the code repository
kyo init --store ~/kyosei/<project>           # links to the store that already
                                              # exists; scaffolds nothing
```

`kyo init --store` pointed at a directory that is already a store **adopts** it —
it writes the `.kyosei` link and says so, rather than scaffolding over it. Every
record, id, event and criterion is preserved: nothing about the store's contents
changes, only where it is committed from.

**What this does not do, stated plainly:** the prose that is already in the code
repository's history stays there. Deleting the files in a new commit removes them
from the working tree, not from the commits that added them, and not from any
clone or fork already made. Removing them from history is a history rewrite
(`git filter-repo` and a force-push, coordinated with everyone who has a clone) —
the same limit `kyo redact` names about a commit that has already been pushed.

### Reclaiming the disk an old events ledger cost (KYO-268)

The shard layout stops the bleeding; it does not refund what an in-repo store has
already spent. Every commit that touched a monolithic `_events.jsonl` left a
complete copy of it in the object store, and those copies stay there after the
migration — 5,783 loose objects, 7.23 GiB, against a 219 MiB pack, measured on
one store-bearing repository. Removing them is **one deliberate act per
repository**, and the design is what makes it lossless.

**Why dropping the earlier versions loses nothing.** The ledger is append-only,
and `kyo redact` only ever scrubs text *out of* existing lines. So every earlier
committed version of `_events.jsonl` is exactly the latest one minus later
appends and minus later scrubs: there is nothing in an old version that the
current one does not have, except values a redaction has since removed. Dropping
the old versions therefore loses no history — and it removes the un-scrubbed
copies a redaction could never reach, which is the limit `kyo redact` names about
a commit that has already been pushed.

**What it is worth, stated as the arithmetic it is.** The measured before is
above: 5,783 loose objects at 7.23 GiB against a 219 MiB pack, almost all of it
versions of one path. Dropping that path drops those objects, so the expected
after is the pack plus whatever loose objects belong to everything else — a
`.git` two orders of magnitude smaller. That is arithmetic on a measurement, not
a second measurement, and the number you should trust is the one
`git count-objects -vH` gives you before and after your own.

**The order matters, and getting it wrong destroys the ledger.** Migrate first,
commit the shards, and only then rewrite:

```sh
kyo export --migrate-events            # writes kyosei/export/_events/, removes the old file
git add -A && git commit -m "migrate the events ledger to shards"
git filter-repo --path kyosei/export/_events.jsonl --invert-paths   # the rewrite
```

Run the same rewrite **before** the migration and you delete the ledger itself:
the shards do not exist yet, so the only copy of every event in the repository is
in the path being dropped, and `kyo rebuild` afterwards reconstructs a database
with an empty audit log. After the migration, that path carries nothing the
shards do not.

**This is a history rewrite and it is the maintainer's call**, for the same
reason the caveat above says so: every commit id after the touched point changes,
so every clone re-clones and every open branch has to be rebased or recreated, and
a force-push has to be coordinated with everyone who has one. `kyo doctor`
refuses nothing here and never will — nothing in this tool rewrites a repository's
history on its own. What is proved rather than asserted is that the rewrite is
safe to survive: `tests/test_rebuild.py` builds a fixture repository, migrates it,
really rewrites it with `git filter-branch --index-filter` over that path, drops
`refs/original` and expires the reflog, and asserts the rebuild produces a
byte-identical database. (`filter-repo` is the right tool against a real
repository and drops the same path; the test uses `filter-branch` only because it
ships with git and the assertion must not depend on what is installed.)

### Before your first push: `kyo check-push`

governs: src/kyosei/cmd_publish.py

The layout decision is made once, at `kyo init`, often by someone who has not yet
written anything private into the store. The consequence arrives at a `git push`
months later, when the store holds a great deal more than it did and the choice
has been forgotten — and `kyo redact` cannot help then, for the reason just
above: it cannot reach a commit that has already been pushed.

So under the in-repo layout, something warns before that push, and it says what
is actually there rather than describing what a store generally contains:

```
$ kyo check-push
kyo: contract push-publishes-the-store not met:
  - pushing this repository would publish this project's Kyosei store: ...
  -     60 idea files — ideas/ — the zero-ceremony intake layer, verbatim ...
  -     69 records — kyosei/export/*.json — every record's description ...
  -     178 evidence artifacts — ... whatever was in the terminal ...
  -     17 shift notes — ... what an orchestrator suspects, and what it thinks is going wrong
  -     25 frozen dispatch briefs — ... which quote all of the above and are immutable
  -     706 events — ... which has already copied every version of every one of them
```

**An agent is blocked; a human is warned.** The hooks (Kyosei's Claude Code plugin,
or `kyo install-hooks` on the copies) wire `kyo
check-push --pre-tool-use` into `PreToolUse`, where it exits 2 and the push never
runs — an agent asking to push has no standing to publish the maintainer's
private thinking, and must surface the decision instead of taking it. Setup
also writes `.git/hooks/pre-push`, which runs `kyo check-push --advisory`: same
message, exit 0, and `git push --no-verify` skips it, because a human pushing
their own repository owns that call. On the plugin `kyo harness install
claude-code` writes it, and `kyo harness migrate` writes it when a project moving
from the copies lacks it (KYO-612); on the copies `kyo install-hooks` does. A
`pre-push` hook you wrote yourself is never overwritten: the command names the
line below to add to it. **`.git/hooks` is not versioned**, so a fresh clone does
not have that half until the same command is run there.

**A shared hooks directory is never written (KYO-416).** Git finds hooks through
`core.hooksPath` when it is set — a global dotfiles directory such as
`~/.githooks` that every repository on the machine runs, or a tracked in-repo
directory such as husky's `.husky/`. Writing `pre-push` into the first would make
every repository run `kyo check-push`; into the second, it would add a tracked
file to your code. So the hook is written only inside the
repository's own git directory, and otherwise writes nothing and prints the
directory and the line to add to its `pre-push` yourself:

```sh
command -v kyo >/dev/null 2>&1 && { kyo check-push --advisory || true; }
```

**It fires once.** Two ways to make it stop, and they are genuinely different:

```sh
kyo check-push --acknowledge   # accept it: records who and when in kyosei/config.yml
```

or move the store out of the repository (the section above), after which the
check is silent because there is nothing left to publish. The acknowledgement is
committed with the store, so it is per project rather than per clone — a
colleague who clones the project inherits it, and can see from the recorded actor
that it was not their call.

A project on the split layout is never warned. A project that moved its store out
but still has the old copy committed in the code repository *is* — that is the
case this matters most for, and the counts then describe the copy the push would
publish rather than the live store.

**What it does not catch, stated plainly.** Matching a push in a `PreToolUse`
hook is imperfect: `git push`, `git push -u origin main` and a `gh pr create`
that pushes implicitly are not one pattern, and nothing catches a `make release`
that pushes three layers down. On a hook that *blocks*, matching broadly is the
dangerous direction, so the match is narrow and the misses are accepted. The git
`pre-push` hook is what stands behind that gap: it sees every push git itself
performs, however it was invoked.

**What it does recognise, and whose store it judges (KYO-409).** A push is a
`git` invocation whose subcommand is `push` (global options such as `-C` and `-c`
before it), or `gh pr create` / `gh repo create`, found by the same shell parse
`kyo check-command` uses (`kyosei.shell_words`): segments split on `&&`, `||`, `;`,
`|`, newlines and a single `&`, `( … )` unwrapped, and the command word read past
`NAME=value` prefixes, redirections and the wrappers `env`, `command`, `nohup`,
`time`, `timeout N`, `nice` and `exec`. So `GIT_TERMINAL_PROMPT=0 git push`, `env
git push`, `command git push`, `sleep 1 & git push` and `(git push origin main)`
are pushes, while `echo "git push"`, `git push-foo`, a comment and a heredoc body
that mention one are not. A Bash call that is not a push returns before the store
is measured at all — every Bash call reaches this hook, and measuring counts every
event line. A push is judged against **the project its own directory belongs
to**: the payload's `cwd`, moved by any `cd`/`pushd` ahead of it and by `git -C`.
A push of a scratch repository typed from inside a project is not blocked with
that project's counts — including one nested inside the project's tree, since a
project above the push's git top level is a different repository — and a push
into an unacknowledged project from another one is blocked with *its* counts. A
directory the parse cannot follow (`cd "$DIR"`, `cd -`, `git --git-dir=…`) fails
closed: it is judged against the hook's own project, as every push was before, and
`--json` says `directory_followed: false`; the other holes are the
suite guard's (`$( … )` bodies, shell keywords, `sudo`, `bash -c`, scripts).

## What `kyo init` creates

governs: src/kyosei/scaffold.py

Under the default (split) layout, `<store>` is `~/kyosei/<project>/` and the code
repository holds only `.kyosei`; under `--in-repo`, `<store>` *is* the project
directory. The contents are identical either way:

```
<store>/
  ideas/                   # empty but for a .gitignore; the scratchpad is not here
                           # but at ~/kyosei/<project>/scratchpad.md (KYO-566)
  kyosei/
    kyosei.db              # authoritative SQLite store (gitignored — see below)
    export/                # deterministic per-record JSON, committed to git
      _events/               # append-only audit ledger: bounded shards, one JSON
        000001.jsonl           # line per event, appended to the newest shard until
                               # it reaches 4 MiB, so a commit re-stores one shard
                               # rather than the whole history (KYO-268)
      _allocations.json     # id-counter snapshot; kyo rebuild reads ids from here,
                             # never from max(seq) alone -- floored at max(seq) + 1
                             # (law 4: never reissue an id; KYO-414)
    config.yml             # schema version, project id prefix, export dir; split
                           # stores also record code_repo_id, the code repository's
                           # root commit(s), checked before adoption (KYO-416)
    kinds.yml               # the kind registry — validated on every load
    scopes.yml              # the scope registry — which parts of the system exist
    statuses.yml            # the status registry — the ladder, and each rung's role
    gate-tags.yml           # the gate-tag registry — what a gate can be waiting on
    bases.yml               # saved view definitions — `kyo base list` / `kyo base run`
    procedures.md           # standing rules, tagged audience: worker|orchestrator|human
    sessions.json           # which Claude Code sessions this project has and where each
                             # one is on claude.ai; written by the session hooks, gitignored
                             # (account-linked local state, never git-canonical) -- KYO-186
    doctor-report.txt       # the full report behind `kyo doctor --hook`'s one line;
                             # written by a failing run, deleted by the next clean one,
                             # gitignored (KYO-362)
    .doctor-cache/          # what kyo doctor already verified: the last passing export
                             # round-trip and the package's parsed source, so the Stop
                             # hook re-checks only what changed; carries its own
                             # `*` .gitignore, safe to delete (KYO-408)
    .gitignore               # excludes kyosei.db, the -shm/-wal sidecars SQLite keeps
                             # beside it in WAL mode, sessions.json and doctor-report.txt,
                             # and the temporaries an interrupted atomic write can leave
                             # (sessions.json.*.tmp, *.kyosei-tmp); the export directory
                             # is git-canonical (KYO-322, KYO-362, KYO-407)
```

**The code directory's git state decides whether the store records an identity.** The
split store's `code_repo_id` is the code repository's root commit, so `kyo init` treats
three cases differently:

- *Not inside any git repository* (the usual fresh folder): `kyo init` runs `git init`
  there and commits exactly the files it wrote in that directory (`.gitignore`, the
  `.kyosei` link and CLAUDE.md), with a message that names `kyo init`, then records the
  identity.
  `--no-git-init` keeps the old behaviour: no repository is made, no identity is
  recorded, and the output says so.
- *A repository with no commits yet*: it may hold your staged work, so `kyo init` makes
  no commit. Its output says no identity was recorded; after your first commit, the
  next `kyo doctor` or session-start brief (or a re-run of `kyo init` there) records
  it in the store's config and commits it in the store, with one line saying so. No
  `--adopt` is needed for this fill-in, and no automatic path ever overwrites an
  identity already recorded (KYO-559).
- *A recorded identity this repository no longer has* (its history was replaced, or
  the store belongs to another repository): `kyo doctor` reports it as a problem,
  `code_repo_identity_mismatch`, and names `kyo init --store <store> --adopt`, run in
  the code repository, as the remedy; that command records the new root commit even
  where the repository already links to the store (KYO-635).
- *A repository with commits*: the identity is recorded at init.

**CLAUDE.md in the code repository** (KYO-558): when it has no Kyosei section,
`kyo init` writes a short one between the markers `kyo install-skill` manages,
saying this project uses Kyosei, setup is incomplete, and the next step is
`kyo onboard --check`. A later session is pointed at the missing setup even if
nothing else was installed. The section names both routes (KYO-613). On the plugin,
`kyo harness install claude-code` removes it once it has enabled the plugin, and `kyo
harness migrate` removes it with the copies (KYO-595, KYO-596); on the copies, `kyo
install-hooks --copies` or `kyo install-skill --copies` replaces it with the usual section,
and `kyo doctor` does not report it as drift while no skill is installed. A file
that already has a marked section is left alone.

Every file in that tree is yours once it is written; `kyo init` never runs again
over an existing store. Two of them can still be *topped up* later, and the same
command does both. `procedures.md` is copied from a packaged default that keeps
growing, so `kyo
install-procedures` adds the rules a newer build ships without disturbing the
ones you have — see "Operating a Kyosei project" in [operating.md](operating.md), and `kyo doctor`'s
`procedures_rules_missing` (a problem, cleared by that command) and
`procedures_rules_differ` (a notice) for what it reports and what the plain
merge declines to change (KYO-212). `--adopt "<rule title>"`/`--adopt-preamble`
are the opt-in override for exactly that notice: they rewrite one named rule,
or the preamble, from the packaged default on request, refusing a rule the
packaged default does not ship or one already identical — everything else
stays a notice nothing clears on its own (KYO-151.c2).
The second top-up is this store's own `.gitignore`, which `kyo init` writes once
and every project scaffolded before a line was added to it never hears of again:
the same command appends any pattern it is missing, additively, leaving your own
lines and your own ordering alone. The lines KYO-322 added are `kyosei.db-shm`
and `kyosei.db-wal` — SQLite keeps both beside the database for as long as
anything has it open, which with the desktop app following your store is always,
and ignoring only `kyosei.db` made `git add kyosei` fail on a file that vanished
between the status and the add.
That command is a **orchestrator** act and refuses to be anything else (KYO-151), on
every write it makes — the plain merge or an `--adopt` alike: run from a linked
git worktree — what `git worktree add`, and so every `kyo dispatch --worktree`,
creates — or from a branch other than `main` where a `main` exists, it refuses to
write and names the checkout and the branch to run it in instead.
`kyo check-branch` already fails a dispatch branch that diffs `kyosei/`, but that is
a diff check at stop time: without this refusal the store copy would have been
written for real and the worker told afterwards. `--print` writes nothing and is
never refused, so a worker can still see what the hand-off will add — which is what
it needs in order to report the hand-off accurately.
The registries beside it are a different case and get no such command:
`scopes.yml`, `gate-tags.yml` and `bases.yml` ship declaring nothing,
`statuses.yml` and `documents.yml` fall back to a default the *code* holds when a
project has no such file, and `kinds.yml` is a project's own answer to what kinds
of work it does — none of them is a ledger of rules this build keeps adding to.

And one entry in the **code** repository's root `.gitignore`, under both layouts
(KYO-206): `.kyosei-report.json`, the path every generated brief tells a
dispatched worker to write its structured report to. `kyo install-hooks` writes
the same entry, so a project that predates this gets it by upgrading rather than
by hand; an existing `.gitignore` is appended to, never rewritten, and an entry
the maintainer already added is left alone.

## The store

governs: src/kyosei/schema.py

| Table | Holds |
| --- | --- |
| `records` | id, kind, title, status, plan membership, scope, description, timestamps, and on a carried kind `since_note` — the id of the shift note that first recorded the item, whose time its carried age counts from (KYO-498) |
| `criteria` | stable opaque id, text, **state**, reason |
| `notes` | append-only prose with supersede markers |
| `evidence_artifacts` (redaction) | `kyo redact` replaces the text with `[redacted]`, recomputes the digest over the marker, and records `redacted_at`/`redacted_why`. The **original digest is not kept**: a sha256 of a short identifier is brute-forceable, so preserving it would republish the value. An event's snapshots name an artifact by `id` and `sha256` and never carry its body (KYO-268), so a new line has only the digest to rewrite; lines older builds wrote carry the text, and the scrub still reaches those. Scrubbing the events ledger is the only place Kyosei rewrites an append-only ledger — named as a trade, not hidden; the redaction is itself an event. The database's own `events` snapshots are scrubbed by the same rules in the same transaction, so a copy of `kyosei.db` does not keep what the ledger lost (KYO-405). The row commits before the ledger is scrubbed, so an interrupted run leaves the row redacted and the ledger not: `kyo doctor`'s `redactions` check reads the ledger and the events table for the id and fails on any surviving text or original digest, and rerunning `kyo redact` with the reason already recorded is a **retry** that finishes the scrub (a different reason is still refused). When the text survives in any live column the redaction did not rewrite, the output says INCOMPLETE and names each place, and `--json` carries `complete: false` with `surviving_occurrences`. A record's own title, description or criterion is a target too (`KYO-1.title`, `KYO-1.description`, `KYO-1.c2`; KYO-426): every version of that field any snapshot up to the redaction holds becomes the marker — in the row, the record's export, the ledger and the events table — and each version is also scrubbed as whole-blob text from briefs, reports and the ledger under the same length guard, so "edit first, redact second" and "redact, then edit" both leave nothing behind (an edit after the redaction snapshots the marker). A criterion keeps its id, state and verdicts; only its text becomes the marker (KYO-58). Those fields have no `redacted_at` column: the redaction event carries the reason, and `kyo doctor` reads it back and checks every snapshot up to it. It scans the events table once for all redacted ids together (KYO-426.c4) |
| `touches` | files/symbols a record touches: a *working scope*, not a claim. Authored at `kyo promote` or `kyo new`, replaced wholesale by `kyo edit --touches-file`, and validated against the repo at each of those moments. A path ending in `/` is a **directory touch** — scope the work creates or owns, accepted whether or not it exists yet, stored with the slash because that slash is what every reader keys off (KYO-209). A path beginning `+` is a **new-file touch** — one file the work creates, stored with the marker for the same reason and stripped by `contracts.touch_target` wherever a path is compared, so it claims exactly that file and never a tree (KYO-225). The symbol `+` is an **additive claim** — `path#+`, "this work adds to that existing file and edits nothing already in it" — the one symbol not required to be found in the file, disjoint from every other additive and `#symbol` claim on it, and held to by `kyo touch-drift` (KYO-300). The prior list stays readable in the events ledger |
| `gates` | orthogonal holds, each with its own id |
| `claims` | statement, level (asserted/derived/verified), method, author, time; append-only, supersede-marked like notes |
| `verifications` | per-criterion verdict + method + author + time; a criterion's `state` is derived from its latest one |
| `evidence_artifacts` | raw captures stored verbatim with a sha256, linked to what they support |
| `links` | typed edges between records |
| `dispatches` | frozen brief, base sha, state (`running`/`reported`/`terminated-no-report`/`withdrawn`), touch-override record, formally-covered record ids, closed-at |
| `reports` | a structured worker report's full validated body, linked to its dispatch |
| `flags` | a `kyo sweep`/creation-time flag (child of the record it concerns), flag_type, detail, resolve fields |
| `sweep_runs` | one row per recorded `kyo sweep` (a run at the last recorded base that finds nothing new records nothing — KYO-418): its base sha and the identity of the code history that sha belongs to, so the *next* sweep knows what to diff since — and whether it may trust it; plus that run's staleness `coverage`/`coverage_detail` |
| `brief_runs` | one row per `kyo brief`, its event/shift-note cursor, so the *next* brief knows what's new, and the build of `kyo` that produced it (commit, UTC commit time, source — KYO-398), so a session-start brief can say when the tool changed since the last session; a session-start brief run from the hook also records the Claude Code `session_id` it opened (KYO-418), and `kyo check-build --hook` compares against that session's own opening brief, silent for a payload whose session has none |
| `shift_notes` | the continuously-written shift log `kyo note --shift` appends to |
| `events` | append-only audit log of what each mutation changed. From schema_version 25 (KYO-418) an event carries `after`, the record's whole document as the mutation left it, and `changed`, the **prior value of each top-level key that differs**, in place of a second whole `before` document: a one-field edit used to store the record twice, and a top-level diff measured 19.7% of the bytes on one store's last three shards. Lines written earlier carry a whole `before` and are never rewritten, so a line with `before` is the old format and a line with `changed` the new one; every reader of a prior value keys on which it holds (`events_ledger.event_prior`), never on a version. Three kinds of mutation no longer write a line at all: a sweep at the same base as the last recorded run that files and widens nothing records nothing (no `sweep_runs` row, no event, no `_allocations.json` rewrite — the recorded run that saw that base already says it); a SubagentStop whose outcome is `unresolved` (a subagent whose prompt names no dispatch — an Explore or research agent, most of them) is counted in place on each **running** candidate dispatch's `tick_audit_json` with its reason, like KYO-375's ticks, and `kyo worktrees`, `kyo doctor` and the census fold that count in; and a tick is counted against running candidates only, so it never rewrites a finished dispatch's record export. `matched` stops, helpers of a worker and an unreadable store still write a row. A sweep that widens a standing flag (touch-changed, touch-missing, description-predates-claim — one open flag of each per record) logs a flag-level event (`after` and `changed: {detail}`) and does not move the record's `updated_at`. A snapshot is the record's whole document with every **body** named rather than carried: an evidence artifact reduced to `{id, sha256}` (KYO-268), a dispatch's frozen brief as `brief_sha256`, a report's ingested body as `body_sha256` (KYO-274). Each body is stored once, on the record's own export document; embedding them here made each one a per-mutation copy, and on one store's ledger that cost 52.0 MiB for evidence, 67.7 MiB for briefs and 49.2 MiB for reports, of 230.4 MiB. A dispatch and a report keep their other fields, where an artifact does not, because a dispatch row mutates (`running` → `reported`) and the ledger is the only place that transition is readable. Lines written by older builds still carry the bodies and are never rewritten, so both formats sit in one ledger and every reader of a snapshot keys on content rather than on a version |
| `allocations` | the only source of ids; counters never go backwards |
| `meta` | schema version — the authority `kyo` checks at open time; `kyosei/config.yml` carries a second copy that must agree |

**Statuses** (work archetype only — `knowledge`-archetype records have no status
flow) are per-project, declared in `kyosei/statuses.yml` (see "The status registry"
in [registries.md](registries.md)). The shipped default, and what a store with no such file uses, is
`open → working → built → verified → done` plus terminal `wont-do`. `built` exists
because "finished but not verified live" is a real state. `wont-do` retires the id
permanently. Entering the `complete` status on a `requires_criteria` kind demands at
least one `met` criterion with none `unmet-*` — zero or all-void criteria refuse it
("all criteria met" is vacuously true at zero of them; the `retired` status is the
honest one when nothing was actually established).

**Standings** (knowledge archetype): `current` and `void`, the two states that
archetype's lifecycle actually has. The `status` column is shared by both archetypes
and a knowledge record's sits at `open` forever, so every human-facing surface —
`kyo show`, `kyo list`, the session brief — renders the standing
instead, derived from the archetype at display time and never stored twice. Before
this (KYO-44) a decision that could never be anything else read `open`, identically
to a bug genuinely awaiting dispatch. `kyo list --status current|void` filters on
it, and `kyo show --json` carries `standing` beside the stored `status`;
`kyosei/export/` deliberately carries only `status`, since a derived field in the
git-canonical export is a second copy of one fact that could go stale.

**Criterion states**: `met`, `unmet-unbuilt`, `unmet-unobserved`,
`void-premise-gone`, `void-withdrawn`. The last three require a reason —
"I could not observe it" is not "it is fine", and `void-withdrawn` (deliberately
dropped, premise intact) is kept distinct from `void-premise-gone` (premise died)
so the first can never be used to disguise the second.

A criterion's state is **derived**, never set directly, for the three states that
are verified facts (`met`, `unmet-unbuilt`, `unmet-unobserved`): `kyo verify
<criterion-id> --verdict ... --method machine-checked|human-observed|reasoned-about
[--evidence-file f]` records the verification and moves the state to match its
verdict. Only the two void states may still be set directly, via `kyo edit
--criterion <id> --state void-premise-gone|void-withdrawn --why ...` — premise
judgments, not verified facts. `kyo doctor` fails if a criterion's state ever
disagrees with its latest verification's verdict.

**Criterion text is immutable from its first verdict, not from creation.** The
hazard immutability exists for is a verdict attesting to text that later changed —
"met, by this method, on this date, with this evidence" silently re-attaching to a
different claim. That is entirely about verdicts, and a criterion nobody has
verified has no attestation to protect. So `kyo edit --criterion <id> --text-file
f.txt` corrects the wording while the criterion has no verification recorded and is
still `unmet-unbuilt`; after that it refuses (exit 2,
`criterion-text-is-attested`) and names `kyo restate <criterion-id> --text-file
f.txt`, which voids the old criterion and adds the new wording as its own criterion
in one transaction. Being quoted into a frozen brief does *not* close the
correction window — a brief is an instruction, not an attestation, and it keeps its
own verbatim copy of what the worker read — but the correction names every dispatch
whose brief quoted the old wording, because someone read it.

The restatement relation is mechanical, not prose: the voided criterion carries a
canonical reason naming its replacement (`contracts.restate_reason`), and every
surface that shows a criterion — `kyo show`, `kyo list`, the dispatch brief —
derives `superseded by <id>` from it and prints it beside the stored state.
`kyo doctor`'s `restate_backlink_symmetry` check proves the pair stays consistent,
the way `split_backlink_symmetry` does for `kyo split` (which is the verb for
moving a criterion to a different *record*). A **dispatch brief renders only live
criteria** and states the count it withheld, naming each one and its replacement —
never silent omission.

**Gate types**: `awaiting:event`, `awaiting:human-run`, `awaiting:human-review`,
`holding-for`. A record can be `working` *and* gated. `awaiting:human-run` and
`awaiting:human-review` are different asks and are never flattened.

**Link types**: `blocks`, `duplicates`, `supersedes`, `same-question-as`,
`needs-same-change-as`, `produced-by`. `blocks` is cycle-refused at write time, and it
is the one type that changes readiness: `A blocks B` keeps B out of `kyo next`'s ready
list and makes `kyo dispatch B` refuse without `--force` until A is settled (KYO-570).

## Reading the store

governs: src/kyosei/cmd_views.py

There is no generated copy of the board to read, and that is the design rather
than a gap. An earlier build shipped `kyo html`, which regenerated a self-contained
static site under `kyosei/html/` — board, kind map, link graph, per-record pages,
all safe to open off disk. It was **retired (KYO-215)**. A
regenerated site is true at the moment it is written and wrong from the next
write onward; keeping it true meant re-running it, so the `Stop` hook re-ran it,
so every session ended with a directory of regenerated HTML churn that no commit
carried — and `kyo doctor`'s `html_freshness` check, which existed to say when
the site had fallen behind, lost a race with the session-event write on most
stops and failed by one event anyway. The desktop app had by then become the
viewer and never read the generated site at all.

**With the desktop app**: [`apps/desktop/`](../../../apps/desktop/README.md) (see
[serve-and-desktop.md](serve-and-desktop.md)) is
the board, the record view, the kind map and the link graph, read live from the
store. Nothing to regenerate and nothing to be stale.

**Without it**, every question the site answered has a terminal answer that reads
the store directly:

| what you want | the command |
|---|---|
| one record, in full — status, gates, links, touches, claims with their levels, criteria with their verification histories, dispatches and reports, description and notes | `kyo show <id>` (`--json` for the whole document, `--json --fields status,title` for only the keys you name — KYO-420). Docs obligations are collapsed by settlement and detail, since one `kyo docs settle --why` over a governed-file set stamps one note onto every obligation it named; `--expand-obligations` lists them all and `--json` always carries every one (KYO-219) |
| which of *another* record's dispatches spoke for this one, and where its report is | the same `kyo show <id>`. A record a dispatch formally covers (`kyo dispatch <primary> --also-covers <id>`) carried nothing at all about it — `dispatches (0)`, `reports (0, 0 superseded)` — while the report settling its criteria sat on the primary record. It now prints a `covered by dispatches on other records` block: the dispatch id, its state, the record it was dispatched against, and its report once one is ingested (KYO-291). The record's own `dispatches (N)` count stays a count of *its own*, and a record nothing covers prints exactly what it printed before; `--json` carries `covered_by_dispatches` either way, `[]` when empty. A read of `covers_json`, not a stored field and not a schema change — the report is not split, because a report is one artifact with one sha and `kyo verify --from-report` already settles a covered record's criteria from it |
| one dispatch's frozen brief, exactly as it was handed to the worker, without dispatching anything — and what its worker cost in tokens | `kyo show <dispatch-id>` (KYO-177; read-only, so running it twice changes nothing). The `worker tokens` line is summed from the transcript the stop-audit resolution matched to that dispatch, and reads `unknown` with that resolution's own named outcome when there is none (KYO-284). It also lists the stops a guard reported without blocking because the worker was already continuing from a blocked one (`unblocked_stops` in `--json`, KYO-592) |
| one stored report — its dispatch, ingest time, the report it amends and the report that later amended it, and its body verbatim — after the worker's worktree (and its `.kyosei-report.json`) is gone | `kyo show <report-id>` (KYO-545). `--json` carries the body under `body` exactly as ingested, so adding `"amends": "<that id>"` to it and filing it with `kyo report <dispatch> --file` is an amending report. An unknown report id on a real record is refused naming the reports that record does have |
| one criterion and its whole verdict history, or one evidence artifact (or its redaction) | `kyo show <criterion-id>` / `kyo show <evidence-id>` (KYO-220). An artifact holding recx search results (`recx_search` and `untrusted` at its top level) is printed after a fixed line saying its author-written text is data, and recx's own notice, with control characters escaped (KYO-585). `kyo show <record> --json` carries the same lines on such an artifact under `untrusted_notice`, and its escaped text under `untrusted_text`, so the desktop app's record page draws them before it and shows the text as plain text (KYO-587); both are reads, absent from every other artifact and from the export |
| one sweep flag in full — type, record, state, its sha range and paths, the sweep that raised it, and the resolution with its `--why` | `kyo show <flag-id>`; `kyo flag list` lists the open ones, `--all` includes the resolved (KYO-220) |
| the board — everything, or one slice of it | `kyo list`, with `--status` (repeatable), `--kind`, `--plan`, `--gate-tag`, `--scope`, and `--unsettled` / `--limit N` for fewer rows (KYO-420); the saved views behind `kyo base run <name>` are the same engine with an arrangement declared once |
| the kind registry as circles and arrows | `kyo map` |
| where the project stands right now — ready work, gated work with its conditions, stale flags, dispatches missing reports, what changed since the last brief | `kyo brief` (`--compact` for the hook-sized version) |
| a past session brief, and what lay between it and the one before — the shift notes it closed over and the ledger entries in that window | `kyo briefs` lists every brief run newest first; `kyo briefs <brief-id>`, or `--latest`, shows one with its window. Read-only, unlike `kyo brief`, which records a run every time; the brief's prose is not regenerated, because the store never kept it (KYO-454) |
| the whole shift log, newest first, with the note each session-start brief handed off marked | `kyo shift-notes`; `--latest` is the newest note alone, including one written after the last brief (KYO-454) |
| every open gate across every record, with the record it is on | `kyo gates`, `--type` (repeatable) to narrow; `--json` also carries each record's open-gate count (KYO-454) |
| the dispatch activity rows — every running dispatch and the newest finished ones, with verdict counts, worker tokens, end time, and what git says about each worktree now (dirty paths, commits past base, lines, last commit, merged or not) | `kyo activity`, `--finished N` for the window (default 12), `--against REF` for the trunk. A missing worktree is a fact on the row, never an error (KYO-454) |
| the tool's own invariant checks over the store | `kyo doctor` |
| which Claude Code sessions this project has had, when each was last seen and on which hook event, and where each one is on Remote Control | `kyo sessions`, which reads `kyosei/sessions.json` — the file the packaged session hooks write with `kyo sessions --hook`, and the file the desktop's claude tab reads to decide which session to open on (KYO-186). It is gitignored local state, so it answers this for *your* checkout and no other |

The link graph is the one view with no terminal equivalent; it is the desktop
app's or nothing.

**Who acted: `--actor`, and why a query for the orchestrator matches two values.**
Every write records an actor: `--actor`, else `$KYOSEI_ACTOR`, else `orchestrator`.
Before KYO-519 that default was `controller`, and rows already stored keep it, because
they record what the actor was called when it acted and the ledger is never rewritten for
vocabulary. So a query, a saved base or a script that means "the orchestrator's own
writes" has to match both `orchestrator` and `controller` in `actor`, `author` and
`resolved_by`; matching one finds only one side of the rename.

A project that still has a `kyosei/html/` directory from before the retirement is
told so once, by `kyo doctor`'s `retired_html_directory` **notice** — nothing
writes it any more, so its pages are frozen at whatever the board looked like when
they were last generated. It is a notice and not a problem because there is no
command that clears it: deleting the directory is your decision about your own
files, and keeping the old pages as a frozen record is a legitimate thing to want.
