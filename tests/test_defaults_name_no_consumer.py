"""KYO-628 (after KYO-579): no private name, record prefix, listed word or home path in
the public tree.

WHAT IS PRIVATE, AND WHERE THE LIST LIVES. The names this guard protects cannot be
written here, and neither can digests of them: a SHA-256 of a short name is reversed by
hashing a list of common names, so a published digest list publishes what it guards.
The guard therefore reads its terms at run time from two places outside the tracked
tree:

- **the machine's project registry** (``$KYOSEI_HOME/projects.json``, what
  ``kyo projects list`` prints), read through :func:`kyosei.cmd_projects.registered`.
  Each entry gives a project name and a record prefix;
- **the word file** ``private-words.txt`` beside ``kinds.yml`` in this project's store,
  found through :func:`kyosei.config.resolve_layout` (so a dispatch worktree reaches the
  main checkout's store, and a split-layout store is found through its link). One word
  per line; ``#`` starts a comment.

Where one cannot be reached (a fresh clone, a CI runner), the check that needs it skips
and names the file it looked for. The home-path check needs neither and always runs.

THE MATCHING RULES. Several registry entries are ordinary words, so:

1. A **project name** matches whole, case-insensitively, between non-alphanumeric
   characters; ``-`` and ``_`` in a name match each other.
2. A **component** of a hyphenated name (the parts between ``-``, ``_`` or ``.``)
   matches on its own only when it is at least :data:`MIN_COMPONENT` characters long and
   is not a common English word. A word is common when it is in the system word list
   (:data:`DICTIONARY`, lowercase entries only, so proper names are not common) or in
   :data:`COMMON_WORDS`, the software vocabulary that list lacks.
3. A **record prefix** matches only as a record id, ``PREFIX-<digits>``, in its own
   case. A prefix that is also an English word is never matched as a bare word.
4. A **listed word** matches whole, case-insensitively.
5. This project's own name and prefix, and ``recx`` (a public tool Kyosei integrates
   with), are never private (:data:`NEVER_PRIVATE`).

A **home path** is ``/Users/<account>`` or ``/home/<account>``. In the prose roots
(:data:`PROSE_ROOTS`) every one fails, since a page for a stranger should write ``~``.
In code and tests, where placeholder accounts are fixtures for the home-path logic
itself, one fails when its account is the running account's own name.

WHAT IS SCANNED. Text files under :data:`ROOTS` that git tracks, or that are new and not
ignored. Ignored files are skipped: that is how generated output regenerated from the
store (``src/kyosei/_changelog.json``) and build products stay out, while generated
output that is tracked (``docs/reference/cli.md``) is still scanned.

A failure names the file, the line and the kind of term, never the term. The tests
below prove each rule with names generated at run time.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import secrets
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The public tree: code, tests, the public docs, the design record, the desktop app,
#: templates, tools and the build backend.
ROOTS = (
    "src",
    "tests",
    "README.md",
    "AGENTS.md",
    "llms.txt",
    "RECX.yaml",
    "docs/guide",
    "docs/reference/topics",
    "docs/reference/cli.md",
    "docs/design",
    "apps/desktop",
    "templates",
    "tools",
    "build_backend",
)

#: Pages written for a reader, where any absolute home path fails.
PROSE_ROOTS = (
    "README.md",
    "AGENTS.md",
    "llms.txt",
    "RECX.yaml",
    "docs/guide",
    "docs/reference/topics",
    "docs/reference/cli.md",
    "docs/design",
)

#: The word file's name, beside ``kinds.yml`` in the store.
WORD_FILE = "private-words.txt"

#: Never private, whatever the registry or the word file says (rule 5). Lowercase.
NEVER_PRIVATE = frozenset({"kyosei", "kyo", "recx"})

#: A component shorter than this never matches alone (rule 2): two- and three-letter
#: parts are abbreviations that occur everywhere.
MIN_COMPONENT = 4

#: The system word list (rule 2). Absent on some machines; then only COMMON_WORDS counts,
#: which makes the component rule stricter, never looser.
DICTIONARY = Path("/usr/share/dict/words")

#: Software vocabulary the system word list does not carry (rule 2).
_SOFTWARE_WORDS = """
    api apis app apps async auth backend bot cert certs cli config configs cron css
    dashboard demo dev devops docs frontend github gui html http https infra ios json
    kotlin linux macos mobile monorepo multi npm oauth pdf plugin plugins prod repo
    repos sdk sql ssh svg toml tui url urls webapp website yaml
"""
COMMON_WORDS = frozenset(_SOFTWARE_WORDS.split())

#: The environment as it was when this module was imported, before the conftest's
#: autouse ``store_home`` fixture points ``$KYOSEI_HOME`` at each test's tmp directory.
#: The guard reads the machine's registry, so it must see the machine's home.
_IMPORT_ENVIRON = dict(os.environ)

_HOME_PATH = re.compile(r"(?<![\w~.])/(?:Users|home)/([A-Za-z0-9._-]+)")


# ---------------------------------------------------------------------- terms


@dataclass(frozen=True)
class Terms:
    """What is private, as compiled matchers. Never printed."""

    names: tuple[re.Pattern[str], ...]
    components: tuple[re.Pattern[str], ...]
    prefixes: tuple[re.Pattern[str], ...]
    words: tuple[re.Pattern[str], ...]

    def kinds_on(self, line: str) -> list[str]:
        found = []
        for kind, patterns in (
            ("project name", self.names),
            ("name component", self.components),
            ("record prefix", self.prefixes),
            ("listed word", self.words),
        ):
            if any(p.search(line) for p in patterns):
                found.append(kind)
        return found


def common_words(dictionary: Path = DICTIONARY) -> frozenset[str]:
    try:
        text = dictionary.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return COMMON_WORDS
    return COMMON_WORDS | {w for w in (raw.strip() for raw in text.splitlines()) if w.islower()}


def _whole(term: str) -> re.Pattern[str]:
    body = "".join("[-_]" if ch in "-_" else re.escape(ch) for ch in term)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def compile_terms(
    entries: Iterable[tuple[str, str | None]],
    words: Iterable[str],
    *,
    common: frozenset[str],
    own: Iterable[str] = (),
) -> Terms:
    """``entries`` are ``(project name, record prefix or None)``; ``own`` are this
    project's name and prefix, never private (rule 5)."""
    never = NEVER_PRIVATE | {o.lower() for o in own if o}
    names: set[str] = set()
    components: set[str] = set()
    prefixes: set[str] = set()
    for name, prefix in entries:
        lowered = name.strip().lower()
        if lowered and lowered not in never:
            names.add(lowered)
            parts = [p for p in re.split(r"[-_.]+", lowered) if p]
            if len(parts) > 1:
                components.update(
                    p
                    for p in parts
                    if len(p) >= MIN_COMPONENT and p not in common and p not in never
                )
        if prefix and prefix.strip() and prefix.strip().lower() not in never:
            prefixes.add(prefix.strip())
    listed = {w.strip().lower() for w in words if w.strip() and w.strip().lower() not in never}
    return Terms(
        names=tuple(_whole(n) for n in sorted(names)),
        components=tuple(_whole(c) for c in sorted(components - names)),
        prefixes=tuple(
            re.compile(rf"(?<![A-Za-z0-9]){re.escape(p)}-\d+") for p in sorted(prefixes)
        ),
        words=tuple(_whole(w) for w in sorted(listed)),
    )


def read_word_file(path: Path) -> list[str]:
    words = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            words.append(line)
    return words


# ---------------------------------------------------------------------- sources


def own_layout():
    from kyosei.config import resolve_layout

    return resolve_layout(REPO_ROOT, required=False)


def word_file_path() -> Path | None:
    """``private-words.txt`` beside ``kinds.yml`` in this project's store, if configured."""
    from kyosei.config import config_path_for

    layout = own_layout()
    if not layout.configured:
        return None
    return config_path_for(layout.store_root).parent / WORD_FILE


def registry_entries(
    environ: dict[str, str] | None = None,
) -> tuple[list[tuple[str, str | None]], list[str], Path]:
    """``(other projects' (name, prefix), this project's name and prefix, registry path)``."""
    from kyosei.cmd_projects import registered, registry_path

    environ = _IMPORT_ENVIRON if environ is None else environ
    path = registry_path(environ)
    layout = own_layout()
    mine = {layout.code_root.resolve(), layout.store_root.resolve()}
    if layout.main_checkout is not None:
        mine.add(layout.main_checkout.resolve())
    others: list[tuple[str, str | None]] = []
    own: list[str] = []
    for row in registered(environ):
        root = Path(row.root).expanduser()
        basename = root.name
        if layout.configured and root.resolve() in mine:
            own.extend(t for t in (row.name, basename, row.prefix) if t)
            continue
        others.append((row.name, row.prefix))
        if basename.lower() != row.name.lower():
            others.append((basename, None))
    return others, own, path


# ---------------------------------------------------------------------- files


def _under(rel: str, roots: Iterable[str]) -> bool:
    return any(rel == r or rel.startswith(r.rstrip("/") + "/") for r in roots)


_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "dist", "build", "release", "__pycache__"})


def public_files(base: Path = REPO_ROOT, roots: Iterable[str] = ROOTS) -> list[str]:
    """Paths relative to ``base``: tracked, or new and not ignored. Outside a git
    checkout, every file under the roots except the usual build directories."""
    roots = tuple(roots)
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *roots],
            cwd=base,
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout.decode("utf-8")
        listed = sorted({p for p in out.split("\0") if p and (base / p).is_file()})
        if listed:
            return listed
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for root in roots:
        top = base / root
        candidates = [top] if top.is_file() else top.rglob("*")
        for path in candidates:
            if path.is_file() and not (_SKIP_DIRS & set(path.relative_to(base).parts)):
                found.append(path.relative_to(base).as_posix())
    return sorted(found)


def _text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan(base: Path, files: Iterable[str], terms: Terms) -> list[str]:
    """``path:line (kinds)`` for every line holding a private term; never the term."""
    hits = []
    for rel in files:
        text = _text(base / rel)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            kinds = terms.kinds_on(line)
            if kinds:
                hits.append(f"{rel}:{number} ({', '.join(kinds)})")
    return hits


def scan_home_paths(
    base: Path,
    files: Iterable[str],
    *,
    account: str,
    prose_roots: Iterable[str] = PROSE_ROOTS,
) -> list[str]:
    prose_roots = tuple(prose_roots)
    hits = []
    for rel in files:
        text = _text(base / rel)
        if text is None:
            continue
        strict = _under(rel, prose_roots)
        for number, line in enumerate(text.splitlines(), start=1):
            for match in _HOME_PATH.finditer(line):
                if strict or match.group(1) == account:
                    hits.append(f"{rel}:{number}")
                    break
    return hits


def _account() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


# ---------------------------------------------------------------------- the guard


def name_hits(
    environ: dict[str, str] | None = None, base: Path = REPO_ROOT
) -> tuple[list[str], Path]:
    """Lines under ``base`` naming a registered project or prefix; skips, saying why,
    when the registry lists no project but this one."""
    entries, own, path = registry_entries(environ)
    if not entries:
        pytest.skip(
            f"no project registry with another project in it is reachable at {path}, "
            "so there is no name to look for"
        )
    terms = compile_terms(entries, (), common=common_words(), own=own)
    return scan(base, public_files(base), terms), path


def word_hits(path: Path | None, base: Path = REPO_ROOT) -> list[str]:
    """Lines under ``base`` carrying a word from ``path``; skips, saying why, when the
    word file cannot be reached."""
    if path is None or not path.is_file():
        pytest.skip(f"no {WORD_FILE} is reachable (looked at {path or 'no configured store'})")
    _, own, _ = registry_entries()
    terms = compile_terms((), read_word_file(path), common=common_words(), own=own)
    return scan(base, public_files(base), terms)


def test_no_registered_project_name_or_record_prefix_in_the_public_tree() -> None:
    hits, path = name_hits()
    assert not hits, (
        f"the public tree names a project registered in {path} (the name is not printed); "
        "write 'a consumer project', or 'PROJ-n' for a record id, at: " + "; ".join(hits)
    )


def test_no_listed_private_word_in_the_public_tree() -> None:
    path = word_file_path()
    hits = word_hits(path)
    assert not hits, (
        f"the public tree carries a word listed in {path} (the word is not printed); "
        "rewrite the line generically at: " + "; ".join(hits)
    )


def test_no_home_path_in_the_public_tree() -> None:
    hits = scan_home_paths(REPO_ROOT, public_files(), account=_account())
    assert not hits, (
        "an absolute home path is in the public tree: on a page for a reader, write '~/' "
        "instead; in code or a test, use a placeholder account rather than this machine's. "
        "At: " + "; ".join(hits)
    )


# ---------------------------------------------------------------------- the mechanism


def _made_up(length: int = 9) -> str:
    """A name no dictionary carries, generated now so that nothing private is written."""
    return "q" + "".join(secrets.choice("bcdfghjklmnpqrstvwxz") for _ in range(length - 1))


def _write_registry(home: Path, projects: list[tuple[Path, str]]) -> dict[str, str]:
    """A temporary registry whose entries are initialised in-repo projects with the given
    record prefixes, and the environment that points at it."""
    entries = []
    for root, prefix in projects:
        (root / "kyosei").mkdir(parents=True)
        (root / "kyosei" / "config.yml").write_text(f"project_prefix: {prefix}\n", encoding="utf-8")
        entries.append({"root": str(root)})
    home.mkdir(parents=True, exist_ok=True)
    (home / "projects.json").write_text(json.dumps({"projects": entries}), encoding="utf-8")
    return {"KYOSEI_HOME": str(home)}


@pytest.mark.parametrize("root", ROOTS)
def test_a_registered_name_added_under_each_root_fails(tmp_path: Path, root: str) -> None:
    """Under every root, a line naming a project from a temporary registry is caught,
    and so is a record id with its prefix."""
    name = _made_up()
    prefix = _made_up(5).upper()
    environ = _write_registry(tmp_path / "home", [(tmp_path / "projects" / name, prefix)])
    entries, own, _ = registry_entries(environ)
    assert entries == [(name, prefix)]
    terms = compile_terms(entries, (), common=frozenset(), own=own)

    tree = tmp_path / "tree"
    is_file = root in (
        "README.md", "AGENTS.md", "llms.txt", "RECX.yaml", "docs/reference/cli.md"
    )
    target = tree / root if is_file else tree / root / "added.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"clean line\nsee {name.capitalize()} here\n{prefix}-12 too\n")
    rel = target.relative_to(tree).as_posix()
    assert public_files(tree) == [rel]
    assert scan(tree, public_files(tree), terms) == [
        f"{rel}:2 (project name)",
        f"{rel}:3 (record prefix)",
    ]


def test_matching_rules(tmp_path: Path) -> None:
    """Rules 1 to 5 of the module docstring, each with generated names."""
    private_part = _made_up(6)
    common_part = "harbor"  # an English word: in a hyphenated name, not private alone
    short_part = "zq"  # under MIN_COMPONENT: never matched alone
    hyphenated = f"{private_part}-{common_part}-{short_part}"
    bare_prefix = "SAIL"  # a prefix that is an English word
    word = _made_up(7)
    terms = compile_terms(
        [(hyphenated, bare_prefix), ("kyosei", "KYO"), ("recx", "RECX")],
        [word],
        common=frozenset({common_part}),
    )
    cases = {
        # rule 1: the whole name, either separator, any case (its private part with it)
        f"{hyphenated.upper()} is here": ["project name", "name component"],
        hyphenated.replace("-", "_"): ["project name", "name component"],
        f"x{hyphenated}": [],
        # rule 2: a private component alone matches; a common or short one does not
        f"the {private_part} app": ["name component"],
        f"a {common_part} view": [],
        f"the {short_part} flag": [],
        # rule 3: a prefix only as a record id, in its own case
        f"{bare_prefix}-42 shipped": ["record prefix"],
        f"{bare_prefix.lower()}-42 shipped": [],
        f"set {bare_prefix} and {bare_prefix.lower()}": [],
        # rule 4: a listed word, whole
        f"{word.upper()}!": ["listed word"],
        f"{word}s": [],
        # rule 5: never private
        "kyosei KYO-1 recx RECX-2": [],
    }
    for line, kinds in cases.items():
        assert terms.kinds_on(line) == kinds, line

    own = compile_terms([(hyphenated, bare_prefix)], [], common=frozenset(), own=[hyphenated])
    assert own.kinds_on(hyphenated) == []

    words = tmp_path / WORD_FILE
    words.write_text(f"# a comment\n{word}  # trailing\n\n", encoding="utf-8")
    assert read_word_file(words) == [word]
    found = compile_terms([], read_word_file(words), common=frozenset())
    assert found.kinds_on(f"see {word}") == ["listed word"]


def test_the_system_dictionary_makes_a_component_common(tmp_path: Path) -> None:
    dictionary = tmp_path / "words"
    dictionary.write_text("harbor\nHarbor\nZqlname\n", encoding="utf-8")
    common = common_words(dictionary)
    assert "harbor" in common and "zqlname" not in common
    assert common_words(tmp_path / "absent") == COMMON_WORDS


def test_each_check_skips_with_its_reason_where_its_source_is_unreachable(
    tmp_path: Path,
) -> None:
    """A machine with no registry (or only this project in it) and a store with no word
    file: the checks skip and name the file they looked for, rather than passing."""
    absent = {"KYOSEI_HOME": str(tmp_path / "no-home")}
    with pytest.raises(pytest.skip.Exception, match="no project registry"):
        name_hits(absent)
    with pytest.raises(pytest.skip.Exception, match=WORD_FILE):
        word_hits(tmp_path / WORD_FILE)
    with pytest.raises(pytest.skip.Exception, match="no configured store"):
        word_hits(None)


def test_a_reachable_word_file_is_applied(tmp_path: Path) -> None:
    word = _made_up(8)
    words = tmp_path / WORD_FILE
    words.write_text(f"# private words\n{word}\n", encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "docs" / "design").mkdir(parents=True)
    (tree / "docs" / "design" / "x.md").write_text(f"one\nthe {word.title()} menu\n")
    assert word_hits(words, base=tree) == ["docs/design/x.md:2 (listed word)"]


def test_the_word_file_is_found_beside_kinds_yml() -> None:
    path = word_file_path()
    if path is None:
        pytest.skip("this checkout has no configured Kyosei store")
    assert path.parent.name == "kyosei" and path.name == WORD_FILE
    assert (path.parent / "config.yml").is_file()


def test_the_guard_reads_the_machine_registry_not_the_tests_tmp_home() -> None:
    """The conftest points $KYOSEI_HOME at a tmp directory for every test; the guard
    reads the environment captured at import, which that redirection did not reach."""
    from kyosei.cmd_projects import registry_path

    assert registry_path(_IMPORT_ENVIRON) != registry_path(dict(os.environ))


def test_home_paths(tmp_path: Path) -> None:
    (tmp_path / "docs" / "guide").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs" / "guide" / "a.md").write_text("run /Users/someone/x\nor ~/x\n")
    (tmp_path / "tests" / "t.py").write_text('A = "/Users/someone/x"\nB = "/home/me/x"\n')
    files = ["docs/guide/a.md", "tests/t.py"]
    assert scan_home_paths(tmp_path, files, account="me") == ["docs/guide/a.md:1", "tests/t.py:2"]


def test_the_digest_list_is_gone() -> None:
    """KYO-628.c4: the published digest list is removed."""
    assert not (REPO_ROOT / "tests" / "private_names.sha256").exists()
