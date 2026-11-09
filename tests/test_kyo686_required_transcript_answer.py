"""KYO-686: a new project answers setup decision D14 before its store exists (KYO-685)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from kyosei import cmd_onboard, scaffold

from .conftest import git_init, invoke

ROUTES = {
    "defaults": ["--defaults"],
    "in-repo": ["--in-repo"],
    "store": ["--store", "{store}"],
    "adopt-with-no-store-there": ["--adopt"],
}


def _argv(route: str, code: Path, tmp_path: Path) -> list[str]:
    flags = [flag.format(store=tmp_path / "chosen") for flag in ROUTES[route]]
    return ["init", str(code), *flags]


def _config(store_root: Path) -> dict:
    return yaml.safe_load((store_root / "kyosei" / "config.yml").read_text(encoding="utf-8"))


def _item(result) -> dict:
    items = result.json["projects"][0]["items"]
    return next(i for i in items if i["label"] == "read_transcripts")


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_that_creates_a_store_refuses_without_the_answer(
    route: str, tmp_path: Path, store_home: Path
) -> None:
    """c1: names the flag and what it controls, and leaves no file anywhere."""
    code = tmp_path / "proj"
    code.mkdir()
    refused = invoke(_argv(route, code, tmp_path), transcript_answer=None)
    assert refused.code != 0
    for words in ("--read-transcripts yes", "--read-transcripts no", "transcripts", "D14"):
        assert words in refused.err, refused.err
    assert "wrote nothing" in refused.err
    assert list(code.iterdir()) == []
    assert not (tmp_path / "chosen").exists()
    assert not store_home.exists() or not list(store_home.iterdir())


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(("answer", "stored"), [("yes", True), ("no", False)])
def test_supplying_the_flag_clears_the_refusal_and_is_written_to_config(
    route: str, answer: str, stored: bool, tmp_path: Path
) -> None:
    """c1's remedy and c2: each value reaches the new project's config.yml, and the split
    route's second write of that file (the code-repository identity) keeps it."""
    code = tmp_path / "proj"
    code.mkdir()
    if route != "in-repo":
        (code / "main.py").write_text("x = 1\n", encoding="utf-8")
        git_init(code)
    argv = [*_argv(route, code, tmp_path), "--read-transcripts", answer, "--json"]
    made = invoke(argv, transcript_answer=None)
    assert made.code == 0, made
    assert _config(Path(made.json["store_root"]))["read_transcripts"] is stored


def test_a_value_other_than_yes_or_no_is_refused(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    refused = invoke(["init", str(code), "--in-repo", "--read-transcripts", "maybe"])
    assert refused.code != 0 and "--read-transcripts" in refused.err
    assert not code.exists()


def test_init_layout_refuses_a_caller_that_gives_no_answer(tmp_path: Path) -> None:
    """The refusal is in the scaffold, so a caller other than the CLI cannot skip it."""
    code = tmp_path / "proj"
    with pytest.raises(Exception, match="--read-transcripts"):
        scaffold.init_layout(code, in_repo=True)
    assert not code.exists()


def test_a_second_checkout_linking_an_existing_store_is_not_asked(tmp_path: Path) -> None:
    """No store is created, so the refusal does not apply and the store's answer stands."""
    origin = tmp_path / "one" / "proj"
    origin.mkdir(parents=True)
    (origin / "main.py").write_text("x = 1\n", encoding="utf-8")
    git_init(origin)
    made = invoke(["init", "--defaults", str(origin), "--read-transcripts", "yes", "--json"])
    assert made.code == 0, made
    clone = tmp_path / "two" / "proj"
    clone.parent.mkdir()


    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    linked = invoke(["init", "--defaults", str(clone), "--json"], transcript_answer=None)
    assert linked.code == 0, linked
    assert linked.json["adopted"] is True
    assert _config(Path(made.json["store_root"]))["read_transcripts"] is True


def test_a_flag_given_over_an_existing_store_says_it_was_not_applied(tmp_path: Path) -> None:
    origin = tmp_path / "one" / "proj"
    origin.mkdir(parents=True)
    (origin / "main.py").write_text("x = 1\n", encoding="utf-8")
    git_init(origin)
    made = invoke(["init", "--defaults", str(origin), "--read-transcripts", "yes", "--json"])
    store = Path(made.json["store_root"])
    clone = tmp_path / "two" / "proj"
    clone.parent.mkdir()


    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    linked = invoke(["init", "--defaults", str(clone), "--read-transcripts", "no"])
    assert linked.code == 0, linked
    assert "--read-transcripts was not applied" in linked.out
    assert _config(store)["read_transcripts"] is True


def test_a_project_with_no_answer_is_not_refused_anything(project: Path) -> None:
    """A store from before this change: no key, and the commands still run."""
    path = project / "kyosei" / "config.yml"
    raw = _config(project)
    del raw["read_transcripts"]
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert invoke(["--root", str(project), "doctor"]).code in (0, 1)
    assert invoke(["--root", str(project), "list"]).code == 0


def test_bare_init_names_the_question_and_carries_a_given_answer(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    code.mkdir()
    err = invoke(["init", str(code)], transcript_answer=None).err
    assert "`kyo init --defaults --read-transcripts <yes|no>" in err
    assert "`kyo init --in-repo --read-transcripts <yes|no>" in err
    carried = invoke(["init", str(code), "--read-transcripts", "yes"], transcript_answer=None).err
    assert "`kyo init --in-repo --read-transcripts yes" in carried


def test_d14_recorded_as_declined_or_deferred_stays_missing(project: Path) -> None:
    """c3: `onboarding:` cannot stand in for the answer."""
    path = project / "kyosei" / "config.yml"
    for status in ("declined", "deferred"):
        raw = _config(project)
        raw.pop("read_transcripts", None)
        raw["onboarding"] = {"D14": status}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        result = invoke(["--root", str(project), "onboard", "--check", "--json"])
        item = _item(result)
        assert item["status"] == "missing", item
        assert "cannot be declined or deferred" in item["fix"]
    raw = _config(project)
    raw["read_transcripts"] = False
    del raw["onboarding"]
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = invoke(["--root", str(project), "onboard", "--check", "--json"])
    item = _item(result)
    assert item["status"] == "present"


def test_the_prompt_asks_d14_before_init_with_no_default() -> None:
    """c3: round one, because Step 4's `kyo init` needs the answer."""
    prompt = cmd_onboard.packaged_prompt_text()
    start = prompt.index("**[D14]")
    assert prompt.index("## Step 3") < start < prompt.index("## Step 4")
    entry = prompt[start : prompt.index("Do not ask these; do them or state them:")]
    assert "Default" not in entry
    assert "do not record it as declined or deferred" in " ".join(entry.split())
    step = prompt[prompt.index("## Step 4") : prompt.index("## Step 5")]
    assert step.count("--read-transcripts <yes|no>") >= 2


def test_the_guide_gives_d14_no_default() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "docs/guide/setup-decisions.md").read_text(encoding="utf-8")
    section = text[text.index("### D14.") : text.index("## E. The machine")]
    assert "**Default:** none" in " ".join(section.split())
    assert "cannot be recorded as declined or deferred" in " ".join(section.split())
