# Kyosei for Apple platforms

A native Mac app for working with the Claude Code sessions of a Kyosei project. It is a
client of `kyo serve`, built for the maintainer's own devices and the current macOS. A client
displays what the server returns and holds no store logic. The server's routes are described
in
[`docs/reference/topics/serve-and-desktop.md`](../../docs/reference/topics/serve-and-desktop.md).

## What the app adds to a session

The app draws a session's conversation as rendered markdown and sends prompts to it through
the server, which types them into the session's pane. Beside the conversation it shows the
project's records and files, and a terminal on the pane. To a session run in a terminal it
adds the following.

- **Anchors.** A paragraph of a prompt can be anchored to a place: a sentence or a selection
  in a reply or an earlier prompt, a record or an element of one, such as a criterion or
  a gate, or lines of a file. A right-click on the place offers Anchor. When the prompt is delivered,
  the server types a line above the paragraph that names the place and quotes its first
  words, so the agent reads which passage the paragraph is about. A place in the conversation
  stays highlighted after the prompt is sent.
- **Tags.** A paragraph can also carry tags. At delivery the server files each anchored or
  tagged paragraph in the store as a note, with its text, anchors and tags, and
  `kyo notes list --tag` later finds what the person wrote on a subject. A tag is not typed
  to the agent.
- **Attachments.** A file or a pasted image can be attached to a paragraph. The server saves
  it on the machine the session runs on and types its path above the paragraph.
- **The session's task list.** A live session has one list that the person and the
  orchestrator both write. The person keeps reminders on it in the app, above the composer,
  and the orchestrator keeps its plan there with `kyo agenda`. The hooks print the unchecked
  items into the session on each prompt and name the person's at each stop.
- **Drafts kept on the server.** The server saves the unsent prompts of each session and of
  the project. A prompt written on one device appears on another while both are open, a
  prompt sent from one leaves the other, and one written while no session runs is carried
  into the next session.
- **Held prompts.** A prompt sent while the agent is in a turn is held by the server and
  delivered when the session's screen is idle. Until then it can be taken back or sent at
  once.

All of these need a device token with the `send` permission, and anchors and tags need
`notes` as well; [Running it](#running-it) describes the token. The sections below describe
how the app draws each and where its code is.

## Layout of the source

<!-- governs:
- apps/apple/Package.swift
- apps/apple/Sources/KyoseiClient/ServeClient.swift
- apps/apple/Sources/KyoseiMac/App.swift
- apps/apple/Sources/KyoseiMac/SessionFeed.swift
- apps/apple/web/build.mjs
- apps/apple/web/static/chat.css
- apps/apple/web/src/pipeline.ts
- apps/apple/web/src/marks.ts
- apps/apple/web/src/chipwords.ts
- apps/apple/web/src/sending.ts
- apps/apple/web/src/agents.ts
- apps/apple/web/src/start.ts
- apps/apple/web/src/terminal.ts
- apps/apple/web/src/history.ts
-->

`Sources/KyoseiClient` holds the code that does not draw: requests to the server and the
parsing of its event stream, with tests in `Tests/`. `Sources/KyoseiMac` holds the views.
`web/` is the markdown renderer shown in a web view. It is adapted from
[MDHero](https://github.com/vaibhav-kakde-in/mdhero), whose MIT licence is kept in
`web/LICENSE-mdhero`.

The terminal drawer is [xterm.js](https://github.com/xtermjs/xterm.js), whose MIT licence is
kept in `web/LICENSE-xterm` and copied into the app beside the renderer.

The files that the later sections do not name, with what each holds:

| File | What it holds |
|---|---|
| `Package.swift` | the package: the `KyoseiClient` library, the `KyoseiMac` executable and a test target for each |
| `Sources/KyoseiClient/ServeClient.swift` | the requests to `kyo serve`, the parser of its event stream, and the follower that reconnects a session's stream |
| `Sources/KyoseiMac/App.swift` | the application delegate, the one window, the File menu's Projects… item and the Settings scene |
| `web/build.mjs` | the build of the page into the app's resources, which are not committed |
| `web/static/chat.css` | the page's styles |
| `web/src/pipeline.ts` | the markdown renderer adapted from MDHero |
| `web/src/marks.ts` | which line of a prompt each anchor or tag belongs to while the text is edited |
| `web/src/chipwords.ts` | the words an anchor's chip quotes, drawn as inline markdown |
| `web/src/sending.ts` | the gate through which one press of Send posts a prompt once |
| `web/src/agents.ts` | how a subagent is named in the status bar and the side panel |
| `web/src/start.ts` | the Start orchestrator panel: when it is offered and what it says |
| `web/src/terminal.ts` | the terminal drawer's sizes and the bytes of a stream's frame |
| `web/src/history.ts` | the list of places shown, and the position in it, that the Files and Records views step back and forward through |

`Sources/KyoseiMac/SessionFeed.swift` holds the window's state and every exchange between the
page and the server, `Sources/KyoseiMac/ChatView.swift` holds the web view and the status
bar, and `web/src/chat.ts` is the page, which draws the chat, the composer and each view from
the frames the app hands it. The sections below name the parts of these three that they
describe.

## Projects

<!-- governs:
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#toggleProjectList
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#choose
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#sendProjects
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#projects
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#readProjects
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#followProjects
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#verifyProject
- apps/apple/web/src/projects.ts
- apps/apple/web/src/chat.ts#projectRows
- apps/apple/web/src/chat.ts#chooseProject
- apps/apple/web/src/chat.ts#drawProjects
- apps/apple/web/src/chat.ts#openProjects
- apps/apple/web/src/chat.ts#closeProjects
- apps/apple/web/src/chat.ts#toggleProjectList
- apps/apple/web/src/chat.ts#focusProject
-->

The window shows one project at a time. The bar above the top bar begins with a dropdown that
names it and lists every project the server serves (`GET /v1/projects`); a project whose store
cannot be opened is listed with the server's reason and cannot be chosen. Choosing a project
turns the window to it: the chat, the side panel, Records, Files and the status bar are then
that project's. After the dropdown the bar holds a link for each project that has a live session
in a pane, which turns the window to that project and follows that session. The server has no
route that lists live sessions across projects, so the app reads each project's session list
every five seconds, and the list of projects every thirty and whenever the dropdown is opened.

The app opens on the project it last showed. On a first run, or when the server no longer
lists that project, it shows the list of projects in place of the chat. The logic is in
`web/src/projects.ts`, and the switch is `SessionFeed.open`. File > Projects… (Command-O)
shows that list in place of everything under the top bar (KYO-766), and choosing it again
returns to the project the window was showing.

### Drafts of unsent prompts

<!-- governs:
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#DraftPlace
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#draftPath
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#loadDraft
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#draftChanged
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#handDraft
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#saveDraft
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#settleDraftBeforeQuit
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#sendPendingDraft
- apps/apple/Sources/KyoseiClient/DraftSettlement.swift
- apps/apple/web/src/drafts.ts
- apps/apple/web/src/projects.ts
- apps/apple/web/src/chat.ts#savedBox
- apps/apple/web/src/chat.ts#currentBoxes
- apps/apple/web/src/chat.ts#agreedWith
- apps/apple/web/src/chat.ts#applyBoxes
- apps/apple/web/src/chat.ts#keepCurrent
- apps/apple/web/src/chat.ts#flushDraft
- apps/apple/web/src/chat.ts#draftSave
- apps/apple/web/src/chat.ts#adoptShelf
- apps/apple/web/src/chat.ts#remember
- apps/apple/web/src/chat.ts#showTexts
-->

The composer's unsent prompts belong to the project they were written in. The server keeps a
draft for each session and one for the project (KYO-763). The app reads and saves the
followed session's draft while that session is live, and the project's draft when the
project has no session or the followed session has ended, so a prompt written between
sessions is found on any device that opens the project. The server moves the prompts between
the two: an ended session's go to the project's draft, and a new session's first read takes
them. The app learns that the followed session has ended from the session list it reads every
five seconds, and then reads the project's draft in place of what the window holds, so that
the copy another device saved last is the one shown. A save that is waiting is sent before
the composer turns from one draft to another. The page also keeps each project's prompts in
its own storage, under the server and the project, which is all a device with no token has.

A draft saved on another device is shown without reopening anything (KYO-798). The server
announces a save of the session's draft on the session's event stream and a save of the
project's on the project's, both of which the app already follows. `SessionFeed.swift` reads
the draft when the etag announced is not one it holds, and hands it to the page. Every box
has an id that all devices and the server share, and the page merges by it
(`web/src/drafts.ts`). A box not edited in this window follows the server: it takes words
typed elsewhere, and it leaves when it was sent or deleted elsewhere. A box holding edits
the server has not acknowledged keeps them. If the same box was edited on the other device
too, that version is added after it, and a line under the composer says that both were kept.
The merged draft is then saved, which brings the other device to the same boxes. The same
merge runs when the server refuses a save as stale. The page learns what the server holds
from the app's answer to each save, and a box that already shows what is wanted is left in
place, so the caret and the box's undo history stay.

A quit waits for the last save (KYO-782). The page saves after a pause in typing, so the app
asks it for a save still waiting, makes that save, and waits up to two seconds for the
server's acknowledgement before the process ends. Prompts the server has not acknowledged by
then are kept in the app's defaults with their server and project, replace the server's draft
in the composer when that project's draft is next read, and are forgotten once a save has
been acknowledged. The wait and the kept prompts are `settleDraft` and `KeptDraft` in
`Sources/KyoseiClient/DraftSettlement.swift`.

A prompt carried into a later session keeps its tags, attachments and anchors. An anchor made
in a session other than the one shown is sent as its line of words, which the server reads
from the session the anchor names. Its chip in the composer is drawn dimmed with a dashed
outline and takes no press, because the chat on screen has no place for it to open. The choice
of draft is `DraftPlace` in `SessionFeed.swift`, and the page's part is in `web/src/projects.ts`.

### Anchors in an unsent prompt

<!-- governs:
- apps/apple/web/src/chat.ts#trackEdit
- apps/apple/web/src/chat.ts#trackHistory
- apps/apple/web/src/chat.ts#stepPlacing
- apps/apple/web/src/chat.ts#addAnchor
- apps/apple/web/src/chat.ts#anchorsHeld
- apps/apple/web/src/chat.ts#anchoredBlocks
- apps/apple/web/src/chat.ts#paintAnchors
- apps/apple/web/src/chat.ts#paintStalePlaces
- apps/apple/web/src/chat.ts#paintStaleAnchors
-->

Placing an anchor is a step of its own in a prompt's undo (KYO-780): Command-Z directly after
it takes the anchor back and leaves the text as it was, and Shift-Command-Z puts it back. The
text area's own history holds edits of the text only, so the page keeps the placings beside it
and steps through whichever was made last. What an unsent prompt is anchored to is highlighted
in the chat, and the highlight goes when the anchor does, unless another prompt holds an anchor
on the same words. An anchor of a sent paragraph that the server reports as stale (KYO-712)
cannot be highlighted, so the chip above that paragraph in the sent prompt reads "place lost"
and its title names the paragraph.

The page's tests are in `web/test/` and run with `npm test` in `web/`. Those in
`anchors.test.ts` need a web view: they build the page, open it in a headless Chrome and feed
it the frames the app would send, and are skipped on a machine with no Chrome.

### A session's task list

<!-- governs:
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#showAgenda
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#agendaFrame
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#loadAgenda
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#agendaChanged
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#saveAgenda
- apps/apple/web/src/agenda.ts
- apps/apple/web/src/chat.ts#agendaFrame
- apps/apple/web/src/chat.ts#agendaItemsNow
- apps/apple/web/src/chat.ts#saveAgenda
- apps/apple/web/src/chat.ts#agendaRow
- apps/apple/web/src/chat.ts#drawAgenda
-->

A live session's task list (KYO-770, KYO-771) is drawn above the composer's prompts, in a
slightly dimmed bubble that is the first thing in the prompts' own scrolling column: it rises
as the prompts grow and scrolls out of view with them when they need the room. The person
adds items and edits, checks and removes their own; the orchestrator's items show their state
and take no press, since the server refuses a device's change to one. The app reads the list
when it turns to a live session and again when the session's stream says its etag changed,
and a save the server refuses as stale leaves the typed words in their field and says so. With
no live session there is no bubble, and a token that may not read the list is told why in it.
The app's part is `showAgenda` and `saveAgenda` in `SessionFeed.swift`; the page's is
`web/src/agenda.ts` and the section of `web/src/chat.ts` that draws it.

### Spelling in the page's text fields

<!-- governs:
- apps/apple/Sources/KyoseiMac/ChatView.swift
-->

The page's text fields underline misspelled words and change nothing that is typed (KYO-796).
WebKit on macOS takes both from the app's defaults rather than from the page: it underlines
when `WebContinuousSpellCheckingEnabled` is true, and it applies the system checker's
corrections, which include the capital at the start of a sentence, when words are being
underlined and `WebAutomaticSpellingCorrectionEnabled` is true or absent with the correction
on system-wide. `ChatPage` in `ChatView.swift` registers the first as true and the second as
false before the web view is built. The Spelling and Grammar menu stores a choice under the
same keys, and a stored value is read before a registered one, so a choice made there is
kept. Smart quotes, smart dashes and text replacement have their own keys and still follow
the system-wide settings. `Tests/KyoseiMacTests` builds the web view without a window and
checks the keys and that typed text is left as typed.

## Views

<!-- governs:
- apps/apple/web/static/chat.html
- apps/apple/web/src/chat.ts#VIEWS
- apps/apple/web/src/chat.ts#currentView
- apps/apple/web/src/chat.ts#drawViews
- apps/apple/web/src/chat.ts#showView
-->

After the dropdown and the links, the bar lists the views, and choosing one replaces the pane
under it. The
chat stays drawn beneath the others and keeps following its session, so returning to it
reloads nothing. A view is one entry in `VIEWS` in `web/src/chat.ts` and the elements of
`web/static/chat.html` marked with its `data-view`.

### Search in a view

<!-- governs:
- apps/apple/web/src/search.ts
- apps/apple/web/src/chat.ts#searchable
- apps/apple/web/src/chat.ts#indexed
- apps/apple/web/src/chat.ts#runSearch
- apps/apple/web/src/chat.ts#hitsOnScreen
- apps/apple/web/src/chat.ts#paintHits
- apps/apple/web/src/chat.ts#refreshSearch
- apps/apple/web/src/chat.ts#moveToHit
- apps/apple/web/src/chat.ts#openSearch
- apps/apple/web/src/chat.ts#closeSearch
-->

Command-F searches what the view shows: the chat, the open record or the open file (KYO-741,
KYO-754). The query is matched as one phrase, without regard to case, against the text as it
is drawn, and nothing in it is an operator. A record's unopened folds are searched too, and
stepping to a hit inside one opens it. Every hit is counted, but only the hits on screen are
marked, and none inside a shut fold: the web view was measured stalling for about a
millisecond for each marked range that lies in a shut fold. The matching is in
`web/src/search.ts`.

### Records

<!-- governs:
- apps/apple/web/src/records.ts
- apps/apple/web/src/chat.ts#drawKindControls
- apps/apple/web/src/chat.ts#drawRecordLists
- apps/apple/web/src/chat.ts#recordItem
- apps/apple/web/src/chat.ts#drawRecord
- apps/apple/web/src/chat.ts#enterRecords
- apps/apple/web/src/chat.ts#openRecord
- apps/apple/web/src/chat.ts#closeRecord
- apps/apple/web/src/chat.ts#recordFrames
-->

Records shows the project's records, one at a time. With none open it lists them all,
newest first and without groups (KYO-759); the side bar lists them in the same order above
the records viewed most recently. Search
and the three filters (Open, Dispatchable, Awaiting you) apply to both lists, as does a Kind
control beside the search field (KYO-785): a dropdown of the kinds the store holds, with a
checkbox each. A `kind:plan` or `kind:plan,task` token typed in the search field checks the
same kinds, and the rest of the query is searched as text. While a kind is checked the control
names it in the accent colour, with an x that clears it. A
`kyosei://` link in the chat opens its record there. The view reads `cmd/list`, `cmd/next`,
`cmd/gates` and `cmd/show/<id>`, which need no device token, and reads them again when the
project's event stream reports a change to the store. Its logic is in `web/src/records.ts`.
The viewing history is kept in the app's own defaults for each server and project, not in
the store.

### Files

<!-- governs:
- apps/apple/web/src/files.ts
- apps/apple/web/src/chat.ts#linkPaths
- apps/apple/web/src/chat.ts#askPath
- apps/apple/web/src/chat.ts#dressPathLink
- apps/apple/web/src/chat.ts#drawFilesSide
- apps/apple/web/src/chat.ts#fileMarkdown
- apps/apple/web/src/chat.ts#fileSource
- apps/apple/web/src/chat.ts#drawFile
- apps/apple/web/src/chat.ts#showFile
- apps/apple/web/src/chat.ts#refreshOpenFile
- apps/apple/web/src/chat.ts#refreshTree
- apps/apple/web/src/chat.ts#enterFiles
- apps/apple/web/src/chat.ts#fileFrames
-->

Files shows the project's files as `kyo serve` serves them: the code repository and the store
repository, each as git shows it, and the followed session's scratchpad directory when the
server has one for it. The side bar holds a chip for each of these roots, the chosen root's
tree, a toggle for dotfiles, which are hidden at first, and the files the session's replies and
prompts have named, newest first; the chat's side bar lists those files too. A markdown file is
drawn as a reply is, and its relative links and images are read against its own folder. A source
or text file is read-only, with line numbers and with colouring for the types the renderer
knows. An image is shown, and any other file is described by its name, type and size. A text
file longer than 400,000 characters is shown as its first lines, with the rest offered, and the
whole of one longer than 1,500,000 characters is drawn without colouring. While the view is
shown, the open file is asked for again every three seconds with its `ETag`, which the server
answers without the bytes when nothing changed, and the open folders every twelve.

A file path in a reply, a prompt or a record's page is drawn as a link when the server's
`files/resolve` route places it in a served root, and a click opens it in Files, at the line
when one follows the path after a colon. The words asked about are absolute paths, paths under
`~/`, and relative paths that hold a slash and an extension; fenced code blocks are left as
they are. The logic is in `web/src/files.ts`. All of this needs the device token to hold the
`files` permission. Without it the view shows the server's refusal and no path is a link.

### History

<!-- governs:
- apps/apple/web/src/searchhistory.ts
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#history
- apps/apple/web/src/chat.ts#enterHistory
- apps/apple/web/src/chat.ts#askHistory
- apps/apple/web/src/chat.ts#drawHistory
- apps/apple/web/src/chat.ts#drawOpened
- apps/apple/web/src/chat.ts#openMessage
- apps/apple/web/src/chat.ts#showReaderPlace
- apps/apple/web/src/chat.ts#drawReader
- apps/apple/web/src/chat.ts#historyFrames
-->

History searches what was said in the project's sessions (KYO-825). It has a search field and
two chips, You and Claude, which choose whose messages are searched. A query of two characters
or more is sent to the server's `search` route, and each result names who wrote the message,
when, and the session's title, above an excerpt with the match marked. A click opens that
session's chat at the message, reports the result to the server's `history/recent` route, and
leaves a Results button in the top bar that returns to the list. The side bar lists the
results opened most recently as the server keeps them, so that every device shows the same
list. Command-Shift-F opens the view. It needs a device token of any permission and a project
that has opted in to reading transcripts. The logic is in `web/src/searchhistory.ts`, and the


### Files outside the served roots

<!-- governs:
- apps/apple/web/src/files.ts#localPath
- apps/apple/Sources/KyoseiMac/SessionFeed.swift#files
-->

`kyo serve` serves no file outside those roots, so a path that lies elsewhere on the server's
machine cannot be opened in Files. Such a path is drawn as a dotted link when it reads as a
file: it is under `~/` or one of the usual top folders, or it has an extension. A click offers
Copy path, and Reveal in Finder when the file exists on the machine the app runs on. That is
the case in two situations:

- The app runs on the server's own machine, which it takes to be so when the server's address
  is loopback.
- Settings holds a path mapping, a prefix of a path on the server's machine and the folder it
  stands for on this one, and the mapped path exists. Mounting the server's home folder over
  the network and mapping it, for example `/Users/name` to `/Volumes/name`, is one way to
  arrange this.

Anything else, such as opening these files in the app itself or fetching them by another
route, is left to whoever needs it. The mapping is in `localPath` in `web/src/files.ts`, and
the check and the reveal are in `SessionFeed.files(body:)`.

## Running it

<!-- governs:
- apps/apple/package.sh
- apps/apple/Sources/KyoseiMac/SettingsView.swift
-->

```
cd web && npm ci && npm run build && cd ..
swift run KyoseiMac
```

The app reads `kyo serve` at `http://127.0.0.1:47370` unless its Settings name another server,
which `KYOSEI_SERVE_URL` overrides. The project is chosen in the window, and `KYOSEI_PROJECT`
names the one a launch opens on. Reading a session needs a device
token from `kyo devices issue`, entered in Settings and kept in the Keychain; a token of any
permission reads, and the app sends it on every session read. With no token, or one the
server refuses, the chat says that a token is needed and how to issue one. Sending needs the
token to hold the `send` permission. A project with
no live session is shown a Start orchestrator button, which needs the token to hold the `start`
permission. The terminal drawer is a terminal on the session's pane, and needs the token to hold
the `terminal` permission; without it the drawer shows the pane's text and the server's reason. While it is open the
terminal covers the chat pane (KYO-753), which keeps its scroll place beneath.
Files and file links need it to hold the `files` permission.

`package.sh` builds `Kyosei.app` under `.build/package`. It signs with `KYOSEI_SIGN_IDENTITY`,
and notarizes with `KYOSEI_NOTARY_KEY`, `KYOSEI_NOTARY_KEY_ID` and `KYOSEI_NOTARY_ISSUER`.
