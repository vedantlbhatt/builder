import BuilderModel
import Foundation

/// Talks to the server.
///
/// Deliberately does NOT certificate-pin. The privacy page tells people to run Builder
/// behind mitmproxy and watch what it sends — pinning would make the strongest possible
/// verification impossible, and a claim nobody can check is just a claim.
public actor SyncClient {

    public struct BatchResult: Sendable {
        public let accepted: Int
        public let unchanged: Int
        public let rejected: [(sessionID: String, reason: String)]
    }

    public enum SyncError: Error, CustomStringConvertible {
        case notPaired
        case http(status: Int, body: String)
        case pairingTimedOut
        case transport(String)

        public var description: String {
            switch self {
            case .notPaired:
                return "not paired — run `builder pair` first"
            case .http(let status, let body):
                return "server returned \(status): \(body.prefix(200))"
            case .pairingTimedOut:
                return "pairing expired before it was approved"
            case .transport(let m):
                return "network error: \(m)"
            }
        }
    }

    private let baseURL: URL
    private let session: URLSession
    private let clientVersion: String

    public init(baseURL: URL, clientVersion: String = "0.1.0", session: URLSession = .shared) {
        self.baseURL = baseURL
        self.clientVersion = clientVersion
        self.session = session
    }

    // MARK: - Pairing

    public struct PairingStart: Sendable {
        public let deviceCode: String
        public let userCode: String
        public let verificationURI: String
        public let intervalSeconds: Int
    }

    /// RFC 8628 device grant. The agent is open source, so any embedded client secret
    /// would be public by construction — this is the flow designed for exactly that, and
    /// the same one `gh auth login` uses.
    public func startPairing(machineID: String, label: String) async throws -> PairingStart {
        struct Body: Encodable {
            let machine_id: String
            let label: String
            let platform: String
            let agent_version: String
        }
        struct Response: Decodable {
            let device_code: String
            let user_code: String
            let verification_uri: String
            let interval: Int
        }

        let r: Response = try await post(
            "/v1/auth/device/start",
            body: Body(
                machine_id: machineID, label: label, platform: "macos",
                agent_version: clientVersion),
            authorized: false)

        return PairingStart(
            deviceCode: r.device_code, userCode: r.user_code,
            verificationURI: r.verification_uri, intervalSeconds: r.interval)
    }

    /// Poll until approved. `authorization_pending` is the expected answer for as long as
    /// the person has not walked to their phone, so it is a status rather than an error.
    public func awaitPairing(deviceCode: String, intervalSeconds: Int, timeout: TimeInterval = 900)
        async throws
    {
        struct Body: Encodable { let device_code: String }
        struct Response: Decodable {
            let status: String
            let access_token: String?
            let refresh_token: String?
        }

        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let r: Response = try await post(
                "/v1/auth/device/poll", body: Body(device_code: deviceCode), authorized: false)
            if r.status == "ok", let access = r.access_token, let refresh = r.refresh_token {
                try Keychain.set(access, for: .accessToken)
                try Keychain.set(refresh, for: .refreshToken)
                return
            }
            try await Task.sleep(nanoseconds: UInt64(intervalSeconds) * 1_000_000_000)
        }
        throw SyncError.pairingTimedOut
    }

    public var isPaired: Bool { Keychain.get(.refreshToken) != nil }

    public func signOut() { Keychain.deleteAll() }

    // MARK: - Sync

    /// Content hashes the server already has, so unchanged sessions are never re-sent.
    public func knownHashes() async throws -> [String: String] {
        struct Response: Decodable { let known: [String: String] }
        let r: Response = try await get("/v1/sync/known")
        return r.known
    }

    /// Upload in chunks.
    ///
    /// 200 per request: the whole corpus on the reference machine is 557 sessions, so a
    /// first-run backfill is three requests. Larger chunks risk a single rejected payload
    /// taking a bigger batch's worth of work with it.
    public func upload(_ sessions: [SessionUpload], chunkSize: Int = 200) async throws
        -> BatchResult
    {
        struct Body: Encodable { let sessions: [SessionUpload] }
        struct Rejected: Decodable {
            let client_session_id: String
            let reason: String
        }
        struct Response: Decodable {
            let accepted: Int
            let unchanged: Int
            let rejected: [Rejected]
        }

        var accepted = 0
        var unchanged = 0
        var rejected: [(String, String)] = []

        for chunk in stride(from: 0, to: sessions.count, by: chunkSize).map({
            Array(sessions[$0..<min($0 + chunkSize, sessions.count)])
        }) {
            let r: Response = try await post("/v1/sync/sessions:batch", body: Body(sessions: chunk))
            accepted += r.accepted
            unchanged += r.unchanged
            rejected += r.rejected.map { ($0.client_session_id, $0.reason) }
        }

        return BatchResult(accepted: accepted, unchanged: unchanged, rejected: rejected)
    }

    // MARK: - Transport

    private func post<B: Encodable, R: Decodable>(
        _ path: String, body: B, authorized: Bool = true
    ) async throws -> R {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try SessionUpload.encoder().encode(body)
        if authorized {
            guard let token = Keychain.get(.accessToken) else { throw SyncError.notPaired }
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return try await send(request, retryOn401: authorized)
    }

    private func get<R: Decodable>(_ path: String) async throws -> R {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        guard let token = Keychain.get(.accessToken) else { throw SyncError.notPaired }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await send(request, retryOn401: true)
    }

    private func send<R: Decodable>(_ request: URLRequest, retryOn401: Bool) async throws -> R {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw SyncError.transport(error.localizedDescription)
        }

        let status = (response as? HTTPURLResponse)?.statusCode ?? 0

        if status == 401 && retryOn401 {
            try await refreshTokens()
            var retried = request
            if let token = Keychain.get(.accessToken) {
                retried.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            return try await send(retried, retryOn401: false)
        }

        guard (200..<300).contains(status) else {
            throw SyncError.http(status: status, body: String(decoding: data, as: UTF8.self))
        }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(R.self, from: data)
    }

    /// Refresh, serialised by the actor.
    ///
    /// Refresh tokens ROTATE and reuse is treated as a leak, so two concurrent refreshes
    /// would present the same spent token twice and get the whole device revoked. Actor
    /// isolation is what makes that impossible here rather than merely unlikely.
    private func refreshTokens() async throws {
        guard let refresh = Keychain.get(.refreshToken) else { throw SyncError.notPaired }

        struct Body: Encodable { let refresh_token: String }
        struct Response: Decodable {
            let access_token: String
            let refresh_token: String
        }

        var request = URLRequest(url: baseURL.appendingPathComponent("/v1/auth/refresh"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Body(refresh_token: refresh))

        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            // The refresh token is dead. Clearing it turns every later call into a clear
            // "run `builder pair`" instead of an endless 401 loop.
            Keychain.deleteAll()
            throw SyncError.http(status: status, body: String(decoding: data, as: UTF8.self))
        }

        let r = try JSONDecoder().decode(Response.self, from: data)
        try Keychain.set(r.access_token, for: .accessToken)
        try Keychain.set(r.refresh_token, for: .refreshToken)
    }
}
