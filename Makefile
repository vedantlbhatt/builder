SWIFT_PKG := Packages/BuilderKit

.PHONY: help gen check-gen build test scan watch doctor share clean

help:
	@echo "gen        regenerate everything from privacy/, spec/ and design/"
	@echo "check-gen  regenerate and fail if anything changed (this is the CI gate)"
	@echo "build      swift build"
	@echo "test       swift test — the ground-truth regression suite"
	@echo "scan       parse everything on disk into the local store"
	@echo "watch      run the daemon: watch, sessionize, notify on completion"
	@echo "doctor     diagnostics, per-source row counts, records, rollups"

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
