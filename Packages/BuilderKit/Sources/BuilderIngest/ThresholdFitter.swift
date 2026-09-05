import BuilderModel
import Foundation

/// The v3 idle-gap threshold, fitted to the user's own data — with a safety net.
///
/// Halfaker et al. (WWW 2015): inter-activity times are bimodal on a log scale, and the
/// session threshold belongs at the valley between the two modes, fitted per dataset
/// rather than taken from a convention. This is that fit: a two-component Gaussian
/// mixture on log10(gap seconds) by EM, standard library only, and written to reproduce
/// `scripts/measure_boundaries.fit_tau` operation for operation — same initialisation,
/// same accumulation order, same stopping rule — so `spec/fixtures/boundaries/
/// threshold_fit.json` can hold this code to the Python reference at 1e-6.
///
/// THE SAMPLE IS THE HUMAN'S ACTS, NOT THE MACHINE'S RECORDS. Halfaker's events were edits
/// and queries, each one a person doing something. A transcript writes thousands of
/// records per sitting, so the gaps between sittings are under 2% of record gaps and the
/// mixture cannot see them — MEASURED on the container corpus, a fit on record gaps found
/// modes at 7 ms (records of one turn flushed together) and 3.4 s (the tool cadence), a
/// valley at 0.1 s, and would have clamped that to 300 s with full confidence. The fit
/// therefore runs on `Sessionizer.presenceGaps`: the intervals between consecutive
/// presence signals in a pool, including the ones that span idle gaps.
///
/// Every constant is in `Tuning` with its reason. The fallback is `Tuning.tauSessionSec`.
public struct TauFit: Sendable, Equatable {
    public enum Source: String, Sendable { case fitted, fallback }

    /// Gaps used (strictly positive ones only).
    public var n: Int
    /// Lower and upper modes, log10 seconds. NaN when no fit was attempted.
    public var m1: Double
    public var m2: Double
    public var s1: Double
    public var s2: Double
    public var w1: Double
    public var w2: Double
    /// log10 seconds where the two weighted densities cross, when they do.
    public var valley: Double?
    public var bimodal: Bool
    /// Why it is or is not bimodal, one phrase. For the doctor, never for a rule.
    public var reason: String
    /// What the sessionizer should use.
    public var tau: Double
    public var source: Source

    public init(
        n: Int, m1: Double, m2: Double, s1: Double, s2: Double, w1: Double, w2: Double,
        valley: Double?, bimodal: Bool, reason: String, tau: Double, source: Source
    ) {
        self.n = n
        self.m1 = m1
        self.m2 = m2
        self.s1 = s1
        self.s2 = s2
        self.w1 = w1
        self.w2 = w2
        self.valley = valley
        self.bimodal = bimodal
        self.reason = reason
        self.tau = tau
        self.source = source
    }

    static func fallback(n: Int, reason: String) -> TauFit {
        TauFit(
            n: n, m1: .nan, m2: .nan, s1: .nan, s2: .nan, w1: .nan, w2: .nan,
            valley: nil, bimodal: false, reason: reason, tau: Tuning.tauSessionSec,
            source: .fallback)
    }
}

/// What the sessionizer cuts with: the fitted tau when the pool supports one, the
/// fallback otherwise. A value, so the deriver can store it next to the sessions it
/// produced and the doctor can print why.
public struct SessionThresholds: Sendable, Equatable {
    public var tau: Double
    public var fit: TauFit

    public init(tau: Double, fit: TauFit) {
        self.tau = tau
        self.fit = fit
    }

    /// `Tuning.tauSessionSec`, no fit attempted. What every caller gets today.
    public static let fallback = SessionThresholds(
        tau: Tuning.tauSessionSec, fit: .fallback(n: 0, reason: "not fitted"))

    /// Fit over a gap sample (seconds). Use `Sessionizer.presenceGaps` to build it.
    public static func fitted(gaps: [Double]) -> SessionThresholds {
        let fit = ThresholdFitter.fit(gaps: gaps)
        return SessionThresholds(tau: fit.tau, fit: fit)
    }

    /// The refit policy: whenever the sample has grown by `Tuning.tauRefitGrowthFraction`
    /// since the last fit, or `Tuning.tauRefitIntervalSec` has passed, or there has never
    /// been one. Cheap to evaluate every tick; the fit itself is a few hundred passes over
    /// a list of a few thousand doubles.
    public static func needsRefit(
        gapsAtLastFit: Int?, gapsNow: Int, lastFitAt: Double?, now: Double
    ) -> Bool {
        guard let last = gapsAtLastFit, let at = lastFitAt else { return true }
        if Double(gapsNow) >= Double(last) * (1.0 + Tuning.tauRefitGrowthFraction) { return true }
        return now - at >= Tuning.tauRefitIntervalSec
    }
}

public enum ThresholdFitter {

    /// Normal density. `(x - m) / s` first, exactly as the reference writes it.
    static func pdf(_ x: Double, _ m: Double, _ s: Double) -> Double {
        let z = (x - m) / s
        return exp(-0.5 * z * z) / (s * (2.0 * Double.pi).squareRoot())
    }

    /// EM for a 1-D two-Gaussian mixture on `xs` (already log10). Returns m1 <= m2.
    ///
    /// Initialisation is deterministic — the means of the lower and upper halves of the
    /// sorted sample, one shared variance, equal weights. No `reduce`: the accumulations
    /// are explicit left-to-right loops so they match the reference's, which avoids
    /// Python's compensated `sum()` for the same reason.
    public static func fitTwoGaussians(_ input: [Double])
        -> (m1: Double, m2: Double, s1: Double, s2: Double, w1: Double, w2: Double)
    {
        let xs = input.sorted()
        let n = xs.count
        guard n >= 2 else { return (.nan, .nan, .nan, .nan, .nan, .nan) }
        let nD = Double(n)
        let half = n / 2
        var acc = 0.0
        for x in xs[0..<half] { acc += x }
        var m1 = acc / Double(half)
        acc = 0.0
        for x in xs[half..<n] { acc += x }
        var m2 = acc / Double(n - half)
        acc = 0.0
        for x in xs { acc += x }
        let mean = acc / nD
        acc = 0.0
        for x in xs {
            let d = x - mean
            acc += d * d
        }
        var variance = acc / nD
        if variance < Tuning.tauFitVarianceFloor { variance = Tuning.tauFitVarianceFloor }
        var s1 = variance.squareRoot()
        var s2 = s1
        var w1 = 0.5
        var w2 = 0.5

        var gammas = [Double](repeating: 0, count: n)
        for _ in 0..<Tuning.tauFitMaxIterations {
            var n1 = 0.0
            var sx1 = 0.0
            var sx2 = 0.0
            for (j, x) in xs.enumerated() {
                let p1 = w1 * pdf(x, m1, s1)
                let p2 = w2 * pdf(x, m2, s2)
                let tot = p1 + p2
                let g = tot <= 0.0 ? 0.5 : p1 / tot
                gammas[j] = g
                n1 += g
                sx1 += g * x
                sx2 += (1.0 - g) * x
            }
            let n2 = nD - n1
            if n1 <= 1e-12 || n2 <= 1e-12 { break }
            let nm1 = sx1 / n1
            let nm2 = sx2 / n2
            var sv1 = 0.0
            var sv2 = 0.0
            for (j, x) in xs.enumerated() {
                let g = gammas[j]
                let d1 = x - nm1
                let d2 = x - nm2
                sv1 += g * d1 * d1
                sv2 += (1.0 - g) * d2 * d2
            }
            var v1 = sv1 / n1
            var v2 = sv2 / n2
            if v1 < Tuning.tauFitVarianceFloor { v1 = Tuning.tauFitVarianceFloor }
            if v2 < Tuning.tauFitVarianceFloor { v2 = Tuning.tauFitVarianceFloor }
            let ns1 = v1.squareRoot()
            let ns2 = v2.squareRoot()
            let nw1 = n1 / nD
            let nw2 = n2 / nD
            let delta = max(
                max(abs(nm1 - m1), abs(nm2 - m2)),
                max(max(abs(ns1 - s1), abs(ns2 - s2)), abs(nw1 - w1)))
            m1 = nm1
            m2 = nm2
            s1 = ns1
            s2 = ns2
            w1 = nw1
            w2 = nw2
            if delta < Tuning.tauFitTolerance { break }
        }

        if m1 > m2 {
            return (m2, m1, s2, s1, w2, w1)
        }
        return (m1, m2, s1, s2, w1, w2)
    }

    /// The log-gap between the two means where the weighted densities cross, by
    /// bisection; nil when there is no crossing between them to call a valley.
    public static func valley(
        m1: Double, m2: Double, s1: Double, s2: Double, w1: Double, w2: Double
    ) -> Double? {
        func f(_ x: Double) -> Double { w1 * pdf(x, m1, s1) - w2 * pdf(x, m2, s2) }
        var lo = m1
        var hi = m2
        guard f(lo) > 0.0, f(hi) < 0.0 else { return nil }
        for _ in 0..<200 {
            let mid = 0.5 * (lo + hi)
            if f(mid) > 0.0 { lo = mid } else { hi = mid }
        }
        return 0.5 * (lo + hi)
    }

    /// The v3 threshold: clamp(10^valley, tauSessionMinSec, tauSessionMaxSec) when the
    /// sample is bimodal by the rule in `Tuning`, else the fallback.
    public static func fit(gaps: [Double]) -> TauFit {
        var xs: [Double] = []
        xs.reserveCapacity(gaps.count)
        for g in gaps where g > 0.0 { xs.append(log10(g)) }
        let n = xs.count
        if n < Tuning.tauFitMinGaps {
            return .fallback(n: n, reason: "\(n) gaps < \(Tuning.tauFitMinGaps)")
        }
        let p = fitTwoGaussians(xs)
        let v = valley(m1: p.m1, m2: p.m2, s1: p.s1, s2: p.s2, w1: p.w1, w2: p.w2)
        func mix(_ x: Double) -> Double { p.w1 * pdf(x, p.m1, p.s1) + p.w2 * pdf(x, p.m2, p.s2) }

        var reason = "bimodal"
        var bimodal = true
        if p.m2 - p.m1 < Tuning.tauFitMinSeparationDecades {
            bimodal = false
            reason = "modes \(String(format: "%.2f", p.m2 - p.m1)) decades apart < \(Tuning.tauFitMinSeparationDecades)"
        } else if min(p.w1, p.w2) < Tuning.tauFitMinComponentWeight {
            bimodal = false
            reason = "minor component weight \(String(format: "%.3f", min(p.w1, p.w2))) < \(Tuning.tauFitMinComponentWeight)"
        } else if v == nil {
            bimodal = false
            reason = "no crossing between the modes"
        } else if let vv = v, !(Tuning.tauFitValleyMinLog10 <= vv && vv <= Tuning.tauFitValleyMaxLog10) {
            bimodal = false
            reason = "valley outside the plausible range"
        } else if let vv = v, !(mix(vv) < mix(p.m1) && mix(vv) < mix(p.m2)) {
            bimodal = false
            reason = "mixture has no dip between the modes"
        }

        if bimodal, let vv = v {
            let raw = pow(10.0, vv)
            let clamped = min(max(raw, Tuning.tauSessionMinSec), Tuning.tauSessionMaxSec)
            if clamped != raw { reason = "bimodal; clamped" }
            return TauFit(
                n: n, m1: p.m1, m2: p.m2, s1: p.s1, s2: p.s2, w1: p.w1, w2: p.w2,
                valley: vv, bimodal: true, reason: reason, tau: clamped, source: .fitted)
        }
        return TauFit(
            n: n, m1: p.m1, m2: p.m2, s1: p.s1, s2: p.s2, w1: p.w1, w2: p.w2,
            valley: v, bimodal: false, reason: reason, tau: Tuning.tauSessionSec,
            source: .fallback)
    }
}
