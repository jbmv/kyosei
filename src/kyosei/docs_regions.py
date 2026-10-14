"""Where a symbol's region lies in a file, per language, and whether a branch's diff
changed it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import sweep
from .docs_parse import HEADING_RE, _fenced, split_target
from .prose import contains_phrase

# ------------------------------------------------------------------ KYO-153: the symbol
#
# A `governs: path#symbol` target raises only when the symbol's own region changed, not
# on any change to the file (docs/design/docs.md#kyo-153 for what file level cost).
#
# **Decision 9 is the constraint on the cure, and it is the whole design.** An
# instrument too weak to SETTLE a question must not be re-admitted as a VETO: a raise
# produces a visible question a controller dismisses in one command, a suppression
# produces a silent false negative nobody ever sees. So the instrument below may suppress
# on exactly one outcome — it positively located the symbol's region in the file on BOTH
# sides of the diff and found no changed line inside either. Every other outcome —
# unsupported file type, unparseable revision, a symbol that resolves as text but names
# no region, a file added or deleted outright — is "cannot tell", and cannot tell raises.
# That asymmetry is why this is safe to put in front of a blocking gate, and why grepping
# the symbol's name in the diff hunks is not an acceptable cheaper instrument: a line
# added inside a function need not mention the function's name
# (docs/design/docs.md#kyo-153).

#: The brace languages :func:`_declaration_regions` reads (KYO-352). Textual and
#: indentation-based rather than parsed: there is no TypeScript parser in the standard
#: library, a dependency would have to be installed in every consumer's environment to
#: run a docs check, and the region a document needs is the declaration's block rather
#: than a syntax tree. What makes an approximation safe in front of a blocking gate is
#: unchanged from KYO-153: a declaration this cannot place is cannot-tell and raises.
#:
#: Exactly the four the consumer asked for, and not every brace language the finder
#: would technically read (`.go`, `.rs`, `.java`, `.c`): a suffix here is a promise that
#: this project has measured the finder against real files of that kind, and the four
#: below are the ones KYO-352's tests cover. Adding another is one entry and its tests.
BRACE_REGION_SUFFIXES: tuple[str, ...] = (".ts", ".tsx", ".js", ".mjs")

#: The two spellings of YAML :func:`_yaml_regions` reads (KYO-363). Both, because which
#: one a project writes is a coin flip and a target that narrows in `config.yml` and not
#: in `config.yaml` would be the kind of difference nobody can see in the declaration.
YAML_REGION_SUFFIXES: tuple[str, ...] = (".yml", ".yaml")

#: Suffixes :func:`symbol_regions` can locate a region in. A file type absent here is
#: "cannot tell" by construction, not by a failed lookup — a project whose documents
#: govern Go or Swift keeps the file-level behaviour it had before KYO-153 rather
#: than getting a silently weaker check.
#:
#: Read as a question through :func:`narrows_by_symbol` by everything that *advises* a
#: consumer to narrow (KYO-351), so this tuple is the single source for what the docs,
#: the nudge and `kyo docs check`'s notice all say.
REGION_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".md",
    *BRACE_REGION_SUFFIXES,
    ".json",
    *YAML_REGION_SUFFIXES,
    ".css",
)


def narrows_by_symbol(path: str) -> bool:
    """Would a ``#symbol`` on ``path`` narrow anything? (KYO-351)

    :data:`REGION_SUFFIXES` asked as a question rather than read as a tuple, because
    every surface that *advises* a consumer to narrow has to ask it and none of them
    should re-spell the suffix test. The suffix is taken from the path as written: a
    base-relative target and its root-relative resolution end in the same characters,
    and the answer must not depend on which of the two a caller happens to hold.

    Offering the narrower ``governs: path#symbol`` form for a suffix with no region
    finder is advice to make an edit that changes nothing
    (docs/design/docs.md#kyo-351). A declaration this cannot narrow is still *accepted* — it
    resolves, it raises at file level, nothing about it is broken — so this is the
    difference between an offer and a
    lie, not the difference between valid and invalid.
    """
    return Path(path).suffix.lower() in REGION_SUFFIXES


def region_suffixes_says() -> str:
    """The suffixes a ``#symbol`` narrows for, spelled from :data:`REGION_SUFFIXES`.

    One spelling, derived rather than repeated, so the nudge, the ``kyo docs check``
    notice and (through its test) docs/reference/topics/docs-governance.md cannot drift
    from the tuple the instrument
    actually consults — law 3 (docs/design/docs.md#kyo-351).
    """
    return ", ".join(f"`{suffix}`" for suffix in REGION_SUFFIXES)


def narrowable_targets(targets: Sequence[str]) -> tuple[str, ...]:
    """Which of these written targets a ``#symbol`` would actually narrow (KYO-351.c1).

    Order and spelling kept, deduplication left to the caller: this is a filter over a
    list somebody else built, and re-ordering it would change the sentence it is about
    to be rendered into. A target with no path half (a bare ``module.sub.symbol``) is
    not one this can judge and is dropped — it is never a whole-file target either, so
    no offer is made about it.
    """
    found: list[str] = []
    for raw in targets:
        path, _ = split_target(raw)
        if path and narrows_by_symbol(path):
            found.append(raw)
    return tuple(found)


def _python_regions(text: str, symbol: str) -> tuple[tuple[int, int], ...] | None:
    """Line spans of ``symbol`` in Python source, or ``None`` if the source will not parse.

    Matches a definition by its bare name (``build_parser``) or by its dotted qualified
    name (``Store.allocate``), so a document may narrow to a method without ambiguity and
    may also name one that happens to be unique. A decorated definition's span starts at
    its first decorator: a change to ``@lru_cache`` above a function is a change to that
    function, and starting at the ``def`` line would suppress it.

    **Assignments** (KYO-396) are located in two places and by two different rules:

    * at module level, by bare name, every one of them — ``contracts.py#GUARDED_BRANCH_DIRS``
      names a constant, and a name rebound at module level is the same name twice;
    * in a class body — a dataclass field (``total_cents: int``), a plain class
      attribute (``kind = "task"``), a nested class's field — by the dotted qualified name
      ``OrderSnapshot.total_cents`` / ``Outer.Inner.field`` always, and by the bare
      name ``total_cents`` **only when that is the file's only definition of the name**:
      no def, class or module-level assignment carries it and no second qualified name
      does either. A bare ``name`` that two classes both declare cannot say which one a
      document meant, and locating both would narrow to lines the document never named
      while locating neither keeps the declaration raising at file level, which is the
      side decision 9 puts the error on. The same bare name that IS unique is located,
      because refusing it would make the narrowest spelling of the one field in a file
      raise like the whole file — KYO-396's complaint, one step removed.

    An assignment inside a function body is a local and is never located: the enclosing
    ``def`` is the region, and naming its locals is not a governs: target anyone writes.
    """
    import ast

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        # A revision that does not parse is a revision this instrument cannot read. It is
        # not evidence that the symbol is unchanged.
        return None
    found: list[tuple[int, int]] = []
    #: Class-body assignments whose BARE name is the symbol, keyed by qualified name, held
    #: back until the whole file is read — whether they count depends on what else does.
    fields_by_bare: dict[str, list[tuple[int, int]]] = {}
    other_bare = False

    def assigned(node: ast.AST) -> list[str]:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        return [target.id for target in targets if isinstance(target, ast.Name)]

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        nonlocal other_bare
        for child in ast.iter_child_nodes(node):
            if in_class and isinstance(child, ast.Assign | ast.AnnAssign):
                span = (child.lineno, child.end_lineno or child.lineno)
                for field in assigned(child):
                    if symbol == f"{prefix}.{field}":
                        found.append(span)
                    elif symbol == field:
                        fields_by_bare.setdefault(f"{prefix}.{field}", []).append(span)
            name = getattr(child, "name", None)
            if name is None or not isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                continue
            qualified = f"{prefix}.{name}" if prefix else name
            if symbol in (name, qualified):
                other_bare = other_bare or symbol == name
                start = min(
                    [child.lineno, *(d.lineno for d in child.decorator_list)]
                )
                found.append((start, child.end_lineno or child.lineno))
            visit(child, qualified, isinstance(child, ast.ClassDef))

    visit(tree, "", False)
    # Module-level names too: a `governs: contracts.py#GUARDED_BRANCH_DIRS` target names a
    # constant, which is the shape half this project's own governed symbols would take.
    for node in tree.body:
        if symbol in assigned(node):
            other_bare = True
            found.append((node.lineno, node.end_lineno or node.lineno))
    if len(fields_by_bare) == 1 and not other_bare:
        # KYO-396.c2: the bare name of a class-body assignment, located only when it is
        # the one definition of that name this file has.
        [spans] = fields_by_bare.values()
        found.extend(spans)
    return tuple(sorted(set(found)))


def _markdown_regions(text: str, symbol: str) -> tuple[tuple[int, int], ...] | None:
    """Line spans of the section headed ``symbol``, heading line through the line before
    the next heading at the same or a higher level.

    Heading text is matched with :func:`kyosei.prose.contains_phrase` — the same
    wrap-tolerant whole-word rule :func:`_resolve_target` validated the target with, so a
    target that resolved cannot fail to locate here for a reason the author could not
    have seen. Never returns ``None``: markdown that "will not parse" is not a thing, so
    an unmatched symbol is an empty tuple (this instrument ran and found no region),
    which the caller still treats as cannot-tell.
    """
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    for number, (line, inside_fence) in enumerate(_fenced(lines), start=1):
        # KYO-412: the same heading rule :func:`_sections` splits on, and only
        # that — an unindented ``#`` run followed by whitespace. The earlier
        # lstrip-and-startswith test took a ``    # create the venv`` line inside an
        # indented code block, or a prose ``#1 priority``, for a heading, ended the
        # governed section there, and so called a later edit in the real section
        # clean: a suppressed obligation at a blocking gate. One rule for both
        # functions is what stops them disagreeing about where a section ends.
        match = None if inside_fence else HEADING_RE.match(line)
        if match is None:
            continue
        headings.append((number, len(match.group(1)), match.group(2).strip()))
    found: list[tuple[int, int]] = []
    for index, (number, level, heading) in enumerate(headings):
        if not contains_phrase(heading, symbol, whole_word=True):
            continue
        end = len(lines)
        for later_number, later_level, _ in headings[index + 1 :]:
            if later_level <= level:
                end = later_number - 1
                break
        found.append((number, max(number, end)))
    return tuple(found)


#: A line that is only a comment, above a declaration: the brace-language analogue of the
#: decorator :func:`_python_regions` deliberately starts a region at. A JSDoc block
#: describing ``showLeft`` is part of ``showLeft``, and a region starting at the ``export``
#: line would call an edit to it clean.
_COMMENT_LINE_RE = re.compile(r"^\s*(//|/\*|\*|#)")


def _declares(line: str, name: str) -> bool:
    """Does ``line`` declare ``name``, by the same reading :mod:`kyosei.sweep` uses?

    :data:`kyosei.sweep._NAME_RES` is the project's one notion of "this line names the
    thing it defines" — the patterns behind ``sweep.enclosing_symbol``, whose whole
    contract (KYO-300.c2) is that a symbol it suggests is one a region finder can then
    place. Asking them here rather than writing a fifth regex is that contract kept from
    the other end, and it is why a suggestion `kyo touch-drift` makes on a `.ts` file is
    a target this gate can narrow.

    Deliberately permissive in the raising direction: the second of those patterns
    matches any line beginning ``name(``, ``name:`` or ``name =``, so a call statement at
    the top of a line counts as a declaration and widens the region. Over-raising is the
    side decision 9 puts the error on.
    """
    for pattern in sweep._NAME_RES:
        found = pattern.search(line)
        if found is not None and found.group(1) == name:
            return True
    return False


def _declaration_regions(text: str, symbol: str) -> tuple[tuple[int, int], ...]:
    """Line spans of ``symbol``'s declarations in a brace language (KYO-352).

    Region by next declaration rather than by brace matching
    (docs/design/docs.md#kyo-352), and the reason is which way each fails. Brace matching
    has to know that a brace inside a string, a template literal, a regex or a comment is
    not a brace; every one of those it gets wrong ends the region early
    and **suppresses** an obligation, silently, at a gate that blocks. The block's extent
    by indentation cannot end early for a reason a reader cannot see: it ends at the next
    line indented no further than the declaration, which in any formatted file is the next
    declaration, and in an unformatted or minified one is the end of the file — wider,
    which raises.

    Three shapes are covered by one rule rather than three, because
    :data:`kyosei.sweep._NAME_RES` already reads all of them: a top-level function
    (``export function showLeft() {``), a class method (``  showLeft(): void {``, or
    behind its modifiers, ``  private async showLeft(): void {`` — KYO-358) and a
    top-level const (``export const DEFAULT_WIDTH = 240;``). A dotted ``Class.method``
    target is matched on its last segment — the enclosing class is not checked, so a
    same-named method elsewhere in the file widens the region rather than narrowing the
    wrong one.

    Never ``None``: a textual instrument that ran and found no declaration returns ``()``,
    which :func:`symbol_region_changed` reads as cannot-tell and raises on, exactly as it
    does for a markdown heading that matched nothing. ``()`` is also what a file whose
    braces do not close gets — see the veto below, which is the only use brace counting
    is put to here.
    """
    name = symbol.rsplit(".", 1)[-1].strip()
    if not name:
        return ()
    lines = text.splitlines()
    found: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        # A substring test first: `_declares` requires the declared name to BE `name`,
        # so a line without it cannot match, and a bare-symbol target asks this of
        # every file it resolved to (KYO-397).
        if name not in line or not _declares(line, name):
            continue
        start = index
        while start > 0 and _COMMENT_LINE_RE.match(lines[start - 1]):
            start -= 1
        end = _declaration_end(lines, index)
        # KYO-358: the LAST member of a class has no sibling to end its block, so the
        # block runs on through the class's own closing brace and the veto below threw
        # the whole symbol away. A closing line shallower than the declaration closes
        # something that encloses it, never the declaration itself, so it is not part of
        # the region; the veto still judges what is left.
        indent = sweep._indent_width(line)
        inner = end
        while inner > index + 1 and (
            not lines[inner - 1].strip()
            or (
                sweep._CLOSER_RE.match(lines[inner - 1])
                and sweep._indent_width(lines[inner - 1]) < indent
            )
        ):
            if lines[inner - 1].strip():
                end = inner - 1
            inner -= 1
        if not _brace_balanced(lines[start:end]):
            # The braces say this region is not a whole block, so the indentation that
            # produced it is not describing this file's structure: truncated source, a
            # file mid-edit, a brace inside a string or a template literal. Counting
            # braces can only VETO a region here and never define one, which is what
            # makes an instrument this crude safe — every way it is wrong adds a raise.
            # Every region of THIS symbol goes, not just the unbalanced one: keeping a
            # balanced sibling would let a region found by an assumption already shown
            # false in this same file suppress an obligation. It is not a claim about
            # the whole file -- another symbol whose own block closes is still placed,
            # because "this file is unreadable" is a bigger claim than the evidence.
            return ()
        found.append((start + 1, end))
    return tuple(sorted(set(found)))


#: A line that opens with closing punctuation continues the construct above it, whatever
#: follows on the line: ``): void {`` ends a multi-line signature and opens the body,
#: ``} else {`` and ``}).then(`` carry a block on. Never where a declaration's block ends.
_CONTINUES_RE = re.compile(r"^\s*[)\]}]")


def _declaration_end(lines: Sequence[str], index: int) -> int:
    """Where the declaration on 0-based ``index`` ends, as a 1-based inclusive line.

    :func:`kyosei.sweep.region_end`, minus the half of it that is for prose (KYO-360).
    That function first takes the whole unbroken run of non-blank lines below the anchor
    and only then looks for a terminator, because a symbol found on one line of a
    hard-wrapped paragraph must claim the paragraph. On a declaration that claims the
    siblings — an object-literal property runs on to the end of the literal, the run is
    not brace-balanced, and the veto throws away the region of a method of the same name
    (docs/design/docs.md#kyo-360).

    So: the first line indented no further than the declaration that is neither blank,
    nor closing punctuation, nor a line that begins with it. Never later than
    ``region_end`` says, so the docs gate's region stays inside the touch finder's
    (``tests/test_docs_check.py`` pins that). Ending EARLY is the direction that could
    suppress, and the brace veto is what covers it: a braced block cut short is missing
    its closing brace, which is unbalanced, which raises.
    """
    limit = sweep.region_end(lines, index)
    indent = sweep._indent_width(lines[index])
    for j in range(index + 1, limit):
        line = lines[j]
        if not line.strip() or _CONTINUES_RE.match(line):
            continue
        if sweep._indent_width(line) <= indent:
            return j
    return limit


def _brace_balanced(lines: Sequence[str]) -> bool:
    """Do the braces across these lines close? The veto :func:`_declaration_regions` uses."""
    text = "\n".join(lines)
    return text.count("{") == text.count("}")


def _line_starts(text: str) -> tuple[int, ...]:
    """Character offsets of every line's first character, for :func:`_line_at`.

    The data-format finders below scan characters rather than lines — a brace inside a
    string is only invisible to a scanner that never left the string — and still have to
    answer in line numbers, which is the one thing every caller of :func:`symbol_regions`
    consumes. One table built once per file beats counting newlines per answer.
    """
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return tuple(starts)


def _line_at(starts: Sequence[int], index: int) -> int:
    """The 1-based line holding character ``index``, given :func:`_line_starts`."""
    import bisect

    return bisect.bisect_right(starts, index)


def _json_string_end(text: str, index: int) -> int | None:
    """Index just past the JSON string opening at ``index``, or ``None`` if it never closes.

    Escapes are honoured because the whole reason this is a scanner and not a regex is
    that ``"path": "a\\"{b"`` must not look like an opening brace to anybody.
    """
    limit = len(text)
    position = index + 1
    while position < limit:
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == '"':
            return position + 1
        position += 1
    return None


def _json_value_end(text: str, index: int) -> int | None:
    """Index just past the JSON value starting at ``index``, or ``None`` if it never ends.

    Bracket matching that knows what a string is, which is the whole of the difficulty:
    a value is an object or array run to its matching closer, a string run to its closing
    quote, or a scalar run to the ``,`` or closer that follows it. The closer or comma is
    not part of the value, so the caller rstrips before asking which line it ended on.
    """
    limit = len(text)
    depth = 0
    position = index
    while position < limit:
        char = text[position]
        if char == '"':
            end = _json_string_end(text, position)
            if end is None:
                return None
            position = end
            if depth == 0:
                return position
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            if depth == 0:
                return position
            depth -= 1
            if depth == 0:
                return position + 1
        elif char == "," and depth == 0:
            return position
        position += 1
    return None


def _json_regions(text: str, symbol: str) -> tuple[tuple[int, int], ...] | None:
    """Line spans of the object key ``symbol`` in JSON, or ``None`` if the file will not parse.

    The symbol is an object key at any depth and its region is the key's line through the
    end of its value — the shape the reporting consumer's own targets take, where
    ``package.json#scripts`` means the eight-line script block and a dependency bump is
    somebody else's business. Every occurrence of the key is a region, the rule
    :func:`_python_regions` already follows for a repeated name: a second ``"name"`` deeper
    in the tree widens what raises rather than choosing between them.

    ``json.loads`` decides whether this is JSON at all — a file it refuses is cannot-tell
    and raises, the veto the brace finder gets from counting braces — and then a scanner
    finds the spans, because the parsed object has no line numbers in it and line numbers
    are the entire output. The scanner walks *into* each value rather than over it, so a
    nested key is found; a string is skipped whole, so a brace or a quote inside one ends
    nothing.
    """
    import json

    try:
        json.loads(text)
    except (ValueError, RecursionError):
        # Not JSON this can read. Not evidence the symbol's region is unchanged.
        return None
    starts = _line_starts(text)
    limit = len(text)
    found: list[tuple[int, int]] = []
    position = 0
    while position < limit:
        if text[position] != '"':
            position += 1
            continue
        end = _json_string_end(text, position)
        if end is None:
            return None
        after = end
        while after < limit and text[after] in " \t\r\n":
            after += 1
        if after >= limit or text[after] != ":":
            # A string value, not a key: step over it whole so its contents are never
            # read as structure.
            position = end
            continue
        value_start = after + 1
        while value_start < limit and text[value_start] in " \t\r\n":
            value_start += 1
        value_end = _json_value_end(text, value_start)
        if value_end is None:
            return None
        if json.loads(text[position:end]) == symbol:
            last = len(text[:value_end].rstrip())
            found.append(
                (_line_at(starts, position), _line_at(starts, max(last - 1, position)))
            )
        # Carry on INSIDE the value: `scripts` and `build` are both keys, and a finder
        # that skipped a matched key's value could never place the second.
        position = end
    return tuple(sorted(set(found)))


#: A YAML mapping key, with any number of block-sequence dashes in front of it so that
#: ``- name: build`` is the key ``name``. The value half is not read here beyond where it
#: starts: the region is decided by indentation, and a non-greedy run up to the first
#: unquoted ``:`` is what keeps ``url: http://x`` from being the key ``url: http``.
_YAML_KEY_RE = re.compile(
    r"""^(?P<lead>[ \t]*(?:-[ \t]+)*)
         (?P<key>"[^"]*"|'[^']*'|(?!["'])[^\s#][^:#]*?)
         [ \t]*:(?:[ \t]|$)""",
    re.VERBOSE,
)

#: A value that opens a block scalar (``|``, ``>``, with the chomping and indentation
#: indicators YAML allows). Everything indented under one is text, not structure — a line
#: reading ``build: tsc`` inside one declares nothing.
_YAML_BLOCK_RE = re.compile(r"^[|>][+-]?\d*$")

def _yaml_quoted_end(line: str, index: int) -> int | None:
    """Index just past the quoted YAML scalar opening at ``index``, or ``None`` if it runs
    off the end of the line — which means a multi-line scalar, and a veto."""
    quote = line[index]
    limit = len(line)
    position = index + 1
    while position < limit:
        char = line[position]
        if quote == '"' and char == "\\":
            position += 2
            continue
        if char == quote:
            if quote == "'" and line.startswith("''", position):
                position += 2
                continue
            return position + 1
        position += 1
    return None


@dataclass(frozen=True)
class _YamlLine:
    """One YAML line reduced to the three facts :func:`_yaml_regions` asks of it."""

    #: The line's structural characters, with quoted scalars and the trailing comment
    #: blanked to spaces so positions still line up with the original.
    code: str
    #: A quoted scalar left open at end of line: a multi-line scalar, which the
    #: indentation rule cannot describe.
    open_quote: bool
    #: An anchor, an alias or a merge key — see :func:`_yaml_regions` for why each is a
    #: veto rather than an approximation.
    anchored: bool


def _yaml_line(line: str) -> _YamlLine:
    """Read ``line`` once for structure, quoting and anchors.

    The whole difficulty is that ``'``, ``&`` and ``*`` are only special **where a node
    may begin** — at the start of the line's content, after a ``- ``, after a ``: ``, or
    after a flow ``[``, ``{`` or ``,``. Anywhere else they are ordinary characters in a
    plain scalar, and a reader that does not know the difference vetoes every file that
    says ``the tree's default`` or ``npm ci && node install.js``
    (docs/design/docs.md#kyo-363).
    """
    out: list[str] = []
    at_node_start = True
    anchored = False
    position = 0
    limit = len(line)
    while position < limit:
        char = line[position]
        if char in " \t":
            out.append(char)
            position += 1
            continue
        if char == "#" and (position == 0 or line[position - 1] in " \t"):
            break
        if at_node_start:
            if char in "\"'":
                end = _yaml_quoted_end(line, position)
                if end is None:
                    return _YamlLine("".join(out), True, anchored)
                out.append(" " * (end - position))
                position = end
                at_node_start = False
                continue
            if char in "&*" or (
                line.startswith("<<", position)
                and line[position + 2 :].lstrip(" \t").startswith(":")
            ):
                anchored = True
        following = line[position + 1 : position + 2]
        if char == "-" and following in ("", " ", "\t"):
            # A sequence dash: what follows it is still the start of a node.
            out.append(char)
            position += 1
            continue
        if char == ":" and following in ("", " ", "\t"):
            out.append(char)
            position += 1
            at_node_start = True
            continue
        out.append(char)
        at_node_start = char in "[{,"
        position += 1
    return _YamlLine("".join(out), False, anchored)


def _yaml_compact_item(line: str, indent: int) -> bool:
    """Is ``line`` an item of a block sequence written at column ``indent`` (KYO-412)?

    ``steps:`` followed by ``- run: make build`` at the **same** column is valid YAML and
    the usual GitHub Actions and Kubernetes style: the items are the key's value although
    they are not indented under it. The region rule's "strictly deeper" test would end
    the key's region on its own line, and an edit to ``make test`` would then read as
    outside the region — the suppressing direction. Only asked for a key whose value is
    empty on its own line; that is the only key a same-column sequence can belong to,
    since a mapping key at that column with a value on its line cannot be followed by a
    sequence item at its own column in valid YAML (which :func:`yaml.safe_load_all` has
    already confirmed the file is).
    """
    if sweep._indent_width(line) != indent:
        return False
    stripped = line.strip()
    return stripped == "-" or stripped.startswith(("- ", "-\t"))


def _yaml_regions(text: str, symbol: str) -> tuple[tuple[int, int], ...] | None:
    """Line spans of the mapping key ``symbol`` in YAML, or ``None`` when this cannot read it.

    The symbol is a mapping key at any depth; its region is the key's line through the
    last following line indented deeper than it, with the comment block directly above the
    key included — the same reasoning :func:`_python_regions` starts a region at a
    decorator and :func:`_declaration_regions` at a JSDoc block: a comment above a key is
    part of the key, and a region starting at the key's own line would call an edit to it
    clean. A block scalar's content is covered by the indentation rule for free, and a
    flow mapping on one line is the one-line region.

    Parsed once for the veto and never for the spans. ``yaml.safe_load_all`` answers "is
    this YAML at all" better than any hand-rolled check could, and it answers nothing
    about line numbers, which is all this function returns. Three further shapes are
    vetoed although they parse — anchors, aliases and merge keys, and a flow collection
    left open at the end of a line — because they are exactly the shapes whose content is
    not the lines indented under the key, so a span for them would be *wrong* rather than
    absent, and a wrong region is the one outcome worse than no region.

    Block-scalar content is skipped when looking for keys and kept when measuring a
    region: ``run: |`` followed by an indented ``build: tsc`` declares ``run`` and does
    not declare ``build``.

    A key whose value is empty on its own line also keeps, in its region, a block
    sequence written at the key's own column (``steps:`` then ``- run: ...``) and those
    items' deeper continuation lines — see :func:`_yaml_compact_item` (KYO-412).
    """
    lines = text.splitlines()
    try:
        list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return None
    #: (line index, key column, key name, whether the key's value is empty on its line)
    keys: list[tuple[int, int, str, bool]] = []
    block_indent: int | None = None
    for index, line in enumerate(lines):
        if block_indent is not None:
            if not line.strip() or sweep._indent_width(line) > block_indent:
                continue
            block_indent = None
        read = _yaml_line(line)
        if read.open_quote or read.anchored:
            return None
        code = read.code
        if code.count("{") != code.count("}") or code.count("[") != code.count("]"):
            # A flow collection spanning lines: the key's value is not "the lines indented
            # under it", so the indentation rule does not describe this file.
            return None
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _YAML_KEY_RE.match(line)
        if match is None:
            continue
        # The key's own visual column, dashes included: `- name:` nested in a sequence is
        # a key at the column `name` starts in, and everything the region rule compares
        # it against (`sweep._indent_width`) is a visual column too.
        indent = len(line[: match.start("key")].expandtabs(4))
        name = match.group("key")
        if name[:1] in ("'", '"'):
            name = name[1:-1]
        value = _yaml_line(line[match.end() :]).code.strip()
        keys.append((index, indent, name, not value))
        if _YAML_BLOCK_RE.match(value):
            block_indent = indent
    found: list[tuple[int, int]] = []
    for index, indent, name, empty in keys:
        if name != symbol:
            continue
        start = index
        while start > 0 and lines[start - 1].strip().startswith("#"):
            start -= 1
        end = index
        for later in range(index + 1, len(lines)):
            line = lines[later]
            if not line.strip():
                continue
            deeper = sweep._indent_width(line) > indent
            if line.strip().startswith("#"):
                # A comment never ENDS a region: a stray comment at column 0 in the middle
                # of a block would otherwise cut the region short, and ending early is the
                # direction that suppresses.
                if deeper:
                    end = later
                continue
            if not deeper and not (empty and _yaml_compact_item(line, indent)):
                break
            end = later
        found.append((start + 1, end + 1))
    return tuple(sorted(set(found)))


def _css_string_end(text: str, index: int) -> int | None:
    """Index just past the CSS string opening at ``index``, or ``None`` if it never closes."""
    quote = text[index]
    limit = len(text)
    position = index + 1
    while position < limit:
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == quote:
            return position + 1
        if char == "\n":
            return None
        position += 1
    return None


def _css_declares(segment: str, wanted: str) -> bool:
    """Is ``segment`` the declaration of the custom property ``wanted``?

    Only a custom property, and only on the left of the colon: ``color: var(--bg)`` uses
    ``--bg`` and does not declare it, and a document narrowing to ``--bg`` means the line
    that says what it is.
    """
    if not wanted.startswith("--"):
        return False
    name, separator, _ = segment.partition(":")
    return bool(separator) and name.strip() == wanted


def _css_regions(text: str, symbol: str) -> tuple[tuple[int, int], ...] | None:
    """Line spans of the rule ``symbol`` in CSS, or ``None`` if its braces do not close.

    The symbol is a selector or an at-rule prelude, matched **whole** against the text
    before the ``{`` with whitespace collapsed, and its region is that line through the
    matching ``}``. Whole rather than by containment because a selector list is one
    decision written on one line: ``.pane-left, .pane-right`` is a rule about both, and a
    document naming ``.pane-left`` alone has not named it. A custom property (``--name``)
    is its declaration, start of the declaration through its ``;``.

    Brace matching is what :func:`_declaration_regions` refused for the brace languages,
    and the reason it is right here is the reason it was wrong there: in TypeScript a
    brace can hide in a template literal, a regex or a comment, and every one this got
    wrong would end a region early and *suppress*. CSS has two places a brace can hide —
    a string and a ``/* */`` comment — both are closed forms this scanner reads, and an
    unclosed one is a veto rather than a guess. Nesting is free: ``@media`` is a prelude
    with a block, and the rules inside it are preludes with blocks.

    Unbalanced braces anywhere are ``None`` for the whole file, not ``()`` for one symbol:
    a ``}`` with nothing open means the nesting this scanner believed in was never there,
    so every span it produced was measured against a structure it had already misread.
    """
    wanted = " ".join(symbol.split())
    if not wanted:
        return ()
    starts = _line_starts(text)
    limit = len(text)
    stack: list[tuple[str, int]] = []
    found: list[tuple[int, int]] = []
    segment_start: int | None = None
    position = 0
    while position < limit:
        char = text[position]
        if text.startswith("/*", position):
            close = text.find("*/", position + 2)
            if close < 0:
                return None
            position = close + 2
            continue
        if char in "\"'":
            if segment_start is None:
                segment_start = position
            end = _css_string_end(text, position)
            if end is None:
                return None
            position = end
            continue
        if char in "{};":
            segment = text[segment_start:position] if segment_start is not None else ""
            if char == "{":
                stack.append(
                    (
                        " ".join(segment.split()),
                        segment_start if segment_start is not None else position,
                    )
                )
            elif char == "}":
                if not stack:
                    return None
                prelude, opened_at = stack.pop()
                if prelude == wanted:
                    found.append((_line_at(starts, opened_at), _line_at(starts, position)))
            elif segment_start is not None and _css_declares(segment, wanted):
                found.append(
                    (_line_at(starts, segment_start), _line_at(starts, position))
                )
            segment_start = None
            position += 1
            continue
        if segment_start is None and not char.isspace():
            segment_start = position
        position += 1
    if stack:
        return None
    return tuple(sorted(set(found)))


def symbol_regions(path: str, text: str, symbol: str) -> tuple[tuple[int, int], ...] | None:
    """Where ``symbol`` lives in ``text``, as 1-based inclusive line spans.

    Three distinct answers, and the caller must keep them distinct (decision 9):

    * ``None`` — **the instrument does not apply or could not run**: a file type it
      cannot read, or a revision that will not parse. Not evidence of anything.
    * ``()`` — it ran, and this symbol names no region here. Also not evidence the
      region is unchanged: ``_resolve_target`` only requires the symbol to appear as
      whole-word *text*, so a target may legitimately name a string literal, a comment,
      or an import. The join treats this as cannot-tell too.
    * a non-empty tuple — the region, located. The only answer that can lead to a
      suppression, and only when the other side of the diff also produced one.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return _python_regions(text, symbol)
    if suffix == ".md":
        return _markdown_regions(text, symbol)
    if suffix in BRACE_REGION_SUFFIXES:
        return _declaration_regions(text, symbol)
    if suffix == ".json":
        return _json_regions(text, symbol)
    if suffix in YAML_REGION_SUFFIXES:
        return _yaml_regions(text, symbol)
    if suffix == ".css":
        return _css_regions(text, symbol)
    return None


# ------------------------------------------- KYO-500: the region a code pointer sits in
#
# The other direction of the same instrument. A `governs:` target names a symbol and asks
# where it lives; a code pointer (`docs/design/<file>.md#kyo-<n>` written in a comment or
# docstring) names a SECTION from inside the code, and the question is which region of the
# code it is written in. The answer is the innermost `def`/`class` that contains the
# pointer's line, and otherwise the
# module-level statement the comment precedes -- the leading comment block above a
# top-level statement belongs to that statement (docs/design/docs.md#kyo-500).

#: Suffixes :func:`pointer_region` can place a pointer in. Python only: it is the one
#: language the measurement behind that choice read (227 pointers, all in `src/kyosei/*.py`), and a
#: suffix outside this tuple is "cannot tell", which raises at file granularity rather
#: than quietly less (decision 9, the same asymmetry :func:`symbol_regions` keeps).
POINTER_REGION_SUFFIXES: tuple[str, ...] = (".py",)

#: One pointer's region: first line, last line (1-based, inclusive), and how it reads in
#: an obligation's ``says`` (``def generate_brief``, ``class Store``, ``module-level
#: statement at line 40``).
PointerRegion = tuple[int, int, str]


def pointer_region(path: str, text: str, line: int) -> PointerRegion | tuple[()] | None:
    """The code region that holds line ``line`` of ``text``, for a pointer written there.

    Three answers, kept distinct exactly as :func:`symbol_regions` keeps them:

    * ``None`` — no finder for this suffix, or the revision will not parse.
    * ``()`` — the finder ran and no region holds the line: a comment after the last
      top-level statement, or in a file with no statements at all.
    * ``(start, end, label)`` — the innermost ``def``/``class`` whose span (from its first
      decorator) holds the line, or else the module-level statement whose span, widened
      upward to the line after the previous top-level statement, holds it.

    Both of the first two are cannot-tell, and the join raises them at file granularity.
    """
    import ast

    if Path(path).suffix.lower() not in POINTER_REGION_SUFFIXES:
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    best: PointerRegion | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        end = node.end_lineno or node.lineno
        if start <= line <= end and (best is None or end - start < best[1] - best[0]):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            best = (start, end, f"{kind} {node.name}")
    if best is not None:
        return best
    previous_end = 0
    for node in tree.body:
        end = node.end_lineno or node.lineno
        if previous_end < line <= end:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                return (previous_end + 1, end, f"{kind} {node.name}")
            return (previous_end + 1, end, f"module-level statement at line {node.lineno}")
        previous_end = end
    return ()


@dataclass(frozen=True)
class FileChange:
    """One changed file with enough evidence to ask whether a symbol's region moved.

    Both revisions in full, plus the line numbers the diff touched on each side. Two
    sides rather than one because a change is only invisible to this instrument if it is
    invisible to BOTH: an insertion inside a function shows on the after side, a deletion
    on the before side, and asking only one of them would suppress the other.

    ``before_text``/``after_text`` are ``None`` when that revision has no readable copy
    of the file — added, deleted, binary, or unreadable — which is cannot-tell and raises.
    """

    path: str
    before_text: str | None
    after_text: str | None
    #: Line numbers in ``before_text`` the diff removed or replaced.
    before_lines: frozenset[int] = frozenset()
    #: Line numbers in ``after_text`` the diff added or replaced.
    after_lines: frozenset[int] = frozenset()


def symbol_region_changed(change: FileChange, symbol: str) -> bool | None:
    """Did ``symbol``'s region change in ``change``? ``None`` when this cannot tell.

    ``False`` — the answer that suppresses an obligation — is returned only when the
    region was located on both sides and neither side's changed lines fall inside it.
    """
    located = _located_both_sides(change, symbol)
    if located is None:
        return None
    return _region_verdict(located)


_Located = list[tuple[tuple[tuple[int, int], ...], frozenset[int]]]


def _located_both_sides(change: FileChange, symbol: str) -> _Located | None:
    """``symbol``'s regions on each side of ``change``, paired with that side's changed
    lines — or ``None`` when either side cannot be read or will not parse.

    Split out of :func:`symbol_region_changed` (KYO-397) because a bare-symbol target
    asks a question before the verdict: *does this file define the symbol at all*, on
    either side — and asking it from the same two region reads is what keeps the
    definition test and the changed test from being two instruments that can disagree.
    """
    located: _Located = []
    for text, lines in (
        (change.before_text, change.before_lines),
        (change.after_text, change.after_lines),
    ):
        if text is None:
            return None
        regions = symbol_regions(change.path, text, symbol)
        if regions is None:
            return None
        located.append((regions, lines))
    return located


def _region_verdict(located: _Located) -> bool | None:
    """:func:`symbol_region_changed`'s answer from both sides' located regions."""
    present = [regions for regions, _ in located if regions]
    if not present:
        # Ran on both sides, named a region on neither: the target resolves as text but
        # this instrument cannot say where it lives. Cannot tell.
        return None
    if len(present) == 1:
        # Located on one side only — the definition was added or removed by this diff,
        # which is the strongest possible "changed".
        return True
    for regions, lines in located:
        if any(start <= line <= end for start, end in regions for line in lines):
            return True
    return False


# ------------------------------------- KYO-530: a change that moved no code token raises nothing
#
# A branch that edits only comments and docstrings in a governed file changes nothing a
# section can describe, yet it raised every section governing the file and every section a
# pointer in it names. The instrument is the language's own lexer, run on both sides of the
# diff: the change is comment-only when the two code-token sequences are identical. Decision
# 9 holds as it does for a region: a side that is missing, will not lex, or is in a language
# with no lexer here is cannot-tell, and cannot-tell raises (docs/design/docs.md#kyo-530).

#: Suffixes :func:`comment_only_change` can lex. Python through :mod:`tokenize` and
#: :mod:`ast`, YAML through PyYAML's own scanner, which drops comments by the spec. Not
#: TypeScript/JavaScript or CSS: a lexer for those has to tell a regex literal from a
#: division, and a misread ``/*`` swallows code as a comment — the error that quiets a raise.
COMMENT_ONLY_SUFFIXES: tuple[str, ...] = (".py", *YAML_REGION_SUFFIXES)

_CODING_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")


def _char_col(lines: Sequence[str], line: int, byte_col: int) -> int | None:
    """``ast``'s UTF-8 byte column as :mod:`tokenize`'s character column."""
    if not 1 <= line <= len(lines):
        return None
    try:
        return len(lines[line - 1].encode("utf-8")[:byte_col].decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _python_code_tokens(text: str) -> tuple[tuple[int, str], ...] | None:
    """``text``'s Python tokens without its comments, blank lines and docstrings.

    ``None`` when the source will not parse or lex, or when a docstring cannot be matched
    to its tokens exactly. Kept: every other token by type and text (an ``INDENT`` by its
    whitespace too, so re-indenting is a change), the logical-line ``NEWLINE`` by type, and
    the comments that are not commentary — a shebang on line 1 and a coding cookie on line
    1 or 2, which decide how the file runs and is decoded. A docstring is the first
    statement of a module, class or function when it is a plain string; its tokens and the
    ``NEWLINE`` ending it are dropped, so adding or removing one is comment-only too.
    """
    import ast
    import io
    import tokenize

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    lines = io.StringIO(text).readlines()
    starts: dict[tuple[int, int], tuple[int, int]] = {}
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        first = node.body[0] if node.body else None
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        value = first.value
        start = _char_col(lines, value.lineno, value.col_offset)
        end = _char_col(lines, value.end_lineno or value.lineno, value.end_col_offset or 0)
        if start is None or end is None:
            return None
        starts[(value.lineno, start)] = (value.end_lineno or value.lineno, end)
    found: list[tuple[int, str]] = []
    dropping: tuple[int, int] | None = None
    after_docstring = False
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            kind = token.type
            if kind == tokenize.COMMENT:
                row = token.start[0]
                if (row == 1 and token.string.startswith("#!")) or (
                    row <= 2 and _CODING_RE.match(token.string)
                ):
                    found.append((kind, token.string))
                continue
            if kind == tokenize.NL:
                continue
            if dropping is None and token.start in starts:
                dropping = starts.pop(token.start)
            if dropping is not None:
                if kind not in (tokenize.STRING, tokenize.OP) or (
                    kind == tokenize.OP and token.string not in "()"
                ):
                    return None
                if token.end == dropping:
                    dropping = None
                    after_docstring = True
                elif token.end > dropping:
                    return None
                continue
            if after_docstring:
                after_docstring = False
                if kind == tokenize.NEWLINE:
                    continue
            if kind in (tokenize.NEWLINE, tokenize.DEDENT, tokenize.ENDMARKER):
                found.append((kind, ""))
            else:
                found.append((kind, token.string))
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    if starts or dropping is not None:
        # A docstring the token stream never reached exactly: cannot tell.
        return None
    return tuple(found)


def _yaml_code_tokens(text: str) -> tuple[tuple, ...] | None:
    """``text``'s YAML tokens as PyYAML's scanner reads them, which never yields a
    comment, or ``None`` when it will not scan or parse. Positions are not compared.

    Parsed as well as scanned: the scanner alone accepts an unclosed ``[``, and a
    document the parser refuses is one whose comments this cannot vouch for."""
    try:
        for _ in yaml.parse(text, Loader=yaml.SafeLoader):
            pass
        return tuple(
            (
                type(token).__name__,
                getattr(token, "name", None),
                getattr(token, "value", None),
                getattr(token, "plain", None),
                getattr(token, "style", None),
            )
            for token in yaml.scan(text, Loader=yaml.SafeLoader)
        )
    except yaml.YAMLError:
        return None


def code_tokens(path: str, text: str) -> tuple | None:
    """``text``'s code tokens, comments and docstrings left out, or ``None`` when this
    cannot lex ``path``'s language or this revision of it (KYO-530)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return _python_code_tokens(text)
    if suffix in YAML_REGION_SUFFIXES:
        return _yaml_code_tokens(text)
    return None


def comment_only_change(change: FileChange) -> bool:
    """Did ``change`` touch only comments and docstrings? (KYO-530)

    ``True`` — the answer that quiets an obligation — only when both revisions exist, both
    lex, and their code-token sequences are identical. Every other outcome is ``False``,
    which raises as before.
    """
    if Path(change.path).suffix.lower() not in COMMENT_ONLY_SUFFIXES:
        return False
    if change.before_text is None or change.after_text is None:
        return False
    before = code_tokens(change.path, change.before_text)
    if before is None:
        return False
    return before == code_tokens(change.path, change.after_text)


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _hunk_lines(count_at: str | None, start: str) -> set[int]:
    """The line numbers one side of a ``@@`` header covers.

    A zero-length side is an insertion point rather than a range, and git names the line
    *before* it. Both neighbours are marked: it is one line of over-raising against the
    alternative of missing an insertion at a region's edge, and over-raising is the side
    decision 9 puts the error on.
    """
    first = int(start)
    count = 1 if count_at is None else int(count_at)
    if count == 0:
        return {first, first + 1}
    return set(range(first, first + count))


def branch_file_changes(
    root: Path, against: str, branch: str, paths: Sequence[str] = ()
) -> dict[str, FileChange]:
    """Read ``against...branch`` as :class:`FileChange` evidence, one entry per file.

    The same three-dot diff :func:`kyosei.git_queries.branch_changed_files` already names
    the files of, read at ``--unified=0`` so the hunk headers ARE the changed line
    numbers and nothing has to be inferred from context. ``paths`` narrows the read to
    the files some ``governs:`` target actually names, which is the difference between
    two ``git show`` calls and two hundred on a large branch.

    **Never raises and never reports a failure as an empty result.** A file this cannot
    read is simply absent from the map, or present with a ``None`` side, and both mean
    cannot-tell to :func:`_target_raises`, which raises the obligation. There is no
    outcome here that can quiet the gate — which is why it can afford to be best-effort
    where :func:`kyosei.git_queries.branch_changed_files` cannot.
    """
    import subprocess

    from .git_queries import run_git

    def run(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return run_git(root, *args)
        except OSError:
            return None

    wanted = sorted(set(paths)) if paths else None
    base = run("merge-base", against, branch)
    if base is None or base.returncode != 0 or not base.stdout.strip():
        return {}
    before_rev = base.stdout.strip()
    diff_args = ["diff", "--unified=0", "--no-color", f"{against}...{branch}"]
    if wanted:
        diff_args += ["--", *wanted]
    diff = run(*diff_args)
    if diff is None or diff.returncode != 0:
        return {}

    per_file: dict[str, tuple[set[int], set[int]]] = {}
    current: str | None = None
    removed_from: str | None = None
    for line in diff.stdout.splitlines():
        if line.startswith("--- "):
            source = line[4:].strip()
            removed_from = source[2:] if source.startswith("a/") else None
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else None
            if current is None and target == "/dev/null":
                # KYO-500: a deleted file is kept, under its old name, with no after
                # side. Its before side is every line a pointer in it sat in, and a
                # missing after side is cannot-tell to every symbol reader, so no
                # `governs:` answer this map gives changes by including it.
                current = removed_from
            if current is not None:
                per_file.setdefault(current, (set(), set()))
            continue
        if current is None:
            continue
        match = _HUNK_RE.match(line)
        if match:
            before, after = per_file[current]
            before |= _hunk_lines(match.group(2), match.group(1))
            after |= _hunk_lines(match.group(4), match.group(3))

    def blob(rev: str, path: str) -> str | None:
        shown = run("show", f"{rev}:{path}")
        if shown is None or shown.returncode != 0:
            return None
        return shown.stdout

    return {
        path: FileChange(
            path=path,
            before_text=blob(before_rev, path),
            after_text=blob(branch, path),
            before_lines=frozenset(before),
            after_lines=frozenset(after),
        )
        for path, (before, after) in per_file.items()
    }


__all__ = [
    "BRACE_REGION_SUFFIXES",
    "COMMENT_ONLY_SUFFIXES",
    "REGION_SUFFIXES",
    "YAML_REGION_SUFFIXES",
    "POINTER_REGION_SUFFIXES",
    "FileChange",
    "PointerRegion",
    "branch_file_changes",
    "code_tokens",
    "comment_only_change",
    "pointer_region",
    "region_suffixes_says",
    "narrowable_targets",
    "narrows_by_symbol",
    "symbol_region_changed",
    "symbol_regions",
]
