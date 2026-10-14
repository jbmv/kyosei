# The docs check and its readers: the history behind the rules

The code in `src/kyosei/docs_check.py` and the seven `docs_*` modules split out of it
(KYO-434) keeps the rule each function enforces and why a reader would otherwise break it.
This file keeps the incidents and measurements those rules came from, moved here from the
docstrings and comments that used to carry them, one heading per record. A code comment
points here as `docs/design/docs.md#kyo-<n>`. The design itself is in
`docs/design/charter.md`, Layer 3.

Modules: `docs_check.py` (`kyo docs check`: the validator, its states and notices, and the
statement of the format), `docs_parse.py` (which files are documents, sections, section
metadata, dead sections, target splitting), `docs_targets.py` (target shapes and resolution,
glob and brace shorthand, document enumeration), `docs_regions.py` (per-language symbol
regions and the branch diff), `docs_staleness.py` (staleness and the per-document query
row), `docs_obligations.py` (docs obligations and their grouped rendering),
`docs_numbered.py` (numbered documents, doc fragments, matching a report's docs verdicts)
and `docs_suggest.py` (`kyo docs suggest`).

These passages record what was true when each record was worked, and they are not kept
current: the numbers are measurements of one store or one consumer on one date.

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-98

status: amended
amended-by: KYO-499

The "pairs agree" half of Layer 3 (an `amended-by` cross-checked against `status`) has no
acceptance criterion on the milestone that built the validator, and would be exactly the
kind of unspecified check KYO-8's flag classes were explicitly not built past
(`docs/design/review.md#kyo-8`). Until that half ships, the charter states it as
aspirational instead of asserting a build failure the validator cannot produce -- the gap
is named in both places, which is the whole point of naming it in either. What KYO-109
changed about that gap, and only this: the two fields gained a reader, so they are no
longer fields nothing at all consumes (law 2).

The metadata pattern is built from the key tuple rather than written out so that a key
cannot be declared and unread -- the shape the `status:` gap already had once.

## KYO-109

KYO-45 makes a Base runnable "over records, ideas, or docs", and the docs-mode
`touches x governs` banner one of its renderings. What the banner needs is not the
validator's yes/no -- it is the per-document state: what this file governs, which of those
targets no longer resolve, and whether the file has been touched since the code it governs
was. So a second half was written that READS the format the validator checks. One parser
(`section_metadata`), one target-resolution rule (`resolve_target`), two consumers.

Reading `status:` and `amended-by:` did not build the "pairs agree" check: what changed at
KYO-109 is that the two fields gained a reader -- a Base over the docs source can select on
them -- which is the law-2 gap the charter names, closed from the query side rather than
the validation side.

## KYO-129

Document discovery was four lines -- `root / "docs"`, `rglob("*.md")`, `[]` if the
directory is not there -- and it was the only file enumerator in the module, so both the
check and the query source inherited its blind spot. In one consumer project with no `docs/`
directory, `README.md` and one operating document beside it went stale, and `kyo docs check`
exited clean and silent over both. The enforcement was real; it could not see the files that
went stale, so discovery was fixed rather than documented.

Note where that failure landed. A project with no `docs/` got exit 0 and no output, which is
indistinguishable from a project that was checked and found fine: reported clean on no
evidence, which is the exact failure `governs_staleness`'s `cannot-tell` state was
deliberately built to avoid, reappearing one level up at file discovery. `DocsCheck`'s
`no-documents` state is that state, at this level -- and it is the state the reporting
consumer was in, and could not see they were in. The same blind spot was never confined to
the check: `documents()` is the KYO-109 query source behind `kyo base run --source docs`, so
a document the enumerator could not see was missing from a `stale-docs` base result too, on
the same projects and just as quietly.

Fence awareness (`_fenced`) arrived with the fix, because KYO-129 made root-level `*.md` a
document by default and the file that lands in first is the README -- the one markdown file
in a project guaranteed to contain examples of the format. This repository's own README
quotes a `governs:` line inside a fence to explain what one is; without fence awareness that
example becomes a live claim the build enforces, and every consumer's README does the same
the day they upgrade. Widening discovery without it would have shipped a second bug behind
the fix for the first. It was already wrong for `docs/` and nobody had been bitten yet: a
`# comment` in a shell fence read as a level-one heading (`docs/design/charter.md` has one),
and a `status: current` inside a YAML example read as section metadata.

## KYO-137

KYO-136 decision 2: the moment a document goes stale is the moment to say so, and that
moment is the worker's report, while the change is fresh, not the next session's sweep. Decision 4: the unit is the section. Decision 3: what clears
one is an orchestrator's asserted verdict, never a timestamp; nothing in the obligation code
decides that a document is fresh, and that is the whole point of the split.

What raises one is a join, not a new declaration: `kyo report` already computed what a
dispatch's branch changed (KYO-71), and a `governs:` section already names the paths it
speaks for. Intersecting those two is the mechanism, and it needed no syntax nobody had
written yet. `doc-refs` covers only the residual the join provably misses -- a document
about a record's subject that shares no file with it.

`ideas/` never raises an obligation because of KYO-136 decision 7: intake is
zero-ceremony and an idea file is supposed to be a stale snapshot of a thought, so an
obligation raised over one would be noise proportional to how well the layer is being used
-- the routine false positive KYO-33 already measured the cost of.

## KYO-142

`documents.yml` reads `globs:` and `scopes.yml` reads `paths:` because the two are different
glob dialects (`pathlib` glob over a filesystem, where `*` stops at `/`; `fnmatch` over a
known path, where it does not). Both keys were `paths:` until a line copied between the two
files was found to keep working and select a different set; the dialect now owns the
spelling (`registry.PathDialect`), so a copied line lands on a key the destination does not
accept and is refused naming both dialects.

## KYO-153

`_resolve_target` has always validated the `#symbol` half of a `path#symbol` target -- the
symbol must be a whole-word match in the file, wrap-tolerantly since KYO-66. Until KYO-153
`_governs_obligations` then took `target.partition("#")[0]` and never looked at the symbol
again, so `governs: src/kyosei/cli.py#build_parser` raised on any change to `cli.py`. KYO-144
measured what that cost on this repository's own history: of the 30 dispatches touching
`cli.py`, 13 moved the command surface and 17 did not, so 57% of that one section's fires
were false positives the target format already had a spelling for and the join declined to
read.

A cheaper instrument than the region finders -- grep the symbol's name in the diff hunks --
was rejected: it would have suppressed exactly the case the charter's "CLI surface" section
exists for, an `add_parser(` line added inside `build_parser` without the name
`build_parser` appearing on any changed line.

Two sections governing one file raise two obligations (KYO-153.c4). KYO-144 measured the
cost of that: 12 of this repository's 35 governed files were claimed by more than one
section, because the charter says why a mechanism exists and the README says how to operate
it, and deduplicating per governed file would have taken 384 obligations to 287 -- 25% of
them. The dedup was refused anyway, for decision 9's reason.

## KYO-199

The measurement the grouped rendering answers, from two consumer stores:
obligations settled `not-affected` outnumbered `updated` by roughly nine to one in one store
and two to one in the other, and a single dispatch there raised sixty. The stored unit stayed
one obligation per section (KYO-136 decision 4, KYO-153.c4). What changed is the shape of the
output: a flat list of sixty rows is read document by document anyway, so the rendering says
so and hands over one ready-to-run settle line per document rather than one per row.

## KYO-200

The record that added the list form, the `:` separator, bare-symbol targets and
`paths_relative_to:`. Every one of them came from one consumer's 178-section document, which
already carried its `governs:` lists as YAML list items, already used `path:symbol`, and was
generated and validated by that project's own drift checker since before Kyosei existed. Telling them to rewrite it would have been this parser dictating
a format to a document that predates it, so the parser learned the document's format
instead, and several rules are the consumer checker's own, matched deliberately so the two
tools classify one string one way: the two-dot minimum for `module.sub.symbol`
(`_dotted_module_symbol`), `MODULE_PATH_SEGMENT_RE`, `MIN_TOKEN_LEN`, and code-only symbol
search (`CODE_TOKEN_EXTS`).

A quarter of that document's targets were the bare-symbol shape, unrepresentable before
this record. Measurements on the same corpus: 189 bare-symbol targets over a tree with
thousands of files, which is why one `_SymbolIndex` is shared per scan (reading the tree once
rather than 189 times); roughly 80,000 regex runs saved per scan by skipping the phrase rule
in `_SymbolIndex.find`; and 35 targets that failed when a dotted symbol had to appear
verbatim -- `pkg/base.py:Adapter.prepare` names a method, and a class and its method
are declared on different lines -- every one of them naming code that is plainly there.

Before this record, five call sites each partitioned a target for themselves; resolving once
in `_sections` is what made "a `:` separator resolves exactly as a `#` one" a property of the
code rather than a coincidence between them.

## KYO-202

A consumer's `governs:` targets carried two more shapes `resolve_target` did not read: a
single `{a,b,c}` brace group naming several files in one line
(`pkg/{a,b,c}/normalize.py`), and a shell glob resolving against whatever
real files exist (`deploy/run_*.sh`). That project's own drift checker
has `expand_governs_entry()` for the brace half -- a deliberately narrow single-group
comma-list expansion -- reused as the reference rather than reinvented,
including its `_BRACE_ALT_RE` and its tolerance for a group that does not look like a plain
comma list. The glob half is this project's own addition, since their checker never verifies
a target resolves to a real file at all.

## KYO-204

The consumer's own format spells two metadata keys with an underscore (`amended_by:`,
`superseded_by:`), written by a generator that predates this parser and was never going to be
rewritten to match it -- the same "their format, not a rewrite" stance KYO-200 took. Only
those two keys got an alias; `governs` and `status` had no underscore variant in any real
document seen, and adding one nobody writes would be a guess at a format that does not exist.
The same documents write `see_also: [159, 165]`, which is not read as a list because nothing
consumes a see-also; the record that would widen that is held.

## KYO-299

The failure doc fragments are against, measured on a consumer rather than imagined: one
notes file of 27,471 lines, append-only by that project's own rule, its sections
headed `## 204. <title> (<date>)`, and its numbers cited across the repository.
Two generated index files are built from it. So two workers who each add "## 205." collide
three times -- on the number, on the tail of the file, and on both generated indexes -- and
they collide textually no matter how their touches were declared, because a symbol-level
touch narrows a claim and does not move a byte.

## KYO-303

Kyosei does not prescribe who owns which document; `kyo docs suggest` offers declarations a
project may take or ignore. The tool stays flexible about document ownership and supplies
defaults only as suggestions.

## KYO-351

Measured by a consumer: every file their four documents govern is
`.ts`/`.tsx`/`.js`/`.json`/`.css`/`.yml`, `kyo report` offered them the narrower
`governs: path#symbol` form unconditionally, and they took the advice on 101 targets before
measuring that it changed nothing. The prose that offered it was true of the code that shipped it and false
of the code beside it -- which is why the suffix list is now asked as a question
(`narrows_by_symbol`) and spelled once (`region_suffixes_says`).

## KYO-352

The consumer's own suggestion for TypeScript regions -- "a brace-matched or
next-top-level-declaration region would do" -- was resolved toward the second, because of
which way each fails (brace matching ends a region early on a brace inside a string, template
literal, regex or comment, and early is the direction that suppresses).

The suppression report exists because of the warning the consumer filed beside the request:
with the check inert on their suffixes, ~100 judgment calls about what each section is
"about" had been written and verified by nothing, and every one of them would go live the day
a suffix was added.

## KYO-360

`_declaration_end` stopped reusing the whole of `sweep.region_end` because the prose half of
it claimed a declaration's siblings: `serialize: (fn) => this.serialize(fn),` in an object
literal ran on through every property after it to the end of the literal, the run was not
brace-balanced, and the veto threw away the region of the method of the same name along with
it -- a consumer's dependency-injection shape, so it landed on exactly the methods worth
naming. A method directly followed by a sibling, no blank line between, failed the same way.

## KYO-363

The first version of `_yaml_line` treated the apostrophe in `the tree's default` as an
unterminated scalar and the `&&` in `npm ci && node install.js` as an anchor, and so vetoed
real stores' own `config.yml`. Both were measured there.

## KYO-393

In a `numbered:` document the section number became a second matching key because a consumer
lost ten verdicts to a heading's tail differing from the document's.

## KYO-397

A bare `Snapshot.total_units` resolved by text to every searchable file carrying
its words -- 110 of them in the consumer tree that reported this -- and every one of them
raised the section on any change, because a file that only uses the field gets `()` from the
region finder and `()` is cannot-tell. The same corpus had 182 bare-symbol targets over files
carrying up to 110 hits each, which is why `_SymbolIndex.defining` puts Python files through a
regex before parsing them.

## KYO-412

`<!-- governs: src/x.py -->`, the one-line compression of the multi-line comment form, used
to govern nothing: the metadata patterns are anchored at the key, so the `<!--` in front hid
the line entirely, and a `governs: x.py -->` written across two lines absorbed the terminator
into its target. A one-line value was split with a plain `split(",")`, which cut a `{a,b,c}`
group in two, so following `GOVERNS_FORMAT`'s own advice produced two missing-file problems.
And a `governs:` that parsed to no target left the section governing nothing while
`kyo docs check` reported clean -- the author never saw `GOVERNS_FORMAT`, because that text
is printed only on a failure.

## KYO-422

`_SymbolIndex` used `rglob("*")`, which listed every file under `.git`, `node_modules` and the
rest of `_UNSEARCHED_DIRS` and only then threw them away. It now prunes them from the
`os.walk` so they are never entered, with the same file set as before: a directory is excluded
wherever it sits below the base (never the base itself), and symlinked directories are not
followed (`Path.walk`, which `rglob` used, did not follow them either).

## KYO-493

A worker whose new numbered section corrects an older one could not mark the older one
amended: the back-link needs the new number, and only `kyo docs assemble` knows it. A
consumer's orchestrator made that edit by hand in 14 of 31 assemblies (KYO-493's
proposal). So a set declares the spelling,
`numbered.amends: {field, status}` -- `field` is `amended-by` or `amended_by`, Kyosei's own
back-link key in either spelling its parser reads; `status` is non-empty and not dead; both
are required because writing one without the other is the footgun charter law 3 names -- and
a fragment's header names the sections, `<!-- kyosei-fragment amends: 178, 186 -->` (brackets
allowed, beside `document:`, each key once). Assemble writes the back-link (inserted after the
section's `status:` line in the declared spelling, or appended to an existing line in its own
spelling, several amenders on one line in assembly order) and the status, on the merged tree,
inside the same all-or-nothing plan. A dead section (`section_is_dead`) gains the back-link and
keeps its status, and the output says so. The header line is consumed, so the document holds
one copy of the relation. Refusals, each voiding the run before a byte is written:
`fragment-header-unreadable`, `amends-undeclared`, `amends-unknown-section`,
`amends-unassembled-section` (a number above the pre-run last section),
`amends-ambiguous-section`, `amends-no-status-line`, `amends-unreadable-back-link` (not a flow
list of numbers, or the key twice). `kyo check-branch` runs every one of them except
`amends-unassembled-section` against the worker's branch, since a number above the tail may be
another branch's section. A `status:` written inside a one-line `<!-- status: x -->` comment is
not found as a status line and is refused as `amends-no-status-line`: the write keeps a line's
own shape, and rewriting a one-line comment is a format choice this does not make.

## KYO-499

Why `kyo docs check` holds a section's status and links to agree (`docs_check.section_status_problems`):

KYO-491's proposal measured `docs/design/` a week after its files were created:
9 of 58 sampled sections were amended by a later record and nothing on them said so, and three of the
amending records (KYO-450, KYO-466, KYO-485) had written no section at all. The marks it asked for are
Layer 3's own keys -- `status:`, `amended-by:`, `superseded-by:` -- on the lines under the heading,
only on a section that is not current. This record is the check that keeps those marks honest; it
catches no unmarked drift, which is the pointer obligation's job (KYO-500, a separate record).

The pairs-agree rule (A1) runs in every document of every project because it can only fail a
section that contradicts itself: measured read-only across the seven registered projects, it
refused 0 of one consumer's 352 marked sections and one line in this repository's README, a prose
line that wrapped to begin with `amended_by:` and parsed as metadata. The other rules need a
project's own vocabulary and assume that a link names a heading and that code points at sections,
so they run only in a set that declares `section_status:`: a consumer's statuses include
`accepted` and `proposed`, and a default closed set would refuse real documents. A bare key
resolves in its own document only, because 30 record ids have sections in more than one design
file. The forward relation is derived from the amending section's prose (A3) rather than stored
as an `amends:` key, which would be a second copy for law 3 to need a validator for. A `src/`
pointer to a superseded section passes only when its comment block names the successor (A5): the
code is where a section is cited as current, and prose citing a superseded section is ordinary
history. One reader serves `kyo docs check` (the working tree) and `kyo check-branch` (the
branch's blobs, kept to problems the branch's changes are part of), so the worker and the
orchestrator are refused in the same words. `kyo doctor` runs none of it: documents already have
their surface and their moment, and a third place would print the same problem twice a session.

It ships the half KYO-98 named as unbuilt: the charter's Layer 3 bullet no longer states the
pairs-agree cross-check as aspirational.

## KYO-500

Why a code pointer raises a docs obligation on its section (`docs_obligations._pointer_join`):

KYO-491 found that no `## KYO-n` section in `docs/design/` carries `governs:`, so the KYO-137 join
reached none of them, while the code already cites them by 227 `docs/design/<file>.md#kyo-<n>`
pointers. This reads that relation from the code's
side instead of asking for 154 hand-written `governs:` blocks, which would go stale where a
pointer moves with its code (law 3). It is one more `cause` value on the existing obligation, so
the preview, `docs_verdicts[]`, `kyo docs settle` and the completion block apply unchanged, and
`cause` is free text in the export, so no schema version moved.

The region is the innermost `def`/`class` holding the pointer, or else the top-level statement a
comment sits above, which is the rule KYO-491's proposal measured. Both sides of the diff are read, so
a pointer whose region the branch deleted raises as well as one it added, and a region whose text
(ignoring blank lines at its ends) is the same on the other side for the same pointer raises
nothing: the measured range over-raised three times on one commit (KYO-454) where code appended
after a function marked the function's last line as an insertion point. Decision 9 holds: the
region may quiet a pointer only when it was located, so a file with no finder (only Python is
placed) or a pointer outside every region raises at file granularity, and the preview says so.
Re-running that proposal's 20 commits through the built join gives 8 obligations where the script
counted 11: the three dropped are that KYO-454 edge, and the two confirmed amendments
(doctor.md#kyo-309, scaffold.md#kyo-316) still raise.

## KYO-505

Why a bare number in a numbered set names its numbered heading, and which cite-backs A3 accepts there (`docs_check._Corpus.resolve`, `docs_check.NUMBERED_CITE_SPELLINGS`):

A consumer declared `section_status:` on its five `numbered:` notes sets and `kyo docs check`
raised 332 `section-link-unresolved`, every one a bare number such as `amended_by: [15]`. That is
the form KYO-493's `kyo docs assemble` writes, and `_Corpus.by_key` matched a bare key only
against the whole heading text and its anchor, so the feature that writes the links and the one
that checks them disagreed about what a section's key is. In a set declaring `numbered:` a bare
number now names the section whose heading the set's own `numbered.heading` renders for it; the
template reaches the corpus on `SectionStatusSet.numbered_heading`, read from the same
documents.yml entry as `section_status:`, so `kyo docs check`, `kyo check-branch` and
`kyo docs index` resolve through it without a second reading of the registry. A number naming no
section stays unresolved and says which heading it looked for; two sections carrying one number
resolve to neither. A set without `numbered:` resolves exactly as before.

Measured on a copy of that consumer's five files with the opt-in added: 332 links, 0
unresolved. A3 then refused every link whose amending section cited the amended one only by
number, so in a numbered set the number counts in three spellings, the ones those notes use:
`§N`, `<path> §N` and `section N` (with lists such as `sections 3, 9 and 15`, and a part letter
such as `§53b`). 154 of the 332 links are cited back that way. A path written before the number
must name the cited document, root-relative or relative to the citing one, because the same notes
cite `client/NOTES.md §62` from the server's file. A bare number does not count: of the 178
links still refused, 67 mention the number only in a `see_also: [..]` list, which is a link line
rather than prose saying what changed; 7 mention it only as a count, a version or a fragment's
`amends:` header; and 104 never mention it at all. Those are the consumer's to write, not this
check's to suppress.

## KYO-510

A3 reads the linked section's region, not only the text above its first sub-heading (`docs_check._region_prose`):

`_parse.sections` makes every heading its own Section, so `_prose_lines(target.section)` saw only
the text above a section's first `###`. A consumer's 38 refused links each cited
the amended section from under a sub-heading; the reported `§N` spellings all matched
`_cites_number`, so the token boundary was not the cause. The region is the one touch symbols use:
the heading through the next heading at the same or a shallower depth. A citation in the next
section at the same depth still does not count. The preamble (level 0) has no region beyond itself.

## KYO-530

Why a changed file whose diff moved no code token raises no obligation (`docs_regions.comment_only_change`, `docs_obligations._obligations_and_pointer_suppressions`):

A consumer's dispatch changed only comments and docstrings and raised 15
obligations, every one settled by hand as not-affected. The rule: a changed file is
comment-only when the language's own lexer, run on both sides of the diff, yields the same
code-token sequence, and a comment-only file raises nothing, through `governs:` (whole-file
and `#symbol` targets alike), through a recx module path that prefixes it (KYO-606, since a
comment cannot change anything a manifest states), or through a code pointer in it. The rule is per file: one code
token anywhere in the file and every section it would have raised is raised exactly as
before, a `#symbol` region answering as KYO-153 always has. A section governing two files is
raised when either one changed code.

What counts as a comment, per language. Python, through `tokenize` and `ast`: comments,
blank lines and docstrings (a plain string that is the first statement of a module, class or
function, with the `NEWLINE` ending it, so adding or removing one counts too). Every other
token is compared by type and text, so a literal respelled in other quotes, a re-indented
block or a changed f-string is a change. A shebang on line 1 and a coding cookie on line 1 or
2 are compared as code, because they decide how the file runs and is decoded. YAML, through
PyYAML's scanner, which drops comments by the spec; both sides must also parse, since the
scanner alone accepts an unclosed `[`. Not TypeScript, JavaScript or CSS: their lexer has to
tell a regex literal from a division, and a misread `/*` swallows code as a comment, which is
the error that quiets a raise. Not Markdown or JSON, which have no comments to strip. A file
added, deleted, renamed, unreadable, or that will not lex or parse on either side is
cannot-tell and raises (decision 9).

Not raised, rather than raised already settled `not-affected`. KYO-136 decision 3 keeps a
verdict an orchestrator's assertion, and a machine-settled row would put the build's answer
in that field, add rows to the not-affected counts KYO-199 measured, and need a write on the
report path. The instrument is positive evidence of the kind decision 9 lets quiet a raise,
the same kind as a located `#symbol` region (KYO-153) or an unchanged pointer region
(KYO-500), so it joins them: `kyo docs preview` counts what it quieted on its own line
(`quieted as comment-only`), and `--suppressed` and `--json` (`comment_only: true`) name each
section. The cost is that nothing is stored: after the report, `kyo show` has no row saying a
section was not asked.

Limits. A docstring is runtime data (`__doc__`): a project whose help text or output is
built from docstrings gets no question for that change, by the record's choice. A pragma
comment (`# type:`, `# noqa`) changes a tool's reading of the file and counts as a comment.
Only paths that a `governs:` target names or that a pointer can sit in are read, two
`git show` calls each, so the rule costs nothing on a branch that changes no governed file.

Measured on this repository's own branches, re-run through the built join with the documents
as the orchestrator read them at report time (the base tree): KYO-516 raised 12 and the rule
leaves 5 open; the 7 it quiets come from files whose diff only re-pointed README
references in comments and docstrings to the new topic pages (`docs_check.py`,
`docs_numbered.py`, `docs_regions.py`, `events_ledger.py`, `sweep.py`). The 5 it keeps changed string literals that name the README
(`scaffold.py`, `cmd_publish.py`) and `defaults/skill.md`, which no lexer here reads. KYO-519
raised 32 and the rule leaves all 32 open: it re-pointed citations too, but every governed
file it touched also changed code (`controller_rules` became `orchestrator_rules`), and of its
49 lexable changed files only `doctor_registry.py` was comment-only, and the one section
governing it was raised by the code changes in `doctor.py` and `contracts.py` beside it. Every stored obligation on both records is accounted for, and the rule raises
none they did not.
