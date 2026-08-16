# Builder — working notes

Strava for build sessions. Reads the logs your AI coding tools already write to disk,
turns them into sessions with a shape and a story, and tells you when one finishes.

Scope of this build is **single-player only**. No clubs, feeds, kudos, comments,
challenges, digests, or co-op. Capture → sessionize → analyze → notify → present.

## The rule that matters most

**The failure mode of a log parser is not a crash. It is a plausible wrong number that
nobody questions.** Every constant in `Tuning.swift` carries the measurement it came from,
and every trap in a parser carries the count that justifies the handling. If you change a
number here without a measurement, you have made the product less true.

## Layout

```
privacy/upload-contract.json   THE ONLY definition of what may leave the machine
spec/strip.v1.json             THE ONLY place strip class ordinals live
design/tokens.json             THE ONLY place colours live
scripts/gen_*.py               → Swift + TypeScript + Python. Never hand-edit the output.
Packages/BuilderKit/           The engine. Zero external dependencies, on purpose.
mobile/                        Expo / React Native, shipped via EAS
server/                        FastAPI on Railway
```

`make gen && git diff --exit-code` is the first CI gate. If it fails, someone hand-edited a
generated file — possibly one that defines what leaves the machine.

## Verified against the real corpus

`swift run builder groundtruth` reproduces measurements taken independently of this code:

| | reference | this build |
|---|---|---|
| sessions at tau 900 / 1800 / 3600 / 7200 | 84 / 52 / 30 / 22 | exact |
| hours at same | 98.98 / 110.76 / 125.46 / 137.79 | exact |
| gap p50 / p90 / p98 / p99 | 1.0 / 13 / 63 / 171 | 1.0 / 12.8 / 63.0 / 171.1 |
| content-block token overcount | 1.878x | 1.877x |

Agreement is evidence, not tautology — the measurements predate the implementation.

## Things that were tried and REVERTED, with the reason

**Summing `.message.usage` across records.** Inflates by 1.878x. Claude Code writes one
JSONL record per content block and repeats the identical usage object on each: 44,419
records carry usage but only 22,887 distinct `message.id` exist. Dedupe on
`(source_id, message.id)`; the partial unique index makes a second authoritative row a
constraint violation rather than a summation choice.

**Globbing `**/*.jsonl`.** A second, independent overcount that deduplication cannot fix —
subagent sidecars carry tokens the parent's `Agent` tool result already reports in
aggregate, and their message ids genuinely differ. Sidecar detection is an ALLOWLIST on
path shape (`<projectdir>/<uuid>.jsonl` is a root), never a denylist on `subagents/`: the
tree has sibling `workflows/` and `tool-results/` directories that a denylist waves
through.

**Indexing the DAG walk by event id.** Content blocks get suffixed ids (`uuid#tu0`) while
`parentUuid` refers to the bare uuid, so every parent lookup missed, the chain terminated
at the first hop, and 80,923 of 109,820 events were misclassified as rewound — reporting
entire sessions' token totals as abandoned work. Walk on BASE ids.

**Computing active time within a session.** The gap after a session's last record was
dropped, so merging two sessions recovered up to 120s. Total active moved by 4.5 hours
across the threshold range, silently rewriting history whenever the threshold was tuned.
Credit every gap over the whole POOL, and extend `endedAt` by the trailing credit so
active can never exceed elapsed.

**Ranking sessions by duration alone.** The longest session in the corpus — 5h40m active —
had ZERO typed prompts: an autonomous run in an automation repo, about to become the
headline personal record. Sessions with no typed prompt are `unattended`: they count toward
hours, they cannot win a record or trigger a notification.

**Treating `type: "user"` as a prompt.** 18,836 user records, 1,456 typed prompts. The rest
are tool results and system injections. Checked whether records missing `promptSource` were
real prompts: they are slash commands (`/model`, `/effort`) and `isMeta` injections. The
strict rule is correct.

**`immutable=1` when reading Cursor's database.** It has a live 5.1 MB WAL against a 1.21 GB
main file; `immutable=1` skips the WAL and returns stale rows with no error. Use `mode=ro`
and close the connection after each poll so Cursor can still checkpoint.

**`git rev-parse --show-toplevel`.** Six of thirteen project directories on this machine are
worktrees of one repository; `--show-toplevel` fragments it into seven project arcs. Use
`--git-common-dir`. And the folder is named `RideGT` while the remote is `gt-transit` — the
display name comes from the normalized origin, never the directory.

**A `~Copyable` LineReader returning `UnsafeRawBufferPointer`.** The pointer is invalidated
by the next `next()`, producing a use-after-free generator on exactly the 78 MB file that is
hardest to debug. Measured headroom is ~20x, so it buys nothing.

**Imputing timestamps for the ~24,151 records that lack them.** They are entirely
bookkeeping and carry no usage, tool call or typed prompt. Interpolating between file-order
neighbours can run the clock backwards, because file order is not time order — 2,472
adjacent pairs are already inverted.

## Silent-failure traps to watch

- A partial trailing line must NEVER be consumed. Transcripts are appended to while being
  read, so the last line is routinely half-written; committing an offset mid-line loses
  that record forever.
- `.message.content` is a plain String on 3,299 records. `.toolUseResult` is a String on
  401 and a list on 3. Type-check at every nesting level.
- `Write` has `structuredPatch: []` always — count newlines in the created content instead,
  or every file created from scratch scores zero lines.
- Cursor never writes token counts: all 14,565 message rows are `{0, 0}`. Absent, not zero.
- Codex writes EMPTY STRINGS, not NULLs, for columns added in later migrations.
- Codex version-stamps its filenames (`state_5.sqlite`). Glob and take the highest integer.

## Commands

```bash
make gen          regenerate from the three specs
make test         the ground-truth regression suite
make scan         parse everything and report
make watch        daemon: watch, sessionize, notify on completion
make doctor       diagnostics, records, rollups
```
