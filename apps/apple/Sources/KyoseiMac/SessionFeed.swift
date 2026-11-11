import AppKit
import Foundation
import KyoseiClient
import Observation
import WebKit

/// A subagent still at work, as the status bar lists it.
struct RunningAgent: Identifiable, Equatable {
    let id: Int
    let name: String
    let type: String
    /// The short model name and the effort its transcript records, such as `fable` and
    /// `medium`; empty before the subagent's first reply (KYO-726).
    var model = ""
    var effort = ""
    let since: Date?
    /// Its tokens so far, in words, or empty before the server has been asked.
    var tokens = ""

    /// What the status bar and its list call the agent: `fable medium`, and its type until it
    /// has replied and the transcript states them.
    var kind: String {
        let words = [model, effort].filter { !$0.isEmpty }
        return words.isEmpty ? type : words.joined(separator: " ")
    }

    /// The record a dispatched worker is on, read from its description.
    var record: String? {
        name.firstMatch(of: /[A-Z][A-Z0-9]*-\d+/).map { String($0.output) }
    }
}

/// Whether the chat shows its session as the session is now (KYO-725).
enum FeedState: Equatable {
    case connecting
    case live
    case noSession
    /// The chat has not followed its session since `since`, for `reason`, and the feed is
    /// connecting again.
    case notLive(since: Date, reason: String)
}

/// Reads one session from `kyo serve` and hands each response to the web view unchanged.
@MainActor
@Observable
final class SessionFeed {
    private static let menuRefresh = Duration.seconds(5)
    /// The server's list of projects is read once in this many rounds of the session links'
    /// refresh, and whenever the dropdown is opened: it costs the server one `kyo where` for
    /// each project, and changes only when a project is registered or removed (KYO-761).
    private static let projectsEvery = 6

    private var client: ServeClient
    private var selected: String?
    private var current: String?
    private var task: Task<Void, Never>?
    private var menuTask: Task<Void, Never>?
    /// Follows the project's change events for the Records view (KYO-732).
    private var recordsTask: Task<Void, Never>?
    @ObservationIgnored weak var webView: WKWebView?
    /// Whether the page has been told its project and the tasks that follow it are running.
    private var started = false

    /// The window's project, or the empty string while none is chosen: on a first run, and
    /// when the project last shown is no longer served (KYO-761).
    private(set) var project: String
    /// Reads the server's projects and each one's live sessions, whatever project is shown.
    private var projectsTask: Task<Void, Never>?
    /// The server's projects as last read, or nil before a read has succeeded.
    private var served: [ProjectRow]?
    private var servedProblem: String?
    private var liveSessions: [String: [SessionRow]] = [:]
    /// Whether the window's project has been looked for in this server's list.
    private var projectVerified = false
    /// The last projects frame the page was sent, so that an unchanged one is not sent again.
    private var projectsFrame = ""
    private(set) var state = FeedState.connecting
    /// Why the server shows this device no session, when the reason is its device token
    /// (KYO-791): none is saved, or the server refuses the one that is. The status bar says
    /// it in place of the feed's state, whichever project the window shows, so that sessions
    /// the server will not list are not taken for sessions that do not exist. Nil once a
    /// session read has been answered.
    private(set) var tokenNeeded: String?
    /// The waits between attempts to read the session while they keep failing.
    private var retry = Backoff()
    private(set) var facts: SessionFacts?
    private(set) var remote: String?
    /// Commits on the branch that its upstream lacks; nil without an upstream (KYO-717).
    private(set) var ahead: Int?
    var runningSubagents = 0
    var runningAgents: [RunningAgent] = []
    /// Where the composer's boxes are read from and saved to (KYO-763): the followed
    /// session's draft while it is live, and the project's when no session is live. The
    /// server moves the boxes between the two itself, when a session has ended and when a
    /// new one first asks for its draft, so the app only chooses which to ask for.
    /// The rows of the project's session list, which the menu's frame carries whole.
    private struct ListedSessions: Decodable {
        let sessions: [SessionRow]
    }

    private enum DraftPlace: Equatable {
        case session(String)
        case project
    }
    /// The place the composer's draft was last asked of, or nil before any was.
    private var draftPlace: DraftPlace?
    /// The place whose draft the page's boxes were last given from, which is where a save
    /// goes: until the draft of the place just turned to has arrived, the boxes on the
    /// screen are still the ones written under the place that was left.
    private var shownPlace: DraftPlace?
    /// The session whose sent marks were last asked for, so that they are asked for once.
    private var marksSession: String?
    /// The etag of each draft read, by its path. A save still on its way when the window
    /// turns to another session answers for its own draft and not the one shown (KYO-761).
    private var draftEtags: [String: String] = [:]
    /// The etag of the draft the page was last handed, by its path (KYO-798). The page states
    /// it with each save it asks for, which is how a save asked for before the page had
    /// merged a newer draft is told from one asked for after.
    private var handedEtags: [String: String] = [:]
    /// The reads and saves of drafts, which run one at a time in the order they were asked.
    private var draftTask: Task<Void, Never>?
    /// The wait between asking the page for a save that is pending and reading the draft
    /// of the place the composer turns to.
    private static let draftFlushWait = Duration.milliseconds(300)
    /// The longest a quit waits for the server to acknowledge the composer's last save
    /// (KYO-782). A save to a server that answers takes a small part of it.
    static let quitLimit = Duration.seconds(2)
    /// The save last asked for, which answers whether the server acknowledged it.
    private var latestSave: Task<Bool, Never>?
    private var saveCount = 0
    /// What the latest save holds, from when it is asked for until the server has
    /// acknowledged it: what a quit keeps on this machine when the server has not.
    private var unsavedDraft: (count: Int, kept: KeptDraft)?
    /// Whether the composer was given prompts kept at the last quit, which the next
    /// acknowledged save then holds.
    private var restoredDraft = false
    /// The live session whose task list the composer shows, or nil when it shows none
    /// (KYO-771). The list is the session's, so there is none between sessions.
    private var agendaSession: String?
    /// The etag of each list read or saved, by its session. A change announced on the
    /// stream with the etag held here is this window's own save and is not read again.
    private var agendaEtags: [String: String] = [:]
    /// Whether the server has answered for the list of `agendaSession`. Until it has, the
    /// stream's next word of the list is a reason to ask again, whatever etag it states.
    private var agendaAnswered = false
    /// The reads and saves of the list, which run one at a time in the order they were asked.
    private var agendaTask: Task<Void, Never>?
    private var startWatch: Task<Void, Never>?
    /// Whether the terminal drawer is open, and the size of its view once the page has
    /// measured one (KYO-721).
    private var terminalOpen = false
    private var terminalSize: (cols: Int, rows: Int)?
    /// The session whose terminal was last asked for, so that it is asked for once.
    private var terminalSession: String?
    private var terminalTask: Task<Void, Never>?
    /// Read when the drawer opens, since reading it starts a process and a key must not.
    private var terminalToken: String?
    /// The open stream's connection, which an input or a resize names.
    private var terminalConnection: String?
    private var typed = ""
    private var typing = false
    private var resizing: Task<Void, Never>?
    private var currentLive = false

    init(client: ServeClient, project: String) {
        self.client = client
        self.project = project
    }

    private static let serverKey = "serverURL"
    private static let projectKey = "project"
    private static let localServer = "http://127.0.0.1:47370"
    private static let tokenService = "dev.kyosei.device-token"
    private static var environment: [String: String] { ProcessInfo.processInfo.environment }

    static var savedServer: String {
        environment["KYOSEI_SERVE_URL"] ?? UserDefaults.standard.string(forKey: serverKey)
            ?? localServer
    }

    /// The project the app last showed, or the empty string on a first run.
    static var savedProject: String {
        environment["KYOSEI_PROJECT"] ?? UserDefaults.standard.string(forKey: projectKey) ?? ""
    }

    static func fromEnvironment() -> SessionFeed {
        SessionFeed(
            client: ServeClient(base: URL(string: savedServer)!, token: deviceToken()),
            project: savedProject)
    }

    /// Points this window at another server or project, and reads it afresh.
    func configure(server: URL, project: String) {
        UserDefaults.standard.set(server.absoluteString, forKey: Self.serverKey)
        open(server: server, project: project, session: nil, save: true)
    }

    /// File > Projects… (KYO-766): the page shows the list of projects in place of the chat,
    /// or returns to the project it was showing when the list is already up.
    func toggleProjectList() {
        send("projectList", "null")
    }

    /// What the page's dropdown and session links ask (KYO-761): another project, with the
    /// session to follow when a link names one. A link to the window's own project turns
    /// the chat to that session.
    func choose(project name: String, session: String?) {
        guard !name.isEmpty else { return }
        if name == project {
            if let session, session != current { select(session: session) }
            return
        }
        open(server: client.base, project: name, session: session, save: true)
    }

    /// Turns the window to `project` on `server`, the empty string being no project. Every
    /// task that reads the project left is cancelled, and each of them checks that it was
    /// not cancelled before it changes anything or hands the page a frame, so nothing of
    /// that project is drawn afterwards. `save` is false when the app leaves a project the
    /// server no longer lists, which stays the saved one in case it is served again.
    private func open(server: URL, project: String, session: String?, save: Bool) {
        if save { UserDefaults.standard.set(project, forKey: Self.projectKey) }
        task?.cancel()
        menuTask?.cancel()
        recordsTask?.cancel()
        startWatch?.cancel()
        task = nil
        menuTask = nil
        recordsTask = nil
        startWatch = nil
        closeTerminal()
        terminalSession = nil
        if server != client.base {
            // Another server's projects are not this one's.
            projectsTask?.cancel()
            projectsTask = nil
            served = nil
            servedProblem = nil
            liveSessions = [:]
            projectVerified = false
            draftEtags = [:]
            handedEtags = [:]
            agendaEtags = [:]
            tokenNeeded = nil
            client = ServeClient(base: server, token: Self.deviceToken())
        } else {
            // Settings may have saved another token for the same server.
            takeSavedToken()
        }
        self.project = project
        selected = session
        current = nil
        currentLive = false
        draftPlace = nil
        shownPlace = nil
        marksSession = nil
        showAgenda(nil)
        facts = nil
        remote = nil
        ahead = nil
        runningSubagents = 0
        runningAgents = []
        state = .connecting
        retry.reset()
        send("clear", "null")
        started = false
        start()
    }

    /// Stores the device token through the system's own `security` tool.
    static func storeDeviceToken(_ token: String) -> Bool {
        // A token saved under another account name, as one added by hand is, would be found
        // first and sent in place of this one. Every item for the service goes before the add.
        for _ in 0..<8 {
            let old = Process()
            old.executableURL = URL(fileURLWithPath: "/usr/bin/security")
            old.arguments = ["delete-generic-password", "-s", tokenService]
            old.standardOutput = Pipe()
            old.standardError = Pipe()
            guard (try? old.run()) != nil else { break }
            old.waitUntilExit()
            if old.terminationStatus != 0 { break }
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "add-generic-password", "-U", "-s", tokenService, "-a", "device", "-w", token,
        ]
        guard (try? process.run()) != nil else { return false }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    func start() {
        guard !started else { return }
        started = true
        // The page is told its project before any frame of it, and keeps the composer's
        // unsent prompts under the server and project they were written for.
        let scope = "\(client.base.absoluteString) \(project)"
        send("project", #"{"project": \#(quoted(project)), "scope": \#(quoted(scope))}"#)
        projectsFrame = ""
        sendProjects()
        if projectsTask == nil { projectsTask = Task { await followProjects() } }
        // With no project there is nothing to follow, and the page shows the list of them.
        guard !project.isEmpty else { return }
        task = Task { await run() }
        menuTask = Task {
            while !Task.isCancelled {
                await sendMenu()
                try? await Task.sleep(for: Self.menuRefresh)
            }
        }
        sendRecordsStart()
        sendFilesStart()
        recordsTask = Task { await followProject() }
    }

    // MARK: The project dropdown and the session links (KYO-761)

    /// Hands the page the projects and their live sessions, when they differ from what it has.
    private func sendProjects() {
        let frame = ProjectsFrame.json(
            project: project, projects: served, problem: servedProblem, live: liveSessions)
        guard frame != projectsFrame else { return }
        projectsFrame = frame
        send("projects", frame)
    }

    /// What the page asks when its dropdown opens: the server's list, read again.
    func projects(body: [String: Any]) {
        guard body["want"] as? String == "list" else { return }
        Task { await readProjects(list: true) }
    }

    /// Reads the server's projects when `list` is set or none has been read, then the
    /// sessions of every project that can be opened, one project after another, and tells
    /// the page. The reads were made in a task group until KYO-813: with seventeen projects
    /// open, a child's completion aborted the process (`__cxa_pure_virtual` under
    /// `AsyncTask::completeFuture`) within seconds of launch, and read one at a time it did not.
    /// Why the group aborted is not established.
    /// `kyo serve` has no route that lists live sessions across projects, so each project's
    /// own list is asked for; a project that refuses its list has no link.
    private func readProjects(list: Bool) async {
        let client = client
        if list || served == nil {
            do {
                let rows = try await client.projects()
                guard !Task.isCancelled, client.base == self.client.base else { return }
                served = rows
                servedProblem = nil
                verifyProject()
            } catch {
                guard !Task.isCancelled, client.base == self.client.base else { return }
                servedProblem = ServeClient.describe(error)
            }
        }
        if let served {
            let names = served.filter(\.can_open).map(\.name)
            typealias Read = (name: String, rows: [SessionRow]?, needed: String?)
            var reads: [Read] = []
            for name in names {
                do {
                    reads.append((name, try await client.sessions(project: name), nil))
                } catch {
                    reads.append((name, nil, ServeClient.tokenNeeded(error)))
                }
            }
            guard !Task.isCancelled, client.base == self.client.base else { return }
            var found: [String: [SessionRow]] = [:]
            for read in reads {
                if let rows = read.rows { found[read.name] = rows.filter(\.live) }
            }
            liveSessions = found
            // A list with no session link is said to be for want of a token when it is, and
            // only by reads made with the token the feed now holds.
            if client.token == self.client.token {
                if let needed = reads.compactMap(\.needed).first {
                    tokenNeeded = needed
                } else if !found.isEmpty {
                    tokenNeeded = nil
                }
            }
        }
        sendProjects()
    }

    private func followProjects() async {
        var round = 0
        while !Task.isCancelled {
            await readProjects(list: round % Self.projectsEvery == 0)
            round += 1
            try? await Task.sleep(for: Self.menuRefresh)
        }
    }

    /// Leaves a project the server does not list, once for each server: the app then shows
    /// the list of projects in place of the chat. A project chosen from the dropdown or a
    /// link is in the list it was chosen from.
    private func verifyProject() {
        guard !projectVerified, let served else { return }
        projectVerified = true
        guard !project.isEmpty, !served.contains(where: { $0.name == project }) else { return }
        open(server: client.base, project: "", session: nil, save: false)
    }

    /// Show another session of this window's project. A window stays on one project, so that
    /// one project's session is never opened from another's.
    func select(session: String) {
        selected = session
        facts = nil
        state = .connecting
        retry.reset()
        task?.cancel()
        task = Task { await run() }
    }

    /// Reads the saved token again, and makes the session reads carry it when it is not the
    /// one they carry. A token saved after the server refused the last one is found here.
    private func takeSavedToken() {
        let saved = Self.deviceToken()
        guard saved != client.token else { return }
        client = ServeClient(base: client.base, token: saved)
    }

    /// Takes what a session read answered: a refusal for want of a token is said in the
    /// status bar, and an answer clears it. Any other failure says nothing about the token.
    private func noteSessionRead(_ error: Error?) {
        guard let error else {
            tokenNeeded = nil
            return
        }
        if let needed = ServeClient.tokenNeeded(error) { tokenNeeded = needed }
    }

    /// The device token, read through the system's own `security` tool, which created it.
    private static func deviceToken() -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "find-generic-password", "-s", tokenService, "-w",
        ]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()
        guard (try? process.run()) != nil else { return nil }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return nil }
        let text = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func sessionPath(_ session: String, _ route: String) -> String {
        "v1/projects/\(project)/sessions/\(session)/\(route)"
    }

    private func refusal(_ data: Data) -> String {
        let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return object?["error"] as? String ?? "The server refused that."
    }

    /// Hands a message to the server, which holds it until the turn has ended. A prompt with
    /// marks goes by the route that puts each mark's anchors above its paragraph and files it.
    func sendMessage(body: [String: Any]) {
        let marked = !((body["marks"] as? [Any]) ?? []).isEmpty
        var body = body
        if !marked { body["marks"] = nil }
        guard let session = current, let token = Self.deviceToken(),
            let data = try? JSONSerialization.data(withJSONObject: body)
        else {
            send("sent", #"{"error": "No session or device token to send with."}"#)
            return
        }
        let client = client
        let path = sessionPath(session, marked ? "send-prompt" : "held")
        Task {
            let reply = try? await client.authorised("POST", path, token: token, body: data)
            // The answer is for the composer of the session that sent; the page waits for
            // it before it leaves a project, so another session here is the app's own turn.
            guard session == current else { return }
            guard let reply else {
                send("sent", #"{"error": "The server could not be reached."}"#)
                return
            }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            if reply.status == 200 || reply.status == 201 {
                let held = object?["held"] as? [String: Any] ?? object
                send("sent", #"{"id": \#(quoted(held?["id"] as? String))}"#)
            } else {
                send("sent", #"{"error": \#(quoted(refusal(reply.data)))}"#)
            }
        }
    }

    /// Reads what the store holds of this session's sent prompts: the places their paragraphs
    /// were anchored to, which the chat keeps showing, and the tags in use, which the tag
    /// field suggests. A token without the `notes` permission is refused, and nothing is shown.
    private func loadMarks(session: String, after delay: Duration = .zero) {
        guard let token = Self.deviceToken() else { return }
        Task {
            try? await Task.sleep(for: delay)
            guard session == current else { return }
            let project = project
            if let reply = try? await client.authorised(
                "GET", sessionPath(session, "marks"), token: token), reply.status == 200,
                session == current
            {
                send("marks", String(decoding: reply.data, as: UTF8.self))
            }
            guard session == current else { return }
            if let reply = try? await client.authorised(
                "GET", "v1/projects/\(project)/tags", token: token), reply.status == 200,
                session == current
            {
                send("tags", String(decoding: reply.data, as: UTF8.self))
            }
        }
    }

    private func draftPath(_ place: DraftPlace) -> String {
        switch place {
        case .session(let session): sessionPath(session, "draft")
        case .project: "v1/projects/\(project)/draft"
        }
    }

    /// Brings the saved draft of `place` into the composer, once each time the composer
    /// turns to it. A session's sent marks are read with its draft, once for each session.
    ///
    /// When the composer turns from another draft of the same project, the page is first
    /// asked for any save it was holding, which goes to the draft that is left, and the
    /// frame says `turned`: the boxes on the screen are then that other draft's, and a place
    /// with no draft of its own does not take them as its own.
    private func loadDraft(_ place: DraftPlace) {
        if case .session(let session) = place, marksSession != session {
            marksSession = session
            loadMarks(session: session)
        }
        guard draftPlace != place, let token = Self.deviceToken() else { return }
        draftPlace = place
        let turned = shownPlace != nil
        if turned { send("flushDraft", "null") }
        let client = client
        let path = draftPath(place)
        Task {
            if turned { try? await Task.sleep(for: Self.draftFlushWait) }
            guard place == draftPlace, client.base == self.client.base else { return }
            // A save still on its way is read after it has landed, as it is when the window
            // left a session a moment ago and has come back (KYO-761), and a save asked for
            // while this read is out is made after it.
            let previous = draftTask
            draftTask = Task {
                await previous?.value
                guard let reply = try? await client.authorised("GET", path, token: token)
                else {
                    // The server was not reached. The draft is asked for again when the
                    // session is next read or listed, and until then nothing is saved over it.
                    if place == draftPlace, client.base == self.client.base { draftPlace = nil }
                    return
                }
                guard
                    reply.status == 200, place == draftPlace, client.base == self.client.base,
                    var frame = try? JSONSerialization.jsonObject(with: reply.data)
                        as? [String: Any]
                else { return }
                frame["turned"] = turned
                guard let json = try? JSONSerialization.data(withJSONObject: frame) else { return }
                draftEtags[path] = frame["etag"] as? String
                handedEtags[path] = frame["etag"] as? String
                shownPlace = place
                send("draft", String(decoding: json, as: UTF8.self))
                // Prompts the server had not acknowledged when the app last quit are newer
                // than its draft, and take its place in the composer (KYO-782). The page
                // saves them as it saves anything typed, and they are forgotten here once
                // a save has been acknowledged.
                if let kept = KeptDraft.kept(
                    in: .standard, server: client.base.absoluteString, project: project)
                {
                    restoredDraft = true
                    send("restoreDraft", kept.body)
                    send(
                        "note",
                        #"{"text": "Restored what had not been saved when the app last quit."}"#)
                }
            }
        }
    }

    /// The followed session is live or is not, as a list of the project's sessions just
    /// read says. A session that has ended leaves its unsent boxes to the project, and the
    /// composer turns to the project's draft, where the server has put what the session's
    /// draft held. Reading it from the server, and not keeping what this window shows, is
    /// what makes the newest copy win when another device saved to the session last.
    private func followedSession(_ session: String, live: Bool) {
        guard session == current else { return }
        let place: DraftPlace = live ? .session(session) : .project
        // A draft that could not be read is asked for again here.
        if draftPlace == nil || draftPlace == (live ? .project : .session(session)) {
            loadDraft(place)
        }
        showAgenda(live ? session : nil)
    }

    /// Turns the composer's task list to that of `session`, a live one, or to none (KYO-771).
    private func showAgenda(_ session: String?) {
        guard session != agendaSession else { return }
        agendaSession = session
        agendaAnswered = false
        guard let session else {
            send("agenda", #"{"state": "absent"}"#)
            return
        }
        loadAgenda(session)
    }

    private func agendaFrame(_ fields: [String: Any]) -> String {
        guard let json = try? JSONSerialization.data(withJSONObject: fields) else { return "null" }
        return String(decoding: json, as: UTF8.self)
    }

    /// The server's sentence in a refusal, when it sent one.
    private func refusal(in data: Data) -> String? {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
    }

    /// Reads the list of `session` and hands it to the page, or says why it cannot be read.
    /// A list that could not be reached is left as the page shows it, and is read again when
    /// the stream next opens, since the stream's first event carries the list's etag.
    private func loadAgenda(_ session: String) {
        guard let token = Self.deviceToken() else {
            send(
                "agenda",
                agendaFrame([
                    "state": "unavailable",
                    "why": "The session's list needs this device's token, which is not in the keychain.",
                ]))
            return
        }
        let client = client
        let path = sessionPath(session, "agenda")
        let previous = agendaTask
        agendaTask = Task {
            await previous?.value
            guard session == agendaSession, client.base == self.client.base,
                let reply = try? await client.authorised("GET", path, token: token),
                session == agendaSession, client.base == self.client.base
            else { return }
            showAgenda(reply: reply, session: session, saved: false)
        }
    }

    /// Hands the page a list the server answered with, or the reason it gave for not reading it.
    private func showAgenda(reply: (data: Data, status: Int), session: String, saved: Bool) {
        guard reply.status == 200 || reply.status == 201,
            var frame = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
        else {
            if reply.status == 401 || reply.status == 403 {
                agendaAnswered = true
                // A token that is not known, or lacks the permission: the server says which.
                let why = refusal(in: reply.data) ?? "This device may not read the session's list."
                send("agenda", agendaFrame(["state": "unavailable", "why": why]))
            }
            return
        }
        agendaEtags[session] = frame["etag"] as? String
        agendaAnswered = true
        frame["state"] = "shown"
        frame["saved"] = saved
        send("agenda", agendaFrame(frame))
    }

    /// The stream said the list of `session` has the etag in `event`: read it if this window
    /// does not hold that one. Its own saves are held already.
    private func agendaChanged(_ event: String, session: String) {
        guard session == agendaSession,
            let object = try? JSONSerialization.jsonObject(with: Data(event.utf8)) as? [String: Any]
        else { return }
        let stated = (object["agenda"] as? [String: Any] ?? object)["etag"] as? String
        if !agendaAnswered || stated != agendaEtags[session] { loadAgenda(session) }
    }

    /// Saves the list as the person, with the etag of the copy the page was shown. The answer
    /// is the saved list. A save refused as stale is said to the page, which keeps what the
    /// person typed, and the list is then read again so that the newer one is shown.
    func saveAgenda(body: [String: Any]) {
        guard let session = agendaSession,
            let data = try? JSONSerialization.data(withJSONObject: body)
        else { return }
        guard let token = Self.deviceToken() else {
            send("agendaRefused", agendaFrame(["status": 401]))
            return
        }
        let client = client
        let path = sessionPath(session, "agenda")
        let previous = agendaTask
        agendaTask = Task {
            await previous?.value
            let etag = agendaEtags[session]
            guard
                let reply = try? await client.authorised(
                    "PUT", path, token: token, body: data, ifMatch: etag,
                    ifNoneMatch: etag == nil ? "*" : nil)
            else {
                if session == agendaSession { send("agendaRefused", agendaFrame(["status": 0])) }
                return
            }
            guard session == agendaSession, client.base == self.client.base else { return }
            if reply.status == 200 || reply.status == 201 {
                showAgenda(reply: reply, session: session, saved: true)
                return
            }
            var refused: [String: Any] = ["status": reply.status]
            if let error = refusal(in: reply.data) { refused["error"] = error }
            send("agendaRefused", agendaFrame(refused))
            // The list as it is now, whoever changed it; this runs after the task that asked.
            loadAgenda(session)
        }
    }

    private func etag(in data: Data) -> String? {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["etag"] as? String
    }

    /// The stream said the draft of `place` was saved (KYO-798): the session's stream with
    /// the new etag as `stated`, the project's with none. A draft whose etag this window
    /// holds is its own save, or one it has read, and is not read again. The read waits for
    /// a save still on its way, so that the save's own announcement finds its etag held.
    private func draftChanged(_ place: DraftPlace, stated: String?? = nil) {
        guard place == shownPlace, place == draftPlace, let token = Self.deviceToken()
        else { return }
        let client = client
        let path = draftPath(place)
        let previous = draftTask
        draftTask = Task {
            await previous?.value
            guard place == shownPlace, place == draftPlace, client.base == self.client.base
            else { return }
            if case .some(let etag) = stated, etag == draftEtags[path] { return }
            await handDraft(place, path: path, client: client, token: token)
        }
    }

    /// The session stream's word of the session's draft: `ready` states it under `draft`
    /// when the stream opens or resumes, and `draft` is the change itself.
    private func draftChanged(_ event: String, session: String) {
        guard
            let object = try? JSONSerialization.jsonObject(with: Data(event.utf8)) as? [String: Any]
        else { return }
        // A server from before drafts were announced says nothing of one in `ready`.
        guard let stated = object["draft"] as? [String: Any] ?? (object["etag"] != nil ? object : nil)
        else { return }
        draftChanged(.session(session), stated: .some(stated["etag"] as? String))
    }

    /// Reads the draft of `place` and hands it to the page as one saved from another device,
    /// unless this window already holds that one. The page merges it with the prompts on
    /// the screen: those not edited here follow it, and those with unsaved edits keep them.
    private func handDraft(
        _ place: DraftPlace, path: String, client: ServeClient, token: String
    ) async {
        guard let reply = try? await client.authorised("GET", path, token: token),
            reply.status == 200,
            let frame = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
        else { return }
        let etag = frame["etag"] as? String
        // The etag is taken only with the draft it names: a later save made under it would
        // otherwise replace boxes the page was never shown.
        guard etag != draftEtags[path], place == shownPlace, client.base == self.client.base
        else { return }
        draftEtags[path] = etag
        handedEtags[path] = etag
        send("draftElsewhere", String(decoding: reply.data, as: UTF8.self))
    }

    /// Replaces the draft the composer was last given. Saves run one at a time, so each
    /// uses the last etag. The task returned answers whether the server acknowledged this
    /// save; nil is returned when there is nowhere to save to.
    ///
    /// The page numbers each save it asks for (`seq`) and states the etag of the draft it was
    /// last handed (`seen`); neither is part of the draft. An acknowledged save is said back
    /// to the page by its number, which is how the page knows what the server holds
    /// (KYO-798). A save asked for before the page had merged a draft handed to it since is
    /// not made, because it would replace what another device saved: the page's merge asks
    /// for a save of its own.
    @discardableResult
    func saveDraft(body: [String: Any]) -> Task<Bool, Never>? {
        var body = body
        let seq = body.removeValue(forKey: "seq") as? Int
        let seen = body.removeValue(forKey: "seen")
        guard let place = shownPlace, let token = Self.deviceToken(),
            let data = try? JSONSerialization.data(withJSONObject: body)
        else { return nil }
        let previous = draftTask
        // The server and the draft are those of the moment the page asked. The page asks
        // just before it leaves a project, and the save is then made after the window has
        // turned to the next one (KYO-761).
        let client = client
        let path = draftPath(place)
        saveCount += 1
        let count = saveCount
        unsavedDraft = (
            count,
            KeptDraft(
                server: client.base.absoluteString, project: project,
                body: String(decoding: data, as: UTF8.self))
        )
        let save = Task { () -> Bool in
            await previous?.value
            if let seen, seen as? String != handedEtags[path] { return false }
            let etag = draftEtags[path]
            guard
                let reply = try? await client.authorised(
                    "PUT", path, token: token, body: data, ifMatch: etag,
                    ifNoneMatch: etag == nil ? "*" : nil)
            else { return false }
            if reply.status == 200 || reply.status == 201 {
                draftEtags[path] = self.etag(in: reply.data)
                if unsavedDraft?.count == count { unsavedDraft = nil }
                if restoredDraft {
                    restoredDraft = false
                    KeptDraft.forget(in: .standard)
                }
                if let seq, place == shownPlace, client.base == self.client.base {
                    send("draftSaved", #"{"seq": \#(seq)}"#)
                }
                return true
            } else if reply.status == 412 {
                // Another device saved first. Its draft is read and handed to the page,
                // which keeps what was typed here beside it, says so, and saves the two
                // together (KYO-798).
                await handDraft(place, path: path, client: client, token: token)
            }
            return false
        }
        latestSave = save
        draftTask = Task { _ = await save.value }
        return save
    }

    /// Before the app quits (KYO-782): the page is asked for a save it is holding for a
    /// pause in the typing, the save is made, and the server's acknowledgement is waited
    /// for, all within `quitLimit`. What the server has not acknowledged by then is kept
    /// on this machine, and `loadDraft` gives it back to the composer at the next launch.
    func settleDraftBeforeQuit() async {
        let outcome = await settleDraft(limit: Self.quitLimit) { await self.sendPendingDraft() }
        if outcome == .unsaved, let unsaved = unsavedDraft {
            unsaved.kept.keep(in: .standard)
        }
    }

    /// Sends the page's pending save and answers whether the server acknowledged the
    /// latest save, or nil when the server already holds everything that was typed.
    private func sendPendingDraft() async -> Bool? {
        if let webView {
            // The page answers in the call's own value, as text, so that the app holds the
            // prompts before it waits on anything else.
            let answer: String? = await withCheckedContinuation { continuation in
                webView.evaluateJavaScript("JSON.stringify(window.kyo.pendingDraft())") { value, _ in
                    continuation.resume(returning: value as? String)
                }
            }
            if let data = answer?.data(using: .utf8),
                let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            {
                if saveDraft(body: body) == nil {
                    // There is nowhere to save to: no draft has been read, or no token.
                    saveCount += 1
                    unsavedDraft = (
                        saveCount,
                        KeptDraft(
                            server: client.base.absoluteString, project: project,
                            body: String(decoding: data, as: UTF8.self))
                    )
                    return false
                }
            }
        }
        guard unsavedDraft != nil else { return nil }
        return await latestSave?.value ?? false
    }

    /// The file types the server keeps as they are. Anything else an image can be made from,
    /// such as HEIC from an Apple camera, is sent as JPEG.
    private static let keptTypes: Set<String> = [
        "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
    ]

    private static let largeImageBytes = 3 * 1024 * 1024
    private static let longestEdge = 4096

    /// An image as a JPEG no longer than `longestEdge` on a side, turned the right way up.
    private static func jpeg(from data: Data) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: longestEdge,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
        else { return nil }
        let output = NSMutableData()
        guard
            let destination = CGImageDestinationCreateWithData(
                output, "public.jpeg" as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(
            destination, image,
            [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
        return CGImageDestinationFinalize(destination) ? output as Data : nil
    }

    /// Uploads a file pasted into a prompt to the machine the session runs on, and tells the
    /// composer the id and path the server gave it.
    func attach(body: [String: Any]) {
        let ref = body["ref"] as? String ?? ""
        let asked = current
        func fail(_ why: String) {
            guard asked == current else { return }
            send("attached", #"{"ref": \#(quoted(ref)), "error": \#(quoted(why))}"#)
        }
        guard let session = current, let token = Self.deviceToken() else {
            return fail("No session or device token to attach with.")
        }
        guard var data = Data(base64Encoded: body["data"] as? String ?? ""), !data.isEmpty else {
            return fail("That file could not be read.")
        }
        var type = body["type"] as? String ?? ""
        // A photo pasted from the clipboard arrives as a PNG many times the size of the file
        // it came from, so a large image is sent as JPEG, as is any type the server does not keep.
        let large = type.hasPrefix("image/") && data.count > Self.largeImageBytes
        if !Self.keptTypes.contains(type) || large {
            guard let jpeg = Self.jpeg(from: data) else {
                return fail("That kind of file cannot be attached.")
            }
            data = jpeg
            type = "image/jpeg"
        }
        let client = client
        let path = sessionPath(session, "attachments")
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", path, token: token, body: data, contentType: type)
            else { return fail("The server could not be reached.") }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            let file = object?["attachment"] as? [String: Any] ?? object
            guard reply.status == 200 || reply.status == 201, let id = file?["id"] as? String else {
                return fail(refusal(reply.data))
            }
            guard session == current else { return }
            send(
                "attached",
                #"{"ref": \#(quoted(ref)), "id": \#(quoted(id)), "path": \#(quoted(file?["path"] as? String))}"#)
        }
    }

    /// The frame the page rebuilds a taken-back prompt from (KYO-809): its text, and the
    /// prompt's own marks when it had any, which the server returns to whichever device asks.
    /// A prompt sent with marks was delivered with lines added above its paragraphs, so the
    /// text is the prompt as it was typed.
    nonisolated static func retractedFrame(_ data: Data) -> String {
        let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        let held = object?["held"] as? [String: Any] ?? object ?? [:]
        let prompt = held["prompt"] as? [String: Any]
        let typed = prompt?["segments"] as? [[String: Any]]
        let segments = typed ?? held["segments"] as? [[String: Any]] ?? []
        var frame: [String: Any] = ["text": segments.compactMap { $0["text"] as? String }.joined()]
        if let marks = prompt?["marks"] as? [[String: Any]], !marks.isEmpty {
            frame["prompt"] = ["marks": marks]
        }
        guard let json = try? JSONSerialization.data(withJSONObject: frame) else {
            return #"{"text": ""}"#
        }
        return String(decoding: json, as: UTF8.self)
    }

    /// Takes a held message back, returning its text to the composer.
    func retract(id: String) {
        guard let session = current, let token = Self.deviceToken() else { return }
        let client = client
        let path = sessionPath(session, "held")
        Task {
            guard
                let reply = try? await client.authorised(
                    "DELETE", path, token: token, ifMatch: id),
                reply.status == 200
            else {
                guard session == current else { return }
                send("note", #"{"text": "It could not be taken back; it may have been sent."}"#)
                return
            }
            // The page waits for this answer before it leaves a project, as it does for a
            // send's, so the text returns to the composer it was taken from.
            guard session == current else { return }
            send("retracted", Self.retractedFrame(reply.data))
        }
    }

    /// Delivers the held message during the turn (KYO-722). Claude Code queues it and gives it to
    /// the agent at its next tool call. The server's `held` event then clears the held box; a
    /// refusal is said in words and the message stays held.
    func sendNow(id: String) {
        guard let session = current, let token = Self.deviceToken() else { return }
        let client = client
        let path = sessionPath(session, "held-now")
        Task {
            let reply = try? await client.authorised(
                "POST", path, token: token, body: Data("{}".utf8), ifMatch: id)
            guard session == current else { return }
            guard let reply else {
                send("note", #"{"text": "The server could not be reached."}"#)
                return
            }
            if reply.status != 200 {
                send("note", #"{"text": \#(quoted(refusal(reply.data)))}"#)
            }
        }
    }

    func interrupt() {
        guard let session = current, let token = Self.deviceToken() else { return }
        let client = client
        let path = sessionPath(session, "interrupt")
        Task {
            let reply = try? await client.authorised(
                "POST", path, token: token, body: Data("{}".utf8))
            guard reply?.status != 200, session == current else { return }
            send("note", #"{"text": "The interrupt was refused."}"#)
        }
    }

    // MARK: The terminal drawer (KYO-721)

    private static let terminalTokenHelp =
        "The terminal needs a device token holding the terminal permission. Issue one on the "
        + "server's machine with `kyo devices issue <name> --permission terminal`, repeating "
        + "--permission for send, notes and start, and enter it in Settings."
    /// How much one input request carries. The server takes 65536 bytes, and a scalar is at
    /// most four.
    private static let inputScalars = 8000

    /// What the page's terminal drawer asks: to open or close the stream, to type, or to
    /// state the size of its view.
    func terminal(body: [String: Any]) {
        if let cols = body["cols"] as? Int, let rows = body["rows"] as? Int {
            terminalSize = (cols, rows)
        }
        switch body["action"] as? String {
        case "open":
            terminalOpen = true
            terminalToken = Self.deviceToken()
            openTerminal()
        case "close":
            terminalOpen = false
            terminalSession = nil
            closeTerminal()
        case "input":
            if let data = body["data"] as? String { type(data) }
        case "resize":
            resizeTerminal()
        default:
            break
        }
    }

    private func terminalFrame(_ name: String, _ event: String) {
        send("terminalEvent", #"{"name": \#(quoted(name)), "event": \#(event)}"#)
    }

    private func closeTerminal() {
        terminalTask?.cancel()
        terminalTask = nil
        terminalConnection = nil
        typed = ""
    }

    /// Follows the current session's terminal while the drawer is open. A session with no
    /// live process has none, and the page then shows the pane's last text.
    private func openTerminal() {
        closeTerminal()
        guard terminalOpen else { return }
        terminalSession = current
        guard let session = current, currentLive else {
            terminalFrame("refused", #"{"error": "This session has no terminal to show."}"#)
            return
        }
        guard let token = terminalToken else {
            terminalFrame("refused", #"{"error": \#(quoted(Self.terminalTokenHelp))}"#)
            return
        }
        terminalTask = Task { await followTerminal(session: session, token: token) }
    }

    private func followTerminal(session: String, token: String) async {
        while !Task.isCancelled {
            var components = URLComponents(
                url: client.base.appending(path: sessionPath(session, "terminal")),
                resolvingAgainstBaseURL: false)!
            if let size = terminalSize {
                components.queryItems = [
                    URLQueryItem(name: "cols", value: String(size.cols)),
                    URLQueryItem(name: "rows", value: String(size.rows)),
                ]
            }
            var request = URLRequest(url: components.url!)
            request.timeoutInterval = 120
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            do {
                for try await event in Self.terminalEvents(request) {
                    // A stream that was cancelled still yields what it had buffered.
                    if Task.isCancelled { return }
                    if event.name == "ready" {
                        let object =
                            try? JSONSerialization.jsonObject(with: Data(event.data.utf8))
                            as? [String: Any]
                        terminalConnection = object?["connection"] as? String
                    }
                    terminalFrame(event.name, event.data)
                    if event.name == "closed" {
                        terminalConnection = nil
                        return
                    }
                }
            } catch ServeError.status(_, let body) {
                if Task.isCancelled { return }
                // A refusal is the server's own sentence, which names the remedy. It is not
                // asked again until the drawer is opened again.
                terminalConnection = nil
                terminalFrame("refused", #"{"error": \#(quoted(refusal(Data(body.utf8))))}"#)
                return
            } catch {
                if Task.isCancelled { return }
            }
            guard !Task.isCancelled else { return }
            terminalConnection = nil
            terminalFrame(
                "note", #"{"text": "The terminal's connection was lost. Connecting again."}"#)
            try? await Task.sleep(for: .seconds(1))
        }
    }

    /// The terminal stream's events. It carries the device token, which the session's event
    /// stream does not, and a refusal's body, which says why.
    private nonisolated static func terminalEvents(
        _ request: URLRequest
    ) -> AsyncThrowingStream<ServerEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard code == 200 else {
                        var body: [UInt8] = []
                        for try await byte in bytes where body.count < 65536 { body.append(byte) }
                        throw ServeError.status(code, String(decoding: body, as: UTF8.self))
                    }
                    var parser = ServerEventParser()
                    var line: [UInt8] = []
                    for try await byte in bytes {
                        guard byte == UInt8(ascii: "\n") else {
                            line.append(byte)
                            continue
                        }
                        if let event = parser.feed(line: String(decoding: line, as: UTF8.self)) {
                            continuation.yield(event)
                        }
                        line.removeAll(keepingCapacity: true)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func terminalPost(_ route: String, _ body: [String: Any]) async -> String? {
        guard let session = terminalSession, let connection = terminalConnection,
            let token = terminalToken
        else { return "The terminal is not connected." }
        var body = body
        body["connection"] = connection
        guard let data = try? JSONSerialization.data(withJSONObject: body),
            let reply = try? await client.authorised(
                "POST", sessionPath(session, "terminal/\(route)"), token: token, body: data)
        else { return "The server could not be reached." }
        return reply.status == 200 ? nil : refusal(reply.data)
    }

    /// Sends what was typed, one request at a time and in order. Keys typed while a request
    /// is out go together in the next.
    private func type(_ data: String) {
        typed += data
        guard !typing else { return }
        typing = true
        Task {
            while !typed.isEmpty {
                let part = String(typed.unicodeScalars.prefix(Self.inputScalars))
                typed = String(typed.unicodeScalars.dropFirst(part.unicodeScalars.count))
                if let problem = await terminalPost("input", ["data": part]) {
                    typed = ""
                    terminalFrame("note", #"{"text": \#(quoted(problem))}"#)
                }
            }
            typing = false
        }
    }

    /// States the view's size. The requests run one after another, so the last one stands.
    private func resizeTerminal() {
        let previous = resizing
        resizing = Task {
            await previous?.value
            guard let size = terminalSize, terminalConnection != nil else { return }
            _ = await terminalPost("resize", ["cols": size.cols, "rows": size.rows])
        }
    }

    private static let startWatchPoll = Duration.seconds(2)
    private static let startWatchLimit = Duration.seconds(600)

    /// Asks the server to start this project's orchestrator session (KYO-719), and tells the
    /// page what came of it. The page puts the reply into words; a refusal is the server's
    /// own sentence, which names the remedy.
    func startOrchestrator() {
        guard let token = Self.deviceToken() else {
            let why =
                "Starting a session needs a device token holding the start permission. Issue "
                + "one on the server's machine with `kyo devices issue <name> --permission "
                + "start`, repeating --permission for send and notes, and enter it in Settings."
            send("start", #"{"phase": "failed", "text": \#(quoted(why))}"#)
            return
        }
        send("start", #"{"phase": "starting"}"#)
        let project = project
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", "v1/projects/\(project)/orchestrator/start", token: token,
                    body: Data("{}".utf8))
            else {
                guard project == self.project else { return }
                send("start", #"{"phase": "failed", "text": "The server could not be reached."}"#)
                return
            }
            guard project == self.project else { return }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            let document = object == nil ? "null" : String(decoding: reply.data, as: UTF8.self)
            send(
                "start",
                #"{"phase": "answered", "status": \#(reply.status), "reply": \#(document)}"#)
            // A session already running may not be listed yet, so it is watched for as well.
            let running = object?["reason"] as? String == "already-running"
            if reply.status == 200 || running { followStartedSession() }
        }
    }

    /// Shows the project's live session once the server lists one. A session is listed when
    /// Claude Code has begun in it, which waits on the trust prompt when there is one, so the
    /// watch is long and ends by itself.
    private func followStartedSession() {
        startWatch?.cancel()
        let project = project
        startWatch = Task {
            let deadline = ContinuousClock.now + Self.startWatchLimit
            while ContinuousClock.now < deadline {
                try? await Task.sleep(for: Self.startWatchPoll)
                guard !Task.isCancelled, project == self.project else { return }
                guard let rows = try? await client.sessions(project: project),
                    let row = rows.first(where: { $0.live && $0.has_pane })
                        ?? rows.first(where: { $0.live })
                else { continue }
                guard !Task.isCancelled else { return }
                if current != row.session_id { select(session: row.session_id) }
                await sendMenu()
                return
            }
            // The button is given back, so that a session that never began can be started.
            let why =
                "The session was started, but the server has not listed it after ten minutes. "
                + "`kyo orchestrator list` on the server's machine says what is running."
            send("start", #"{"phase": "failed", "text": \#(quoted(why))}"#)
        }
    }

    /// Fetches one of the person's images for the page, which may load only its own files.
    func loadImage(turn: String, index: Int) {
        guard let session = current else { return }
        Task {
            guard
                let image = try? await client.image(
                    project: project, session: session, turn: turn, index: index),
                session == current
            else { return }
            let data = "data:\(image.mediaType);base64,\(image.data.base64EncodedString())"
            let key = "\(turn)/\(index)"
            send("image", #"{"key": \#(quoted(key)), "data": \#(quoted(data))}"#)
        }
    }

    /// A sent attachment's image, by its id, for the prompt it was sent in (KYO-720).
    func loadAttachment(_ id: String) {
        guard let session = current, let token = Self.deviceToken() else { return }
        Task {
            guard
                let reply = try? await client.authorised(
                    "GET", sessionPath(session, "attachments/\(id)"), token: token),
                reply.status == 200
            else {
                if session == current {
                    send("image", #"{"key": \#(quoted("a:" + id)), "data": null}"#)
                }
                return
            }
            guard session == current else { return }
            let bytes = [UInt8](reply.data.prefix(4))
            let type =
                bytes.starts(with: [0x89, 0x50]) ? "image/png"
                : bytes.starts(with: [0xFF, 0xD8]) ? "image/jpeg"
                : bytes.starts(with: [0x47, 0x49]) ? "image/gif" : "image/webp"
            let data = "data:\(type);base64,\(reply.data.base64EncodedString())"
            send("image", #"{"key": \#(quoted("a:" + id)), "data": \#(quoted(data))}"#)
        }
    }

    // MARK: The Records view (KYO-732)

    private static let recordsHistoryKey = "recordsHistory"
    private static let recordsHistoryLimit = 30
    /// The gates that wait on the person, which the Awaiting you filter shows.
    private static let awaitingTypes = ["awaiting:human-run", "awaiting:human-review"]

    /// The records viewed most recently are kept in the app's own defaults, under the server
    /// and project they belong to. They are what one person looked at on one machine, so they
    /// are not written to the project's store, and reading them needs no server.
    private var recordsHistoryName: String { "\(client.base.absoluteString) \(project)" }

    private var recordsHistory: [String] {
        let all = UserDefaults.standard.dictionary(forKey: Self.recordsHistoryKey)
        return all?[recordsHistoryName] as? [String] ?? []
    }

    private func saveRecordsHistory(_ ids: [String]) {
        var all = UserDefaults.standard.dictionary(forKey: Self.recordsHistoryKey) ?? [:]
        all[recordsHistoryName] = Array(ids.prefix(Self.recordsHistoryLimit))
        UserDefaults.standard.set(all, forKey: Self.recordsHistoryKey)
    }

    /// Tells the page which project its Records view shows, and the history kept for it. The
    /// page drops what it held of another project.
    private func sendRecordsStart() {
        let history =
            (try? JSONSerialization.data(withJSONObject: recordsHistory)).map {
                String(decoding: $0, as: UTF8.self)
            } ?? "[]"
        send("records", #"{"project": \#(quoted(project)), "history": \#(history)}"#)
    }

    /// What the page's Records view asks: the lists, one record, or to keep its history.
    func records(body: [String: Any]) {
        switch body["want"] as? String {
        case "lists":
            loadRecordLists()
        case "record":
            if let id = body["id"] as? String { loadRecord(id) }
        case "history":
            if let ids = body["ids"] as? [String] { saveRecordsHistory(ids) }
        default:
            break
        }
    }

    /// A served command's JSON for the page, or `null` when it was refused or is not JSON.
    private func document(_ reply: (data: Data, status: Int)?) -> String {
        guard let reply, reply.status == 200,
            (try? JSONSerialization.jsonObject(with: reply.data)) != nil
        else { return "null" }
        return String(decoding: reply.data, as: UTF8.self)
    }

    /// Reads `kyo list`, `kyo next` and the gates that wait on the person, and hands the page
    /// all three. These are reads any viewer may make, so no device token is sent.
    private func loadRecordLists() {
        let project = project
        let client = client
        Task {
            async let list = try? client.command(project: project, "list")
            async let next = try? client.command(project: project, "next")
            async let gates = try? client.command(
                project: project, "gates",
                flags: Self.awaitingTypes.map { (name: "type", value: $0) })
            let (listed, ready, waiting) = await (list, next, gates)
            guard project == self.project else { return }
            var problem = "null"
            if listed == nil {
                problem = quoted("The server could not be reached.")
            } else if let listed, listed.status != 200 {
                problem = quoted(refusal(listed.data))
            }
            send(
                "recordLists",
                #"{"project": \#(quoted(project)), "list": \#(document(listed)), "next": "#
                    + #"\#(document(ready)), "gates": \#(document(waiting)), "error": \#(problem)}"#)
        }
    }

    /// Reads one record as `kyo show --json` prints it. A refusal is the server's sentence.
    private func loadRecord(_ id: String) {
        let project = project
        let client = client
        Task {
            let reply = try? await client.command(project: project, "show", positionals: [id])
            guard project == self.project else { return }
            var problem = "null"
            if reply == nil {
                problem = quoted("The server could not be reached.")
            } else if let reply, reply.status != 200 {
                problem = quoted(refusal(reply.data))
            }
            send(
                "record",
                #"{"id": \#(quoted(id)), "document": \#(document(reply)), "error": \#(problem)}"#)
        }
    }

    /// Follows the project's change events and tells the page of each, so that the Records
    /// view reads again what changed. After a lost connection the page is told once more,
    /// since a change made meanwhile was not heard. A change of the project's own composer
    /// draft is the app's to read (KYO-798), and matters while no session is live.
    private func followProject() async {
        let project = project
        var retry = Backoff()
        var lost = false
        let client = client
        while !Task.isCancelled {
            if lost {
                send("recordsChanged", #"{"kind": "store"}"#)
                draftChanged(.project)
            }
            do {
                for try await event in client.projectEvents(project: project)
                where event.name == "changed" {
                    // A stream that was cancelled still yields what it had buffered.
                    if Task.isCancelled { return }
                    retry.reset()
                    if event.data.contains(#""draft""#) {
                        draftChanged(.project)
                    } else {
                        send("recordsChanged", event.data)
                    }
                }
            } catch {
                if Task.isCancelled { return }
            }
            lost = true
            try? await Task.sleep(for: retry.next())
        }
    }

    // MARK: The Files view (KYO-736)

    private static let pathMappingServerKey = "pathMappingServer"
    private static let pathMappingHereKey = "pathMappingHere"

    /// The optional path mapping: a prefix of a path on the server's machine, and what it
    /// stands for on this one. It is this device's own, so it is kept in the app's defaults.
    static var savedPathMapping: (server: String, here: String) {
        (
            UserDefaults.standard.string(forKey: pathMappingServerKey) ?? "",
            UserDefaults.standard.string(forKey: pathMappingHereKey) ?? ""
        )
    }

    static func savePathMapping(server: String, here: String) {
        UserDefaults.standard.set(
            server.trimmingCharacters(in: .whitespaces), forKey: pathMappingServerKey)
        UserDefaults.standard.set(
            here.trimmingCharacters(in: .whitespaces), forKey: pathMappingHereKey)
    }

    /// The device token the file routes are asked with. It is read when the app starts and
    /// when Settings are saved, since reading it starts a process and a request must not.
    private var filesToken: String?

    /// Whether the server's machine is this one, which it is when its address is loopback.
    private var serverIsThisMachine: Bool {
        ["127.0.0.1", "localhost", "::1", "[::1]"].contains(client.base.host() ?? "")
    }

    /// Tells the page which project's files it shows, whether a path on the server's machine
    /// is a path here, and the mapping Settings holds. The page asks again what was refused.
    private func sendFilesStart() {
        filesToken = Self.deviceToken()
        let mapping = Self.savedPathMapping
        let mappings =
            mapping.server.isEmpty || mapping.here.isEmpty
            ? "[]" : #"[{"server": \#(quoted(mapping.server)), "here": \#(quoted(mapping.here))}]"#
        send(
            "files",
            #"{"project": \#(quoted(project)), "same_machine": \#(serverIsThisMachine), "#
                + #""mappings": \#(mappings)}"#)
    }

    /// A file route's JSON for the page, or `null` when it was refused or is not JSON.
    private func filesDocument(_ reply: ServeClient.FileReply?) -> String {
        guard let reply, reply.status == 200,
            (try? JSONSerialization.jsonObject(with: reply.data)) != nil
        else { return "null" }
        return String(decoding: reply.data, as: UTF8.self)
    }

    /// Why a file route gave nothing, quoted for the page: the server's own sentence, which
    /// names the remedy, or that it was not reached. `null` when it answered.
    private func filesProblem(_ reply: ServeClient.FileReply?) -> String {
        guard let reply else { return quoted("The server could not be reached.") }
        return reply.status == 200 || reply.status == 304 ? "null" : quoted(refusal(reply.data))
    }

    /// What the page's Files view and its path links ask: the roots, one folder, one file,
    /// where a path written in a reply is, and whether a path is a file on this machine,
    /// which Reveal in Finder then shows. Without a device token the request is sent
    /// without one, and the server's refusal says how a token is issued.
    func files(body: [String: Any]) {
        let project = project
        let client = client
        let token = filesToken
        let root = body["root"] as? String ?? ""
        let path = body["path"] as? String ?? ""
        // The scratch root is one directory for each session, named after the root.
        let session = body["session"] as? String
        let parts = path.split(separator: "/").map(String.init)
        let named = #""root": \#(quoted(root)), "path": \#(quoted(path)), "session": \#(quoted(session))"#
        switch body["want"] as? String {
        case "roots":
            Task {
                let reply = try? await client.files(project: project, segments: [], token: token)
                guard project == self.project else { return }
                send(
                    "filesRoots",
                    #"{"status": \#(reply?.status ?? 0), "document": \#(filesDocument(reply)), "#
                        + #""error": \#(filesProblem(reply))}"#)
            }
        case "tree":
            Task {
                let reply = try? await client.files(
                    project: project, segments: [root] + (session.map { [$0] } ?? []) + ["tree"] + parts,
                    token: token)
                guard project == self.project else { return }
                send(
                    "filesTree",
                    #"{\#(named), "status": \#(reply?.status ?? 0), "document": "#
                        + #"\#(filesDocument(reply)), "error": \#(filesProblem(reply))}"#)
            }
        case "blob":
            Task {
                let reply = try? await client.files(
                    project: project, segments: [root] + (session.map { [$0] } ?? []) + ["blob"] + parts,
                    token: token, ifNoneMatch: body["etag"] as? String)
                guard project == self.project else { return }
                var text = "null"
                var data = "null"
                let type = reply?.contentType ?? ""
                let mediaType = type.split(separator: ";").first.map {
                    $0.trimmingCharacters(in: .whitespaces)
                }
                if let reply, reply.status == 200 {
                    // A text type states its charset, which is how this route tells text
                    // from other bytes. An image travels as the chat's images do.
                    if type.lowercased().contains("charset=") {
                        text = quoted(String(decoding: reply.data, as: UTF8.self))
                    } else if let mediaType, mediaType.hasPrefix("image/") {
                        data = quoted("data:\(mediaType);base64,\(reply.data.base64EncodedString())")
                    }
                }
                send(
                    "filesBlob",
                    #"{\#(named), "status": \#(reply?.status ?? 0), "etag": \#(quoted(reply?.etag)), "#
                        + #""media_type": \#(quoted(mediaType)), "size": "#
                        + (reply?.status == 200 ? String(reply?.data.count ?? 0) : "null")
                        + #", "text": \#(text), "data": \#(data), "error": \#(filesProblem(reply))}"#)
            }
        case "resolve":
            guard let given = body["given"] as? String else { return }
            Task {
                let reply = try? await client.files(
                    project: project, segments: ["resolve"], query: [(name: "path", value: given)],
                    token: token)
                guard project == self.project else { return }
                send(
                    "filesResolved",
                    #"{"given": \#(quoted(given)), "status": \#(reply?.status ?? 0), "document": "#
                        + #"\#(filesDocument(reply)), "error": \#(filesProblem(reply))}"#)
            }
        case "search":
            // One root at a time, so that a common word in one root cannot fill the server's
            // cap and leave the other root out (KYO-814).
            guard let text = body["q"] as? String else { return }
            Task {
                let reply = try? await client.files(
                    project: project, segments: ["search"],
                    query: [(name: "q", value: text), (name: "root", value: root)], token: token)
                guard project == self.project else { return }
                send(
                    "filesSearched",
                    #"{"q": \#(quoted(text)), "root": \#(quoted(root)), "status": \#(reply?.status ?? 0), "#
                        + #""document": \#(filesDocument(reply)), "error": \#(filesProblem(reply))}"#)
            }
        case "recent", "opened":
            // `opened` records a file the person opened; both answer with the list.
            let post = body["want"] as? String == "opened"
                ? try? JSONSerialization.data(withJSONObject: ["root": root, "path": path]) : nil
            Task {
                let reply = try? await client.files(
                    project: project, segments: ["recent"], token: token, post: post)
                guard project == self.project else { return }
                send(
                    "filesRecent",
                    #"{"status": \#(reply?.status ?? 0), "document": \#(filesDocument(reply)), "#
                        + #""error": \#(filesProblem(reply))}"#)
            }
        case "exists":
            let here = NSString(string: path).expandingTildeInPath
            let exists = path.hasPrefix("/") || path.hasPrefix("~/")
                ? FileManager.default.fileExists(atPath: here) : false
            send("filesExists", #"{"path": \#(quoted(path)), "exists": \#(exists)}"#)
        case "reveal":
            // Only a path that is a file or a folder on this machine has a place in Finder.
            let here = NSString(string: path).expandingTildeInPath
            guard here.hasPrefix("/"), FileManager.default.fileExists(atPath: here) else { return }
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: here)])
        default:
            break
        }
    }

    /// The page's History view (KYO-825): a search of this project's transcripts, and the
    /// results the person opened, read and added to. Each answer goes back with the request's
    /// own `ask` so the page can tell a stale one from the current.
    func history(body: [String: Any]) {
        let project = project
        let client = client
        let token = Self.deviceToken()
        let ask = body["ask"] as? Int ?? 0
        switch body["want"] as? String {
        case "search":
            guard let text = body["q"] as? String else { return }
            var query: [(name: String, value: String)] = [(name: "q", value: text)]
            for name in ["role", "cursor", "context"] {
                if let value = body[name] as? String, !value.isEmpty { query.append((name: name, value: value)) }
                if let value = body[name] as? Int { query.append((name: name, value: String(value))) }
            }
            Task {
                let reply = try? await client.history(
                    project: project, under: "search", query: query, token: token)
                guard project == self.project else { return }
                send(
                    "historySearched",
                    #"{"ask": \#(ask), "status": \#(reply?.status ?? 0), "#
                        + #""document": \#(filesDocument(reply)), "error": \#(filesProblem(reply))}"#)
            }
        case "turns", "marks":
            // A session read whole for the History pane, which never switches the chat
            // (KYO-836): its transcript objects, or the paragraphs sent with marks.
            guard let session = body["session"] as? String, let want = body["want"] as? String else {
                return
            }
            Task {
                let reply = try? await client.history(
                    project: project, under: "sessions", segments: [session, want], token: token)
                guard project == self.project else { return }
                send(
                    want == "turns" ? "historyTurns" : "historyMarks",
                    #"{"ask": \#(ask), "session": \#(quoted(session)), "status": \#(reply?.status ?? 0), "#
                        + #""document": \#(filesDocument(reply)), "error": \#(filesProblem(reply))}"#)
            }
        case "recent", "opened":
            var post: Data?
            if body["want"] as? String == "opened" {
                post = try? JSONSerialization.data(withJSONObject: [
                    "session": body["session"] as? String ?? "",
                    "message": body["message"] as? String ?? "",
                    "words": body["words"] as? String ?? "",
                ])
            }
            Task {
                let reply = try? await client.history(
                    project: project, under: "history", segments: ["recent"], token: token, post: post)
                guard project == self.project else { return }
                send(
                    "historyRecent",
                    #"{"status": \#(reply?.status ?? 0), "document": \#(filesDocument(reply)), "#
                        + #""error": \#(filesProblem(reply))}"#)
            }
        default:
            break
        }
    }

    private func send(_ function: String, _ json: String) {
        webView?.evaluateJavaScript("window.kyo.\(function)(\(json))") { _, error in
            if let error { print("[feed] \(function): \(error)") }
        }
    }

    private func quoted(_ text: String?) -> String {
        guard let text, let data = try? JSONEncoder().encode(text) else { return "null" }
        return String(decoding: data, as: UTF8.self)
    }

    /// The project's sessions and the followed session's changes, for the side panel. It is
    /// awaited by tasks the switch to another project cancels, and a task cancelled while
    /// its request was out may still be handed the answer, so it is dropped here.
    private func sendMenu() async {
        let project = project
        let client = client
        guard let sessions = try? await client.sessionsData(project: project),
            !Task.isCancelled
        else { return }
        var changes: Data?
        if let current {
            changes = try? await client.changes(project: project, session: current)
            guard !Task.isCancelled else { return }
        }
        takeRemote(from: changes)
        // The list is how the app learns that the followed session has ended (KYO-717), and
        // so when its unsent boxes become the project's (KYO-763).
        if let current, project == self.project,
            let rows = try? JSONDecoder().decode(ListedSessions.self, from: sessions).sessions,
            let row = rows.first(where: { $0.session_id == current })
        {
            followedSession(current, live: row.live)
        }
        let json =
            #"{"project": \#(quoted(project)), "current": \#(quoted(current)), "changes": "#
            + (changes.map { String(decoding: $0, as: UTF8.self) } ?? "null")
            + #", "sessions": "# + String(decoding: sessions, as: UTF8.self) + "}"
        send("menu", json)
    }

    /// Fetches one subagent's tool calls for the panel.
    func loadAgent(_ agent: String) {
        guard let session = current else { return }
        Task {
            guard
                let data = try? await client.subagent(
                    project: project, session: session, agent: agent),
                session == current
            else { return }
            send("agent", String(decoding: data, as: UTF8.self))
        }
    }

    /// The remote's name, or "No Remote" for a repository the server says has none.
    private func takeRemote(from json: Data?) {
        guard let json,
            let object = try? JSONSerialization.jsonObject(with: json) as? [String: Any],
            object.keys.contains("remote"), object["root"] is String
        else { return }
        remote = object["remote"] as? String ?? "No Remote"
        ahead = object["ahead"] as? Int
    }

    private func takeFacts(from json: Data) {
        if let carried = try? JSONDecoder().decode(FactsCarrier.self, from: json).facts {
            facts = carried
        }
    }

    private func becomeLive() {
        state = .live
        retry.reset()
    }

    /// The chat is not live, for `reason`; it stays dated from the moment it was first lost.
    private func lose(_ reason: String, since: Date = Date()) {
        if case .notLive(let first, _) = state {
            state = .notLive(since: first, reason: reason)
        } else {
            state = .notLive(since: since, reason: reason)
        }
    }

    private func run() async {
        while !Task.isCancelled {
            do {
                let sessions = try await client.sessions(project: project)
                // A request that answered as the task was cancelled does not throw, and
                // what follows would clear the chat of the project the window turned to.
                try Task.checkCancellation()
                noteSessionRead(nil)
                guard
                    let row = sessions.first(where: { $0.session_id == selected })
                        ?? sessions.first(where: { $0.live && $0.has_pane }) ?? sessions.first
                else {
                    current = nil
                    currentLive = false
                    if terminalOpen, terminalSession != nil { openTerminal() }
                    send("clear", "null")
                    // A project with no session keeps its boxes in the project's draft.
                    loadDraft(.project)
                    showAgenda(nil)
                    state = .noSession
                    retry.reset()
                    try await Task.sleep(for: .seconds(3))
                    continue
                }
                let turns = try await client.turns(project: project, session: row.session_id)
                try Task.checkCancellation()
                current = row.session_id
                currentLive = row.live
                send("clear", "null")
                send("apply", String(decoding: turns, as: UTF8.self))
                if terminalOpen, terminalSession != row.session_id { openTerminal() }
                takeFacts(from: turns)
                // With no live session the boxes are the project's, whichever ended session
                // the chat shows (KYO-763).
                loadDraft(row.live ? .session(row.session_id) : .project)
                showAgenda(row.live ? row.session_id : nil)
                await sendMenu()
                try Task.checkCancellation()
                guard row.live else {
                    // An ended session has nothing to follow, and the chat holds all of it.
                    becomeLive()
                    send("screen", #"{"screen": null}"#)
                    send("terminal", #"{"terminal": null}"#)
                    try await Task.sleep(for: .seconds(3600))
                    continue
                }
                // The chat is live once the stream says so, not when the turns have been read.
                await follow(session: row.session_id, turns: turns)
            } catch is CancellationError {
                return
            } catch {
                if Task.isCancelled { return }
                lose(ServeClient.describe(error))
                noteSessionRead(error)
                // The next attempt carries a token saved since this one was refused.
                if tokenNeeded != nil { takeSavedToken() }
                print("[feed] \(error)")
            }
            try? await Task.sleep(for: retry.next())
        }
    }

    /// Follows the session's stream until it asks for the session to be read again from the
    /// top, or is refused. The follower reconnects by itself after a lost connection, a
    /// restarted server, an error from the server's reader and a stream that has stopped
    /// carrying the session (KYO-725), and says each time whether the chat is live.
    private func follow(session: String, turns: Data) async {
        let cursor =
            (try? JSONSerialization.jsonObject(with: turns) as? [String: Any])?["cursor"] as? String
        let follower = SessionFollower(client: client, project: project, session: session)
        for await update in follower.updates(cursor: cursor) {
            // A stream that was cancelled still yields what it had buffered.
            if Task.isCancelled { return }
            switch update {
            case .live:
                becomeLive()
            case .notLive(let since, let reason):
                lose(reason, since: since)
                print("[feed] not live: \(reason)")
            case .event(let event):
                switch event.name {
                case "transcript":
                    send("apply", event.data)
                case "reply":
                    send("live", event.data)
                case "ready":
                    send("ready", event.data)
                    takeFacts(from: Data(event.data.utf8))
                    agendaChanged(event.data, session: session)
                    // A draft saved while the stream was away is read now (KYO-798).
                    draftChanged(event.data, session: session)
                case "draft":
                    // Saved from another device, or moved by the server.
                    draftChanged(event.data, session: session)
                case "agenda":
                    // Saved by the orchestrator, or from another device.
                    agendaChanged(event.data, session: session)
                case "facts":
                    send("facts", event.data)
                    takeFacts(from: Data(event.data.utf8))
                case "screen":
                    send("screen", event.data)
                case "terminal":
                    send("terminal", event.data)
                case "held":
                    send("held", event.data)
                    // A delivered prompt's paragraphs are filed just after its keys are sent.
                    if event.data.contains("delivered"), let session = current {
                        loadMarks(session: session, after: .seconds(2))
                    }
                case "reset", "session":
                    return  // Read the session again from the top.
                default:
                    break
                }
            }
        }
    }
}
