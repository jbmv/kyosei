"""Scenario I of proposal KYO-950: the Config view, editing (KYO-959).

A project whose ``config.yml`` carries ``worker_model`` is served by the real ``kyo serve`` to a
device token that holds the ``config`` permission, issued inside the run's own ``KYOSEI_HOME``.
The check build's Config view is driven through its controls, and each result is read from the
file, from the write log and from the page:

- ``worker_model`` changed in the view is written, every other line of the file is as it was,
  the log names the device, and the row shows the new value and its latest change without the
  view reopened;
- a change the writer refuses (``docs_coverage.level: problem`` without a ``code:`` list) is
  printed under its control in the server's own sentence, and the control stays at the file's
  value;
- a write made after the harness changed the file, with the view not yet told of it, is a 412:
  the row says what the value now is and keeps the attempted value in its field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import harness
from lifecycle import Window, keep_failure

import conftest

WORKER = "worker_model: claude-sonnet-5-5"
NEW_MODEL = "claude-opus-5-5"
ELSEWHERE = "claude-haiku-5-0"
TRIED = "claude-sonnet-5-6"

#: A button of a row, by its label.
PRESS = (
    "const row = document.querySelector(`#config-page .config-row[data-name='${name}']`);"
    "const button = [...row.querySelectorAll('.config-control button')]"
    ".find((entry) => entry.textContent === label);"
    "if (!button || button.disabled) return false; button.click(); return true;"
)

#: Type into a row's text field, as a person does: the value and an input event.
TYPE = (
    "const row = document.querySelector(`#config-page .config-row[data-name='${name}']`);"
    "const input = row.querySelector('.config-control input');"
    "input.value = text; input.dispatchEvent(new Event('input')); return true;"
)

ROW = (
    "const row = document.querySelector(`#config-page .config-row[data-name='${name}']`);"
    "if (!row) return null;"
    "const text = (selector) => row.querySelector(selector)?.textContent ?? null;"
    "return {value: text('.config-value-text'), last: text('.config-last'),"
    " refusal: text('.config-refusal'), field: row.querySelector('.config-control input')?.value ?? null,"
    " pressed: [...row.querySelectorAll('.config-control .chip[aria-pressed=true]')]"
    ".map((chip) => chip.textContent), disabled: [...row.querySelectorAll('.config-control input,"
    " .config-control button')].filter((c) => c.disabled).length};"
)


def _seed(run: harness.Run) -> Path:
    project = harness.make_project(run, "tarn", "TRN")
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8").rstrip("\n")
        + "\n# The model a dispatch names, kept as written.\n"
        + WORKER
        + "\n",
        encoding="utf-8",
    )
    harness.commit_all(run, project, "The tarn project")
    return project


def _show_row(window: Window, name: str) -> dict[str, Any]:
    """Turn to the section that holds the row ``name`` and wait for the row."""
    for key in window.page.eval(
        "return [...document.querySelectorAll('#config-side .config-section-button')]"
        ".map((b) => b.dataset.section).filter((k) => k.startsWith('project:'));"
    ):
        window.page.eval(
            "document.querySelector(`#config-side [data-section='${key}']`).click();", key=key
        )
        if window.page.eval(ROW, name=name) is not None:
            return window.page.eval(ROW, name=name)
    raise harness.CheckError(f"no section of the Config view holds the row {name}")


def _log(run: harness.Run) -> list[dict[str, Any]]:
    log = run.kyosei_home / "config-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def test_scenario_i_editing(run: harness.Run, check_build: Path, results) -> None:
    project = _seed(run)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("I", "The Config view, editing", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server, project=server.name)
    scenario.app = app
    window = Window(app)
    config = project / "kyosei" / "config.yml"
    original = config.read_text(encoding="utf-8")
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
        _show_row(window, "worker_model")
        window.page.wait(
            "the model field to be live",
            "const row = document.querySelector(\"#config-page .config-row[data-name='worker_model']\");"
            "const input = row?.querySelector('.config-control input');"
            "return input && !input.disabled;",
            deadline=30,
        )

        # I.1 a change is written, with every other line as it was, and logged to the device.
        window.page.eval(TYPE, name="worker_model", text=NEW_MODEL)
        assert window.page.eval(PRESS, name="worker_model", label="Save")
        harness.poll(
            "the file to carry the new model",
            lambda: NEW_MODEL in config.read_text(encoding="utf-8"),
            deadline=30,
        )
        written = config.read_text(encoding="utf-8")
        window.page.wait(
            "the row to show the new value and its latest change",
            "const row = document.querySelector(\"#config-page .config-row[data-name='worker_model']\");"
            "return row?.querySelector('.config-value-text')?.textContent === model"
            " && !!row.querySelector('.config-last');",
            model=NEW_MODEL,
            deadline=30,
        )
        row = window.page.eval(ROW, name="worker_model")
        rows = [r for r in _log(run) if r.get("key") == "worker_model" and r.get("outcome") == "written"]
        scenario.check(
            "I.write",
            "worker_model changed in the view is written to config.yml with every other line "
            "of the file unchanged, a row of the write log names the device, and the row shows "
            "the new value and its latest change without the view reopened.",
            written == f"worker_model: {NEW_MODEL}\n".join(original.rsplit(WORKER + "\n", 1))
            and bool(rows)
            and rows[-1].get("device") == "check-harness"
            and "check-harness" in (row["last"] or ""),
            {"log": rows[-1:] if rows else [], "row": row},
            look="written",
        )

        # I.2 a refusal of the writer is printed under its control, in the server's sentence.
        refusal = server.ask(
            "POST", "config/docs_coverage.level", {"value": "problem"},
            headers={"If-Match": server.get("config")["files"]["config"]["etag"]},
        )
        sentence = refusal[1]["error"] if refusal[0] == 422 else None
        before = config.read_text(encoding="utf-8")
        _show_row(window, "docs_coverage.level")
        window.page.wait(
            "the level control to be live",
            "const row = document.querySelector(\"#config-page .config-row[data-name='docs_coverage.level']\");"
            "return row && row.querySelector('.config-control .chip') && !row.querySelector('.config-control .chip').disabled;",
            deadline=30,
        )
        assert window.page.eval(PRESS, name="docs_coverage.level", label="problem")
        window.page.wait(
            "the refusal under the control",
            "return !!document.querySelector(\"#config-page .config-row[data-name='docs_coverage.level'] .config-refusal\");",
            deadline=30,
        )
        level = window.page.eval(ROW, name="docs_coverage.level")
        scenario.check(
            "I.refused",
            "A change the writer refuses, docs_coverage.level problem without a code list, is "
            "printed under its control in the server's own sentence, the file is unchanged and "
            "the control is not pressed on the refused value.",
            bool(sentence)
            and level["refusal"] == sentence
            and "problem" not in level["pressed"]
            and config.read_text(encoding="utf-8") == before,
            {"sentence": sentence, "row": level},
            look="refused-write",
        )

        # I.3 a write after the file changed, the view not told: 412, and what is typed stays.
        _show_row(window, "worker_model")
        window.page.eval(
            "window.kyoCheckRead = window.kyo.configRead; window.kyo.configRead = () => {};"
        )
        config.write_text(before.replace(f"worker_model: {NEW_MODEL}", f"worker_model: {ELSEWHERE}"), encoding="utf-8")
        window.page.eval(TYPE, name="worker_model", text=TRIED)
        assert window.page.eval(PRESS, name="worker_model", label="Save")
        window.page.wait(
            "the row to say the file changed",
            "const row = document.querySelector(\"#config-page .config-row[data-name='worker_model']\");"
            "return !!row?.querySelector('.config-refusal');",
            deadline=30,
        )
        window.page.eval(
            "window.kyo.configRead = window.kyoCheckRead; window.kyo.configChanged({kind: 'config'});"
        )
        window.page.wait(
            "the view to read the file again",
            "const row = document.querySelector(\"#config-page .config-row[data-name='worker_model']\");"
            "return row?.querySelector('.config-value-text')?.textContent === model;",
            model=ELSEWHERE,
            deadline=30,
        )
        changed = window.page.eval(ROW, name="worker_model")
        scenario.check(
            "I.changed",
            "A write made after the file changed is refused with 412: the row says it changed "
            "elsewhere and what it now reads, the attempted value stays in the field, and "
            "nothing was written over the other change.",
            "Changed elsewhere" in (changed["refusal"] or "")
            and ELSEWHERE in changed["refusal"]
            and changed["field"] == TRIED
            and changed["value"] == ELSEWHERE
            and ELSEWHERE in config.read_text(encoding="utf-8"),
            changed,
            look="changed-elsewhere",
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        keep_failure("I", window, run, server)
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_I_seconds"] = scenario.seconds
        results.measurements["scenario_I_quit_status"] = app.quit()
        harness.forget_settings()
        failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
        assert not failed, "\n".join(failed)
        assert harness.leftover_check_builds() == []
