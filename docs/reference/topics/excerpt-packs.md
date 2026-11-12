# Excerpt packs

Sharing part of the discussion behind a credit, so that a reader can check it against the credit's seal.

## Excerpt packs

governs: src/kyosei/cmd_excerpt.py, tools/kyosei-verify-excerpt.py

A credit that cites a discussion span carries `span_seal`, the hash of the span's last line of the
turn index. The index commits every message by line when it is sent: each line becomes a leaf
hashed with a salt that only the machine holding the seal key can compute, the leaves fold into a
Merkle root, and the root and the line count go on the message's index line (`kyo turns`). No
text is stored there. A person who wants a reader to see part of a discussion can therefore show
some lines of a message, with their salts and proofs, and a reader can check that each shown line
was in the message committed when it was sent, without learning the lines not shown. `kyo
excerpt` makes that pack, and `tools/kyosei-verify-excerpt.py` checks it.

### The review flow

`kyo excerpt <credit-id>` has three modes, run in this order. The credit must cite a sealed span,
and the project must read transcripts, because the text of each message is read from the session's
transcript (or its archived copy) and used only when it reproduces the root on the index line.

1. `--draft` writes a readable page of every line in the span, the agent's included, to the
   session's scratch directory (the one `kyo sessions scratch` recorded), or to the file `--out`
   names. Each line begins with a marker, `[m2.l3]` for line 3 of message 2. The page also lists
   the lines that hold a term from the store's `private-words.txt` (and the names of the
   other projects registered on the machine), by marker and kind and never by the term, and so does the command's
   output.
2. The person reads the draft and says what to drop. A line deleted from the draft is a line
   withheld; a line kept is shared. The text of a kept line is not edited, and a draft whose
   remaining lines are not lines of the span, in order, is refused.
3. `--preview --from <draft>` prints the pack as its reader will see it: the shared lines, and
   for each withheld run its position and the number of lines, so that a message shared not at all
   reads as "message 1 (the person) at 15:00:12, 14 line(s), withheld". It writes nothing, and
   says whether the credit's quoted words appear in a shared line of a person's message.
4. The person approves in their own words, and the orchestrator records that as a credit of role
   `authorized` quoting them. A record holds one current credit for each subject, so when the
   excerpted credit is the record's own, the approval names a cleared gate or a verdict of the same
   record.
5. `--pack --from <draft> --approved-by <credit-id>` writes the pack. It is refused under the
   contract `excerpt-requires-approval` when the credit named is missing, is not current, is not
   `authorized`, or is on another record. The pack carries the approving credit's id.

### What the pack holds

The pack is a JSON document (`"object": "excerpt-pack"`) holding the credit as the export holds it
and the words it quotes; the approving credit's id; and for each message of the span an index line
(`seal`, `session`, `prompt_id`, `at`, `role`, `root`, `lines`, `prev`, and `origin`, which the
chain does not cover) and the lines shared, each with its position, text, salt and proof. A line
not shared is its position only. The seal key, the text of lines not shared and the transcript are
never written to it.

Three kinds of message cannot be shared, and the draft says which. A message committed without a
root, which is every message sent while the agent was working, was never committed. A message
whose transcript text does not reproduce its root is not used on a guess. A credit with an empty
`span_seal`, such as a reconstructed credit whose turns were never indexed, cannot be packed at all.

### What the verifier does

`tools/kyosei-verify-excerpt.py pack.json` uses the Python standard library and imports nothing
from Kyosei, so a reader can run it without installing Kyosei. It takes four steps.

1. For each shared line it recomputes the leaf from the salt and the text, folds the proof, and
   compares the result with the message's root. The proof must also have the shape that the line's
   position and the committed line count give, so a line cannot be shown at another position.
2. It recomputes each index line's hash from its committed fields and `prev`, from the first
   message to the last. Each `prev` must be the hash of the line before, and the last hash must
   equal the credit's `span_seal`.
3. It checks that the credit's quoted words occur in the shared lines of a person's message. This
   is reported and does not change the exit status, since a person may withhold the line holding
   their words.
4. It reports each message as whole, partial (lines shown of lines committed, with the number
   omitted), withheld, or never committed, and whether a person's message is stated to be theirs
   (`origin: human`) or unconfirmed.

It exits 0 when steps 1 and 2 hold, 1 when a shared line, salt, proof, index line or `span_seal`
does not check, and 2 when the file is not a pack.

A pack that passes shows that each shared line was in the message committed when it was sent, that
the lines are in their original positions with an exact number omitted, and that nothing was
inserted between the first and last message of the span. It does not show that the commitment was
made at the stated time: the index is a local file that its owner can rewrite consistently from a
given line onward, and timestamping the head of the chain outside the machine is planned and not
built. It does not show who typed the person's lines: `origin` is not covered by the hash, and the
seal says nothing of who was at the keyboard. `kyo dossier` does not attach a pack.
