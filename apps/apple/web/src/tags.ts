// KYO-941: the rule a tag must meet, as the server states it (`normalise_tag` and `TAG_RE` in
// `src/kyosei/cmd_notes.py`): without the leading `#`, 1 to 64 characters with no space and no
// comma. `test/tagfield.test.ts` reads the server's pattern and fails if the two accept
// different tags.

/** The server's pattern, written again here for a tag as `tagName` returns it. */
export const TAG_PATTERN = /^[^\s,]{1,64}$/;

/** What the server says of a tag it refuses, in its own words. */
export const TAG_RULE = "a tag must be 1 to 64 characters with no space or comma";

/** A tag as the server stores it: trimmed, without its leading `#`, lower-cased. */
export function tagName(raw: string): string {
  return raw.trim().replace(/^#+/, "").toLowerCase();
}

/** Why the server would refuse this tag, or null when it would take it. */
export function tagProblem(raw: string): string | null {
  return TAG_PATTERN.test(tagName(raw)) ? null : TAG_RULE;
}

/** A character the field does not take: the characters the rule excludes. */
export function tagCharRefused(char: string): boolean {
  return /^[\s,]$/.test(char);
}
