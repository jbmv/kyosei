# Records

After this page you will be able to read a record, and you will know how its parts keep
work from drifting and carry context from one agent to the next.

## Why records have parts

A to-do line says what someone meant to do. It does not say what done looks like, which
files the work concerns, or whether anyone checked. Those gaps are where drift starts: the
work is marked finished when it was only written, the code under a plan moves while the
plan sits still, and the next agent inherits a summary instead of the facts.

A record closes each gap with a part of its own. The orchestrator writes them; you read
them with `kyo show <id>` or in the app.

## What a record holds

- **An id** such as `APP-12`. The prefix is chosen once, at setup. An id is never reused,
  so a commit message or code comment that cites one always means the same record.
- **A kind**: bug, task, investigation, proposal and so on. The kind decides what the
  record must carry and what it can lead to. See
  [Kinds, scopes and documents](06-kinds-scopes-documents.md).
- **A title and a description.**
- **A status**: `open`, `working`, `built`, `verified`, `done`, or `wont-do`. `built` means
  written but not yet checked, which is the state most worth seeing.
- **Criteria**: the conditions for done.
- **Touches**: the files, or the parts of files, the work concerns.
- **Claims and evidence**: what is believed or known about it, and the raw observations
  behind that.
- **Notes**, **links** to other records (`blocks`, `duplicates`, `supersedes` and others),
  **gates** that park it on a condition, and the history of every **dispatch** that worked
  on it.

## Criteria: done means checked

Each criterion is one testable sentence, such as "a cold start no longer rebuilds the
cache". Its state is never set by hand. It moves when someone records a verification,
which names a verdict and the method used:

- `machine-checked`: a test or command said so;
- `human-observed`: a person looked;
- `reasoned-about`: it was argued, not observed.

So a criterion can be `met`, `unmet-unbuilt` (not done yet), or `unmet-unobserved` (built,
but nobody could see it work), and each of the last kind carries the reason. A record
cannot move to `done` while a criterion is unmet. Once a criterion has a verdict its text is
frozen, so a verdict can never end up attached to different words from the ones that were
checked. Changing it later makes a new criterion and retires the old one, visibly.

## Touches: the record knows what it is about

Touches list what the work concerns: a file (`src/cache.py`), a section or symbol in a file
(`docs/notes.md#Rate limits`, `src/cache.py#rebuild`), a directory the work will create
(`src/importers/`), or one new file (`+src/importers/csv.py`). They are checked against the
repository when the record is filed, so a typo is caught then.

Touches do three jobs:

- **Staleness.** When the files a record touches change, `kyo sweep` flags the record: its
  description and criteria may no longer match the code.
- **Parallel work.** `kyo dispatch` refuses to send two workers at overlapping touches at
  once, so parallel workers do not collide. `kyo next` lists the ready records that could be
  dispatched now without a collision.
- **Scope checking.** After a worker finishes, `kyo touch-drift` compares what its branch
  changed with what the record declared and names anything outside it.

A record with no touches is invisible to all three, and `kyo dispatch` refuses it unless
forced.

## Claims and evidence: belief is not measurement

A **claim** is a statement about a record with a level:

- `asserted`: someone believes it;
- `derived`: it follows from something recorded;
- `verified`: an instrument said so, and the claim names the instrument.

Briefs print claims with their levels, so a worker knows which facts it can lean on and
which it should check. A claim is never edited. A later measurement supersedes it, and the
old one stays readable at the level it was written at.

**Evidence** is a raw capture (a log, a stack trace, a command's output) stored verbatim
with a checksum and linked to the record or claim it supports. Because it is verbatim it
can contain secrets; `kyo redact` removes text from the store after the fact.

## The record is the handoff

When the orchestrator dispatches a worker, the brief is generated from the record: its
criteria, touches, claims and the standing rules. The orchestrator writes only the part
specific to the task. When the worker reports, its report is checked against the same
record: a verdict per criterion, the commits it made, and anything in the brief it
believes is wrong. So the context passed between agents is the record itself, not a
summary someone wrote from memory.

Next: [Sessions and hooks](03-sessions-and-hooks.md).
