// Where the CLI keeps its files under a store root -- names, and nothing that opens them.
//
// Until KYO-459 this file opened the store's database read-only and ran the SELECTs no
// read-only command answered (briefs, shift notes, events, gates, dispatches, the
// records table). Those reads are the CLI's now (`kyo briefs`, `kyo shift-notes`, `kyo
// gates`, `kyo activity`, `kyo list`, KYO-454), asked of `kyo serve` like every other
// read, so the schema has one reader again (law 3) and the app opens no SQLite at all.
// What is left is the layout's names, for a sentence that has to say where a file is.

/** Where the CLI keeps its files under a store root (`kyosei.config`). */
export const KYOSEI_DIR = "kyosei";
export const DB_NAME = "kyosei.db";
export const CONFIG_NAME = "config.yml";
