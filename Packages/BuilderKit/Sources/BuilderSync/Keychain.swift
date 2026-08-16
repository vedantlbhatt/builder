import Foundation
import Security

/// Tokens live in the keychain, never in a plist or a dotfile.
///
/// `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`: the daemon runs at login and needs
/// to reach the token before the user opens anything, but the item must not travel in an
/// iCloud keychain or a backup. There is deliberately NO access group — the Mac agent and
/// the phone are separate installs that pair through the device grant rather than by
/// sharing a keychain, which also removes a shared-entitlement surface from the signing
/// story on both platforms.
public enum Keychain {

    public enum Item: String {
        case accessToken = "dev.builder.access"
        case refreshToken = "dev.builder.refresh"
        case deviceID = "dev.builder.device"
    }

    public static func set(_ value: String, for item: Item) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: item.rawValue,
        ]
        SecItemDelete(query as CFDictionary)

        var add = query
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly

        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw KeychainError(status: status, operation: "store \(item.rawValue)")
        }
    }

    public static func get(_ item: Item) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: item.rawValue,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    public static func delete(_ item: Item) {
        SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: item.rawValue,
        ] as CFDictionary)
    }

    public static func deleteAll() {
        for item in [Item.accessToken, .refreshToken, .deviceID] { delete(item) }
    }
}

public struct KeychainError: Error, CustomStringConvertible {
    public let status: OSStatus
    public let operation: String

    public var description: String {
        let message = SecCopyErrorMessageString(status, nil) as String? ?? "unknown"
        return "keychain \(operation) failed: \(message) (\(status))"
    }
}
