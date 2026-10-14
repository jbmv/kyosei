// Tiny DOM helpers. The v3 shell is static markup driven by a small model -- a rail,
// a sidebar and one to three columns of tabs -- so a UI framework would be dependency
// weight with nothing to pay for it.

export type Child = Node | string | null | undefined | false;

export interface ElementOptions {
  class?: string;
  text?: string;
  /**
   * The control's own explanation, written to `data-tip`.
   *
   * Deliberately NOT the native `title` attribute (KYO-122.c2). Chromium draws its own
   * tooltip from `title` on its own schedule, so a surface that also draws one gets
   * two: the app's, fast, and then the browser's a second later, on top of it. The
   * shipped v2 app did exactly that. The field is spelled `tip`
   * so a call site cannot go back to the native one without TypeScript saying so.
   */
  tip?: string;
  style?: Partial<CSSStyleDeclaration>;
  /**
   * `data-*` attributes, written without the `data-` prefix.
   *
   * camelCase keys become hyphenated, exactly as `element.dataset` does: `tabKind`
   * lands as `data-tab-kind`. Without that conversion `setAttribute` would write
   * `data-tabKind`, HTML would lowercase it to `data-tabkind`, and every selector
   * spelled the obvious way -- in this app and in the checks -- would silently match
   * nothing. Measured: the first run of `shell.spec.ts` failed sixteen checks on it.
   */
  data?: Record<string, string | number>;
  attrs?: Record<string, string>;
  on?: Partial<{ [K in keyof HTMLElementEventMap]: (event: HTMLElementEventMap[K]) => void }>;
}

/** `tabKind` -> `tab-kind`: the same rule `element.dataset` applies. */
function dashed(key: string): string {
  return key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: ElementOptions = {},
  children: Child[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.class) node.className = options.class;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.tip) node.setAttribute("data-tip", options.tip);
  if (options.style) Object.assign(node.style, options.style);
  for (const [key, value] of Object.entries(options.data ?? {})) {
    node.setAttribute(`data-${dashed(key)}`, String(value));
  }
  for (const [key, value] of Object.entries(options.attrs ?? {})) {
    node.setAttribute(key, value);
  }
  for (const [event, listener] of Object.entries(options.on ?? {})) {
    node.addEventListener(event, listener as EventListener);
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

/**
 * A definition list of `label -> value`: how every placeholder tab renders the read
 * model (KYO-232.c5).
 *
 * A definition list with the ids visible is the plainest faithful rendering, and it is
 * a shared helper rather than four copies because the app is in use while the
 * placeholders stand: the four of them should read as one app, not as four sketches.
 */
export function facts(rows: [string, string][]): HTMLElement {
  const list = el("dl", { class: "facts" });
  for (const [label, value] of rows) {
    list.append(
      el("dt", { text: label }),
      el("dd", { class: value === "" ? "facts-empty" : "", text: value === "" ? "—" : value }),
    );
  }
  return list;
}

/** Pre-formatted JSON, for the parts of a read-model answer no list can flatten. */
export function json(value: unknown): HTMLElement {
  return el("pre", { class: "raw mono", text: JSON.stringify(value, null, 2) });
}

/** A section with a heading, for a placeholder tab's raw rendering. */
export function section(heading: string, ...children: Child[]): HTMLElement {
  return el("section", { class: "raw-section", data: { section: heading } }, [
    el("h2", { text: heading }),
    ...children,
  ]);
}
