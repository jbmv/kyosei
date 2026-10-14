"""The per-project registries KYO-46 decided on, and the one shape they share.

KYO-46 turned four config dimensions into per-project registries — kinds, statuses,
scope, gate tags — with the explicit requirement that they *share one shape*. This
module is that shape, made mechanical rather than aspirational (law 1): a dimension
is a :class:`Dimension` value, and every registry is loaded, validated, refused and
reported by the same code. A sibling dimension adds a declaration beside
:data:`SCOPE`; it does not add a loader, a validator, or a refusal message of its own,
because three hand-written copies of one shape is precisely how the four drift apart.

**All four are on it as of KYO-104.** ``kinds.yml`` predates this module and kept its
own loader through KYO-100 for a stated reason — migrating it inside that task would
have rewritten the load path every command depends on for stylistic benefit, before a
second dimension existed to be shared *with*. With ``statuses`` and ``gate-tags``
landed, the shape had three real users and the fold became a simplification rather than
a bet, which is the condition KYO-100's own worker set for doing it.

**The layout, and why it is this one** (KYO-100 is the first of the three step-1
registry tasks, so this is the layout statuses-as-roles and gate tags follow):

* **One file per dimension, under ``kyosei/``**, named for the dimension's plural:
  ``kyosei/kinds.yml`` (already), ``kyosei/scopes.yml`` (here), and — following this —
  ``kyosei/statuses.yml`` and ``kyosei/gate-tags.yml``. Not one combined
  ``registries.yml``: ``kinds.yml`` exists in every store already and no ``kyo``
  command can rewrite it (:func:`missing_required_remedy` says so at length), so a
  combined file would make the first sibling's job a migration of a file the tool
  cannot regenerate. Adding a file is additive; moving one is not.
* **Top-level key = the plural**, mapping *name* -> *entry*, exactly as ``kinds.yml``'s
  ``kinds:`` mapping does. An entry is a mapping with a required ``summary`` and
  whatever optional fields the dimension declares. Nothing else is legal, in either
  direction — an unknown key is refused, because a registry with a decorative field is
  law 2's rot with a config file around it.
* **Names are lowercase slugs**, the same shape a kind name has, so a registry token
  can appear in an id, a path, a filter and a query without quoting.
* **Every problem is collected and raised together** (law 6, and ``kinds.py``'s own
  style): a registry with four mistakes is fixed in one pass.
* **Validated on load, edited as config in v1.** Proposal-gating the edit is KYO-72
  step 6 and deliberately not here; until then a registry is changed the way
  ``kinds.yml`` is, by editing a reviewable file.

**What a missing file means differs by dimension, and that is declared, not assumed.**
``kinds.yml`` is load-bearing — nothing works without it — so its absence is a loud
error. ``scopes.yml`` is not: a project may legitimately declare no scopes, and every
store written before this release has no such file at all. :attr:`Dimension.required`
carries which it is, so "absent" never has to be guessed at by a caller, and an absent
optional registry is a real, nameable, empty registry rather than an exception every
call site re-handles.
"""

from __future__ import annotations

import fnmatch
import ntpath
import re
import shlex
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from . import explanations
from .errors import KyoseiError

#: A registry entry's name: the same shape as a kind name and a record id's kind slug
#: (``kinds.KIND_NAME_RE``), so one token works as a filter value, a filename fragment
#: and a query term without quoting or case rules.
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

#: Every dimension requires this, and it is the only field they all share. A registry
#: whose entries carry a name and nothing else is a list of magic words; the summary is
#: what makes the file reviewable by someone who did not write it.
SUMMARY_FIELD = "summary"


class RegistryError(KyoseiError):
    """A per-project registry is missing, malformed, or was asked for a name it does
    not hold. Always names the file and, where there is one, the offending entry."""


FieldValidator = Callable[[object], str | None]


def string_list(*, allow_empty: bool = False) -> FieldValidator:
    """A field holding a list of strings.

    ``allow_empty`` is a real difference between dimensions rather than a knob: for
    ``SCOPE.paths`` an empty list and an absent key would be the same fact written two
    ways, so one of them is refused; for ``KIND.accepts_from``/``produces`` the empty
    list is the file's own way of saying *this kind has no edges in this direction*,
    written explicitly in nearly every entry the packaged registry ships, with a comment
    beside it saying why (``task.produces: []`` — "deliberately empty (Law 2)"). Refusing
    it there would refuse the registry this project runs on.
    """

    def validate(value: object) -> str | None:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return "must be a list of strings"
        if not value and not allow_empty:
            return "must not be an empty list; omit the key instead"
        return None

    return validate


#: The strict spelling, for the dimensions where an empty list means nothing an absent
#: key does not already mean.
_string_list = string_list()


def match_globs(value: object) -> str | None:
    """A list of globs **matched** against a path something already declared.

    Behaviourally :data:`_string_list` and nothing more — the paths are already known,
    so a pattern reaching outside the project matches nothing and costs nothing, and
    there is no filesystem walk to constrain. It is a named function rather than a
    second reference to :data:`_string_list` because :data:`MATCH` binds to it by
    identity: this is the marker that says *this field holds match-dialect patterns*,
    and a field validated by the bare string-list validator is just a list of strings.
    """
    return _string_list(value)


def _escaping(values: list[str]) -> list[str]:
    """Which of ``values`` are absolute, or climb out of the project root with ``..``.

    One rule, two validators (:func:`relative_globs`, :func:`relative_bases`), because
    the danger is identical: both are joined to the code root and handed to the
    filesystem, so an absolute entry or a ``..`` would make a config file the
    instruction that reads outside the repository, silently. Windows absoluteness is
    checked too (:func:`ntpath.isabs`), because ``C:\\x`` is not absolute to
    :class:`~pathlib.PurePosixPath` and is very much absolute to the machine reading it.
    """
    return [
        value
        for value in values
        if PurePosixPath(value).is_absolute()
        or ntpath.isabs(value)
        or ".." in PurePosixPath(value).parts
    ]


def relative_bases(value: object) -> str | None:
    """One directory, or a list of them, that ``governs:`` targets resolve *under*.

    KYO-200, and the field is ``DOCUMENT.paths_relative_to``. A scalar is accepted
    beside a list on purpose: the overwhelmingly common case is one base
    (``paths_relative_to: apps/server``), and forcing a one-element list on every
    project so that the rarer three-base case has a spelling is a config file charging
    everyone for a minority's needs. Both forms normalise to a tuple in
    :func:`declared_document_bases`, so nothing downstream sees the difference.

    A list is **ordered** and the order is meaningful — the resolver takes the first
    base under which a target exists — so an empty list is refused for
    :data:`_string_list`'s reason: it would be a declaration that decides nothing,
    spelled differently from the absence that already means the same thing.

    These are directories, not patterns. Nothing globs them, so :data:`MATCH` and
    :data:`ENUMERATE` are both the wrong dialect and this is deliberately neither
    (:class:`PathDialect`, :meth:`Dimension._check_path_dialects`). What it shares with
    ``globs:`` is :func:`_escaping`'s rule, for :func:`relative_globs`'s reason.
    """
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        return "must be a directory, or a list of directories, relative to the project root"
    if not values:
        return "must not be an empty list; omit the key instead"
    blank = [item for item in values if not item.strip()]
    if blank:
        return "must not hold an empty directory; use '.' for the project root itself"
    bad = _escaping(values)
    if bad:
        return (
            "must be relative to the project root and must not climb out of it with "
            f"'..'; got {', '.join(repr(base) for base in bad)}"
        )
    return None


def relative_globs(value: object) -> str | None:
    """A list of globs that will be used to **enumerate** files under a root.

    :data:`_string_list` with one extra rule, and the rule is why this is a separate
    validator rather than a note in a comment. Match-dialect globs (:data:`MATCH`,
    ``SCOPE.paths``) are checked against paths a record already declared, so a pattern
    reaching outside the project matches nothing and costs nothing. Enumerate-dialect
    globs (:data:`ENUMERATE`, ``DOCUMENT.globs``) are handed to
    :meth:`pathlib.Path.glob`, which walks the filesystem — an absolute pattern or one
    climbing through ``..`` would make a config file the instruction that reads files
    outside the repository, and would do it silently. So the two fields are validated
    differently because they are used differently, and the difference is stated where a
    reader looking at either one will find it.
    """
    trouble = _string_list(value)
    if trouble:
        return trouble
    assert isinstance(value, list)  # _string_list has already established this
    bad = _escaping(value)
    if bad:
        return (
            "must be relative to the project root and must not climb out of it with "
            f"'..'; got {', '.join(repr(glob) for glob in bad)}"
        )
    return None


@dataclass(frozen=True)
class PathDialect:
    """One of the two ways a registry entry may hold path patterns — and, inseparably,
    the field name that dialect is spelled with.

    KYO-142. Two registries from the same registry family shipped a field called ``paths``
    holding patterns in two different dialects, and the failure that makes dangerous is
    not that a reader is confused: it is that a line copied from one file into the other
    keeps working and selects a **different set**, with no error and no warning. The
    divergence itself is forced and correct — ``scopes.yml`` matches a path it is handed
    and ``documents.yml`` must enumerate files that exist, and ``fnmatch`` cannot
    enumerate without walking the tree itself — so the fix is not to unify the dialects.
    It is to stop the two fields from being *indistinguishable by name*.

    So the dialect owns the field name. There is no dimension-level choice about what to
    call a path-shaped field: a field holding match-dialect patterns is ``paths:``, a
    field holding enumerate-dialect patterns is ``globs:``, and :meth:`Dimension.
    __post_init__` refuses a dimension that spells either one any other way. A copied
    line then lands under a key the destination file does not accept, and the loader's
    existing unknown-key refusal — which has always been strict in both directions —
    turns a silent difference in meaning into a message naming the dialect it came from.

    That is what makes this a rule rather than a fix to two files. The *next* registry
    that ships a path-shaped field cannot reintroduce the collision by choosing a name,
    because it does not choose one; it picks a dialect and inherits the spelling, and
    picking neither means the field is not path-shaped and the trap does not apply.

    Deliberately NOT here: a check on the *patterns* themselves — refusing a pattern
    whose meaning would differ under the other dialect. It was measured rather than
    guessed at (see ``tests/test_registry_dialects.py::test_the_two_dialects_diverge_
    on_ordinary_patterns``) and it disqualifies itself: ``*.md`` and ``docs/**/*.md``,
    which are the two patterns this tool ships as its own defaults, and
    ``src/server/*``, which is the example ``scopes.yml`` ships, all select different
    sets under the two dialects. A validator refusing patterns that differ would refuse
    substantially every useful pattern including our own — KYO-33's routine false
    positive, which is the check people switch off. The name is the narrow thing that
    fires on exactly the mistake and never on correct config.
    """

    #: The one spelling this dialect's field may have. The trap is two fields with one
    #: name; the fix is one name per dialect, so this is the field's identity and not a
    #: default a dimension may override.
    field_name: str
    #: What a pattern in this dialect is *for*, in a few words — the distinction KYO-142 asks
    #: to be recorded where a reader meets either file, carried here so the refusal
    #: message and both packaged YAML files can state it without restating each other.
    job: str
    #: How wildcards behave, one line, concrete about the thing that actually differs.
    rule: str
    #: The validator a field in this dialect must be declared with. Bound by identity:
    #: it is how :meth:`Dimension.__post_init__` recognises a path-shaped field at all.
    validator: FieldValidator

    def describe(self) -> str:
        """``globs:`` — enumerates …; pathlib, where …. One line, for a refusal."""
        return f"`{self.field_name}:` {self.job} ({self.rule})"


#: Patterns checked against a path that is already known. ``fnmatch`` over the
#: root-relative path, so ``*`` crosses ``/``.
MATCH = PathDialect(
    field_name="paths",
    job="matches paths a record already declared",
    rule="fnmatch, where `*` crosses `/`, so `src/server/*` matches `src/server/main/index.ts`",
    validator=match_globs,
)

#: Patterns that walk the filesystem to find the files that exist. ``pathlib``, so
#: ``*`` stops at a ``/`` and only ``**`` crosses one.
ENUMERATE = PathDialect(
    field_name="globs",
    job="enumerates the files that exist",
    rule="pathlib, where `*` stops at `/` and `**` crosses directories, so "
    "`docs/**/*.md` reaches every depth under `docs/`",
    validator=relative_globs,
)

#: Both dialects, by the field name each owns. A path-shaped field is one whose name is
#: a key here *or* whose validator is one of these dialects' — checked in both
#: directions, because either half alone leaves a way to spell the collision back into
#: existence: a field named ``paths`` validated by something else, or a field validated
#: by :func:`relative_globs` under a fresh name.
PATH_DIALECTS: Mapping[str, PathDialect] = {
    dialect.field_name: dialect for dialect in (MATCH, ENUMERATE)
}


def other_dialect(field_name: str) -> PathDialect | None:
    """The dialect a path-shaped field is *not*, or ``None`` for a non-path field.

    What the unknown-key refusal needs: the reader typed one file's field name into the
    other file, and the useful sentence names the dialect they were reaching for.
    """
    if field_name not in PATH_DIALECTS:
        return None
    return next(d for d in (MATCH, ENUMERATE) if d.field_name != field_name)


def mapping_field(value: object) -> str | None:
    """A field holding a mapping, whose *contents* the dimension checks for itself.

    The third validator shape, and the one that admits its own limit. A
    :data:`FieldValidator` sees one value and nothing else, which is enough for a closed
    vocabulary (:func:`one_of`) or a list of strings, and not enough for a field whose
    legal contents depend on a *sibling* field — ``KIND.policy``, whose keys are one set
    for ``archetype: work`` and a different set for ``archetype: knowledge``. This
    validator carries the part that is expressible here; :attr:`Dimension.entries_check`
    is where the rest goes, so that both halves land in one refusal.
    """
    if not isinstance(value, dict):
        return "must be a mapping"
    return None


#: KYO-299. The one placeholder a ``numbered.heading`` template carries, and the reason
#: a project can declare its numbering without writing a regular expression. A template
#: is literal text with this in it — ``"## {number}."`` — and the two things anything
#: needs from it are both derivable: escape the literals and substitute ``(\d+)`` to
#: READ a document's existing numbers, or substitute the next integer to WRITE one.
#:
#: A regex field was the obvious alternative and is the wrong one for this file. Every
#: other path-shaped or pattern-shaped field here is declared in a dialect a reader can
#: check by eye (:class:`PathDialect` exists because two of them were confusable); a
#: field holding `^##\s+(\d+)\.` would be the first that a consumer can get *subtly*
#: wrong — an unanchored pattern, a greedy group, a capture in the wrong place — in a way
#: that reads fine and numbers the wrong thing. The template can be wrong in exactly one
#: way (no ``{number}``), and that way is refused below by name.
HEADING_NUMBER = "{number}"

#: KYO-299. What a ``numbered:`` mapping must hold, and what it may. Named here so the
#: validator's refusal, the packaged ``documents.yml`` comment and
#: :func:`kyosei.docs_numbered.numbered_documents` cannot list different sets (law 3).
NUMBERED_REQUIRED: tuple[str, ...] = ("fragments", "heading")
NUMBERED_OPTIONAL: tuple[str, ...] = ("after", "amends")

#: KYO-493. What a ``numbered.amends:`` mapping must hold — both keys, always, because
#: writing the back-link without the status is exactly the ``amended_by``/``status``
#: footgun charter law 3 names. Declaring the
#: pair together is what makes the tool always write them together.
NUMBERED_AMENDS_KEYS: tuple[str, ...] = ("field", "status")

#: KYO-493. The spellings ``numbered.amends.field`` may take: Kyosei's own back-link key
#: (``docs_parse.SECTION_METADATA_KEYS``' ``amended-by``) and the underscore alias its
#: parser reads as the same field (``docs_parse._KEY_ALIASES``). Any other key would have
#: Kyosei write a field its own parser does not read (law 2).
NUMBERED_AMENDS_FIELDS: tuple[str, ...] = ("amended-by", "amended_by")


def numbered_sections(value: object) -> str | None:
    """A document set's ``numbered:`` block: this set's files grow by numbered section.

    KYO-299. Three facts, and each one has a consumer in the same commit:

    * ``heading`` — the template its numbered headings follow, carrying
      :data:`HEADING_NUMBER` exactly once. Read by
      :func:`kyosei.docs_numbered.heading_pattern` to find the document's last number, and
      by ``kyo check-branch`` to notice a worker writing one by hand.
    * ``fragments`` — the directory a dispatched worker writes its new section into, one
      file per dispatch, which is what makes two workers adding a section to one document
      unable to collide: they never write the same path. Read by
      :func:`kyosei.docs_numbered.numbered_documents` and stated in every brief.
    * ``after`` — the project's own commands to run once sections have been appended (a
      generated index, a validator). **Kyosei names these and never runs them**, exactly
      as it prints ``worktree_setup`` and executes none of it; a tool that ran a
      project's commands because a config file said so is a different tool.

    Every problem is collected and returned in one string rather than the first one
    found, for :func:`_build`'s reason: a registry whose two mistakes take two runs to
    see is the thing this module exists to prevent.
    """
    if not isinstance(value, dict):
        return (
            "must be a mapping declaring how this set's documents are numbered: "
            f"{', '.join(NUMBERED_REQUIRED)} (required), "
            f"{', '.join(NUMBERED_OPTIONAL)} (optional)"
        )
    problems: list[str] = []
    allowed = {*NUMBERED_REQUIRED, *NUMBERED_OPTIONAL}
    unknown = sorted(set(value) - allowed)
    if unknown:
        problems.append(
            f"carries unknown key(s) {', '.join(unknown)}; a numbered: block may hold "
            f"{', '.join(sorted(allowed))}"
        )
    for key in NUMBERED_REQUIRED:
        if key not in value:
            problems.append(f"is missing {key}, which is required")

    heading = value.get("heading")
    if "heading" in value:
        if not isinstance(heading, str) or not heading.strip():
            problems.append("heading must be a non-empty string")
        elif heading.count(HEADING_NUMBER) != 1:
            problems.append(
                f"heading {heading!r} must carry {HEADING_NUMBER} exactly once — it is "
                "the template a numbered heading follows, and without the placeholder "
                "nothing can read the document's last number or write the next "
                f"(e.g. '## {HEADING_NUMBER}.')"
            )
        elif not re.match(r"^#{1,6} ", heading):
            problems.append(
                f"heading {heading!r} must begin with one to six '#' characters and a "
                "space: it is matched against the document's own ATX headings, and a "
                "template that is not one would match nothing at all"
            )

    fragments = value.get("fragments")
    if "fragments" in value:
        if not isinstance(fragments, str) or not fragments.strip():
            problems.append(
                "fragments must be a non-empty directory, relative to the project root"
            )
        elif _escaping([fragments]):
            problems.append(
                f"fragments {fragments!r} must be relative to the project root and must "
                "not climb out of it with '..'; workers write files there and the "
                "orchestrator deletes them, and neither is a thing this tool does outside "
                "the repository"
            )

    after = value.get("after")
    if "after" in value:
        trouble = _string_list(after)
        if trouble:
            problems.append(f"after {trouble} — each one a command line to NAME, never to run")

    if "amends" in value:
        trouble = _numbered_amends(value.get("amends"))
        if trouble:
            problems.append(trouble)

    return "; ".join(problems) or None


def _numbered_amends(value: object) -> str | None:
    """A ``numbered.amends:`` block: how ``kyo docs assemble`` writes the back-link a
    fragment's ``amends:`` header asks for (KYO-493).

    * ``field`` — the back-link key to write when a section has none yet, in this
      project's spelling: one of :data:`NUMBERED_AMENDS_FIELDS`. Read by
      :func:`kyosei.docs_numbered.assemble_plan`.
    * ``status`` — the status value an amended section is given. Non-empty, and never
      one of ``docs_parse.DEAD_STATUSES``: writing ``superseded`` through an *amends*
      relation would retire a section by mistake.

    Both are required; every problem is returned in one string, as above.
    """
    # docs_parse imports this module, so the one set of dead statuses is read lazily
    # rather than copied (law 3).
    from .docs_parse import DEAD_STATUSES

    shape = (
        f"amends must be a mapping holding exactly {' and '.join(NUMBERED_AMENDS_KEYS)} "
        f"(e.g. {{field: amended_by, status: amended}})"
    )
    if not isinstance(value, dict):
        return shape
    problems: list[str] = []
    unknown = sorted(str(key) for key in set(value) - set(NUMBERED_AMENDS_KEYS))
    if unknown:
        problems.append(
            f"amends carries unknown key(s) {', '.join(unknown)}; it may hold only "
            f"{', '.join(NUMBERED_AMENDS_KEYS)}"
        )
    for key in NUMBERED_AMENDS_KEYS:
        if key not in value:
            problems.append(
                f"amends is missing {key}, which is required — the back-link and the "
                "status are written together or not at all"
            )
    if "field" in value and value.get("field") not in NUMBERED_AMENDS_FIELDS:
        problems.append(
            f"amends.field {value.get('field')!r} must be one of "
            f"{', '.join(NUMBERED_AMENDS_FIELDS)} — Kyosei's back-link key in either "
            "spelling its parser reads; any other key would be a field nothing reads"
        )
    if "status" in value:
        status = value.get("status")
        if not isinstance(status, str) or not status.strip() or "\n" in status:
            problems.append("amends.status must be a non-empty one-line string (e.g. amended)")
        elif status.strip().lower() in DEAD_STATUSES:
            problems.append(
                f"amends.status {status!r} is a dead status "
                f"({', '.join(sorted(DEAD_STATUSES))}); an amended section is still "
                "current, and writing this would retire every section a fragment amends "
                "(e.g. amended)"
            )
    return "; ".join(problems) or None


#: KYO-499. What a ``section_status:`` block may hold. ``statuses`` is required because
#: the vocabulary has to be the project's own: a consumer's sections say ``accepted``
#: and ``executed``, and a default closed set would refuse real documents. Named here so
#: the validator's refusal, the packaged ``documents.yml`` comment and
#: :class:`kyosei.docs_parse.SectionStatusSet` cannot list different sets (law 3).
SECTION_STATUS_REQUIRED: tuple[str, ...] = ("statuses",)
SECTION_STATUS_OPTIONAL: tuple[str, ...] = (
    "pointer_obligations",
    "index",
    "index_lists",
    "index_depth",
    "index_fields",
)

#: KYO-520. The columns an ``index_lists: every`` table always carries, so an
#: ``index_fields`` name may not reuse one (a numbered set's ``#``/``title`` and an
#: unnumbered set's ``section`` are both fixed, and either spelling is refused in both).
INDEX_FIXED_COLUMNS: tuple[str, ...] = (
    "#",
    "title",
    "section",
    "status",
    "links",
    "head of chain",
    "governs",
)

#: KYO-512. What a set's generated index lists below its summary table: ``non-current``
#: (the default, KYO-502's shape — each section with a status other than current or a
#: link, opening line included) or ``every`` (one row per section, carrying nothing a
#: prose edit can change, so the index can be a project's per-section table of contents).
#: Spelled once for the validator's refusal, :class:`kyosei.docs_parse.SectionStatusSet`
#: and :mod:`kyosei.docs_index`.
INDEX_LISTS_NON_CURRENT = "non-current"
INDEX_LISTS_EVERY = "every"
INDEX_LISTS_VALUES: tuple[str, ...] = (INDEX_LISTS_NON_CURRENT, INDEX_LISTS_EVERY)


def section_status_block(value: object) -> str | None:
    """A document set's ``section_status:`` block: this set's sections carry a status and
    links the tool holds to agree (KYO-499.c2).

    Its presence turns on the stricter rules :func:`kyosei.docs_check.check_docs` runs on
    the set's documents — a status inside ``statuses``, links that resolve to a heading, a
    linked section that cites the linking one back, and no live ``src/`` pointer to a
    superseded section (docs/design/docs.md#kyo-499). The pairs-agree rule needs no
    block: it holds in every document of every project.

    * ``statuses`` — the closed vocabulary a section's ``status:`` may take here, a
      non-empty list of non-empty strings. Read by the status rule.
    * ``pointer_obligations`` — a boolean, default true: whether a code pointer into this
      set raises a docs obligation on its section. Read into
      :class:`kyosei.docs_parse.SectionStatusSet` and stated by ``kyo docs check``.
    * ``index`` — a root-relative path for the generated section index. Validated the
      way ``numbered.fragments`` is, and stated by ``kyo docs check``.
    * ``index_lists`` — one of :data:`INDEX_LISTS_VALUES` (KYO-512): what that index
      lists below its summary. Meaningless without ``index``, so refused without it.
    * ``index_depth`` — an integer 1–6 (KYO-512): with ``index_lists: every``, only
      headings this deep or shallower get a row — ``2`` lists a numbered notes file's
      ``##`` sections and not the ``###`` headings inside them. Refused otherwise.
    * ``index_fields`` — a list of field names (KYO-520): with ``index_lists: every``,
      each is rendered as its own column, read from a ``name: value`` line in the
      section. A name that is empty, repeated or one of :data:`INDEX_FIXED_COLUMNS`
      is refused, and so is the option without ``index_lists: every``.

    Every problem is returned in one string, for :func:`_build`'s reason.
    """
    allowed = (*SECTION_STATUS_REQUIRED, *SECTION_STATUS_OPTIONAL)
    if not isinstance(value, dict):
        return (
            "must be a mapping declaring this set's section statuses: "
            f"{', '.join(SECTION_STATUS_REQUIRED)} (required), "
            f"{', '.join(SECTION_STATUS_OPTIONAL)} (optional) "
            "(e.g. {statuses: [current, amended, superseded]})"
        )
    problems: list[str] = []
    unknown = sorted(str(key) for key in set(value) - set(allowed))
    if unknown:
        problems.append(
            f"carries unknown key(s) {', '.join(unknown)}; a section_status: block may hold "
            f"{', '.join(sorted(allowed))}"
        )
    if "statuses" not in value:
        problems.append(
            "is missing statuses, which is required — the closed list of values a "
            "section's status: may take in this set (e.g. [current, amended, superseded])"
        )
    else:
        statuses = value.get("statuses")
        if (
            not isinstance(statuses, list)
            or not statuses
            or not all(isinstance(item, str) and item.strip() for item in statuses)
        ):
            problems.append(
                "statuses must be a non-empty list of non-empty strings "
                "(e.g. [current, amended, superseded])"
            )
    if "pointer_obligations" in value and not isinstance(value.get("pointer_obligations"), bool):
        problems.append(
            f"pointer_obligations must be true or false; got {value.get('pointer_obligations')!r}"
        )
    if "index" in value:
        index = value.get("index")
        if not isinstance(index, str) or not index.strip():
            problems.append("index must be a non-empty path, relative to the project root")
        elif _escaping([index]):
            problems.append(
                f"index {index!r} must be relative to the project root and must not climb "
                "out of it with '..'"
            )
    if "index_lists" in value:
        lists = value.get("index_lists")
        if lists not in INDEX_LISTS_VALUES:
            problems.append(
                f"index_lists must be one of {', '.join(INDEX_LISTS_VALUES)}; got {lists!r}"
            )
        elif "index" not in value:
            problems.append(
                "index_lists says what the generated index lists, and this block declares "
                "no index: path; add one (e.g. index: docs/sections.md) or remove index_lists"
            )
    if "index_depth" in value:
        depth = value.get("index_depth")
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 6:
            problems.append(f"index_depth must be a heading level, 1 to 6; got {depth!r}")
        elif value.get("index_lists") != INDEX_LISTS_EVERY:
            problems.append(
                "index_depth limits the rows of an index listing every section, and this "
                f"block does not declare index_lists: {INDEX_LISTS_EVERY}; add it or remove "
                "index_depth"
            )
    if "index_fields" in value:
        fields = value.get("index_fields")
        if (
            not isinstance(fields, list)
            or not fields
            or not all(isinstance(item, str) and item.strip() for item in fields)
        ):
            problems.append(
                "index_fields must be a non-empty list of non-empty field names "
                "(e.g. [area])"
            )
        else:
            names = [item.strip() for item in fields]
            clash = sorted({n for n in names if n.lower() in INDEX_FIXED_COLUMNS})
            if clash:
                problems.append(
                    f"index_fields names {', '.join(clash)}, which the index already has as "
                    f"a column ({', '.join(INDEX_FIXED_COLUMNS)}); rename or remove it"
                )
            if len({n.lower() for n in names}) != len(names):
                problems.append("index_fields names a field twice; list each once")
        if value.get("index_lists") != INDEX_LISTS_EVERY:
            problems.append(
                "index_fields adds columns to an index listing every section, and this "
                f"block does not declare index_lists: {INDEX_LISTS_EVERY}; add it or remove "
                "index_fields"
            )
    return "; ".join(problems) or None


#: KYO-552. What an ``ssot:`` block may hold (slice 1 of KYO-551). Named here so the
#: validator's refusal, :class:`kyosei.docs_parse.SsotDeclaration` and
#: docs/reference/topics/docs-governance.md cannot list different sets (law 3).
#: KYO-553: the three id keys are optional together -- a block declares all of them (an
#: id-bearing set) or none (an id-less set, checked for its tombstones only), never some.
SSOT_ID_KEYS: tuple[str, ...] = ("id_heading", "id_pattern", "prefixes")
SSOT_OPTIONAL: tuple[str, ...] = ("tombstone_max_lines", "history_lint")
#: The two placeholders an ``ssot.id_heading`` template carries, each exactly once.
SSOT_ID = "{id}"
SSOT_TITLE = "{title}"
#: How many non-blank lines a superseded section may keep below its metadata lines when
#: the block does not say (KYO-552.c1).
SSOT_TOMBSTONE_MAX_LINES_DEFAULT = 3
#: KYO-555: what an ``ssot:`` set's ``history_lint`` may say. ``off`` runs no history lint
#: on the set's documents, ``warn`` prints what a branch newly introduces and never
#: blocks, ``block`` also blocks a worker's stop on a newly introduced block-grade match
#: (:mod:`kyosei.docs_history`).
SSOT_HISTORY_LINT_OFF = "off"
SSOT_HISTORY_LINT_WARN = "warn"
SSOT_HISTORY_LINT_BLOCK = "block"
SSOT_HISTORY_LINT_VALUES: tuple[str, ...] = (
    SSOT_HISTORY_LINT_OFF,
    SSOT_HISTORY_LINT_WARN,
    SSOT_HISTORY_LINT_BLOCK,
)
SSOT_HISTORY_LINT_DEFAULT = SSOT_HISTORY_LINT_WARN


def ssot_history_lint(value: object) -> str | None:
    """``value`` as one of :data:`SSOT_HISTORY_LINT_VALUES`, or ``None`` when it is none.

    YAML 1.1 reads a bare ``off`` as ``False``, which is what an operator writing
    ``history_lint: off`` gets back from the loader, so ``False`` is ``off``. ``True``
    (a bare ``on`` or ``yes``) names none of the three and is refused.
    """
    if value is False:
        return SSOT_HISTORY_LINT_OFF
    if isinstance(value, str) and value.strip() in SSOT_HISTORY_LINT_VALUES:
        return value.strip()
    return None


def ssot_block(value: object) -> str | None:
    """A document set's ``ssot:`` block: each section of this set's documents that owns
    a requirement carries its id in its heading, and the id is the one name everything
    else cites it by (KYO-552, slice 1 of KYO-551).

    * ``id_heading`` — the template an owning heading's text follows, carrying
      :data:`SSOT_ID` and :data:`SSOT_TITLE` exactly once each (``'{id}. {title}'``).
      Matched against a heading's TEXT at any level, so it carries no ``#``. Read by
      :class:`kyosei.docs_ssot.SsotIndex` to find each id's owner.
    * ``id_pattern`` — a regular expression an id matches (``'[A-Z]{1,4}-\\d+'``). It
      is searched for as a whole token in prose, so it is refused with a ``^`` or ``$``
      anchor, which would make it match nothing there.
    * ``prefixes`` — a non-empty map from prefix (the part of an id before its ``-``) to
      the one document that owns that prefix's ids. Only a token whose prefix is a key
      here is an id at all, which is what keeps ``UTF-8`` and ``SHA-256`` out of it. That
      each path is one the set's globs match is checked beside the set
      (:func:`_document_entries_check`), since this validator does not see the globs.
    * ``tombstone_max_lines`` — a positive integer, default
      :data:`SSOT_TOMBSTONE_MAX_LINES_DEFAULT`: how many non-blank lines a superseded
      section keeps below its metadata.
    * ``history_lint`` — one of :data:`SSOT_HISTORY_LINT_VALUES`, default
      :data:`SSOT_HISTORY_LINT_DEFAULT` (KYO-555): whether diary prose a branch newly
      introduces into the set's documents is ignored, printed, or blocks a worker's stop
      (:mod:`kyosei.docs_history`). Accepted on a set with or without ids.

    The three id keys (:data:`SSOT_ID_KEYS`) are optional together (KYO-553): a set whose
    sections carry no ids declares none of them (``ssot: {}``), and only the tombstone
    rule applies to it. A block declaring some but not all is refused, naming the missing
    keys and both remedies.

    Every problem is returned in one string, for :func:`_build`'s reason.
    """
    allowed = (*SSOT_ID_KEYS, *SSOT_OPTIONAL)
    if not isinstance(value, dict):
        return (
            "must be a mapping: {} for a set whose sections carry no ids, or one declaring "
            f"how this set's sections own ids with all of {', '.join(SSOT_ID_KEYS)}; "
            f"{', '.join(SSOT_OPTIONAL)} are optional either way "
            "(e.g. {id_heading: '{id}. {title}', id_pattern: '[A-Z]{1,4}-\\d+', "
            "prefixes: {API: docs/specs/api.md}})"
        )
    problems: list[str] = []
    unknown = sorted(str(key) for key in set(value) - set(allowed))
    if unknown:
        problems.append(
            f"carries unknown key(s) {', '.join(unknown)}; remove it — an ssot: block may "
            f"hold {', '.join(sorted(allowed))}"
        )
    present = [key for key in SSOT_ID_KEYS if key in value]
    missing = [key for key in SSOT_ID_KEYS if key not in value]
    if present and missing:
        problems.append(
            f"declares {', '.join(present)} and is missing {', '.join(missing)}; the id "
            "keys go together, so declare all of "
            f"{', '.join(SSOT_ID_KEYS)} for a set whose sections carry ids, or none of "
            "them for a set whose sections carry none (its tombstones are still checked)"
        )
    if "id_heading" in value:
        heading = value.get("id_heading")
        if not isinstance(heading, str) or not heading.strip():
            problems.append("id_heading must be a non-empty string (e.g. '{id}. {title}')")
        elif heading.count(SSOT_ID) != 1 or heading.count(SSOT_TITLE) != 1:
            problems.append(
                f"id_heading {heading!r} must carry {SSOT_ID} and {SSOT_TITLE} exactly "
                "once each — the id names the section and the title says what it requires "
                "(e.g. '{id}. {title}')"
            )
        elif heading.lstrip().startswith("#"):
            problems.append(
                f"id_heading {heading!r} must not begin with '#': it is matched against a "
                "heading's text at any level; drop the leading '#' characters"
            )
    if "id_pattern" in value:
        pattern = value.get("id_pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            problems.append("id_pattern must be a non-empty regular expression")
        else:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                problems.append(
                    f"id_pattern {pattern!r} is not a regular expression that compiles "
                    f"({exc}); correct it (e.g. '[A-Z]{{1,4}}-\\d+')"
                )
            else:
                if pattern.startswith("^") or pattern.endswith("$"):
                    problems.append(
                        f"id_pattern {pattern!r} is anchored; drop the '^' and '$' — an id "
                        "is searched for as a whole token inside prose, where an anchored "
                        "pattern matches nothing"
                    )
                elif compiled.match(""):
                    problems.append(
                        f"id_pattern {pattern!r} matches the empty string; make it match "
                        "an id and nothing shorter (e.g. '[A-Z]{1,4}-\\d+')"
                    )
    if "prefixes" in value:
        prefixes = value.get("prefixes")
        if not isinstance(prefixes, dict) or not prefixes:
            problems.append(
                "prefixes must be a non-empty mapping from an id's prefix to the document "
                "that owns its ids (e.g. {API: docs/specs/api.md})"
            )
        else:
            for prefix, path in prefixes.items():
                if not isinstance(prefix, str) or not prefix.strip():
                    problems.append(
                        f"prefixes key {prefix!r} must be a non-empty string; quote it "
                        "(YAML reads a bare Y, N, ON or OFF as a boolean)"
                    )
                if not isinstance(path, str) or not path.strip():
                    problems.append(
                        f"prefixes.{prefix} must be a document path, relative to the "
                        "project root"
                    )
                elif _escaping([path]):
                    problems.append(
                        f"prefixes.{prefix} {path!r} must be relative to the project root "
                        "and must not climb out of it with '..'"
                    )
    if "tombstone_max_lines" in value:
        limit = value.get("tombstone_max_lines")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            problems.append(f"tombstone_max_lines must be a positive integer; got {limit!r}")
    if "history_lint" in value and ssot_history_lint(value.get("history_lint")) is None:
        problems.append(
            f"history_lint must be one of {', '.join(SSOT_HISTORY_LINT_VALUES)}; got "
            f"{value.get('history_lint')!r} (default {SSOT_HISTORY_LINT_DEFAULT})"
        )
    return "; ".join(problems) or None


#: KYO-600. The formats a document set's ``format:`` may name. ``markdown`` is the default
#: every set without the key has; ``recx-manifest`` reads RECX.yaml through
#: :mod:`kyosei.docs_recx`.
DOCUMENT_FORMAT_MARKDOWN = "markdown"
DOCUMENT_FORMAT_RECX = "recx-manifest"
DOCUMENT_FORMATS: tuple[str, ...] = (DOCUMENT_FORMAT_MARKDOWN, DOCUMENT_FORMAT_RECX)
#: The one file a ``recx-manifest`` set may match: RECX.yaml at the code root.
RECX_MANIFEST_FILE = "RECX.yaml"
#: What a ``recx_manifest:`` block may hold, each optional and each in the governs:
#: dialect: the (preamble) section's targets, and per-id targets for evidence sections and
#: for narrowed module sections.
RECX_MANIFEST_KEYS: tuple[str, ...] = ("project_governs", "evidence_governs", "module_governs")
#: The markdown features a ``recx-manifest`` set may not declare.
RECX_MANIFEST_REFUSED_FIELDS: tuple[str, ...] = ("section_status", "ssot", "numbered")


def _target_list_problem(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return "must be a non-empty list of governs: targets"
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return "must list only non-empty strings (governs: targets)"
    return None


def recx_manifest_block(value: object) -> str | None:
    """A ``recx-manifest`` set's ``recx_manifest:`` block (KYO-600.c4, c8).

    ``project_governs`` is a target list for the (preamble) section; ``evidence_governs``
    and ``module_governs`` map an evidence or module id to a target list. Every target is
    in the ``governs:`` dialect and is resolved as a markdown section's is. Whether an id
    names an entry in RECX.yaml is ``kyo docs check``'s question, because the manifest is
    not read when this file is.
    """
    if not isinstance(value, dict):
        return (
            "must be a mapping holding any of "
            f"{', '.join(RECX_MANIFEST_KEYS)}"
        )
    unknown = sorted(str(key) for key in set(value) - set(RECX_MANIFEST_KEYS))
    if unknown:
        return (
            f"carries unknown key(s) {', '.join(unknown)}; a recx_manifest: block may hold "
            f"{', '.join(RECX_MANIFEST_KEYS)}"
        )
    problems: list[str] = []
    if "project_governs" in value:
        trouble = _target_list_problem(value["project_governs"])
        if trouble:
            problems.append(f"project_governs {trouble}")
    for key in ("evidence_governs", "module_governs"):
        if key not in value:
            continue
        mapping = value[key]
        if not isinstance(mapping, dict) or not mapping:
            problems.append(f"{key} must be a non-empty mapping of id to governs: targets")
            continue
        for named, targets in mapping.items():
            trouble = _target_list_problem(targets)
            if trouble:
                problems.append(f"{key}.{named} {trouble}")
    return "; ".join(problems) or None


def _recx_entries_check(entries: Mapping[str, Entry]) -> list[str]:
    """The ``format: recx-manifest`` rules that read sibling fields (KYO-600.c1)."""
    problems: list[str] = []
    recx_sets: list[str] = []
    for name, entry in entries.items():
        label = f"document-set {name!r}"
        if entry.get("format") != DOCUMENT_FORMAT_RECX:
            if entry.get("recx_manifest") is not None:
                problems.append(
                    f"{label}: recx_manifest is declared on a markdown set; it belongs on "
                    f"the set declaring format: {DOCUMENT_FORMAT_RECX}, so add that format "
                    "or remove the block"
                )
            continue
        recx_sets.append(name)
        for glob in entry.get("globs") or ():
            if glob != RECX_MANIFEST_FILE:
                problems.append(
                    f"{label}: format {DOCUMENT_FORMAT_RECX} reads exactly "
                    f"{RECX_MANIFEST_FILE} at the code root, and its globs name {glob!r}; "
                    f"set globs: [{RECX_MANIFEST_FILE}] and keep other documents in a "
                    "markdown set"
                )
        for refused in RECX_MANIFEST_REFUSED_FIELDS:
            if entry.get(refused) is not None:
                problems.append(
                    f"{label}: {refused} is a markdown feature and a "
                    f"{DOCUMENT_FORMAT_RECX} set cannot declare it; remove {refused} from "
                    "this set"
                )
    if len(recx_sets) > 1:
        problems.append(
            f"document-sets {', '.join(map(repr, recx_sets))} each declare format "
            f"{DOCUMENT_FORMAT_RECX}, and a project has one {RECX_MANIFEST_FILE}; keep one "
            "of them"
        )
    return problems


def glob_matches(glob: str, path: str) -> bool:
    """Whether root-relative ``path`` is one :meth:`pathlib.Path.glob` would yield for
    ``glob`` — ``*`` within one segment, ``**`` across segments (KYO-552). Matched as
    text, so a document the set will hold once it is written already counts."""
    out: list[str] = []
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out.append("(?:[^/]+/)*")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif glob[index] == "*":
            out.append("[^/]*")
            index += 1
        elif glob[index] == "?":
            out.append("[^/]")
            index += 1
        elif glob[index] == "[" and "]" in glob[index + 2 :]:
            end = glob.index("]", index + 2)
            body = glob[index + 1 : end]
            negate = body.startswith("!")
            body = body[1:] if negate else body
            out.append(f"[{'^' if negate else ''}{body.replace(chr(92), chr(92) * 2)}]")
            index = end + 1
        else:
            out.append(re.escape(glob[index]))
            index += 1
    return re.fullmatch("".join(out), path) is not None


def _document_entries_check(
    entries: Mapping[str, Entry], declared: Mapping[str, object]
) -> list[str]:
    """The ``ssot:`` rules that read a sibling field (KYO-552.c1, c9): its prefixes'
    paths against the set's ``globs``, and the block against ``numbered:`` and
    ``section_status:``. Each problem names its remedy."""
    problems: list[str] = _recx_entries_check(entries)
    for name, entry in entries.items():
        block = entry.get("ssot")
        if not isinstance(block, dict):
            continue
        label = f"document-set {name!r}: ssot"
        if entry.get("numbered") is not None:
            problems.append(
                f"{label} and numbered are declared together, and a set cannot be both: "
                "an ssot set edits a section in place and a numbered set only appends; "
                "remove one of them (an append-only log belongs in a numbered set of its "
                "own)"
            )
        status = entry.get("section_status")
        statuses = (
            [str(item).strip().lower() for item in status.get("statuses") or ()]
            if isinstance(status, dict)
            else None
        )
        if statuses is None:
            problems.append(
                f"{label} needs a section_status block whose statuses include superseded, "
                "because a replaced requirement stays as a superseded tombstone; add "
                "section_status: {statuses: [current, superseded]}"
            )
        elif "superseded" not in statuses:
            problems.append(
                f"{label} needs superseded among section_status.statuses, because a "
                "replaced requirement stays as a superseded tombstone; add superseded to "
                "section_status.statuses"
            )
        globs = [glob for glob in entry.get("globs") or () if isinstance(glob, str)]
        for prefix, path in (block.get("prefixes") or {}).items():
            if isinstance(path, str) and not any(glob_matches(g, path) for g in globs):
                problems.append(
                    f"{label}.prefixes.{prefix} names {path}, which none of this set's "
                    f"globs ({', '.join(globs)}) match; add a glob that matches it, or map "
                    "the prefix to a document in this set"
                )
    return problems


def one_of(*allowed: str) -> FieldValidator:
    """A field whose value must be one of a fixed, code-owned vocabulary.

    The counterpart to :func:`_string_list`, and the second validator shape the four
    dimensions need. It exists because a registry may be per-project in its *names*
    while a field on it is not: KYO-101's ``role`` is the archetype's vocabulary, not
    the project's, so a project may call its terminal status ``shipped`` and may not
    invent a seventh role for it. The refusal lists the vocabulary, because a closed
    set the caller cannot see is a guessing game (law 6).
    """

    def validate(value: object) -> str | None:
        if not isinstance(value, str):
            return f"must be a string; one of {', '.join(allowed)}"
        if value not in allowed:
            return f"must be one of {', '.join(allowed)}; got {value!r}"
        return None

    return validate


@dataclass(frozen=True)
class Dimension:
    """One registry dimension: what it is called, where it lives, what an entry may say.

    The four registry dimensions differ in exactly these fields and in nothing else,
    which is the claim this dataclass exists to keep true.
    """

    #: How one entry is referred to in prose and in flags — ``scope``, ``status``.
    singular: str
    #: The plural, which is both the filename stem and the top-level YAML key. One
    #: word, two uses, so a reader who has seen the file knows the key and vice versa.
    plural: str
    #: One line naming what the dimension is *for*, printed by the refusal so a caller
    #: who has never opened the file learns what they are being asked to declare.
    what: str
    #: Optional per-entry fields, each with a validator returning a problem or ``None``.
    optional_fields: Mapping[str, FieldValidator] = field(default_factory=dict)
    #: Per-entry fields this dimension requires *in addition to* ``summary``, each with
    #: the same validator shape. ``summary`` is required of every dimension and is not
    #: listed here; a dimension whose entries mean nothing without a further field says
    #: so here rather than validating it after the fact.
    #:
    #: KYO-101 is why this exists: a status entry without a ``role`` is a name the
    #: contract machinery cannot bind to, so "declared but roleless" must not be a
    #: state the loader can produce. Making it a *declared* required field rather than
    #: a second pass in the status code is what keeps the four dimensions on one
    #: loader — the alternative was a status-shaped check living outside this module,
    #: which is the drift the shared shape exists to prevent.
    required_fields: Mapping[str, FieldValidator] = field(default_factory=dict)
    #: Is the file's absence an error? ``kinds.yml`` yes; ``scopes.yml`` no — see the
    #: module docstring. A required registry that exists but declares *nothing* is
    #: refused too: "load-bearing" is a claim about the entries, not about the inode.
    required: bool = False
    #: The rules this dimension's entries impose on *each other*, or on a field whose
    #: legal values depend on a sibling field. Returns a problem per rule broken, and
    #: they join the same collected list :func:`_build` raises — which is the whole
    #: point of the hook and the reason it is not simply a pass the caller runs
    #: afterwards. KYO-104: ``kinds.yml`` is the first dimension with either kind of
    #: rule (``policy``'s legal keys depend on ``archetype``; every ``accepts_from``
    #: edge must have a matching ``produces``), and a second collect-and-raise in
    #: ``kinds.py`` would have been a registry whose four mistakes take two runs to
    #: see — precisely the drift this module exists to prevent.
    #:
    #: It is declared here rather than implemented here on purpose: the *rules* are the
    #: dimension's own and belong beside the code that depends on them (``kinds.py``
    #: knows what an archetype is; this module must not). What is shared is when they
    #: run, how their problems are worded into a message, and that there is one raise.
    #:
    #: Called with two views of the same file: the entries that passed field validation,
    #: and the raw declaration, entries that failed included. Both, because a rule
    #: *between* entries has to see the ones that are broken too — otherwise one mistake
    #: is printed twice ("bug has a bad summary" *and* "task names undefined kind
    #: 'bug'"), and a broken entry's own cross-references go unchecked until the day
    #: someone fixes its summary, which is law 6's one-pass repair lost in the corner
    #: where a file has two mistakes rather than one.
    entries_check: Callable[[Mapping[str, Entry], Mapping[str, object]], list[str]] | None = None

    def __post_init__(self) -> None:
        """Refuse a dimension that spells a path-shaped field the wrong way (KYO-142).

        This is KYO-142's requirement that "whatever is decided applies to the next
        registry that ships a path-shaped field, rather than being settled once per
        file". A comment asking
        the next author to pick the right name is settled once per file by construction;
        a constructor that will not build the dimension is not.

        It fires at import, on *this project's own* dimension declarations, and never at
        a consumer: a project cannot declare a dimension, so no config anyone writes can
        reach it. That is deliberate — it is the whole reason this is affordable for a
        trap nobody has hit yet. The cost falls on a kyosei developer adding a fourth
        registry with a path-shaped field, at the moment they add it, which is the only
        moment the choice is cheap.

        Both directions, because either alone leaves the collision spellable:

        * a field declared with a dialect's validator under some other name (``files:``
          validated by :func:`relative_globs`) — a third spelling of the same dialect,
          which is the collision again with more names, not fewer;
        * a field named ``paths`` or ``globs`` validated by something else — a field
          that *looks* like one of the two dialects and is not, which is worse than the
          collision, because the name now lies.
        """
        for field_name, validator in {**self.required_fields, **self.optional_fields}.items():
            owns_name = PATH_DIALECTS.get(field_name)
            for dialect in (MATCH, ENUMERATE):
                if validator is dialect.validator and field_name != dialect.field_name:
                    raise RegistryError(
                        f"dimension {self.plural!r} declares {field_name!r} with the "
                        f"{dialect.field_name!r} dialect's validator. A path-shaped "
                        f"field does not get to choose its name: {dialect.describe()}. "
                        f"Name it {dialect.field_name!r} or validate it as something "
                        "else (KYO-142)."
                    )
            if owns_name is not None and validator is not owns_name.validator:
                raise RegistryError(
                    f"dimension {self.plural!r} declares {field_name!r}, which is the "
                    f"reserved name for the {owns_name.field_name!r} dialect "
                    f"({owns_name.describe()}), but validates it with something else. "
                    "Use that dialect's validator or pick a different field name "
                    "(KYO-142)."
                )

    @property
    def filename(self) -> str:
        return f"{self.plural}.yml"

    @property
    def required_entry_keys(self) -> tuple[str, ...]:
        """Every key an entry must carry, ``summary`` first — named once so the two
        refusal messages and the validator cannot list different sets (law 3)."""
        return (SUMMARY_FIELD, *sorted(self.required_fields))

    def path_in(self, kyosei_dir: Path) -> Path:
        return kyosei_dir / self.filename


#: KYO-100: scope, the dimension that had no schema representation at all.
#:
#: ``paths`` is the only optional field, and it ships with its consumer in the same
#: commit (law 2): :func:`suggest_from_paths`, which is what makes a scope *derivable
#: from touches as a suggestion*. KYO-46 is verbatim about the limit — derivation is "a
#: suggester and validator for scope, never the source" — so nothing reads this field
#: to decide a record's scope, and the record's own ``scope`` column is the only place
#: that answer is stored.
#:
#: Deliberately NOT here: a ``docs_root`` field. KYO-72 leaves "does a scope declare its
#: own docs root" open on purpose, and shipping the field before the decision would
#: either decide it by default or store a field nothing reads. What this shape owes that
#: question is that it stays *expressible*, which it is: one entry in ``optional_fields``
#: and the validator, on the day something reads it.
SCOPE = Dimension(
    singular="scope",
    plural="scopes",
    what="the part of the system a record's work belongs to",
    optional_fields={"paths": match_globs},
)

#: KYO-102: gate tags, the dimension that turns a gate's payload from free
#: text into something you can ask a question about. KYO-46 states the problem in one
#: sentence — "a gate's payload is free text, so 'everything waiting on a human to
#: review a UI element' is not a query" — and the fix it names is this file, with the
#: free text kept beside it as optional detail rather than as the only content.
#:
#: **Declared here, beside SCOPE, and not in ``contracts``**, which is the opposite of
#: where KYO-101 put ``STATUS`` — because the reason that one moved does not apply. A
#: status entry has a required ``role`` validated against ``contracts.STATUS_ROLES``, so
#: its declaration had to sit on the side of the import that knows what a role is. A
#: gate-tag entry validates against nothing in ``contracts``: the tag vocabulary is
#: wholly the project's, and the four gate types are a separate, closed dimension a tag
#: neither extends nor constrains. With no import forcing the move, the default holds —
#: a sibling dimension is a declaration beside its siblings.
#:
#: Deliberately NOT here: a field restricting a tag to particular gate types (a
#: ``gate_types:`` list, say). Nothing decided it — KYO-46 asks for a tag registry and
#: says the change "is one more registry dimension, not a new subsystem" — and shipping
#: it would put a rule in every project's config file that no consumer asked for, which
#: is law 2's rot with a config file around it. The same reasoning, and the same
#: escape, as ``docs_root`` on SCOPE above: one entry in ``optional_fields`` and a
#: validator, on the day something reads it.
GATE_TAG = Dimension(
    singular="gate-tag",
    plural="gate-tags",
    what="what a gate is waiting on, classified so that gates can be queried by it",
)

#: KYO-129: which files in this project are Layer 3 documents.
#:
#: The dimension exists because file *discovery* was hardcoded to ``docs/**/*.md``, and
#: a consumer audit found the shape of failure that produces: a project whose operating
#: documents sit at the repository root got a clean exit and no output from
#: `kyo docs check`, because the enumerator could not see a single one of them. "The
#: enforcement is real; it just cannot see the files that went stale" — their words.
#: Which files a project calls Layer 3 documents is exactly the kind of per-project
#: claim KYO-46 turned into registries, so it is one, in the shape the other four share.
#:
#: ``globs`` and not ``paths`` (KYO-142). This field and ``SCOPE.paths`` were both called
#: ``paths`` and hold patterns in different dialects, which made a line copied between
#: the two files keep working and select a different set. The two names come from
#: :data:`ENUMERATE` and :data:`MATCH` rather than from this declaration; see
#: :class:`PathDialect` for why the dialect owns the spelling.
#:
#: It is REQUIRED rather than optional, which is the one place this dimension departs
#: from ``SCOPE``'s otherwise similar-looking field, and the reason is that the two
#: answer different questions. A scope is a name first — it is legitimately declared
#: with no paths at all, and derivation is a bonus. A document set is *nothing but* its
#: globs: an entry with a summary and none would declare a name that selects no file, so
#: "declared but empty" must not be a state the loader can produce. It is validated by
#: :func:`relative_globs` rather than :data:`_string_list` because these globs enumerate
#: the filesystem; see that function.
#:
#: Deliberately NOT here: an exclusion field (``exclude:``, a negative glob). Nothing
#: asked for one — KYO-129 is about documents that are invisible, not about documents
#: that are seen twice — and a project that wants a narrower set already has the whole
#: answer available, because declaring anything at all replaces the default outright
#: rather than adding to it. The same escape as ``SCOPE.docs_root``: one entry in
#: ``optional_fields`` and a validator, on the day something reads it.
#: ``paths_relative_to`` (KYO-200) is the one optional field, and it ships with its
#: consumer in the same commit (law 2): :func:`kyosei.docs_targets.resolve_target`, which
#: is the only thing that reads it. A document lives where its app lives — a consumer's
#: ``apps/server/NOTES.md`` names ``adapters/alpha/client.py``, meaning that path under
#: ``apps/server/src/app``, because the file and the code it governs share an
#: app root that is not the repository root. Without the field every such target fails
#: to exist, and the only remedy this tool could offer would be "move your documents",
#: which is a validator telling a project where to keep its files (KYO-70's argument
#: about worktrees, applied to documents).
#:
#: It is NOT a path dialect (:class:`PathDialect`): these are directories, not patterns,
#: and nothing globs them. It is validated by :func:`relative_bases` rather than
#: :func:`relative_globs` for exactly that reason, and the two must stay distinct
#: validators or :meth:`Dimension._check_path_dialects` refuses the dimension.
#: ``numbered`` (KYO-299) is the second optional field, and it ships with its consumers
#: in the same commit (law 2): :func:`kyosei.docs_numbered.numbered_documents`, the brief's
#: per-document fragment instruction, `kyo docs assemble`, and `kyo check-branch`'s
#: refusal of a numbered heading written by hand. It is NOT a path dialect either — its
#: ``fragments`` is a directory, like ``paths_relative_to``, and nothing globs it.
#: ``section_status`` (KYO-499) is the third, read by :func:`kyosei.docs_parse.document_set`
#: into :class:`kyosei.docs_parse.SectionStatusSet` and acted on by
#: :func:`kyosei.docs_check.check_docs` and ``kyo check-branch``: its presence turns on the
#: per-set section-status rules (:func:`section_status_block`).
#: ``ssot`` (KYO-552) is the fourth, read by :func:`kyosei.docs_parse.document_set` into
#: :class:`kyosei.docs_parse.SsotDeclaration` and acted on by :mod:`kyosei.docs_ssot`
#: through :func:`kyosei.docs_check.section_status_problems`, so ``kyo docs check`` and
#: ``kyo check-branch`` run the same rules. Its rules against ``numbered``,
#: ``section_status`` and ``globs`` are :func:`_document_entries_check`.
#: ``format`` and ``recx_manifest`` (KYO-600) are the fifth and sixth, read by
#: :func:`kyosei.docs_recx.declared_manifest` into the document set and consumed by
#: :func:`kyosei.docs_parse.read_document`: a ``recx-manifest`` set reads RECX.yaml's
#: entries as its sections. Their rules against ``globs`` and the markdown-only fields are
#: :func:`_recx_entries_check`.
DOCUMENT = Dimension(
    singular="document-set",
    plural="documents",
    what="which files in this project are Layer 3 documents, checked and queried as ones",
    required_fields={"globs": relative_globs},
    optional_fields={
        "paths_relative_to": relative_bases,
        "numbered": numbered_sections,
        "section_status": section_status_block,
        "ssot": ssot_block,
        "format": one_of(*DOCUMENT_FORMATS),
        "recx_manifest": recx_manifest_block,
    },
    entries_check=_document_entries_check,
)


# Where the sibling dimensions' `Dimension` values live, so a reader who found this
# one does not have to grep for the others:
#
#   scopes      SCOPE, above.
#   gate-tags   GATE_TAG, above.
#   documents   DOCUMENT, above.
#   statuses    kyosei.contracts.STATUS (KYO-101), declared beside the roles it binds
#               to rather than here. The reason is an import direction, not a
#               preference: a status entry's required `role` field is validated
#               against the *contract* vocabulary (contracts.STATUS_ROLES), so the
#               declaration must sit on the side of the import that knows what a role
#               is — this module stays importable by `contracts`, never the reverse.
#   kinds       kyosei.kinds.KIND (KYO-104), declared beside the `Kind` view it is
#               read into and the archetype vocabulary its `policy` rules branch on.
#               The reason is the same import direction as `statuses`: this module
#               must not know what an archetype is, so the dimension that does is
#               declared on the side of the import that does.
#
# What KYO-46's "four dimensions, one shape" actually requires is that all four are
# loaded, validated and refused by the code *in this module*, which they are: a sibling
# adds a `Dimension` value and no loader.


@dataclass(frozen=True)
class Entry:
    """One validated registry entry."""

    name: str
    summary: str
    #: The dimension's optional fields, present only when the entry declared them.
    #: Absence is not defaulted to a value here: "this scope declares no paths" and
    #: "this scope declares an empty path list" would then be the same fact, and one
    #: of them is refused by :func:`_string_list` precisely so they cannot be.
    fields: Mapping[str, object] = field(default_factory=dict)

    def get(self, name: str, default: object = None) -> object:
        return self.fields.get(name, default)


@dataclass(frozen=True)
class Registry:
    """Every entry declared in one project's registry file for one dimension."""

    dimension: Dimension
    path: Path
    #: Did the file exist? An optional registry that is absent is empty *and says so*,
    #: because the two states need different refusal messages: one tells you to add a
    #: name to a file, the other tells you the file is not there yet.
    exists: bool
    entries: Mapping[str, Entry]

    def __contains__(self, name: str) -> bool:
        return name in self.entries

    def __getitem__(self, name: str) -> Entry:
        return self.entries[name]

    def get(self, name: str) -> Entry | None:
        return self.entries.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self.entries)

    @property
    def declared_names(self) -> tuple[str, ...]:
        """The names **in the order the file declares them**.

        :attr:`names` is sorted, which is right for a refusal message listing what a
        project holds and wrong for a dimension whose file order carries meaning.
        KYO-101's ladder is the first such dimension: ``open → working → built →
        verified → done`` is an order a reader wrote down, and re-deriving it from a
        stored ``position:`` field would be a second copy of a fact the file already
        states (law 3). ``yaml.safe_load`` preserves mapping order and so does
        :func:`_build`, so this is that fact, read where it lives.
        """
        return tuple(self.entries)

    def require(self, name: str) -> Entry:
        """Look up a name, or refuse loudly naming the file and what it does hold.

        KYO-100.c1's message, in one place for all four dimensions. It is one raise on
        purpose: the alternative is the same sentence written once per write path
        (`kyo new`, `kyo promote`, `kyo spawn`, `kyo edit`), which is four copies to
        keep in step and four places for the list of declared names to be printed from
        a different source.
        """
        entry = self.entries.get(name)
        if entry is not None:
            return entry
        raise RegistryError(unknown_name_refusal(self, name))


def unknown_name_refusal(registry: Registry, name: str) -> str:
    """Why ``name`` is not a legal value for this dimension, and what would make it one.

    Split out of :meth:`Registry.require` so a caller that wants to *warn* rather than
    refuse — a viewer listing an unknown value, `kyo doctor` reporting a record whose
    stored scope has since left the registry — prints the same sentence rather than
    inventing a second wording of it (law 3).
    """
    dimension = registry.dimension
    here = shlex.quote(str(registry.path))
    keys = ", ".join(f"`{key}:`" for key in dimension.required_entry_keys)
    if not registry.exists:
        return (
            f"{dimension.singular} {name!r} is not declared: {registry.path} does not "
            f"exist, so this project declares no {dimension.plural} at all "
            f"({dimension.what}). The registry is the rule, so declaring the "
            f"{dimension.singular} is the way through, not a flag: create {here} with a "
            f"top-level `{dimension.plural}:` mapping, each entry carrying {keys}"
        )
    declared = ", ".join(registry.names) or "(none — the file declares an empty mapping)"
    return (
        f"{dimension.singular} {name!r} is not declared in {registry.path}; declared "
        f"{dimension.plural}: {declared}. The registry is the rule, so declaring the "
        f"{dimension.singular} is the way through, not a flag: add a `{name}:` entry "
        f"with {keys} to {here}"
    )


def missing_required_remedy(path: Path) -> str:
    """What actually restores a deleted registry file a project cannot run without
    (KYO-82; generalised from ``kinds.py`` to every required dimension by KYO-104).

    The honest answer is that no ``kyo`` command does, and the message says so. This
    is the sixth instance of the defect class KYO-73 was filed against — a message
    naming a remedy that does not apply to the state that produced it — and it was a
    word-for-word copy of the fifth: ``run `kyo init` or restore it``, the sentence
    ``store.py`` raised for a missing database until KYO-73. The sibling was never
    touched because KYO-73 was filed from the database symptom and nobody looked for
    the other copy.

    KYO-73's was the easy shape: a remedy existed (``kyo rebuild``) and was merely
    unnamed. Here there is nothing to name. A registry file is committed project
    configuration, not an export artifact, so ``kyo rebuild`` — which reconstructs the
    database from ``kyosei/export/`` — does not write it and has no source to write it
    from. Scaffolding a store is the only code path that ever creates one, and it
    refuses a directory that already holds a store, which is exactly the state this
    error is raised in. So the recovery is version control, or the packaged default,
    and the message says which rather than reaching for the nearest command that
    exists.

    ``kyo rebuild`` is named here *to be ruled out*. It is the guess a reader makes
    next, it is wrong, and one sentence saves them the round trip — but it is a
    mention and not an offer, and it is classified as one in KYO-77's table.

    KYO-84 re-examined that sentence, because part of why it was here was not the
    reader. KYO-77's check defined its surface as messages that can carry a backticked
    ``kyo`` command, so a message naming no such command fell off the surface entirely
    and nothing checked it at all — while KYO-82.c3 required this site to stay covered.
    Naming ``kyo rebuild`` in order to rule it out was the only way to satisfy both. A
    check whose coverage definition gives a message a reason to name a command is
    shaping what it measures, which is the defect KYO-84 was filed about and fixed: the
    surface is now every site that names a remedy of any kind, and this one is on it for
    the ``git checkout`` and the ``cp`` below whether or not any ``kyo`` command appears.

    THE SENTENCE IS KEPT, on the merits it always had and now only has: it is the
    reader's next guess, it is wrong, and being told so with the reason costs one clause
    and saves a round trip. What changed is that dropping it would now cost nothing —
    ``test_the_kinds_yml_message_stays_on_the_surface_without_naming_kyo_rebuild``
    strips every ``kyo`` command out of the live message and shows the site still on the
    surface — so it stays because it is useful, not because a table needed it.

    KYO-104 moved it here, from ``kinds.py``, unchanged in substance: the file it names
    is now the caller's rather than ``kinds.yml`` by construction, and every sentence
    above is true of any registry a project is required to declare. Nothing about the
    reasoning was ever specific to kinds — what was specific was which module happened
    to own a loader.
    """
    # Imported inside the function: `scaffold` reaches `store` and `export`, which
    # reach this module. `defaults_dir` is the single locator for the packaged
    # assets (law 3) — this message must not carry a second copy of that path logic.
    from .scaffold import defaults_dir

    packaged = defaults_dir()
    default = None if packaged is None else packaged / path.name
    # Quoted so the lines below are runnable as printed even where a path has a space
    # in it; `shlex.quote` adds nothing to the ordinary case.
    here = shlex.quote(str(path))
    from_default = (
        f"copy the packaged default: cp {shlex.quote(str(default))} {here} — then "
        f"re-add anything this project declared beyond the shipped set"
        if default is not None and default.is_file()
        # Generalised with the function (KYO-104): before it, "no packaged default" and
        # "no defaults/ directory at all" were the same sentence because there was one
        # required registry and it always shipped one. A dimension that ships none is a
        # real state now, and telling a reader to look for a file this build never had
        # would be the KYO-73 defect class in miniature.
        else f"this build ships no packaged {path.name} to copy one from, so version "
        "control is the only recovery"
    )
    return (
        f"no `kyo` command restores it: {path.name} is committed project configuration, "
        "not something kyosei generates, and `kyo rebuild` reconstructs the database "
        f"from the export and never writes this file. Restore it from version control "
        f"(git checkout -- {here}), where the store is tracked; otherwise {from_default}"
    )


def _declares_nothing(path: Path, dimension: Dimension) -> str:
    """A required registry that parses and names no entry.

    Deliberately spells no remedy. What a project's ``kinds.yml`` ought to contain is
    the project's decision, and the only honest instruction — "declare the entries this
    project needs" — is one the reader already knew. :func:`missing_required_remedy`
    names recoveries because there a *specific* file that used to be here is gone.
    """
    return (
        f"{path} declares no {dimension.plural}, and {dimension.plural} are required "
        f"({dimension.what})"
    )


class _DuplicateKey(Exception):
    """Raised inside :class:`_DuplicateKeyRefusingLoader`; turned into a named refusal
    by :func:`load_yaml_refusing_duplicates`, which knows the file."""

    def __init__(self, key: object, first_line: int, line: int) -> None:
        super().__init__(key)
        self.key, self.first_line, self.line = key, first_line, line


class _DuplicateKeyRefusingLoader(yaml.SafeLoader):
    """``yaml.SafeLoader``, except a mapping that repeats a key is refused.

    KYO-413.c6: plain ``safe_load`` keeps the LAST of two equal keys, so a second
    ``bug:`` in ``kinds.yml`` or a second ``done:`` in ``statuses.yml`` replaced the
    first with exit 0 — the one shape of damage the registry's collect-every-problem
    refusal never saw, because by the time :func:`_build` ran the first entry no longer
    existed. Merge keys (``<<:``) are left to the base class: an explicit key that
    overrides a merged one is what a merge is for, not a duplicate.
    """

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        if isinstance(node, yaml.MappingNode):
            seen: dict[object, int] = {}
            for key_node, _value in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = self.construct_object(key_node, deep=deep)
                try:
                    first = seen.get(key)
                except TypeError:  # unhashable: the base class refuses it by name
                    continue
                line = key_node.start_mark.line + 1
                if first is not None:
                    raise _DuplicateKey(key, first, line)
                seen[key] = line
        return super().construct_mapping(node, deep=deep)


def load_yaml_refusing_duplicates(text: str, path: Path) -> object:
    """``yaml.safe_load(text)``, refusing a repeated mapping key with a
    :class:`RegistryError` (a :class:`KyoseiError`, so ``config.yml``'s callers see the
    same class they always caught). One class rather than a caller-chosen one so the
    raise stays a named site ``tests/test_kyo5_error_remedies.py`` can see.

    The refusal names the file, the key, both lines and the remedy. A YAML syntax
    error still propagates as :class:`yaml.YAMLError`, so each caller keeps its own
    "is not valid YAML" message.
    """
    try:
        return yaml.load(text, Loader=_DuplicateKeyRefusingLoader)  # noqa: S506
    except _DuplicateKey as dup:
        raise RegistryError(
            f"{path} line {dup.line} repeats the key {dup.key!r} (first at line "
            f"{dup.first_line}) in the same mapping; YAML keeps only the last of the "
            "two, so the first would be discarded without a word — delete one of them, "
            "or rename it if both were meant"
        ) from None


def load(kyosei_dir: Path, dimension: Dimension) -> Registry:
    """Load and validate one project's registry for ``dimension``.

    An absent file is an empty registry for an optional dimension and a
    :class:`RegistryError` for a required one — see :attr:`Dimension.required`.
    """
    path = dimension.path_in(kyosei_dir)
    if not path.is_file():
        if dimension.required:
            raise RegistryError(f"{path} does not exist; {missing_required_remedy(path)}")
        return Registry(dimension=dimension, path=path, exists=False, entries={})
    try:
        raw = load_yaml_refusing_duplicates(path.read_text(encoding="utf-8"), path)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path} is not valid YAML: {exc}") from exc
    return _build(path, dimension, raw)


def _copied_dialect_hint(unknown: Sequence[str], allowed: Collection[str]) -> str:
    """The sentence a line copied between two registries earns (KYO-142), or ``""``.

    Fires on exactly one situation: an entry carries the field name the *other*
    dialect owns, in a dimension that accepts this one. That is the copied line — the
    single most likely mistake, because the two files come from the same family and a
    consumer reads one after the other — and until KYO-142 it was not a mistake at all,
    because both fields were called ``paths`` and the pattern simply meant something
    else on arrival.

    The unknown-key refusal already stops it; what this adds is the reason, which is
    KYO-142's requirement that the match-versus-enumerate distinction reach a reader *where
    they meet it*. A reader who trips this is meeting it at the only moment they are
    certainly thinking about it. It cannot fire on correct config, which is what keeps
    it clear of KYO-33: there is no pattern it inspects and no judgment it makes.
    """
    for key in unknown:
        wrong = PATH_DIALECTS.get(key)
        mine = other_dialect(key)
        if wrong is None or mine is None or mine.field_name not in allowed:
            continue
        return (
            f". {wrong.describe()}, and this file's {mine.describe()} — so the same "
            f"pattern selects a different set in each, which is why they are spelled "
            f"differently (KYO-142). If you meant this file's dialect, rename the key "
            f"to `{mine.field_name}:` and check the pattern still says what you meant"
        )
    return ""


def _build(path: Path, dimension: Dimension, raw: object) -> Registry:
    plural = dimension.plural
    if raw is None or (isinstance(raw, dict) and raw.get(plural, "missing") is None):
        # `scopes:` with nothing under it, or a file of comments only. A declared-empty
        # registry is a real state — a project that has the file and has not yet named
        # anything — and it is not the same state as a file that is not there, which is
        # why `exists` is True here.
        if dimension.required:
            raise RegistryError(_declares_nothing(path, dimension))
        return Registry(dimension=dimension, path=path, exists=True, entries={})
    if not isinstance(raw, dict) or not isinstance(raw.get(plural), dict):
        raise RegistryError(f"{path} must contain a top-level {plural!r} mapping")
    unknown_top = sorted(set(raw) - {plural})
    if unknown_top:
        raise RegistryError(
            f"{path} carries unknown top-level key(s) {', '.join(unknown_top)}; the only "
            f"one this file may hold is {plural!r}"
        )

    problems: list[str] = []
    entries: dict[str, Entry] = {}
    allowed = {SUMMARY_FIELD, *dimension.required_fields, *dimension.optional_fields}
    for name, body in raw[plural].items():
        label = f"{dimension.singular} {name!r}"
        if not isinstance(name, str) or not NAME_RE.match(name):
            problems.append(f"{label}: name must be a lowercase slug")
        if not isinstance(body, dict):
            problems.append(f"{label}: definition must be a mapping")
            continue
        unknown = sorted(set(body) - allowed)
        if unknown:
            problems.append(
                f"{label}: unknown key(s) {', '.join(unknown)}; a {dimension.singular} "
                f"entry may carry {', '.join(sorted(allowed))}"
                + _copied_dialect_hint(unknown, allowed)
            )
        summary = body.get(SUMMARY_FIELD)
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{label}: {SUMMARY_FIELD} must be a non-empty string")
        declared: dict[str, object] = {}
        complete = True
        for key, validate in dimension.required_fields.items():
            if key not in body:
                problems.append(f"{label}: {key} is required")
                complete = False
                continue
            trouble = validate(body[key])
            if trouble:
                problems.append(f"{label}: {key} {trouble}")
                complete = False
                continue
            declared[key] = body[key]
        for key, validate in dimension.optional_fields.items():
            if key not in body:
                continue
            trouble = validate(body[key])
            if trouble:
                problems.append(f"{label}: {key} {trouble}")
                continue
            declared[key] = body[key]
        if complete and isinstance(summary, str) and summary.strip() and isinstance(name, str):
            entries[name] = Entry(name=name, summary=summary, fields=declared)

    if dimension.entries_check is not None:
        # After the field pass and before the raise, so a dimension's own rules and the
        # shared shape's arrive in ONE message. See `Dimension.entries_check`.
        problems += dimension.entries_check(entries, raw[plural])

    if problems:
        detail = "\n".join(f"  - {p}" for p in sorted(problems))
        raise RegistryError(f"{path} failed validation:\n{detail}")
    if dimension.required and not entries:
        raise RegistryError(_declares_nothing(path, dimension))
    return Registry(dimension=dimension, path=path, exists=True, entries=entries)


# ------------------------------------------------------------ the gate-tag dimension


def load_gate_tag_registry(kyosei_dir: Path) -> Registry:
    """This project's ``kyosei/gate-tags.yml``, or an empty registry if it has none.

    Absence is not an error, for the same two reasons ``scopes.yml``'s is not: every
    store written before this release has no such file, and a project may legitimately
    tag no gates at all — a gate's ``--do`` free text still says what it is waiting on,
    and "no tags" is the state every gate in every existing store is in.
    """
    return load(kyosei_dir, GATE_TAG)


#: The gate-tag flag, spelled once. ``cmd_gate`` refuses with it, ``cli`` builds the
#: parser around it, and the refusals name it — three places that must agree on one
#: string (law 3).
GATE_TAG_FLAG = "--tag"

#: The `kyo list` flag that selects records by an open gate's tag. Named apart from
#: :data:`GATE_TAG_FLAG` because it is a different question asked of the same registry
#: — ``--tag`` writes one onto a gate, ``--gate-tag`` searches for the gates carrying
#: it — and `kyo list` already has a ``--scope`` filter it must not be confused with.
GATE_TAG_FILTER_FLAG = "--gate-tag"


def normalized_tags(names: list[str] | None) -> list[str]:
    """The tags to store for one gate: declaration order, no repeats.

    A repeated ``--tag`` is deduplicated rather than refused, because the two readings
    of ``--tag ui --tag ui`` are "a typo" and "a typo", and neither is a fact the store
    should hold twice. What makes the collapse honest rather than silent is that
    `kyo gate` echoes the tags it *stored* (law 5), so a caller who typed one twice
    sees one back.
    """
    return list(dict.fromkeys(names or []))


# ------------------------------------------------------------ the document dimension


#: What a project that has declared no document sets is scanned for (KYO-129.c3).
#:
#: Root-level markdown FIRST, and in the code rather than in the packaged
#: ``documents.yml``, because the criterion this satisfies is "the fix requires no
#: consumer configuration to take effect". Every store written before this release has
#: no ``documents.yml`` at all, and a default that lived only in the shipped file would
#: reach exactly the projects that do not have the problem — the ones initialised after
#: it. The packaged file therefore ships ``documents: {}`` and documents this tuple; it
#: does not restate it, so the two cannot come apart (law 3).
#:
#: ``*.md`` and not ``**/*.md``: the second would sweep a consumer's ``node_modules``,
#: their vendored dependencies and their ``ideas/`` pool into the governed set, and a
#: validator that fails the build on somebody else's changelog is worse than one that
#: cannot see your README. The reporting consumer's two stale files were ``README.md``
#: and one operating document beside it, which this pair covers with nothing declared.
DEFAULT_DOCUMENT_GLOBS: tuple[str, ...] = ("*.md", "docs/**/*.md")


def load_document_registry(kyosei_dir: Path) -> Registry:
    """This project's ``kyosei/documents.yml``, or an empty registry if it has none.

    Absence is not an error, for ``scopes.yml``'s reasons plus one of its own: an empty
    registry here is not an empty *answer*, because :func:`declared_document_globs`
    falls back to :data:`DEFAULT_DOCUMENT_GLOBS`. Nothing a project has to write for
    `kyo docs check` to start seeing its root-level documents.
    """
    return load(kyosei_dir, DOCUMENT)


def declared_document_globs(registry: Registry) -> tuple[str, ...] | None:
    """The globs this project declared, or ``None`` if it declared none.

    ``None`` rather than :data:`DEFAULT_DOCUMENT_GLOBS`, so the caller can still tell
    the two apart — "this project chose these paths" and "this project chose nothing and
    got the shipped pair" are different facts, and `kyo docs check` prints which one it
    acted on. Collapsing them here would be the absent-flag shape twice over: the state
    KYO-129 exists to fix is a report that cannot be distinguished from a real answer.

    Declaration order across entries, then within each entry, with repeats dropped: a
    project may legitimately name the same glob in two sets, and scanning it twice would
    make a file's own duplicate a query result.
    """
    globs = [
        glob
        for name in registry.declared_names
        for glob in (registry[name].get(ENUMERATE.field_name) or [])
        if isinstance(glob, str)
    ]
    if not globs:
        return None
    return tuple(dict.fromkeys(globs))


def declared_document_bases(registry: Registry) -> tuple[str, ...]:
    """The directories ``governs:`` targets resolve under, in the order to try them.

    KYO-200. Empty when no set declared ``paths_relative_to``, which is the state every
    project that has never heard of the field is in, and the state
    :func:`kyosei.docs_targets.resolve_target` reads as *the project root and nothing
    else* — exactly the behaviour that shipped before this field existed. The tuple is
    empty rather than ``("",)`` so that "declared nothing" and "declared the root" stay
    two facts and ``kyo docs check``'s failure text can name which one it acted on.

    Declaration order across entries, then within each entry, repeats dropped — the same
    rule :func:`declared_document_globs` uses, and here the order is load-bearing rather
    than cosmetic: the resolver takes the **first** base under which a target exists, so
    a project that lists its narrowest app root first gets its own answer first.

    **Collapsed across every set, as the globs already are.** A base declared on one set
    is tried for a target in another, which is not what a reader of one entry would
    predict. It is honest about the shape it sits in — :func:`kyosei.docs_parse.
    document_set` builds ONE :class:`~kyosei.docs_parse.DocumentSet` from the whole
    file, so per-set resolution has nowhere to live until that does — and benign in
    practice, because a base only ever *adds* a place a target may be found and the
    first hit wins. A project that needs two sets resolved differently is the record
    that would make this per-set; nothing has asked yet, and guessing at it would be a
    field with no consumer (law 2).
    """
    bases: list[str] = []
    for name in registry.declared_names:
        declared = registry[name].get("paths_relative_to")
        if isinstance(declared, str):
            declared = [declared]
        if not isinstance(declared, list):
            continue
        bases += [base.strip() for base in declared if isinstance(base, str) and base.strip()]
    return tuple(dict.fromkeys(bases))


# --------------------------------------------------------------- the scope dimension


def load_scope_registry(kyosei_dir: Path) -> Registry:
    """This project's ``kyosei/scopes.yml``, or an empty registry if it has none."""
    return load(kyosei_dir, SCOPE)


#: What every surface calls a value that came out of :func:`suggest_from_paths` rather
#: than out of the ``records.scope`` column. KYO-100.c3 requires the label; naming it
#: once here is what keeps the four write paths from each inventing a word for it.
DERIVED_LABEL = "derived (suggestion only, nothing stored)"


def suggest_from_paths(registry: Registry, paths: list[str]) -> list[tuple[str, list[str]]]:
    """Which scopes ``paths`` fall under, by the registry's own ``paths`` globs.

    **A suggester and a validator, never the source** — KYO-46 verbatim, and the reason
    this returns a list rather than a scope. Two scopes may match; a record may have no
    touches at all (and a doc has none by construction), so derivation is silent for
    exactly the records that need a scope most. Nothing in this module writes to the
    store, and every caller labels what it prints with :data:`DERIVED_LABEL`.

    Matching is ``fnmatch`` over the root-relative path, so ``*`` crosses ``/`` and
    ``apps/desktop/*`` matches ``apps/desktop/main/index.ts``. That is stated in the
    packaged default's own comments, because a glob dialect a reader has to discover by
    experiment is a config field that will be written wrong.
    """
    matches: list[tuple[str, list[str]]] = []
    for name in registry.names:
        globs = registry[name].get(MATCH.field_name) or []
        hit = sorted({path for path in paths for glob in globs if fnmatch.fnmatchcase(path, glob)})
        if hit:
            matches.append((name, hit))
    return matches


def suggestion_lines(
    registry: Registry, *, declared: str | None, paths: list[str]
) -> tuple[list[str], dict]:
    """The human lines and the JSON block a write path prints about scope derivation.

    KYO-100.c3, in one place for all four write paths. Three cases, and the third is
    the one the criterion is about:

    * nothing to say — no touches, no registry paths, or the suggestion agrees with what
      was declared;
    * no scope declared and the touches match one — offered, labeled derived;
    * a scope declared and the touches match a *different* one — a mismatch warning,
      labeled derived, which changes nothing: the declared value is what is stored.

    Returns ``([], {...})`` rather than ``([], None)`` in the quiet case so a JSON
    consumer can always read the block and see that derivation ran and found nothing —
    an absent key would read as "this build does not derive", which is a different fact.
    """
    matched = suggest_from_paths(registry, paths)
    data = {
        "basis": "touches",
        "stored": declared,
        "suggested": [{"scope": name, "paths": hit} for name, hit in matched],
        "label": DERIVED_LABEL,
    }
    if not matched:
        return [], data
    if declared is None:
        offered = ", ".join(
            f"{name!r} (matches {len(hit)} of {len(paths)} touches)" for name, hit in matched
        )
        return (
            [
                f"  {DERIVED_LABEL}: this record's touches fall under {offered} per "
                f"{registry.path}. No scope was declared and none was stored; "
                f"--scope <name> is what declares one."
            ],
            data,
        )
    others = [(name, hit) for name, hit in matched if name != declared]
    if not others:
        return [], data
    mismatch = ", ".join(f"{name!r} ({len(hit)} of {len(paths)} touches)" for name, hit in others)
    return (
        [
            f"  {DERIVED_LABEL}: scope {declared!r} is stored, but this record's touches "
            f"also fall under {mismatch} per {registry.path}. "
            + explanations.say("scope-derivation-never-overrides")
        ],
        data,
    )
