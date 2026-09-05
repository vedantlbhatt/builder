import BuilderModel
import Foundation

/// Watches the harness log directories and drives the ingest + lifecycle loop.
///
/// Two independent clocks, and both are necessary:
///
///   **File-system events** tell us work is happening. They are coalesced, because a busy
///   agent writes to the same transcript dozens of times a second and reparsing on every
///   write would peg a core for nothing.
///
///   **A timer** tells us work has STOPPED. This is the half that is easy to leave out and
///   impossible to do without: a session ends precisely because no event arrives, so no
///   amount of event handling can ever detect it.
///
/// Waking from sleep forces a reconcile before the next tick. Otherwise a laptop closed at
/// lunch and opened at three finalizes everything against a stale view of the world, and
/// the "session finished" alert arrives hours after it was true.
public final class Daemon {

    public struct Config: Sendable {
        /// How long to wait after a burst of file-system events before parsing.
        ///
        /// MEASURED: the median inter-event gap is 1.0s and 98% of gaps are under a
        /// minute, so an agent mid-run generates a continuous stream. Two seconds
        /// coalesces a burst without making the live session timer feel stale.
        public var debounceSeconds: Double = 2.0

        /// The lifecycle tick. Independent of file-system activity, by necessity.
        public var tickSeconds: Double = 30.0

        /// Cursor's database is a single 1.2 GB file with a live WAL; watching it is
        /// useless because every write touches the same path. It gets polled instead.
        public var cursorPollSeconds: Double = 15.0

        /// Safety net for dropped FSEvents. Cheap: MEASURED at 161 ms to stat 321 files.
        public var reconcileSeconds: Double = 60.0

        public init() {}
    }

    public enum Event: Sendable {
        case scanned(events: Int, elapsed: Double)
        case sessionFinished(SessionAlert)
        case liveSession(repo: String?, activeSeconds: Double)
        case idle
        case error(String)
    }

    private let roots: [String]
    private let config: Config
    private var stream: FSEventStreamRef?
    private let queue = DispatchQueue(label: "dev.builder.daemon", qos: .utility)
    private var pendingWork = false
    private var lastScan = Date.distantPast

    /// Called on the daemon queue whenever something happens worth reporting.
    private let onEvent: @Sendable (Event) -> Void
    /// Runs one ingest + derive + lifecycle pass. Supplied by the caller so the daemon
    /// itself has no opinion about storage.
    private let pass: @Sendable () -> Void

    public init(
        roots: [String]? = nil,
        config: Config = Config(),
        onEvent: @escaping @Sendable (Event) -> Void,
        pass: @escaping @Sendable () -> Void
    ) {
        self.roots = roots ?? Daemon.defaultRoots()
        self.config = config
        self.onEvent = onEvent
        self.pass = pass
    }

    public static func defaultRoots() -> [String] {
        let home = NSHomeDirectory() as NSString
        return [
            home.appendingPathComponent(".claude/projects"),
            home.appendingPathComponent(".codex/sessions"),
            home.appendingPathComponent(".gemini/tmp"),
            home.appendingPathComponent(".cursor/projects"),
        ].filter { FileManager.default.fileExists(atPath: $0) }
    }

    public func start() {
        startFSEvents()
        startTimers()
        // One pass immediately, so launching the daemon shows current state rather than
        // an empty screen until the first tick.
        queue.async { [weak self] in self?.runPass() }
    }

    public func stop() {
        if let stream {
            FSEventStreamStop(stream)
            FSEventStreamInvalidate(stream)
            FSEventStreamRelease(stream)
            self.stream = nil
        }
    }

    // MARK: - File system

    private func startFSEvents() {
        guard !roots.isEmpty else { return }

        var context = FSEventStreamContext(
            version: 0,
            info: Unmanaged.passUnretained(self).toOpaque(),
            retain: nil, release: nil, copyDescription: nil)

        let callback: FSEventStreamCallback = { _, info, _, _, _, _ in
            guard let info else { return }
            let daemon = Unmanaged<Daemon>.fromOpaque(info).takeUnretainedValue()
            daemon.fileSystemChanged()
        }

        // `kFSEventStreamCreateFlagNoDefer` delivers the FIRST event of a burst
        // immediately and then coalesces the rest, which is what makes the live session
        // timer start moving the moment work begins rather than one latency period later.
        let flags =
            UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagNoDefer)

        stream = FSEventStreamCreate(
            kCFAllocatorDefault, callback, &context,
            roots as CFArray,
            FSEventStreamEventId(kFSEventStreamEventIdSinceNow),
            config.debounceSeconds, flags)

        guard let stream else {
            onEvent(.error("could not watch \(roots.joined(separator: ", "))"))
            return
        }
        FSEventStreamSetDispatchQueue(stream, queue)
        FSEventStreamStart(stream)
    }

    private func fileSystemChanged() {
        guard !pendingWork else { return }
        pendingWork = true
        queue.asyncAfter(deadline: .now() + config.debounceSeconds) { [weak self] in
            self?.pendingWork = false
            self?.runPass()
        }
    }

    // MARK: - Timers

    private func startTimers() {
        // THE COMPLETION CLOCK. Runs whether or not anything happened, because "nothing
        // happened" is exactly the signal it exists to detect.
        let tick = DispatchSource.makeTimerSource(queue: queue)
        tick.schedule(deadline: .now() + config.tickSeconds, repeating: config.tickSeconds)
        tick.setEventHandler { [weak self] in self?.runPass() }
        tick.resume()
        tickTimer = tick

        // Safety net for dropped or missed FSEvents.
        let reconcile = DispatchSource.makeTimerSource(queue: queue)
        reconcile.schedule(
            deadline: .now() + config.reconcileSeconds, repeating: config.reconcileSeconds)
        reconcile.setEventHandler { [weak self] in self?.runPass() }
        reconcile.resume()
        reconcileTimer = reconcile

        NotificationCenter.default.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: nil
        ) { [weak self] _ in
            // The machine may have been asleep for hours. Reconcile before the next tick
            // so finalization runs against reality, not against a stale watermark.
            self?.queue.async { self?.runPass() }
        }
    }

    private var tickTimer: DispatchSourceTimer?
    private var reconcileTimer: DispatchSourceTimer?

    private func runPass() {
        lastScan = Date()
        pass()
    }
}

#if canImport(AppKit)
    import AppKit
#else
    /// Linux headless builds have no NSWorkspace. Sleep/wake is a laptop concern.
    private enum NSWorkspace {
        static let didWakeNotification = Notification.Name("dev.builder.noop.wake")
    }
#endif
