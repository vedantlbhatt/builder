import BuilderModel
import Foundation

/// Builds the session timeline strip: a fixed 1024-byte column array plus a separate list
/// of marks.
///
/// The format was chosen over four alternatives (segment rows, run-length encoding, a
/// server-binned segment list, a capped segment table) on one decisive property: **marks
/// live outside the columns, so a five-second prompt cannot be resampled away.** Typed
/// prompts are ~6% of events — 1,456 against 23,838 tool calls — which is exactly what a
/// segment-list format loses when it downsamples. Losing them would erase the human from
/// their own timeline.
///
/// Fixed size also means the wire payload is ~1.4 KB regardless of session length, it is
/// text-free by construction (no path or prompt can hide in it), and "renders identically
/// in four places" becomes an integer-decoding property rather than a rasterization
/// negotiation.
public enum StripBuilder {

    public struct Strip: Sendable, Equatable {
        public var t0Ms: Int
        public var t1Ms: Int
        /// Exactly `StripSpec.columns` bytes: 2-bit class, 2-bit density, 4 bits reserved.
        public var cols: [UInt8]
        /// `(millisecond offset from t0, mark kind)`.
        public var marks: [(ms: Int, kind: StripMarkKind)]

        public static func == (a: Strip, b: Strip) -> Bool {
            a.t0Ms == b.t0Ms && a.t1Ms == b.t1Ms && a.cols == b.cols
                && a.marks.map(\.ms) == b.marks.map(\.ms)
        }

        public var marksJSON: String {
            let arr = marks.map { [$0.ms, Int($0.kind.rawValue)] }
            return (try? JSONSerialization.data(withJSONObject: arr))
                .map { String(decoding: $0, as: UTF8.self) } ?? "[]"
        }
    }

    /// Which class an event's *following* interval belongs to.
    ///
    /// The interval after a prompt is you waiting and the agent starting; the interval
    /// after a tool call is the agent working. Attribution is to the event that OPENED
    /// the interval, which is why a prompt paints a visible notch of its own rather than
    /// disappearing into the agent run that follows it.
    private static func classOf(_ kind: EventKind) -> StripClass {
        switch kind {
        case .prompt: return .prompting
        // The interval after an interrupt is the human having stopped the agent and
        // deciding what to say next — theirs, not the agent's. No mark: the mark kinds
        // are fixed by spec/strip.v1.json.
        case .interrupt: return .prompting
        // A `/clear` is the last thing typed into a conversation; the moment after it is
        // the human's, and the session ends there anyway (`EndReason.cleared`).
        case .clear: return .prompting
        case .humanEdit: return .human_edit
        case .assistantMessage, .thinking, .toolUse, .toolResult: return .agent
        case .turnDuration, .compaction, .title, .noise, .unknown: return .agent
        }
    }

    public static func build(
        events: [NormalizedEvent],
        startedAt: Double,
        endedAt: Double
    ) -> Strip {
        let t0 = startedAt
        let span = max(endedAt - startedAt, 0.001)
        let n = StripSpec.columns
        let colSeconds = span / Double(n)

        // Milliseconds of each class per column, plus an event count for density.
        var weightPerColumn = [[Double]](repeating: [0, 0, 0, 0], count: n)
        var eventsPerColumn = [Int](repeating: 0, count: n)
        var marks: [(ms: Int, kind: StripMarkKind)] = []

        let timed = events.compactMap { e -> (Double, EventKind)? in
            guard let ts = e.ts else { return nil }
            return (ts, e.kind)
        }.sorted { $0.0 < $1.0 }

        for (i, ev) in timed.enumerated() {
            let start = ev.0
            let next = i + 1 < timed.count ? timed[i + 1].0 : endedAt

            // Everything past the activity cap is idle, not "still working". Only 2.07% of
            // gaps exceed a minute, so an idle band on the strip reads as a real pause in
            // the session rather than as noise.
            let activeEnd = min(next, start + Tuning.activeGapCapSec)
            paint(&weightPerColumn, from: start, to: activeEnd, t0: t0, colSeconds: colSeconds,
                  klass: classOf(ev.1), n: n)
            if next > activeEnd {
                paint(&weightPerColumn, from: activeEnd, to: next, t0: t0, colSeconds: colSeconds,
                      klass: .idle, n: n)
            }

            let col = column(for: start, t0: t0, colSeconds: colSeconds, n: n)
            eventsPerColumn[col] += 1

            if ev.1 == .prompt {
                marks.append((ms: Int((start - t0) * 1000), kind: .prompt))
            } else if ev.1 == .compaction {
                marks.append((ms: Int((start - t0) * 1000), kind: .compact))
            }
        }

        var cols = [UInt8](repeating: StripSpec.pack(.idle, density: 0), count: n)
        for c in 0..<n {
            // PRIORITY-WEIGHTED argmax, not raw argmax and not majority.
            //
            // At 1024 columns a 71-minute session is 4.2 s/column, and at a 400px render
            // width it is 10.6 s/column — so essentially every column contains several
            // event types. Raw argmax hands every one of them to the agent, because the
            // agent is always the longest-running thing, and the human vanishes from the
            // picture entirely.
            var best = StripClass.idle
            var bestScore = 0.0
            for (idx, ms) in weightPerColumn[c].enumerated() {
                guard let k = StripClass(rawValue: UInt8(idx)) else { continue }
                let score = ms * StripSpec.weight(k)
                if score > bestScore {
                    bestScore = score
                    best = k
                }
            }

            let perSecond = colSeconds > 0 ? Double(eventsPerColumn[c]) / colSeconds : 0
            var density: UInt8 = 0
            for t in StripSpec.densityThresholdsEventsPerSec where perSecond >= t { density += 1 }
            cols[c] = StripSpec.pack(best, density: min(density, 3))
        }

        return Strip(
            t0Ms: Int(t0 * 1000),
            t1Ms: Int(endedAt * 1000),
            cols: cols,
            marks: dedupeMarks(marks, span: span))
    }

    private static func paint(
        _ weights: inout [[Double]], from: Double, to: Double,
        t0: Double, colSeconds: Double, klass: StripClass, n: Int
    ) {
        guard to > from else { return }
        let firstCol = column(for: from, t0: t0, colSeconds: colSeconds, n: n)
        let lastCol = column(for: to, t0: t0, colSeconds: colSeconds, n: n)
        guard firstCol <= lastCol else { return }

        for c in firstCol...lastCol {
            let colStart = t0 + Double(c) * colSeconds
            let colEnd = colStart + colSeconds
            let overlap = min(to, colEnd) - max(from, colStart)
            if overlap > 0 { weights[c][Int(klass.rawValue)] += overlap }
        }
    }

    private static func column(for t: Double, t0: Double, colSeconds: Double, n: Int) -> Int {
        guard colSeconds > 0 else { return 0 }
        return max(0, min(n - 1, Int((t - t0) / colSeconds)))
    }

    /// Collapse marks that would land on the same pixel at typical render widths.
    ///
    /// A collapsed mark is not discarded silently — the surviving one stands for both, and
    /// the renderer's density channel already carries the fact that something busy happened
    /// there.
    private static func dedupeMarks(
        _ marks: [(ms: Int, kind: StripMarkKind)], span: Double
    ) -> [(ms: Int, kind: StripMarkKind)] {
        guard !marks.isEmpty else { return [] }
        let sorted = marks.sorted { $0.ms < $1.ms }
        // Assume a 400pt render as the reference width for collapsing.
        let msPerPx = span * 1000 / 400
        let minGap = Double(StripSpec.markDedupeMinPx) * msPerPx

        var out: [(ms: Int, kind: StripMarkKind)] = [sorted[0]]
        for m in sorted.dropFirst() {
            if Double(m.ms - out[out.count - 1].ms) >= minGap { out.append(m) }
        }
        return out
    }
}
