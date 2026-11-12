import Foundation
import Security

/// The seal key as a synchronizable keychain item, which iCloud Keychain carries (KYO-789).
///
/// `kyo` commits each message with a key (KYO-663) and cannot use a synchronizable item
/// itself: such an item is in the data protection keychain, where access goes by keychain
/// access group, and an unsigned process belongs to none. The app's executable, signed with
/// the entitlement, belongs to one. `kyo turns --seal-key icloud-keychain` therefore runs
/// this executable as `Kyosei seal-key read|add|delete` and keeps a local copy of the key,
/// which its hooks read. The key is never passed as an argument.
///
/// | invocation | input | success | other exits |
/// |---|---|---|---|
/// | `seal-key read` | none | 0, the key as 64 hex characters and a newline | 3 no item, 4 refused |
/// | `seal-key add` | 64 hex characters on standard input | 0 | 5 an item exists, 4 refused |
/// | `seal-key delete` | none | 0, also when there was no item | 4 refused |
///
/// A refusal writes `OSStatus <n>` to standard error. Anything else on the command line is a
/// usage error, exit 64.
public enum SealKeyItem {
    /// The names `kyo` uses for the key in the login keychain (`src/kyosei/seal.py`).
    public static let service = "dev.kyosei.seal-key"
    public static let account = "seal-key"

    public enum Command: String, Sendable { case read, add, delete }

    public enum Exit: Int32, Sendable {
        case done = 0
        case noItem = 3
        case refused = 4
        case itemExists = 5
        case usage = 64
    }

    /// What one run answers: its exit status and what it writes to each stream.
    public struct Outcome: Equatable, Sendable {
        public var exit: Exit
        public var output: String
        public var error: String

        public init(exit: Exit, output: String = "", error: String = "") {
            self.exit = exit
            self.output = output
            self.error = error
        }
    }

    /// The command the arguments after `seal-key` name, or nil when they name none.
    public static func command(_ arguments: [String]) -> Command? {
        arguments.count == 1 ? Command(rawValue: arguments[0]) : nil
    }

    /// The key read from standard input: 64 hexadecimal characters, with the white space
    /// around them dropped, in lower case. Nil for anything else.
    public static func key(fromInput input: String) -> String? {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let hex = Set("0123456789abcdef")
        return text.count == 64 && text.allSatisfy(hex.contains) ? text : nil
    }

    /// One keychain call, as the tests replace it: the status, and for a read the data.
    public typealias Keychain = (Command, Data?) -> (OSStatus, Data?)

    /// Runs one command. `input` is read only for `add`.
    public static func run(
        _ arguments: [String], input: () -> String, keychain: Keychain = securityItem
    ) -> Outcome {
        guard let command = command(arguments) else {
            return Outcome(exit: .usage, error: "usage: Kyosei seal-key read|add|delete\n")
        }
        switch command {
        case .read:
            let (status, data) = keychain(.read, nil)
            if status == errSecItemNotFound { return Outcome(exit: .noItem) }
            guard status == errSecSuccess, let data,
                let key = key(fromInput: String(decoding: data, as: UTF8.self))
            else { return refused(status) }
            return Outcome(exit: .done, output: key + "\n")
        case .add:
            guard let key = key(fromInput: input()) else {
                return Outcome(
                    exit: .usage, error: "seal-key add reads 64 hexadecimal characters\n")
            }
            let (status, _) = keychain(.add, Data(key.utf8))
            if status == errSecDuplicateItem { return Outcome(exit: .itemExists) }
            return status == errSecSuccess ? Outcome(exit: .done) : refused(status)
        case .delete:
            let (status, _) = keychain(.delete, nil)
            return status == errSecSuccess || status == errSecItemNotFound
                ? Outcome(exit: .done) : refused(status)
        }
    }

    private static func refused(_ status: OSStatus) -> Outcome {
        Outcome(exit: .refused, error: "OSStatus \(status)\n")
    }

    /// The Security framework's call on the synchronizable item. No access group is named:
    /// the item goes to the process's first group, which the signature's
    /// `keychain-access-groups` lists, so that no team identifier is written here.
    public static func securityItem(_ command: Command, _ key: Data?) -> (OSStatus, Data?) {
        var query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrSynchronizable: true,
            kSecUseDataProtectionKeychain: true,
        ]
        switch command {
        case .read:
            query[kSecReturnData] = true
            query[kSecMatchLimit] = kSecMatchLimitOne
            var found: CFTypeRef?
            let status = SecItemCopyMatching(query as CFDictionary, &found)
            return (status, found as? Data)
        case .add:
            query[kSecValueData] = key ?? Data()
            // A synchronizable item cannot be bound to one device.
            query[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
            return (SecItemAdd(query as CFDictionary, nil), nil)
        case .delete:
            return (SecItemDelete(query as CFDictionary), nil)
        }
    }
}
