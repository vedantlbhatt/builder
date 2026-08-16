# builder

Strava for build sessions. Reads the logs your AI coding tools already write to your own
disk, turns them into sessions with a shape and a story, and tells you when one finishes.

Nothing about your prompts, your code or your file names leaves your machine. That claim
is checkable, not stated — see [Privacy](#privacy) below.

```
$ builder sessions --limit 1

  Fri 7 Aug 09:03   gt-transit           +2,101 lines
                    5h 17m active of 6h 48m    52 prompts   215 tools   194.4M tokens
                    ▐███▁▅▁·······▐██▅▁▁▁·····▐█▅▁▁·····▐███▁▅▅▁▁···▐█▁▅
```

## What it does

**Capture** — parses `~/.claude/projects` and Cursor's conversation store incrementally.
A cold run reads 1.2 GB in about 75 seconds; every run after that takes about a second,
because sources are watermarked by byte offset, inode and a head hash.

**Sessionize** — pools events by repository, sorts by time, and cuts on a 15-minute idle
gap. Reports **elapsed and active** separately, the way a running app separates elapsed
from moving time.

**Analyse** — token accounting that is actually correct, a timeline strip, personal
records, a contribution graph coloured by hours, project arcs, and an honest
human-versus-agent breakdown.

**Notify** — detects that a session has *ended* and tells you, exactly once. This is the
hard part: a session ends precisely because nothing happens, so no amount of file-watching
can detect it. A timer runs regardless of activity.

**Present** — a recap card you can share, on the Mac and on the phone.

## Getting started

```bash
make gen          # regenerate everything from the three spec files
make test         # the ground-truth regression suite
make scan         # parse everything on disk
make doctor       # records, contribution graph, projects, diagnostics
make watch        # the daemon: watch, sessionize, notify on completion

./scripts/make_app.sh     # assemble Builder.app and run it from the menu bar
```

## Why the numbers are different from every other tool

Every constant in
[`Tuning.swift`](Packages/BuilderKit/Sources/BuilderModel/Tuning.swift) carries the
measurement it came from. `builder groundtruth` reproduces figures that were measured
independently of this code, before it existed:

| | measured | this build |
|---|---|---|
| sessions at 15 / 30 / 60 / 120 min | 84 / 52 / 30 / 22 | exact |
| hours at the same thresholds | 98.98 / 110.76 / 125.46 / 137.79 | exact |
| gap p50 / p90 / p98 / p99 | 1.0 / 13 / 63 / 171 | 1.0 / 12.8 / 63.0 / 171.1 |
| content-block token overcount | 1.878x | 1.877x |

Three things that make the token count differ from what you have seen elsewhere:

1. **Claude Code writes one record per content block** and repeats the identical `usage`
   object on each. 44,419 records carry usage; only 22,887 distinct `message.id` exist.
   Summing them inflates by **1.878x**.
2. **Subagent transcripts double-count on top of that.** The parent's `Agent` tool result
   already carries their aggregated usage, and their message ids genuinely differ, so
   deduplication does not help. A `**/*.jsonl` glob is inflated again.
3. **Rewound work still costs money.** `parentUuid` is a DAG with 225 fork points; branches
   you abandoned are still in the file with valid timestamps. Their tokens count (you paid
   for them, reported separately) but their *edits* do not, because those lines never
   reached the file.

And one thing that is absent rather than zero: **Cursor never records token counts
locally.** All 14,565 message rows are `{0, 0}` — usage is accounted server-side. A Cursor
session shows no token figure rather than a zero, because a zero would be a claim about
the session instead of a fact about Cursor.

## Privacy

Prompts, code, diffs, file paths and file names never leave your machine. Repository names
and session titles leave only for repositories you explicitly mark public. Everything else
is timings, counts, and the shape of the session.

The wire payload is defined in exactly one place,
[`privacy/upload-contract.json`](privacy/upload-contract.json), which generates the Swift
encoder, the server model, the TypeScript types and the published field list. The client
encodes through a generated key enum with **no synthesized `Codable`**, so a field that is
not in that file has no way to be sent — a stronger guarantee than any test.

Check it yourself:

```bash
# Print every byte the agent would send. Sends nothing.
builder sync --dry-run --print-payload | jq

# Pick a phrase you typed to your agent today. Go looking for it.
builder sync --dry-run --print-payload | grep -i "that phrase"

# Diff the actual keys against the published contract.
builder sync --dry-run --print-payload \
  | jq -r '[paths(scalars)]|.[]|join(".")' \
  | sed 's/^sessions\.[0-9]*\.//; s/\.[0-9][0-9]*\./\./g' \
  | sed 's/^tool_calls\..*/tool_calls.<allowlisted tool name>/' | sort -u > /tmp/actual
curl -s "$BUILDER_API_URL/upload-fields.json" | jq -r '.leaf_paths[]' | sort -u > /tmp/declared
comm -23 /tmp/actual /tmp/declared    # empty

# Trust none of the above. Builder does not certificate-pin, on purpose.
mitmproxy -p 8080 & HTTPS_PROXY=http://127.0.0.1:8080 builder sync
```

## Layout

```
privacy/upload-contract.json   the only definition of the wire payload
spec/strip.v1.json             the only place strip class ordinals live
design/tokens.json             the only place colours live
scripts/gen_*.py               → Swift + TypeScript + Python. Never hand-edit the output.

Packages/BuilderKit/           the engine, and the menu bar app. Zero dependencies.
mobile/                        Expo / React Native, shipped via EAS
server/                        FastAPI on Railway
```

`make gen && git diff --exit-code` is the first CI gate. If it fails, someone hand-edited
a generated file — possibly one that defines what leaves your machine.

See [CLAUDE.md](CLAUDE.md) for the working notes, including the things that were tried and
reverted and why.
