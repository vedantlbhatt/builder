import BuilderSync
import Foundation

/// `builder pair` — the terminal half of "Log in with Claude Code".
///
/// Same two `SyncClient` calls as the menu bar app's "Connect your phone" sheet, so the
/// two entry points cannot disagree about the protocol: `startPairing` issues the code,
/// `awaitPairing` blocks on the server's poll interval until the phone approves and then
/// writes both tokens to the Keychain. The Mac app finds those on its next launch and
/// shows "Phone connected" without being asked again.
///
/// No QR here, deliberately. A terminal QR needs an encoder, and an untested encoder that
/// emits a plausible-looking but unscannable grid is the exact failure mode this project
/// exists to avoid. The code is printed large enough to type from across a desk, and
/// the deep link is printed for anyone who wants to make their own.
enum PairCommand {

    static func run() async throws {
        let client = SyncClient(baseURL: SyncCommand.baseURL())

        if await client.isPaired {
            print("")
            print("  This Mac is already paired. Pairing again replaces its tokens.")
        }

        let machineID = SyncCommand.machineID()
        let label = Host.current().localizedName ?? "Mac"

        let start = try await client.startPairing(machineID: machineID, label: label)
        let deepLink = "builder://pair?code=\(start.userCode)"

        print("")
        print("  Open Builder on your phone → Settings → Scan code, and enter:")
        print("")
        for line in box(start.userCode) { print("      \(line)") }
        print("")
        print("  or open \(start.verificationURI) on a device where you are signed in.")
        print("")
        print("  Deep link:  \(deepLink)")
        print("  The Builder menu bar app shows this as a scannable QR under “Connect your phone”.")
        print("")
        print("  Waiting for approval (code expires in 15 minutes)…")

        // 900s matches the server's `expires_in`; polling past it only collects 4xx.
        try await client.awaitPairing(
            deviceCode: start.deviceCode, intervalSeconds: start.intervalSeconds, timeout: 900)

        print("")
        print("  Paired — this Mac is linked as “\(label)”. Run `builder sync` to upload.")
    }

    /// The code in a box-drawing frame with a blank line of breathing room on each side.
    static func box(_ code: String) -> [String] {
        let inner = "    \(code)    "
        let width = inner.count
        let bar = String(repeating: "─", count: width)
        let blank = String(repeating: " ", count: width)
        return [
            "┌\(bar)┐",
            "│\(blank)│",
            "│\(inner)│",
            "│\(blank)│",
            "└\(bar)┘",
        ]
    }
}
