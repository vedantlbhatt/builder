SWIFT_PKG := Packages/BuilderKit

.PHONY: help gen check-gen build test scan watch doctor share clean measure measure-gaps analyze fixtures capture-test

help:
	@echo "gen        regenerate everything from privacy/, spec/ and design/"
	@echo "check-gen  regenerate and fail if anything changed (this is the CI gate)"
	@echo "build      swift build"
	@echo "test       swift test — the ground-truth regression suite"
	@echo "scan       parse everything on disk into the local store"
	@echo "watch      run the daemon: watch, sessionize, notify on completion"
	@echo "doctor     diagnostics, per-source row counts, records, rollups"
	@echo "measure    what the session-boundary rules do to ~/.claude/projects (read-only)"
	@echo "measure-gaps  the gap distribution, the two-mode fit and the fitted tau for YOUR corpus"
	@echo "analyze    T=<transcript.jsonl>  digest it and have your own Claude Code read it"
	@echo "fixtures   regenerate spec/fixtures/boundaries from the reference implementation"
	@echo "capture-test  the cloud uploader: boundary parity, contract conformance, refresh-on-401"

# The four specs are the only hand-edited definitions of the wire payload, the strip
# format, the palette and the session analysis. Everything downstream is generated into
# Swift, TypeScript and Python so the same numbers cannot drift across three languages.
gen:
	@python3 scripts/gen_contract.py
	@python3 scripts/gen_strip.py
	@python3 scripts/gen_tokens.py
	@python3 scripts/gen_analysis.py
	@python3 scripts/gen_fixtures.py

# Fastest gate in CI, so it runs first. If this fails, someone hand-edited a generated
# file — including, potentially, a generated file that defines what may leave the machine.
check-gen: gen
	@git diff --exit-code -- \
		Packages/BuilderKit/Sources/BuilderModel/Generated \
		Packages/BuilderKit/Sources/BuilderSync/Generated \
		mobile/src/generated server/builder/contract.py server/builder/strip.py \
		server/builder/analysis_spec.py analysis \
		Packages/BuilderKit/Sources/BuilderAnalysis/Resources/analysis_schema.json \
		server/builder/static/upload-fields.json PRIVACY.md spec/fixtures \
		|| (echo ""; echo "FAIL: generated files are stale or hand-edited. Run 'make gen' and commit."; exit 1)
	@echo "generated files match their specs"

build:
	swift build --package-path $(SWIFT_PKG)

test:
	swift test --package-path $(SWIFT_PKG)

scan:
	swift run --package-path $(SWIFT_PKG) builder scan

watch:
	swift run --package-path $(SWIFT_PKG) builder watch

doctor:
	swift run --package-path $(SWIFT_PKG) builder doctor

share:
	swift run --package-path $(SWIFT_PKG) builder share --last

clean:
	swift package --package-path $(SWIFT_PKG) clean

# Read-only: presence-interval distribution and the sensitivity grid for the two boundary
# thresholds that ship as judgement calls (docs/session-boundaries.md). Run it on your own
# corpus before changing either number.
measure:
	@python3 scripts/measure_boundaries.py $${ROOT:-$$HOME/.claude/projects}

# Read-only: the inter-event gap histogram, the naive record-gap fit (which finds the
# harness's write cadence) and the v3 fit on human presence intervals (which is what the
# sessionizer uses), with the session count at the fitted tau against the 900 s fallback.
# Pass EXTRA=<dir> for a tree of other harnesses' transcripts; SYNTHETIC=<dir> to also run
# the boundary fixtures, labelled as such.
measure-gaps:
	@python3 scripts/measure_gap_distribution.py --root $${ROOT:-$$HOME/.claude/projects} \
		$${EXTRA:+--extra $$EXTRA} $${SYNTHETIC:+--synthetic $$SYNTHETIC}

# One session, end to end: digest -> claude -p -> validated SessionAnalysis JSON.
analyze:
	@test -n "$(T)" || (echo "usage: make analyze T=path/to/transcript.jsonl"; exit 2)
	@python3 -m analysis run "$(T)" --out "$${OUT:-analysis.json}"

fixtures:
	@python3 scripts/gen_boundary_fixtures.py
	@python3 scripts/gen_real_fixture_expected.py

# `capture/` is the uploader for Claude Code sessions that run in the cloud
# (docs/cloud-capture.md). Stdlib unittest: it must run where nothing is installed.
capture-test:
	@python3 -m unittest discover -s capture/tests -t . -v
