"""KYO-142: two registries shipped one field name over two glob dialects.

``scopes.yml`` and ``documents.yml`` both had a field called ``paths:``. They hold
patterns in different dialects, because they do different jobs: a scope *matches* a
path a record already declared (``fnmatch``), while a document set must *enumerate* the
files that exist (``pathlib``), and ``fnmatch`` cannot enumerate without walking the
tree itself. That divergence is forced and is not what this record changed.

What it changed is that the two fields were indistinguishable by name, so the single
most likely consumer action — copying a line from one file into the other, two files
from the same registry family — produced no error and a **different matched set**.

The fix is that the dialect owns the spelling: :data:`registry.MATCH` is ``paths:``,
:data:`registry.ENUMERATE` is ``globs:``, and a dimension cannot spell either any other
way. Three things are therefore asserted here, and each maps to one criterion:

* **c1** — a copied line is refused, in both directions, rather than matching
  differently (:func:`test_a_documents_line_copied_into_scopes_is_refused` and its
  mirror), and the refusal's named remedy actually clears it.
* **c2** — the refusal states the match-versus-enumerate reason, and so does each
  packaged registry file, so a reader meets it wherever they meet the field.
* **c3** — the rule lives on :class:`registry.Dimension` rather than in two files, so
  the *next* registry with a path-shaped field inherits it
  (:func:`test_a_new_dimension_cannot_spell_a_path_shaped_field_its_own_way`).

:func:`test_the_two_dialects_diverge_on_ordinary_patterns` is the measurement that
decided the shape of the fix. It is the reason this is a rename and not a validator
over the patterns themselves: the patterns that diverge include the two this tool ships
as its own defaults, so a check refusing "a pattern whose meaning would change in the
other file" would refuse substantially every useful pattern — KYO-33's routine false
positive, which is the check people switch off.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

from kyosei import contracts as contracts_module
from kyosei import kinds as kinds_module
from kyosei import registry as registry_module
from kyosei.registry import ENUMERATE, MATCH, Dimension, RegistryError
from kyosei.scaffold import defaults_dir

# The two files this record is about, and the one line a consumer is most likely to
# move between them. `docs/**/*.md` is not a corner case: it is the shipped default and
# the example in documents.yml's own commented block.
COPIED_LINE = "docs/**/*.md"


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------- the measurement


def test_the_two_dialects_diverge_on_ordinary_patterns(tmp_path: Path) -> None:
    """The trap is real, and it is not confined to exotic patterns.

    This is the evidence behind rejecting a validator over the patterns themselves.
    Every pattern below selects a different set under the two dialects, and the list
    includes both members of :data:`registry.DEFAULT_DOCUMENT_GLOBS` — the patterns
    kyosei itself ships. A check that refused ambiguous patterns would refuse its own
    defaults, so the ambiguity had to be addressed by the field's *name* instead.
    """
    for rel in (
        "a.md",
        "docs/top.md",
        "docs/sub/mid.md",
        "docs/sub/deep/low.md",
        "src/server/x.ts",
        "src/server/main/index.ts",
    ):
        _write(tmp_path / rel, "x\n")
    every = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())

    for pattern in (*registry_module.DEFAULT_DOCUMENT_GLOBS, "src/server/*"):
        enumerated = sorted(
            str(p.relative_to(tmp_path)) for p in tmp_path.glob(pattern) if p.is_file()
        )
        matched = sorted(p for p in every if fnmatch.fnmatchcase(p, pattern))
        assert enumerated != matched, (
            f"{pattern!r} selects the same set under both dialects; if that became true "
            "of every pattern, this record's trap would not exist and the two field "
            "names could be reunited"
        )

    # The specific silence this record was filed about, stated as the two directions a
    # reader will actually hit.
    assert "docs/top.md" in {str(p.relative_to(tmp_path)) for p in tmp_path.glob(COPIED_LINE)}
    assert not fnmatch.fnmatchcase("docs/top.md", COPIED_LINE)


# ------------------------------------------------- c1: the copied line is refused


def test_a_documents_line_copied_into_scopes_is_refused(tmp_path: Path) -> None:
    """`globs:` in scopes.yml stops rather than silently matching a different set."""
    _write(
        tmp_path / "scopes.yml",
        f"scopes:\n  server:\n    summary: The API service.\n    globs:\n      - {COPIED_LINE}\n",
    )
    with pytest.raises(RegistryError) as caught:
        registry_module.load(tmp_path, registry_module.SCOPE)
    assert "unknown key(s) globs" in str(caught.value)


def test_a_scopes_line_copied_into_documents_is_refused(tmp_path: Path) -> None:
    """`paths:` in documents.yml stops. It was the required key until KYO-142, so
    before the rename this file loaded cleanly and enumerated a different set."""
    _write(
        tmp_path / "documents.yml",
        "documents:\n"
        "  design:\n"
        "    summary: The design record.\n"
        "    paths:\n"
        f"      - {COPIED_LINE}\n",
    )
    with pytest.raises(RegistryError) as caught:
        registry_module.load(tmp_path, registry_module.DOCUMENT)
    message = str(caught.value)
    assert "unknown key(s) paths" in message
    # The required key is missing too, and law 6 says both arrive in one pass.
    assert "globs is required" in message


@pytest.mark.parametrize(
    ("dimension", "wrong_key", "right_key"),
    [
        (registry_module.SCOPE, "globs", "paths"),
        (registry_module.DOCUMENT, "paths", "globs"),
    ],
)
def test_the_refusal_names_both_dialects_and_the_reason_they_differ(
    tmp_path: Path, dimension: Dimension, wrong_key: str, right_key: str
) -> None:
    """c2, at the moment a reader is certainly thinking about it.

    Being refused is not enough on its own: a bare "unknown key" would leave the reader
    to guess whether the two fields are the same thing spelled twice. The message has to
    carry the match-versus-enumerate distinction and say which file does which.
    """
    _write(
        tmp_path / dimension.filename,
        f"{dimension.plural}:\n"
        "  thing:\n"
        "    summary: A thing.\n"
        f"    {wrong_key}:\n"
        f"      - {COPIED_LINE}\n",
    )
    with pytest.raises(RegistryError) as caught:
        registry_module.load(tmp_path, dimension)
    message = str(caught.value)

    assert "enumerates the files that exist" in message
    assert "matches paths a record already declared" in message
    assert "fnmatch" in message and "pathlib" in message
    assert "selects a different set" in message
    # And the remedy, named as a key the reader can type.
    assert f"rename the key to `{right_key}:`" in message


@pytest.mark.parametrize(
    ("dimension", "wrong_key", "right_key"),
    [
        (registry_module.SCOPE, "globs", "paths"),
        (registry_module.DOCUMENT, "paths", "globs"),
    ],
)
def test_the_remedy_the_refusal_names_clears_the_refusal(
    tmp_path: Path, dimension: Dimension, wrong_key: str, right_key: str
) -> None:
    """The project's standing rule: an error naming a remedy ships with a test proving
    the remedy works. Rename the key, and the same file loads."""
    target = tmp_path / dimension.filename
    body = (
        f"{dimension.plural}:\n"
        "  thing:\n"
        "    summary: A thing.\n"
        "    {key}:\n"
        f"      - {COPIED_LINE}\n"
    )
    _write(target, body.format(key=wrong_key))
    with pytest.raises(RegistryError):
        registry_module.load(tmp_path, dimension)

    _write(target, body.format(key=right_key))
    loaded = registry_module.load(tmp_path, dimension)
    assert loaded["thing"].get(right_key) == [COPIED_LINE]


# ------------------------------------------ c3: the rule outlives these two files


def test_a_new_dimension_cannot_spell_a_path_shaped_field_its_own_way() -> None:
    """c3 — "applies to the next registry that ships a path-shaped field".

    The mechanism this asserts is :meth:`registry.Dimension.__post_init__`. If it were
    deleted, every case below would construct happily and the collision would be
    spellable again — by a third name for an existing dialect, or by a familiar name
    over an unfamiliar dialect, which is worse because the name then lies.

    It fires at construction, on this project's own declarations, so it costs a
    consumer nothing: a project cannot declare a dimension.
    """
    bad = [
        # A dialect's validator under a name of the dimension's own choosing.
        {"required_fields": {"files": registry_module.relative_globs}},
        {"optional_fields": {"sources": registry_module.match_globs}},
        # A dialect's reserved name over some other validator — the name would lie.
        {"optional_fields": {"paths": registry_module.string_list()}},
        {"required_fields": {"globs": registry_module.string_list()}},
        # The two dialects, swapped.
        {"optional_fields": {"globs": registry_module.match_globs}},
        {"required_fields": {"paths": registry_module.relative_globs}},
    ]
    for kwargs in bad:
        with pytest.raises(RegistryError) as caught:
            Dimension(singular="thing", plural="things", what="a thing", **kwargs)
        assert "KYO-142" in str(caught.value)

    # And the two legal spellings still build, or the rule would forbid the shipped
    # dimensions it exists to protect.
    Dimension(
        singular="thing",
        plural="things",
        what="a thing",
        optional_fields={"paths": registry_module.match_globs},
    )
    Dimension(
        singular="thing",
        plural="things",
        what="a thing",
        required_fields={"globs": registry_module.relative_globs},
    )


def test_every_shipped_dimension_obeys_the_rule() -> None:
    """The rule is only worth anything if the dimensions that exist satisfy it, and it
    is the fifth that will be added carelessly, not the first four."""
    shipped = [
        kinds_module.KIND,
        contracts_module.STATUS,
        registry_module.SCOPE,
        registry_module.GATE_TAG,
        registry_module.DOCUMENT,
    ]
    path_shaped = {}
    for dimension in shipped:
        fields = {**dimension.required_fields, **dimension.optional_fields}
        for field_name, validator in fields.items():
            for dialect in (MATCH, ENUMERATE):
                if validator is dialect.validator:
                    assert field_name == dialect.field_name
                    path_shaped[dimension.plural] = field_name

    # The two this record is about, spelled apart — the state c1 asks for, asserted on
    # the shipped dimensions rather than on a fixture.
    assert path_shaped["scopes"] == "paths"
    assert path_shaped["documents"] == "globs"
    assert path_shaped["scopes"] != path_shaped["documents"]


# ------------------------------- c2: the reason reaches a reader of either file


@pytest.mark.parametrize("filename", ["scopes.yml", "documents.yml"])
def test_each_packaged_registry_states_the_reason_the_dialects_differ(
    filename: str,
) -> None:
    """c2 asks for the reason "where a reader of either file meets it" — so it is
    asserted on the packaged asset a project actually receives, not on a docstring."""
    text = (defaults_dir() / filename).read_text(encoding="utf-8")
    lowered = text.lower()

    # The two jobs, which is the reason the dialects cannot be unified.
    assert "enumerat" in lowered
    assert "match" in lowered
    # Both dialects named, in both files.
    assert "fnmatch" in lowered and "pathlib" in lowered
    # Both field names, in both files, so a reader of either learns the other exists.
    assert "`paths:`" in text and "`globs:`" in text
    # And why they are spelled differently rather than merely being different.
    assert "KYO-142" in text
