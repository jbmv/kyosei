// The promote-to-idea dialog (KYO-252): a selection of the scratchpad becomes an idea
// FILE, never a record.
//
// Promotion goes to an idea, never a record: only the controller promotes to a record.
// One control does it -- select text and promote -- and the dialog asks for the idea's
// name and folder and whether to remove the text from the scratchpad. On save it copies
// the idea's relative path, since the usual next step is to paste it into the
// controller's chat.
//
// So: a name, a folder chosen from the vault's own folders, a checkbox that is on by
// default, and on save the path relative to the project root goes to the clipboard and
// the dialog says so. The write is `createIdea` -- the same main-process path the
// ideas list's "New idea here…" uses, behind the same guard -- and what happens to the
// scratchpad's buffer afterwards is the caller's (`index.ts`), because only the caller
// holds the editor.

import type { KyoseiBridge, WriteResult } from "../../../shared/protocol";
import { el } from "../../dom";

export interface IdeaDialogOptions {
  bridge: KyoseiBridge;
  /** The selection being promoted. */
  text: string;
  /** A starting point for the name: the selection's first line. */
  suggestedName: string;
  /** The vault's folders, "" for the root first. */
  folders: string[];
  /** The write succeeded; `remove` is what the checkbox said. The dialog is closed. */
  onSaved(result: WriteResult & { relativePath: string }, remove: boolean): void;
  onCancel(): void;
}

/** A file name from a line of prose: lowercase, words joined by dashes, nothing else. */
export function nameFrom(line: string): string {
  return line
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export function openIdeaDialog(host: HTMLElement, options: IdeaDialogOptions): void {
  const { bridge } = options;
  const nameInput = el("input", {
    class: "promote-title",
    data: { region: "idea-name" },
    attrs: { type: "text", placeholder: "file name (.md is added)" },
  });
  nameInput.value = options.suggestedName;
  const folderSelect = el("select", { class: "promote-kind", data: { region: "idea-folder" } });
  for (const folder of options.folders) {
    folderSelect.append(
      el("option", {
        text: folder === "" ? "ideas/ (root)" : `${folder}/`,
        attrs: { value: folder },
      }),
    );
  }
  const removeBox = el("input", {
    class: "idea-remove",
    data: { region: "idea-remove" },
    attrs: { type: "checkbox" },
  });
  removeBox.checked = true;
  const resultBox = el("div", {
    class: "promote-result",
    data: { region: "idea-result", state: "none" },
  });
  const saveButton = el("button", {
    class: "promote-submit",
    data: { region: "idea-save", action: "idea-save" },
    attrs: { type: "button" },
    text: "Save idea",
  });
  const cancelButton = el("button", {
    class: "promote-cancel",
    data: { region: "idea-cancel", action: "idea-cancel" },
    attrs: { type: "button" },
    text: "Cancel",
  });
  const dialog = el(
    "div",
    { class: "promote-dialog", data: { region: "idea-dialog" }, attrs: { role: "dialog" } },
    [
      el("h2", { text: "Promote selection to an idea" }),
      el("label", { class: "promote-field", data: { field: "name" } }, [
        el("span", { text: "Name" }),
        nameInput,
      ]),
      el("label", { class: "promote-field", data: { field: "folder" } }, [
        el("span", { text: "Folder" }),
        folderSelect,
      ]),
      el("label", { class: "promote-check", data: { field: "remove" } }, [
        removeBox,
        el("span", { text: "Remove the promoted text from the scratchpad" }),
      ]),
      el("pre", { class: "promote-preview", data: { region: "idea-preview" }, text: options.text }),
      resultBox,
      el("div", { class: "promote-actions" }, [cancelButton, saveButton]),
    ],
  );
  const overlay = el("div", { class: "promote-overlay", data: { region: "idea-overlay" } }, [
    dialog,
  ]);
  host.append(overlay);

  let saving = false;
  const close = (): void => {
    overlay.remove();
  };
  cancelButton.addEventListener("click", () => {
    if (saving) return;
    close();
    options.onCancel();
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay && !saving) {
      close();
      options.onCancel();
    }
  });
  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") void save();
    if (event.key === "Escape" && !saving) {
      close();
      options.onCancel();
    }
  });
  saveButton.addEventListener("click", () => void save());

  async function save(): Promise<void> {
    if (saving) return;
    const name = nameInput.value.trim();
    if (name === "") {
      resultBox.dataset.state = "refused";
      resultBox.textContent = "The idea needs a name.";
      return;
    }
    saving = true;
    saveButton.setAttribute("aria-disabled", "true");
    resultBox.dataset.state = "pending";
    resultBox.textContent = "Writing…";
    const folder = folderSelect.value;
    let result: WriteResult;
    try {
      result = await bridge.createIdea({ folder, name, text: options.text });
    } catch (error) {
      saving = false;
      saveButton.removeAttribute("aria-disabled");
      resultBox.dataset.state = "refused";
      resultBox.textContent = `kyosei: ${String(error)}`;
      return;
    }
    if (!result.ok || result.path === null) {
      saving = false;
      saveButton.removeAttribute("aria-disabled");
      resultBox.dataset.state = "refused";
      resultBox.textContent = result.problem ?? "nothing was written";
      return;
    }
    // The path as the reader will paste it: relative to the project root, which is
    // where a controller runs `kyo` and reads `ideas/...` from.
    const relativePath = await relativeToProject(bridge, result.path);
    await bridge.copyText(relativePath);
    resultBox.dataset.state = "ok";
    resultBox.textContent = `Saved ${relativePath} — path copied to the clipboard.`;
    close();
    options.onSaved({ ...result, relativePath }, removeBox.checked);
  }

  nameInput.focus();
  nameInput.select();
}

/** `path` relative to the open project's root; the absolute path when it is not under it. */
async function relativeToProject(bridge: KyoseiBridge, path: string): Promise<string> {
  try {
    const facts = await bridge.getFacts();
    const root = facts.project.root.replace(/\/+$/, "");
    if (path.startsWith(`${root}/`)) return path.slice(root.length + 1);
  } catch {
    // Facts unavailable: the absolute path is still a true answer.
  }
  return path;
}
