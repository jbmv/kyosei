# Docs governance

Layer 3: `governs:` sections, `kyo docs check`, documentation obligations and their settlement.

## Layer 3: the docs-SOT skeleton validator

governs: src/kyosei/docs_check.py, src/kyosei/docs_numbered.py, src/kyosei/docs_obligations.py, src/kyosei/docs_parse.py, src/kyosei/docs_regions.py, src/kyosei/docs_staleness.py, src/kyosei/docs_suggest.py, src/kyosei/docs_targets.py

A project's documents are meant to state only what is *currently true* about
it — no diary, no history (that's what git commit messages are for). `kyo docs
check` (KYO-13.c3) is v1's skeleton of the validator that promise needs: a
`governs:` line anywhere inside a document section (after one heading, before
the next) names one or more `path` or `path#symbol` targets, comma-separated:

```markdown
## How dispatch briefs are generated

governs: src/kyosei/brief.py, src/kyosei/brief.py#generate_brief

Prose describing the mechanism goes here...
```

The same targets may be written as a list — `governs:` alone, then one
`- target` line each (`*` and `+` bullets too, blank lines allowed before the
first item) — and either form may sit inside an HTML comment, one line
(`<!-- governs: src/x.py -->`) or spread over several. A one-line list splits on
commas outside a `{a,b,c}` brace group only, and a `governs:` that names no
target at all is a problem `kyo docs check` reports rather than a section that
quietly governs nothing (KYO-412). `kyo docs check` prints the full format with
every failure.

Each target must resolve — the path must exist as a file relative to the
project root, and a `#symbol` suffix must additionally appear as a whole-word
match in that file's text — exactly the same coarse check every
`--touches-file` (`kyo promote`, `kyo new`, `kyo edit`) already applies to a
record's `touches`, reused rather than reinvented. `kyo docs check` reports every unresolved target in one pass (exit
3, the same family as `kyo doctor`'s invariant failures) rather than stopping
at the first.

**A section's status and its links agree (KYO-499).** A section a later one
changed says so on the lines directly under its heading, Layer 3's own keys,
one per line, and a section with no `status:` reads as current:

```markdown
## KYO-294

status: amended
amended-by: docs/design/dispatch.md#kyo-485
```

In every document of every project, `kyo docs check` refuses a pair that
disagrees: an `amended-by` (or `amended_by`; `[]` names nothing) on a section
whose status is not `amended` or `superseded`, a `superseded-by` on one whose
status is not dead, and status `amended` or `superseded` with no link
(`section-link-without-status`, `section-status-without-link`). A link value is
a comma-separated list, or a bracketed one (`[188, 249]`, the shape
`kyo docs assemble` writes). A document set may opt in to four more rules with a
`section_status:` block in `kyosei/documents.yml`:

```yaml
documents:
  design:
    globs: [docs/design/**/*.md]
    section_status:
      statuses: [current, amended, superseded]   # required, non-empty
      pointer_obligations: true                  # optional, default true
      index: docs/reference/design-sections.md   # optional
      index_lists: every                         # optional: non-current (default) | every
      index_depth: 2                             # optional, only with index_lists: every
      index_fields: [area]                       # optional, only with index_lists: every
```

Any other key or value is refused by name when the file is read. In that set a
status outside `statuses` is refused (`section-status-unknown`); a link must name
a heading (`section-link-unresolved` — a bare key like `KYO-362` resolves in the
same document only, and a section elsewhere is named by its root-relative
pointer, `docs/design/dispatch.md#kyo-485`) and not its own
(`section-link-to-itself`); the linked section's prose must mention the section
linking to it, by its heading or its pointer (`section-link-not-cited-back`);
and a pointer in a `src/` file to a superseded section must name the successor
in the same comment block (`pointer-to-superseded-section`). In a set that also
declares `numbered:` (KYO-505), a bare number is a key as well: `amended_by: [15]`
— the form `kyo docs assemble` writes — names the section whose heading the
set's `numbered.heading` renders for it (`## 15.`), a number naming no such
section is `section-link-unresolved` naming the heading it looked for, and the
linked section may cite the linking one back by its number as `§15`,
`<path> §15` (the document's root-relative path, or its path relative to the
citing document; another document's path cites that document) or `section 15`
(a list such as `sections 3, 9 and 15`, or a part letter such as `§15b`, counts;
`§150` does not cite 15). A bare `15` alone is not a cite-back: it is also a
date, a version or a count, and in the notes this was measured on it was almost
always a `see_also:` list. Each refusal names
the section and its remedy and fails the build like an unresolved target, and
`kyo check-branch --subagent-stop` runs the same rules on the documents and
`src/` files a worker's branch changed, blocking with the same refusals
(`section-status-disagrees`).

`index` names a generated file that `kyo docs index` writes (KYO-502): a summary
table with one row per document of the set (its sections, how many carry each
status, and how many `src/` pointers reach it), then one table per document
listing each non-current section with its status, its links, the current head of
its chain (`amended-by` and `superseded-by` followed to a section with no links
of its own) and its opening line, then the sections no code points at. Current
sections are counted, not listed. `index_lists: every` (KYO-512) makes the
per-document tables list every section instead, one row each, so the file can be a
project's per-section table of contents: in a numbered set the number and the title
(elsewhere the heading, linked), the status, its `amended-by`/`superseded-by` links
and the head of its chain (by number in a numbered set), and the section's `governs:` targets
when the document's sections declare any. `index_depth: N` keeps the rows to
headings of level N or above (`2` lists a notes file's `##` sections and not the
`###` headings inside them), and the summary's section count follows it.
`index_fields: [<field>, ...]` (KYO-520) adds one column per named metadata field
after the fixed ones, read from the section's own `<field>: <value>` line (inside a
`notes-meta` comment too) and empty where a section has none. Those rows carry nothing a prose edit changes — no
opening line, no line count, no position — so editing a section's prose without
changing its heading, status, links or `governs:` leaves the index byte-identical.
A value other than `non-current` or `every`, `index_lists` without `index`, and an
`index_depth` outside 1–6 or without `index_lists: every` are refused by name, and
so is an `index_fields` that is not a list of distinct non-empty names, names a fixed
column, or comes without `index_lists: every`.
`kyo docs index --check` writes nothing and
exits 1, naming `kyo docs index`, when a committed index differs from what the
command would write now; a project declaring no `index` is told so and exits 0.
This repository commits `docs/reference/design-sections.md`, and
`tests/test_kyo491_design_index.py` fails when it is stale.

`pointer_obligations` (default true) makes the code's own pointers raise docs
obligations (KYO-500). When a branch changes a `src/` file, every
`<doc path>#<anchor>` pointer in it that names a live section of a set with the
flag on is placed in its region — the innermost `def`/`class`, or else the
module-level statement the comment sits above — and a pointer whose region the
branch changed raises an obligation on that section, cause `pointer`, with a
`says` naming the file, line and region. A region whose text is identical on
both sides of the diff (code inserted above it, or moved) raises nothing. Only
Python is placed; a pointer in any other file, or one the finder cannot place,
raises on any change to its file, and `kyo docs preview` lists it under "raised
at file granularity" beside the pointers its regions quieted. These obligations
are previewed, answered in `docs_verdicts[]`, settled and block `complete`
exactly as `governs:` obligations do.

**`ssot:` sets: requirements with ids, one owner each (KYO-552).** A set whose
documents say what is true now, and are edited in place, may declare `ssot:` beside
its `section_status:`. Each requirement then carries an id in its heading, and the
id is the one name every other document cites it by:

```yaml
documents:
  specs:
    globs: [docs/specs/**/*.md, docs/foundation.md]
    section_status:
      statuses: [current, superseded]   # must include superseded
    ssot:
      id_heading: '{id}. {title}'       # id key: {id} and {title} once each, no '#'
      id_pattern: '[A-Z]{1,4}-\d+'      # id key: a regex that compiles, unanchored
      prefixes:                         # id key, non-empty: prefix -> owning document
        P: docs/foundation.md
        API: docs/specs/api.md
      tombstone_max_lines: 3            # optional, a positive integer, default 3
```

`id_heading` is matched against a heading's text at any level (`## API-12. Retries
are idempotent` and `### P-1. ...` both own an id), and the
section whose heading carries an id is its owner; nothing else declares ownership.
A *bare id* is a token matching `id_pattern` as a whole word whose prefix, the part
before its `-`, is a key of `prefixes`. Every other token is ignored, so `UTF-8`,
`SHA-256` and `KYO-12` are never read as ids. Ids are unique across every document
of every set declaring `ssot:`, and resolve across all of them.

**The three id keys are optional together (KYO-553).** `id_heading`, `id_pattern`
and `prefixes` are declared all three, or none of them. A set whose sections carry
no ids declares `ssot: {}` (or `ssot: {tombstone_max_lines: 5}`): it keeps the
`section_status:` and not-`numbered:` requirements, and of the refusals below it is
checked for `ssot-tombstone-body` only. Its documents own no ids, nothing in their
headings or prose is read as an id, and their `superseded-by:` values resolve the
way a document outside every ssot set resolves them. An empty `prefixes` never
means every prefix. A block declaring some of the id keys and not the others is
refused, naming the missing keys and both remedies: declare all three, or none.

When the file is read, an unknown key is refused by name, and so are some-but-not-all of the id keys, `ssot:`
together with `numbered:` (in-place edits and an append-only log contradict each
other; remove one), `ssot:` without a `section_status:` whose `statuses` include
`superseded` (a replaced requirement stays as a superseded tombstone; add it), an
`id_heading` missing a placeholder or starting with `#`, an `id_pattern` that does
not compile or is anchored with `^`/`$`, a `prefixes` that is empty or maps a prefix
to a path none of the set's `globs` match, and a `tombstone_max_lines` that is not a
positive integer.

In those documents `kyo docs check` refuses:

- `ssot-prefix-undeclared`: a heading carrying an id whose prefix is not in
  `prefixes`. Declare the prefix, or give the section a declared one.
- `ssot-id-wrong-document`: an id owned in a document other than the one its prefix
  maps to. Move the section, or give it an id whose prefix maps to its document.
- `ssot-id-duplicate`: a second heading, in any document of any ssot set, owning an
  id already owned; the refusal names both locations. Give one a new id (ids are
  never reused or renumbered).
- `ssot-id-unresolved`: a bare id in prose (outside fenced code and outside the
  section's metadata lines) that no heading owns. Write the owning section, which the
  remedy spells out, or correct the id.
- `ssot-cites-tombstone`: a bare id in prose naming a superseded section, in a
  paragraph that does not also name the head of that section's `superseded-by`
  chain. The message names the successor to cite. A paragraph of the head section
  itself may name what it replaced.
- `ssot-tombstone-body`: a superseded section with more than `tombstone_max_lines`
  non-blank lines after its metadata lines (the opening run of `key: value` lines, a
  project's own fields such as `priority: P0` included). The message names the limit
  and the count; what replaced it belongs in its successor.

A bare id is also a link value: `superseded-by: API-31` in `docs/specs/a.md` names
the heading that owns `API-31`, in whichever ssot document that is, and
`section-link-unresolved`, `section-link-to-itself` and `section-link-not-cited-back`
apply through that resolution. The successor cites the tombstone back by its id
(`This replaces A-1.`). `kyo check-branch --subagent-stop` runs the same rules,
reading every ssot document to resolve ids and refusing only what the branch's
changed documents are part of, with `section-status-disagrees`.

**The history lint (KYO-555).** An ssot document states what is true now. A sentence
narrating how it came to be belongs in a decision record or a commit message. Each ssot
set, with or without ids, chooses what happens to such a sentence a branch adds:

```yaml
    ssot:
      history_lint: block     # off | warn | block, default warn
```

- `off`: nothing is linted in the set's documents.
- `warn`: `kyo check-branch --subagent-stop` prints each newly introduced match and never
  blocks the stop.
- `block`: as `warn`, and a newly introduced **block-grade** match also blocks the stop
  with `history-prose-introduced`. A warn-grade match never blocks.

Any other value is refused when the file is read, naming the three. A bare `off` is
YAML's `false`, and is read as `off`.

The patterns are the seven a measurement over several stores found precise (KYO-551;
Python regexes, case-insensitive, one line at a time):

| pattern | grade | matches |
|---|---|---|
| `used-to` | block | `used to`, except after `is`, `are`, `be`, `was`, `were`, `been`, `being`, `get`, `gets`, `got`, `never`, `not`, `only`, `also` |
| `previously` | block | `previously` |
| `no-longer` | warn | `no longer`, except `no longer than` and after `that`, `which`, `that are`, `that can`, `or` |
| `was-changed` | warn | `was`/`were`/`has been`/`have been` followed by `renamed`, `replaced`, `removed`, `dropped`, `retired`, `moved`, `changed`, `rewritten`, `reverted` or `deleted` |
| `until` | warn | `until` followed by a date, a record id (`KYO-12`) or `recently` |
| `anymore` | warn | `anymore`, `any more` |
| `dated-change` | warn | a line carrying both an ISO date (`2026-09`, `2026-09-03`) and a change word (`fixed`, `added`, `removed`, `renamed`, `now`, ...) |

The exact expressions are `kyosei.docs_history.PATTERNS`. Phrases the measurement
dropped as too imprecise are not linted: `since <date or record>` (`Since KYO-12` is
provenance), `as of`, `now` alone, a date in parentheses, a bare date.

**Newly introduced** means: per file and per pattern, the count of matches on the
branch's added lines exceeds the count on its removed lines (`<against>...<branch>`).
Re-adding a line that already carried the phrase, such as an edit to a long table row,
introduces nothing. A document the branch adds counts all of its matches. Only prose
counts: a match inside fenced code or an inline code span is ignored. Each printed match
names the file, the line, the pattern and the remedy: state the current fact, and put
the history in a decision record or the commit message.

To keep one block-grade match deliberately (quoting a changelog, say), commit on the
branch with a trailer naming the file and the pattern:

```
Kyosei-History-OK: docs/reference/topics/x.md used-to quoting the 1.0 changelog verbatim
```

The trailer lets that pattern through in that file and nothing else. `kyo report` lists
every `Kyosei-History-OK` trailer on the dispatch's branch under its `disagreements`, so
the orchestrator reviews each override before accepting. `kyo docs preview` lists the
same matches, both grades, for the branch it previews, and says how many would block.

**Decision obligations (KYO-554).** In an ssot set the decision record is the history
and the section is the current state, so filing a decision that names a section obliges
someone to bring that section into line, or to say it needs no change. A kind whose policy
sets `raises_docs_obligations: true` (the packaged `decision` does; any knowledge kind may)
raises a **standing** docs obligation when a record of it is written:

- What raises one. Filing the record (`kyo new`, `kyo spawn`, `kyo promote`) raises one
  obligation, cause `decision`, per section that a bare id in its title or description
  resolves to — the same bare ids and the same owners `kyo docs check` resolves, in any
  id-bearing ssot set — and one per `doc-refs` entry, cause `doc-refs`, on the section it
  names or, for a bare path, on the document's first heading (as a dispatch's doc-refs do;
  a doc-ref outside the document set raises nothing, and the write says so). A bare id with
  a declared prefix that no heading owns raises nothing, and the write's output names it.
  `kyo edit` of the title, description, doc-refs or kind raises only for sections the record
  did not already hold a standing obligation on, open or settled. A record filed before a
  build that knew the policy holds none until it is next filed or edited, so upgrading a
  store raises nothing by itself.
- Where it is listed. A standing obligation has no dispatch (`dispatch_id` is null), so no
  report will ever list it. `kyo brief` lists every open one under "standing docs
  obligations", with its record, document, section and age (under `--compact` too); `kyo
  show <record>` lists the record's with their state; `kyo doctor` reports the open count
  as a notice that does not fail; `kyo docs obligations` groups them per record.
- How it settles. `kyo docs settle <id> --verdict updated|not-affected --method <how>`,
  exactly as a dispatch's obligation settles, and a settled one leaves the brief. `kyo void`
  on the record settles its open standing obligations `not-affected`, method `void`,
  carrying the void's reason and author.

Ids as code pointers, parameter owners, a history-language lint and freshness are later
slices of KYO-551 and are not built.

**What a `#symbol` actually narrows, and on which files (KYO-153, KYO-351,
KYO-352, KYO-363).** A `path#symbol` target raises its section only when the diff
touches that symbol's own lines — but only where the region can be located,
which is a closed list of file types the instrument can read. Narrowed by
`#symbol` on these suffixes and no others: `.py`, `.md`, `.ts`, `.tsx`, `.js`,
`.mjs`, `.json`, `.yml`, `.yaml`, `.css`. On every other file type the `#symbol` is accepted, resolves, and
raises exactly as the bare path would: the narrowing is silently absent, not
broken. So `kyo docs check` says so, once per such target — a notice, never a
failure, and the exit code is unchanged — and the `governs: <path>#<symbol>`
offer `kyo report` prints beside a whole-file obligation is made only for the
targets it is true of. Without the notice, a project could narrow many TypeScript
targets on the strength of that offer before finding that it changed nothing.

Python is read with `ast` and markdown by its headings — an unindented `#` run
followed by a space, the same rule the section splitter uses, so an indented
`    # comment` in a code block or a prose `#1 priority` ends nothing (KYO-412). The brace languages are
read **textually**, by declaration and indentation rather than by matching
braces: a declaration's region runs from its line (and the comment block
directly above it) to the line before the next line indented no further. Brace
matching would have to know that a brace inside a string, a template literal or
a regex is not a brace, and every case it got wrong would end a region early and
*suppress* an obligation silently, at a gate that blocks. Indentation cannot
fail that way — an unformatted or minified file gives a region that is too
wide, which raises. Every undecidable case still raises, unchanged since
KYO-153: a symbol whose declaration cannot be found, a file added or deleted by
this branch, a revision that will not parse.

A class member is a declaration whether or not words stand in front of its name
— `private`, `public`, `protected`, `static`, `abstract`, `override`,
`readonly`, `async`, `get`, `set`, a generator's `*` — and the last member of a
class ends before the class's own closing brace (KYO-358; before it only a bare
`showLeft(): void {` was found, which missed most of the methods a document
describes). A declaration ends at its next sibling —
the next line indented no further that does not begin with closing punctuation,
so `): void {` continues a signature — whether or not a blank line separates
them (KYO-360: a property of the same name in an object literal,
`serialize: (fn) => this.serialize(fn),`, used to swallow every sibling after
it, fail the brace check, and take the method's own region down with it). The
list of words is closed, so
`return showLeft(` and `await showLeft(` stay calls. And where a `#symbol` on a
readable suffix still finds no region — the name is only imported or called
there — `kyo docs check` lists it beside the unreadable-suffix notices as
"accepted, but no region found", so a project narrowing many targets learns
which ones took without replaying old merges.

**The data formats (KYO-363), where a section governing `package.json#scripts`
used to be raised by every dependency bump.** What a symbol means is different
in each, and in each a shape the finder cannot describe is a veto rather than a
guess:

- **`.json`** — the symbol is an object key at any depth; its region is the
  key's line through the end of its value. `json.loads` decides whether the
  file is JSON at all (if not: cannot tell, which raises); the span itself
  comes from a string-aware bracket scan, because the parsed object carries no
  line numbers. A brace or a quote inside a string value ends nothing. Every
  occurrence of the key is a region, as for a repeated Python name.
- **`.yml` / `.yaml`** — the symbol is a mapping key at any depth; its region is
  the key's line through the last following line indented deeper than it, with
  the comment block directly above the key included. A key whose value is
  empty on its own line also keeps a block sequence written at its own column
  (`steps:` then `- run: make test`, the usual GitHub Actions style), since
  those items are its value (KYO-412). A block scalar's content
  is covered by that same rule, so a line reading `build: tsc` inside one
  declares nothing. Anchors, aliases, merge keys and a flow collection left
  open at the end of a line are vetoed, not approximated: their content is not
  the lines indented under the key, so a span for them would be *wrong* rather
  than absent.
- **`.css`** — the symbol is a selector or at-rule prelude, matched **whole**
  against the text before the `{` with whitespace collapsed, and its region is
  that line through the matching `}`. Whole, because a selector list is one
  decision: a document naming `.pane-left` has not named
  `.pane-left, .pane-right`. `@media` is a prelude with a block and the rules
  inside it are preludes with blocks, so both narrow. A custom property
  (`--name`) is its declaration through its `;` — declared, not merely used in
  a `var()`. Brace matching is safe here where it was not for the brace
  languages: a brace can hide only in a string or a `/* */` comment, both of
  which this reads, and an unclosed one vetoes the file.

`.scss` is deliberately *not* on the list: nesting means the text before a `{`
is not a selector on its own and `&`-composition means the selector a document
names is never the one written.

**A bare symbol, and a field inside a class (KYO-397, KYO-396).** A target with
no path — `OrderSnapshot.total_cents` — still *resolves* to every code
file that mentions it, which is what `kyo docs check` validates. It *raises*
only where the symbol is defined: a changed file raises when its own diff shows
the definition changing, appearing or disappearing, and a file that merely uses
the symbol raises nothing, so long as some file defines it. A file type with no
region finder still raises, as does a revision that will not parse. When no
resolved file defines the symbol, every one of them raises on any change, as it
always did, and `kyo docs check` says so with the file count — in one measured
project, a bare dataclass field raised its section for every one of 110 files that
read it.
In Python, `path#Class.attr` (and the bare `Class.attr`) now locates an
assignment in a class body: a dataclass field, a plain class attribute, a
nested class's field. A bare field name alone locates it only when it is the
file's one definition of that name.

That precision is opt-in and reversible, but a narrowing that goes wrong is
invisible — nothing raised is nothing seen — so `kyo docs preview` counts the
sections a declared `#symbol` suppressed on every run and `--suppressed` lists
them (`--json` always carries them). A project narrowing many targets at once
should read that list once before trusting the quiet. The same run counts the
code pointers whose regions this branch did not change (KYO-500), and names
every pointer obligation raised at file granularity.

**A change to comments and docstrings only raises nothing (KYO-530).** When a
branch's diff to a changed file leaves its code tokens identical, read by the
language's own lexer on both sides, no section raises on that file, whether it
governs the file whole, narrows it to a `#symbol`, is named by a pointer in
it, or is a recx module whose path prefixes it (KYO-606). Comments, blank lines and docstrings are not code tokens; a literal in other
quotes, a re-indented block, a shebang and a coding cookie are. Python and YAML
(`.py`, `.yml`, `.yaml`) are read; every other file, and any side that will not
lex or parse, raises as before. The rule is per file: one changed code token
and every section the file raises is raised. `kyo docs preview` counts what it
quieted as "quieted as comment-only" and `--suppressed` names each section
(`comment_only: true` in `--json`); nothing is stored for them.

**Which files are documents is the project's declaration, not this tool's
assumption (KYO-129).** `kyosei/documents.yml` names sets of documents by
root-relative glob; a project that declares nothing is scanned for root-level
`*.md` plus `docs/**/*.md`, so a README beside the code is governed by default
and a project with no `docs/` directory is no longer scanned for nothing.
Declaring any set *replaces* that default rather than adding to it, and a
project whose globs match no file is told so (`no-documents`) rather than
shown the word "clean" over an empty scan.

**The declaration is yours, and `kyo docs suggest` drafts it (KYO-303).** Every
word above is about a file you write: which files are documents, which sets they
fall in, which are numbered and append-only. Nothing derives that, and nothing
ever will — but a file that has to be written from scratch, whose most useful
block nothing mentions until you go looking for it, is one most projects never
write. So there is a read-only command that says what the repository *looks*
like, with the evidence beside each draft and the exact YAML to paste:

```
$ kyo docs suggest
docs suggest: 1 suggestion(s) for kyosei/documents.yml (suggestion only, nothing written)
  scanned: docs/*.md (declared in .../kyosei/documents.yml)
  [numbered-index-sibling] docs/NOTES.md looks numbered and append-only, and set 'notes' does not say so
      evidence: 120 heading(s) in docs/NOTES.md follow '## {number}.', numbered 1 through 120 in file order — `kyo docs assemble` would give the next section 121
      evidence: its history shows it growing at its end: 130 commit(s) changed it on the first-parent chain: 119 added a numbered section at its end and none edited an earlier section
      evidence: docs/NOTES-INDEX.md is generated and is about this document (its generated header names NOTES.md): '<!-- Generated by scripts/notes_index.py from docs/NOTES.md. Do not hand-edit -- run `make notes-index` to regenerate. -->'
      evidence: after: names 'make notes-index', read from that header — Kyosei prints it after assembly and never runs it
      replace the whole `documents.notes:` entry in kyosei/documents.yml with this — its summary and globs are carried through unchanged:
        documents:
          notes:
            summary: The append-only engineering notes, one section per finding.
            globs:
            - docs/NOTES.md
            numbered:
              fragments: docs/notes-fragments
              heading: '## {number}.'
              after:
              - make notes-index
  nothing was written and nothing is enforced: `documents.yml` is your declaration and this command only says what the repository looks like...
```

Four things it can see, all of them scoped to files your sets already
enumerate: a document whose headings follow a numbered pattern under a set with
no `numbered:` (the template inferred from the headings themselves, the count
and the last number stated), drafted only when the file's git history shows it
growing at its end (KYO-562): at least two commits each adding a numbered section
after the last one, and none editing an earlier section, because numbered headings
alone describe a plan edited in place just as well. A file whose history edits
earlier sections and never appends gets nothing; any other numbered file without
that evidence, a single-commit file included, gets only a question —
`[numbered-question] <path> has numbered headings; is it an append-only log?` — with
no YAML, and `kyo doctor`'s notice does not count it. Then a file that says it is generated — a "generated
by" or "do not edit" header — inside a set's globs, drafted out of them; and a
generated *index* beside a document, which is the strongest evidence a document
is assembled rather than edited and the only place `after:` can be read from
rather than guessed. The fourth (KYO-556) is `ssot-id-headings`: documents whose
headings repeat `## <PREFIX>-<n>. Title` under a set with no `ssot:`, drafted as an
`ssot:` block with `id_heading: '{id}. {title}'`, `prefixes` mapping each prefix to
the one document whose headings carry it (a prefix found in two documents is left
out, and the evidence says so), an `id_pattern` naming exactly those prefixes, and a
`section_status:` that includes `superseded`. The draft is offered only if the
pasted file loads and `kyo docs check` finds nothing in the documents it does not
find today, and a document drafted this way is not also offered `numbered:`.
`--json` carries the same. With nothing to suggest it says so in one line, which is
a real answer and not silence.

It is the posture `kyo new` has had for scope since KYO-100 — "derived
(suggestion only, nothing stored) ... derivation never overrides a
declaration" — and it is held the same way: **nothing is enforced.** No
`kyo doctor` problem reads a suggestion. Doctor prints one NOTICE when there is
something to draft, naming the command and the count and nothing else, and a
project that has read the drafts and declined them turns that line off with
`docs_suggest_notice: false` in `kyosei/config.yml` (offered, commented out, in
every freshly written config). A suggestion that cannot be declined is not a
suggestion. Every block it emits is run through the real `documents.yml`
validator before it is offered, and anything that cannot be drafted validly is
not drafted at all — a suggestion the next command refuses is worse than none.

**A second, independent mechanism lives in the same module and the same
registry file (KYO-299).** A document set may also declare itself `numbered:` —
append-only, with numbered section headings and an index its own command
generates. That declaration is not about `governs:` targets at all and changes
nothing this section describes: what it changes is *who writes a new section*.
Workers write per-dispatch fragment files, `kyo docs assemble` appends them with
the next number on the merged tree, and `kyo check-branch` refuses a branch that
adds a numbered heading to the document directly. The adoption story is in
"Operating a Kyosei project", in [operating.md](operating.md): "Doc fragments: a document that grows by
numbered section".

## RECX.yaml as a governed document

governs: src/kyosei/docs_recx.py, src/kyosei/registry.py#recx_manifest_block

A project that publishes a [recx](https://github.com/jbmv/recx) manifest can have
Kyosei govern it like a document (KYO-600). A document set in
`kyosei/documents.yml` declares `format: recx-manifest`; every other set is
`format: markdown`, which is also the default when the key is absent:

```yaml
documents:
  manifest:
    summary: The recx manifest; each module entry governs its own path.
    globs: [RECX.yaml]
    format: recx-manifest
    recx_manifest:            # optional, and so is each key in it
      project_governs: [LICENSE, pyproject.toml]
      evidence_governs:
        validator-tests: [pyproject.toml, uv.lock]
      module_governs:
        kyosei-cli: [src/kyosei/cli.py, pyproject.toml]
```

Such a set matches exactly `RECX.yaml` at the code root, the one file recx's
specification allows per repository. Any other glob on it, a second
`recx-manifest` set, a `recx_manifest:` block on a markdown set, or
`section_status`, `ssot` or `numbered` (markdown features) on it is refused by
name when `documents.yml` is read.

The file is read with PyYAML, never through recx, so governance works whether or
not recx is installed. Its sections are derived from its structure rather than
from headings:

| Section | Comes from | Governs |
|---|---|---|
| `(preamble)` | the top-level fields | `recx_manifest.project_governs`, or nothing |
| `module <id>` | each entry in `modules` | the module's `path` as a prefix, or `recx_manifest.module_governs[<id>]` in its place |
| `evidence <id>` | each entry in `evidence` | `recx_manifest.evidence_governs[<id>]`, or nothing |

A module's `path` is matched as a **prefix** of each changed path, the rule
recx's own drift reminder uses: a directory path governs every path under it, a
file path governs that file, and a deleted file raises as an edited one does.
The module paths are read from both sides of the diff, the base and the branch,
so a branch that moves a module and updates its `path` raises on both the old
and the new location, and one that removes a module still raises on that
module's section. A file under two modules' paths raises one obligation on
each. A change to comments and docstrings only, under a module's path, raises
nothing on the module, by the comment-only rule above (KYO-606). The target lists under `recx_manifest:` use the `governs:` dialect above
(paths, globs, `path#symbol`) and stay in `documents.yml`, so the public
manifest carries nothing only Kyosei reads. `module_governs` narrows a module
whose path is too broad to be useful, typically a module that is the whole
program; a module it does not name keeps its path.

From there the obligation is an ordinary one. `kyo docs preview` lists
`RECX.yaml [module <id>]`, a worker answers it in `docs_verdicts[]` with that
section name, `kyo docs settle` settles it, and an open one blocks the record's
completion move. A record's doc-refs may name `RECX.yaml#module <id>` (or
`#evidence <id>`), and a decision record raises a standing obligation on it; a
bare `RECX.yaml` names the `(preamble)` section.

`kyo docs check` reports, as problems: a manifest it cannot read (invalid YAML;
an anchor, alias or merge key; or no `modules` list of mappings with a string
`id` and `path`), in which case the whole file is one ungoverned `(preamble)`
section; a module path naming nothing on disk; a `module_governs` or
`evidence_governs` id naming no entry in the manifest; and a target in any of
the lists that does not resolve. It does not otherwise validate the manifest
against recx's schema, which `recx check` does.

recx's own drift reminder runs when a session or one of its subagents stops
(Claude Code's Stop and SubagentStop events) and does not read Kyosei's
obligations, so a worker that answered a module obligation in `docs_verdicts[]`
would be reminded of the same module. From recx-cli 0.1.1, a commit trailer
`Recx-Checked: <module-id>[, <module-id>...]` records that those entries were
checked; the key matches case-insensitively and the trailer may repeat. A
trailer naming a module on commit C covers that module's changes in C and in
the earlier commits of the session's range that C descends from; changes in
later commits and uncommitted changes are still reminded. In a project that
declares a `recx-manifest` set, every brief therefore tells a worker answering
a `RECX.yaml [module <id>]` obligation to put the trailer on its branch's last
commit and on any commit after it, and `kyo report` warns, without refusing,
for each module the report answers that no trailer on the branch tip names.
