import BuilderIngest
import BuilderModel
import Foundation
import Testing

/// The EM itself, held to the Python reference on a bare gap list.
///
/// `spec/fixtures/boundaries/threshold_fit.json` is written by
/// `scripts/gen_boundary_fixtures.py`: 360 gaps and every number
/// `measure_boundaries.fit_tau` produced for them. The Swift fitter must reproduce each to
/// 1e-6. Both implementations initialise identically, accumulate in the same order and stop
/// on the same rule, so agreement is expected to ~1e-12; the tolerance is for libm.
@Suite("Threshold fitter — reference fit")
struct ThresholdFitterTests {

    struct Fixture: Decodable {
        struct Fit: Decodable {
            struct Constants: Decodable {
                let minGaps: Int
                let minSeparationDecades: Double
                let minComponentWeight: Double
                let valleyMinLog10: Double
                let valleyMaxLog10: Double
                let varFloor: Double
                let tauMin: Double
                let tauMax: Double
                let fallback: Double

                enum CodingKeys: String, CodingKey {
                    case minGaps = "min_gaps"
                    case minSeparationDecades = "min_separation_decades"
                    case minComponentWeight = "min_component_weight"
                    case valleyMinLog10 = "valley_min_log10"
                    case valleyMaxLog10 = "valley_max_log10"
                    case varFloor = "var_floor"
                    case tauMin = "tau_min"
                    case tauMax = "tau_max"
                    case fallback
                }
            }

            let n: Int
            let m1: Double
            let m2: Double
            let s1: Double
            let s2: Double
            let w1: Double
            let w2: Double
            let valley: Double?
            let bimodal: Bool
            let tau: Double
            let source: String
            let constants: Constants
        }

        let gaps: [Double]
        let fit: Fit
    }

    static func load() throws -> Fixture {
        guard let dir = BoundaryFixtureTests.fixtureDirectory else {
            throw NSError(domain: "ThresholdFitterTests", code: 1)
        }
        let data = try Data(contentsOf: dir.appendingPathComponent("threshold_fit.json"))
        return try JSONDecoder().decode(Fixture.self, from: data)
    }

    @Test func reproducesTheReferenceFit() throws {
        let fx = try load()
        let got = ThresholdFitter.fit(gaps: fx.gaps)
        let want = fx.fit
        #expect(got.n == want.n)
        #expect(abs(got.m1 - want.m1) < 1e-6, "m1 \(got.m1) vs \(want.m1)")
        #expect(abs(got.m2 - want.m2) < 1e-6, "m2 \(got.m2) vs \(want.m2)")
        #expect(abs(got.s1 - want.s1) < 1e-6, "s1 \(got.s1) vs \(want.s1)")
        #expect(abs(got.s2 - want.s2) < 1e-6, "s2 \(got.s2) vs \(want.s2)")
        #expect(abs(got.w1 - want.w1) < 1e-6, "w1 \(got.w1) vs \(want.w1)")
        #expect(abs(got.w2 - want.w2) < 1e-6, "w2 \(got.w2) vs \(want.w2)")
        #expect(got.bimodal == want.bimodal)
        #expect(got.source.rawValue == want.source)
        #expect(abs(got.tau - want.tau) < 1e-6, "tau \(got.tau) vs \(want.tau)")
        if let v = want.valley {
            #expect(got.valley != nil && abs((got.valley ?? 0) - v) < 1e-6, "valley \(String(describing: got.valley)) vs \(v)")
        } else {
            #expect(got.valley == nil)
        }
    }

    /// The constants the reference fitted with are the constants this build fits with.
    /// If one side moves without the other, this is the test that says so.
    @Test func fitConstantsMatchTuning() throws {
        let c = try load().fit.constants
        #expect(c.minGaps == Tuning.tauFitMinGaps)
        #expect(c.minSeparationDecades == Tuning.tauFitMinSeparationDecades)
        #expect(c.minComponentWeight == Tuning.tauFitMinComponentWeight)
        #expect(abs(c.valleyMinLog10 - Tuning.tauFitValleyMinLog10) < 1e-12)
        #expect(abs(c.valleyMaxLog10 - Tuning.tauFitValleyMaxLog10) < 1e-12)
        #expect(c.varFloor == Tuning.tauFitVarianceFloor)
        #expect(c.tauMin == Tuning.tauSessionMinSec)
        #expect(c.tauMax == Tuning.tauSessionMaxSec)
        #expect(c.fallback == Tuning.tauSessionSec)
    }

    /// The safety net, case by case.
    @Test func fallsBackWhenTheSampleCannotSupportAFit() {
        // Too few.
        let few = ThresholdFitter.fit(gaps: Array(repeating: 60.0, count: 50))
        #expect(few.source == .fallback && few.tau == Tuning.tauSessionSec)
        // Plenty, but one mode: a robot on a fixed cadence. Must not NaN; must fall back.
        let robot = ThresholdFitter.fit(gaps: Array(repeating: 60.0, count: 500))
        #expect(robot.source == .fallback && robot.tau == Tuning.tauSessionSec)
        #expect(!robot.m1.isNaN && !robot.s1.isNaN, "the variance floor keeps the EM finite")
        // Two modes but both machine-fast (7 ms and 3 s): the valley is outside the
        // plausible range, so it is rejected rather than clamped up to 300 s. This is the
        // shape the container corpus's RECORD gaps had.
        var machine: [Double] = []
        for i in 0..<300 { machine.append(0.005 + 0.00001 * Double(i % 7)) }
        for i in 0..<300 { machine.append(2.0 + 0.01 * Double(i % 50)) }
        let m = ThresholdFitter.fit(gaps: machine)
        #expect(m.source == .fallback, "a valley at 0.1 s is not a session boundary: \(m.reason)")
        // Clamp: within-sitting 30 s, between-sitting 3 days puts the valley past an hour.
        var far: [Double] = []
        for i in 0..<400 { far.append(20.0 + Double(i % 20)) }
        for i in 0..<40 { far.append(200_000.0 + 1000.0 * Double(i)) }
        let f = ThresholdFitter.fit(gaps: far)
        if f.source == .fitted { #expect(f.tau <= Tuning.tauSessionMaxSec) }
    }

    @Test func refitPolicy() {
        #expect(SessionThresholds.needsRefit(gapsAtLastFit: nil, gapsNow: 10, lastFitAt: nil, now: 0))
        #expect(!SessionThresholds.needsRefit(gapsAtLastFit: 200, gapsNow: 210, lastFitAt: 0, now: 3600))
        #expect(SessionThresholds.needsRefit(gapsAtLastFit: 200, gapsNow: 220, lastFitAt: 0, now: 3600))
        #expect(SessionThresholds.needsRefit(gapsAtLastFit: 200, gapsNow: 200, lastFitAt: 0, now: 86400))
    }

    /// `presenceGaps` is the sample the fit is defined on: presence-to-presence, across
    /// idle gaps, per pool. On the bimodal fixture it has the 239 intervals the reference
    /// fitted (216 within sittings, 23 between).
    @Test func presenceGapsAreTheFitSample() throws {
        let events = try BoundaryFixtureTests.events(for: "auto_tau_bimodal")
        let gaps = Sessionizer.presenceGaps(
            from: events, options: .init(pooling: .nativeSession, calendar: BoundaryFixtureTests.newYork))
        #expect(gaps.count == 239, "\(gaps.count) presence intervals")
        #expect(gaps.filter { $0 > 2400 }.count == 23)
    }
}
