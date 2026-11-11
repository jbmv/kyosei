import SwiftUI

/// Where this device's `kyo serve` is, and the token it uses. Records, documents and ideas
/// are read without a token; a session is read, and anything is sent, with one (KYO-791).
/// The project is chosen from the dropdown in the window's top bar and is not a setting
/// (KYO-761): saving here keeps the project the window shows, and when the server named
/// does not serve it the window shows that server's list of projects.
struct SettingsView: View {
    let feed: SessionFeed
    @State private var server = SessionFeed.savedServer
    @State private var token = ""
    /// The optional path mapping (KYO-736): a prefix of a path on the server's machine, and
    /// the folder it is on this one, such as a mount of the server's home folder.
    @State private var mappedServer = SessionFeed.savedPathMapping.server
    @State private var mappedHere = SessionFeed.savedPathMapping.here
    @State private var note = ""

    var body: some View {
        Form {
            TextField("Server", text: $server, prompt: Text("https://your-host.ts.net"))
            SecureField("Device token", text: $token, prompt: Text("Leave blank to keep the current one"))
            Section {
                TextField("Path on the server", text: $mappedServer, prompt: Text("/Users/name"))
                TextField("Is here on this Mac", text: $mappedHere, prompt: Text("/Volumes/name"))
            } footer: {
                Text(
                    "Optional. A file outside the folders the server serves cannot be opened in "
                        + "Files; with this mapping it can be revealed in Finder where the "
                        + "server's folder is mounted on this Mac."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Text(note).foregroundStyle(.secondary)
                Spacer()
                Button("Save") { save() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    private func save() {
        guard let base = URL(string: server), base.scheme != nil else {
            note = "That is not a server address."
            return
        }
        if !token.isEmpty {
            guard SessionFeed.storeDeviceToken(token) else {
                note = "The token could not be saved."
                return
            }
            token = ""
        }
        SessionFeed.savePathMapping(server: mappedServer, here: mappedHere)
        feed.configure(server: base, project: feed.project)
        note = "Saved."
    }
}
