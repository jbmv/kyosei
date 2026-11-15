"""Scenario H of proposal KYO-950: the Config view, reading (KYO-953).

A project whose ``config.yml`` carries a comment of two lines above ``worker_model``, with a
record id in it, is served by the real ``kyo serve`` and shown by the check build. What can be
read from the page is asserted from it, and a screenshot of each state is put on the review
sheet for what is judged by eye:

- every section the page draws is made of what the served document lists, and no row is a
  setting the document does not list;
- the comment is shown whole and as written once it is opened, and the record id in it opens
  that record in Records;
- the harness then writes a value this build refuses into ``config.yml``, and the view, which
  is not reopened, shows the refusal the server states for the file, with no live control, and
  shows the file's own value again once the harness restores it: the view reads its documents
  again on the project's ``config`` change event.

No control changes anything in this build, so the scenario never presses one; that none is
enabled is asserted from the page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import harness
from lifecycle import Window, keep_failure

import conftest

#: The two lines of the comment, as written in the file and as the document must serve them.
COMMENT = [
    "# The model a dispatch names, since EMB-1 settled it.",
    '# A second line, kept as written: "quoted", {braces} and <tags>.',
]
SETTING = "worker_model: claude-sonnet-5-5"
REFUSED = "worker_model: [1, 2]"

#: The page's own reading of what is on it, as one object.
PAGE = """
const text = (element) => element ? element.textContent : null;
return {
  side: [...document.querySelectorAll('#config-side .config-section-button')]
    .map((button) => button.dataset.section),
  heads: [...document.querySelectorAll('#config-side h4')].map(text),
  title: text(document.querySelector('#config-page .config-title')),
  rows: [...document.querySelectorAll('#config-page .config-row')].map((row) => row.dataset.name),
  notes: [...document.querySelectorAll('#config-page .config-note')]
    .map((note) => [note.dataset.tone, note.textContent]),
  enabled: [...document.querySelectorAll('#config-page .config-control input,'
    + ' #config-page .config-control button')].filter((control) => !control.disabled).length,
};
"""

CHOOSE = (
    "const button = document.querySelector("
    "`#config-side .config-section-button[data-section='${key}']`);"
    "if (!button) return false; button.click(); return true;"
)


def _seed(run: harness.Run) -> tuple[Path, str]:
    """A project with a record, and a ``config.yml`` whose comment names it."""
    project = harness.make_project(run, "ember", "EMB")
    made = run.kyo("new", "task", "--title", "Ember reef record", "--json", cwd=project)
    record = json.loads(made)["id"]
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8").rstrip("\n")
        + "\n"
        + "\n".join(COMMENT).replace("EMB-1", record)
        + f"\n{SETTING}\n",
        encoding="utf-8",
    )
    harness.commit_all(run, project, "The ember project")
    return project, record


def _comment_above(config: Path, key: str) -> list[str]:
    """The comment lines directly above ``key`` in the file, read from the file itself: the
    lines the scaffold wrote there, a commented example among them, and the two this scenario
    added."""
    lines = config.read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith(f"{key}:"))
    above: list[str] = []
    while at > 0 and lines[at - 1].startswith("#"):
        at -= 1
        above.insert(0, lines[at])
    return above


def _read(window: Window) -> dict[str, Any]:
    return window.page.eval(PAGE)


def _document(server: harness.Server) -> dict[str, Any]:
    return server.get("config")


def _sections(window: Window) -> dict[str, dict[str, Any]]:
    """Each section of the side column pressed in turn: what the page draws under it."""
    drawn: dict[str, dict[str, Any]] = {}
    for key in _read(window)["side"]:
        window.page.eval(CHOOSE, key=key)
        window.page.wait(
            f"the section {key}",
            "return document.querySelector('#config-page .config-title') !== null"
            " && document.querySelector(`#config-side [data-section='${key}']`)"
            ".getAttribute('aria-current') === 'true';",
            key=key,
        )
        drawn[key] = _read(window)
    return drawn


def test_scenario_h_the_config_view(run: harness.Run, check_build: Path, results) -> None:
    project, record = _seed(run)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("H", "The Config view, reading", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server, project=server.name)
    scenario.app = app
    window = Window(app)
    config = project / "kyosei" / "config.yml"
    written = config.read_text(encoding="utf-8")
    try:
        window.loaded()
        window.make_key()
        window.page.wait(
            "the project to be the window's",
            "return document.getElementById('project-name').textContent === name;",
            name=server.name,
        )
        window.view("config")
        window.page.wait(
            "the Config view to draw the project",
            "return document.querySelectorAll('#config-side .config-section-button').length > 0;",
            deadline=30,
        )

        # H.1 the sections are drawn from the served documents.
        served = _document(server)
        settings = {entry["key"] for entry in served["settings"]}
        decisions = {e["title"] for e in served["decisions"] if e["class"] != "omitted"}
        drawn = _sections(window)
        project_keys = [key for key in drawn if key.startswith("project:")]
        rows = {name for key in project_keys for name in drawn[key]["rows"]}
        scenario.check(
            "H.sections",
            "The Config view draws the project's sections and the machine's, and every row of "
            "the project's sections is a setting or a decision of the document the server "
            "serves, each of them listed once: the page holds no list of keys.",
            bool(project_keys)
            and any(key.startswith("machine:") for key in drawn)
            and rows == settings | decisions
            and {"worker_model", "project_prefix"} <= rows,
            {"sections": list(drawn), "rows_missing": sorted((settings | decisions) - rows),
             "rows_extra": sorted(rows - (settings | decisions))},
            look="sections",
        )

        # H.2 a comment is shown verbatim, and the record id in it opens the record.
        window.page.eval(CHOOSE, key="project:workers")
        window.page.wait(
            "the worker_model row",
            "return !!document.querySelector(\"#config-page .config-row[data-name='worker_model']\");",
        )
        above = _comment_above(config, "worker_model")
        wanted = "\n".join(above)
        shown = window.page.eval(
            "const row = document.querySelector(\"#config-page .config-row[data-name='worker_model']\");"
            "const comment = row.querySelector('.config-comment');"
            "comment.open = true;"
            "return {text: row.querySelector('.config-comment-text').textContent,"
            " first: row.querySelector('.config-first').textContent,"
            " links: [...comment.querySelectorAll('a.ref')].map((a) => a.textContent),"
            " value: row.querySelector('.config-value').textContent,"
            " class: row.dataset.class};"
        )
        scenario.check(
            "H.comment",
            "The comment above worker_model, the scaffold's lines with a commented example and "
            "the two lines this scenario wrote, is shown exactly as the file has it, collapsed "
            "to its first line until it is opened, and the record id in it is a link.",
            len(above) > len(COMMENT)
            and above[-2:] == [line.replace("EMB-1", record) for line in COMMENT]
            and shown["text"] == wanted
            and shown["first"] == above[0]
            and shown["links"] == [record]
            and shown["value"] == "claude-sonnet-5-5"
            and shown["class"] == "editable",
            shown,
            look="comment",
        )
        window.page.eval(
            "document.querySelector('#config-page .config-comment a.ref').click();"
        )
        opened = window.page.wait(
            "the record to open in Records",
            "const article = document.getElementById('record');"
            "return document.body.classList.contains('view-records') && !article.hidden"
            " && article.textContent.includes(id) ? article.textContent.slice(0, 60) : null;",
            id=record,
        )
        scenario.check(
            "H.record-link",
            "Pressing the record id in the comment opens that record in Records.",
            bool(opened),
            opened,
            look="record-opened",
        )
        window.view("config")

        # H.3 a file the build refuses is shown with the server's refusal, the view reading
        # the document again on the config event, and no control is live.
        config.write_text(written.replace(SETTING, REFUSED), encoding="utf-8")
        refusal = server.get("config")["files"]["config"]["problem"]
        window.page.wait(
            "the view to show the refusal",
            "return [...document.querySelectorAll('#config-page .config-note[data-tone=problem]')]"
            ".some((note) => note.textContent.includes(refusal));",
            refusal=refusal,
            deadline=30,
        )
        window.page.eval(CHOOSE, key="project:workers")
        refused = _read(window)
        scenario.check(
            "H.refused",
            "A config.yml this build refuses is shown with the server's refusal of it, from "
            "the project's config change event and with the view not reopened; the rows are "
            "still drawn and no control is enabled.",
            bool(refusal)
            and any(tone == "problem" and refusal in said for tone, said in refused["notes"])
            and "worker_model" in refused["rows"]
            and refused["enabled"] == 0,
            {"refusal": refusal, "rows": refused["rows"], "enabled": refused["enabled"]},
            look="refused-file",
        )
        config.write_text(written, encoding="utf-8")
        window.page.wait(
            "the view to show the file as it was",
            "return !document.querySelector('#config-page .config-note[data-tone=problem]');",
            deadline=30,
        )
        scenario.check(
            "H.restored",
            "Once the file is as it was the refusal is gone, again without the view reopened.",
            True,
            "",
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        keep_failure("H", window, run, server)
        raise
    finally:
        config.write_text(written, encoding="utf-8")
        scenario.finish()
        results.measurements["scenario_H_seconds"] = scenario.seconds
        results.measurements["scenario_H_quit_status"] = app.quit()
        harness.forget_settings()
        failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
        assert not failed, "\n".join(failed)
        assert harness.leftover_check_builds() == []
