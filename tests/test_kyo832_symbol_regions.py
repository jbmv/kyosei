"""KYO-832: three gaps in `path#symbol` targets.

A `.swift` file is narrowed to the symbol's declaration and body (c1). A call that is the
last statement of a callback no longer costs its name every region (c2). And `kyo docs
coverage` names the files governed by `#symbol` targets only, where a new part raises
nothing although the file counts as governed (c3).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kyosei import docs_obligations, docs_regions

# ---------------------------------------------------------------------- c1: Swift

_FEED_SWIFT = """import Foundation

/// The window's state.
@MainActor
final class SessionFeed: ObservableObject {
    @Published private(set) var agenda: [String] = []
    private var task: Task<Void, Never>?

    init(server: String) {
        self.server = server
    }

    /// Shows the session's task list.
    @discardableResult
    func showAgenda(
        _ data: String,
        session: String
    ) -> Bool {
        guard let name = data.first else { return false }
        send(
            name: "agenda",
            session: session
        )
        return true
    }

    private func send(name: String, session: String) {
        task = Task {
            showAgenda("", session: session)
        }
    }

    enum Place {
        case project, session
        case none
    }
}

extension SessionFeed {
    static func make() -> SessionFeed { SessionFeed(server: "") }
}
"""

_FEED_PATH = "apps/apple/Sources/KyoseiMac/SessionFeed.swift"


def _line(text: str, needle: str) -> int:
    (found,) = [n for n, line in enumerate(text.splitlines(), 1) if needle in line]
    return found


def _swift_change(lines: set[int], text: str = _FEED_SWIFT) -> docs_regions.FileChange:
    return docs_regions.FileChange(_FEED_PATH, text, text, frozenset(lines), frozenset(lines))


def test_swift_is_a_suffix_a_symbol_narrows() -> None:
    assert ".swift" in docs_regions.REGION_SUFFIXES
    assert docs_regions.narrows_by_symbol(_FEED_PATH)


def test_a_swift_member_is_its_declaration_through_its_closing_brace() -> None:
    """c1. The region starts at the doc comment and the attribute line above the
    declaration, crosses the multi-line signature and ends at the member's own brace."""
    regions = docs_regions.symbol_regions(_FEED_PATH, _FEED_SWIFT, "showAgenda")
    first = _line(_FEED_SWIFT, "/// Shows the session's task list.")
    last = _line(_FEED_SWIFT, "return true") + 1
    assert _FEED_SWIFT.splitlines()[last - 1] == "    }"
    # One region: the call inside `send` is not a declaration.
    assert regions == ((first, last + 1),), regions
    assert not _FEED_SWIFT.splitlines()[last].strip(), "the trailing blank line only"


def test_a_change_outside_the_swift_member_raises_nothing() -> None:
    """c1. Through the join the gate uses: a section governing `SessionFeed.swift#showAgenda`
    is raised by a change inside the member and not by one elsewhere in the file."""
    target = f"{_FEED_PATH}#showAgenda"
    changed = frozenset({_FEED_PATH})
    inside = _line(_FEED_SWIFT, "return true")
    for outside_text in ("task = Task {", "self.server = server", "case none", "static func make"):
        outside = _line(_FEED_SWIFT, outside_text)
        change = _swift_change({outside})
        assert docs_regions.symbol_region_changed(change, "showAgenda") is False, outside_text
        assert (
            docs_obligations._target_raises(target, changed, {_FEED_PATH: change}) is None
        ), outside_text
    change = _swift_change({inside})
    assert docs_regions.symbol_region_changed(change, "showAgenda") is True
    assert docs_obligations._target_raises(target, changed, {_FEED_PATH: change}) == _FEED_PATH
    # The attribute line above the declaration is the declaration's.
    attribute = _swift_change({_line(_FEED_SWIFT, "@discardableResult")})
    assert docs_regions.symbol_region_changed(attribute, "showAgenda") is True


def test_the_shapes_a_swift_document_names_are_each_placed() -> None:
    """c1. A property behind its attribute and modifiers, an initialiser, a type, an
    enum case on a shared line, a nested type and an extension."""
    def region(symbol: str) -> tuple[tuple[int, int], ...]:
        found = docs_regions.symbol_regions(_FEED_PATH, _FEED_SWIFT, symbol)
        assert found, symbol
        return found

    agenda = _line(_FEED_SWIFT, "var agenda")
    assert region("agenda") == ((agenda, agenda),)
    init = _line(_FEED_SWIFT, "init(server: String) {")
    assert region("init") == ((init, init + 3),)
    case = _line(_FEED_SWIFT, "case project, session")
    assert region("session") == ((case, case),)
    assert region("Place")[0][0] == _line(_FEED_SWIFT, "enum Place {")
    # The class and its extension are two regions of one name; a dotted target is read
    # by its last segment, as in the brace languages.
    assert len(region("SessionFeed")) == 2
    assert region("SessionFeed.make") == region("make")
    make = _line(_FEED_SWIFT, "static func make")
    assert region("make") == ((make, make),)


def test_what_declares_nothing_in_swift_adds_no_region() -> None:
    """c1. An argument label, a call and a name bound in a condition are not
    declarations, so a name that is only those has no region and raises (decision 9)."""
    for symbol in ("name", "server", "data"):
        assert docs_regions.symbol_regions(_FEED_PATH, _FEED_SWIFT, symbol) == (), symbol
        change = _swift_change({_line(_FEED_SWIFT, "case none")})
        assert docs_regions.symbol_region_changed(change, symbol) is None, symbol
    assert not docs_regions._declares_swift("        } catch let failure {", "failure")
    assert not docs_regions._declares_swift("    // func showAgenda() {", "showAgenda")
    assert not docs_regions._declares_swift("        super.init(frame: .zero)", "init")
    assert docs_regions._declares_swift("    required init?(coder: NSCoder) {", "init")
    assert docs_regions._declares_swift("    nonisolated(unsafe) static var shared = 1", "shared")


def test_every_case_the_swift_finder_cannot_decide_still_raises() -> None:
    """c1, decision 9. A region that does not close, and one that ends inside a
    multi-line string, are discarded, and a symbol with no region raises."""
    truncated = _FEED_SWIFT.replace("        return true\n    }\n", "        return true\n", 1)
    assert docs_regions.symbol_regions(_FEED_PATH, truncated, "showAgenda") == ()
    quoted = (
        "struct Help {\n"
        '    let text = """\n'
        "Unindented text of a multi-line string.\n"
        '"""\n'
        "    let other = 1\n"
        "}\n"
    )
    assert docs_regions.symbol_regions("Help.swift", quoted, "text") == ()
    assert docs_regions.symbol_regions("Help.swift", quoted, "other") == ((5, 5),)
    # A body whose brace opens on its own line is not cut off from its signature.
    allman = "struct A {\n    func run()\n    {\n        work()\n    }\n\n    func stop() {}\n}\n"
    assert docs_regions.symbol_regions("A.swift", allman, "run") == ((2, 6),)


# ------------------------------------------ c2: a call that ends a callback's body

_CHAT_TS = """let searchTimer: number | undefined;

function showHit(): void {
  const range = hits[currentHit];
  if (range) range.scrollIntoView();
}

findField.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    searchTimer = undefined;
    runSearch();
    showHit();
  }, SEARCH_DEBOUNCE_MS);
});

function runSearch(): void {
  hits = find(findField.value);
}
"""


def test_a_function_also_called_last_in_a_callback_keeps_its_region() -> None:
    """c2. The shape the five functions of apps/apple/web/src/chat.ts shared: a
    zero-argument function whose call is the last statement of a callback closed by a
    line that carries the rest of the enclosing call. The call is read as a declaration;
    its closing line begins with punctuation and holds more than punctuation, so it was
    neither a terminator nor trimmed, the one-statement region held a `}` with no `{`,
    and the brace veto discarded every region of the name."""
    regions = docs_regions.symbol_regions("chat.ts", _CHAT_TS, "showHit")
    declared = _line(_CHAT_TS, "function showHit(): void {")
    called = _line(_CHAT_TS, "    showHit();")
    assert regions == ((declared, declared + 4), (called, called)), regions

    def change(line: int) -> docs_regions.FileChange:
        return docs_regions.FileChange(
            "chat.ts", _CHAT_TS, _CHAT_TS, frozenset({line}), frozenset({line})
        )

    inside = _line(_CHAT_TS, "range.scrollIntoView()")
    assert docs_regions.symbol_region_changed(change(inside), "showHit") is True
    for outside_text in ("hits = find(", "}, SEARCH_DEBOUNCE_MS);", "runSearch();"):
        outside = _line(_CHAT_TS, outside_text)
        assert docs_regions.symbol_region_changed(change(outside), "showHit") is False, (
            outside_text
        )


def test_the_veto_still_discards_a_declaration_whose_block_does_not_close() -> None:
    """c2. Only a line indented less than the declaration is left out; a block cut
    short is still unbalanced and still costs the name its regions."""
    cut = _CHAT_TS.replace("  if (range) range.scrollIntoView();\n}\n", "  if (range) {\n", 1)
    assert docs_regions.symbol_regions("chat.ts", cut, "showHit") == ()


def test_the_five_functions_of_chat_ts_are_placed() -> None:
    """c2, on the file the record names. Each has a region that starts at or above its
    `function` line."""
    path = Path(__file__).resolve().parents[1] / "apps/apple/web/src/chat.ts"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for name in ("showHit", "drawFoldsSoon", "postDraft", "switchWhenFree", "closeProjectList"):
        regions = docs_regions.symbol_regions("apps/apple/web/src/chat.ts", text, name)
        assert regions, name
        assert any(
            any(f"function {name}(" in line for line in lines[start - 1 : end])
            for start, end in regions
        ), name


# ------------------------------------------------- c3: governed by symbols only

_SYMBOL_DOC = """# Notes

## Alpha

governs: src/narrow.py#alpha

What alpha does.

## Beta, and the whole of both.py

governs: src/both.py#beta, src/both.py

What both.py does.

## A symbol the reader does not place

governs: src/called.py#helper, src/other.go#run

Where helper is called.
"""


def _put(project: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for args in (("add", "-A"), ("commit", "-q", "-m", "files")):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def test_coverage_names_the_files_governed_by_symbol_targets_only(
    kyo, git_project: Path
) -> None:
    """c3. `narrow.py` is governed through `#alpha` alone, so it is listed with its
    symbol. A file a bare path also names, a file whose symbol has no region and a file
    whose suffix has no reader each raise on any change, and are not listed."""
    _put(
        git_project,
        {
            "docs/notes.md": _SYMBOL_DOC,
            "src/narrow.py": "def alpha():\n    return 1\n\n\ndef gamma():\n    return 3\n",
            "src/both.py": "def beta():\n    return 2\n",
            "src/called.py": "from narrow import helper\n\nhelper()\n",
            "src/other.go": "func run() {}\n",
        },
    )
    result = kyo("docs", "coverage", "--json")
    assert result.code == 0, result
    data = result.json
    assert data["governed"] == 4 and data["ungoverned_count"] == 0
    assert data["symbol_only"] == [{"path": "src/narrow.py", "symbols": ["alpha"]}]
    assert data["symbol_only_count"] == 1

    text = kyo("docs", "coverage")
    assert text.code == 0, text
    assert "1 governed file(s) are governed by `#symbol` targets only" in text.out
    assert "src/narrow.py: 1 symbol(s)" in text.out
    assert "raises nothing" in text.out

    # The statement is true of the file it is made about: a new function in narrow.py
    # is outside alpha's region on both sides, so the section is not raised.
    before = (git_project / "src/narrow.py").read_text(encoding="utf-8")
    after = before + "\n\ndef delta():\n    return 4\n"
    added = frozenset(range(len(before.splitlines()) + 1, len(after.splitlines()) + 1))
    change = docs_regions.FileChange("src/narrow.py", before, after, frozenset(), added)
    assert (
        docs_obligations._target_raises(
            "src/narrow.py#alpha", frozenset({"src/narrow.py"}), {"src/narrow.py": change}
        )
        is None
    )


def test_the_report_changes_no_finding(kyo, git_project: Path) -> None:
    """c3. A report, not a level: with every file governed the doctor says nothing
    about coverage, whether or not a file is governed by symbols only."""
    _put(
        git_project,
        {
            "docs/notes.md": "# Notes\n\n## Alpha\n\ngoverns: src/narrow.py#alpha\n\nText.\n",
            "src/narrow.py": "def alpha():\n    return 1\n",
        },
    )
    assert kyo("docs", "coverage", "--json").json["symbol_only_count"] == 1
    checks = kyo("doctor", "--json").json["checks"]
    assert checks["docs_coverage"]["failures"] == []
    assert checks["docs_coverage_required"]["failures"] == []


def test_a_project_with_no_symbol_only_file_is_told_nothing_about_it(
    kyo, git_project: Path
) -> None:
    _put(
        git_project,
        {
            "docs/notes.md": "# Notes\n\n## Alpha\n\ngoverns: src/a.py\n\nText.\n",
            "src/a.py": "A = 1\n",
        },
    )
    assert kyo("docs", "coverage", "--json").json["symbol_only"] == []
    assert "targets only" not in kyo("docs", "coverage").out
