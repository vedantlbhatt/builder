# Builder — working notes

Strava for build sessions. Reads the logs your AI coding tools already write to disk,
turns them into sessions with a shape and a story, and tells you when one finishes.

Capture → sessionize → analyze → notify → present, then share. The single-player loop is
the product; the social layer (`docs/social.md`: posts, kudos, comments, follows,
factions, a reverse-chronological feed) is built server-side on top of it and is
deliberately small — no challenges, digests, reposts, DMs, or any ranking that is not a
sum. Co-op detection is still out of scope.

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
headline personal record. The rule that replaced it: every session carries two clocks,
`attended` (within `tauAutonomousSec` of a presence signal) and `autonomous`, and
**attended time decides records** — a kickoff prompt plus eight autonomous hours scores its
attended minutes. `unattended` means zero PRESENCE SIGNALS (typed or remote-human prompt,
interrupt, human file edit), not zero prompts; such runs count toward hours, can never win a
record or extend a streak, and when they stop they fire "Agent run finished" rather than
"Session finished". A presence signal after 2 h of autonomy, or 04:00 during autonomy, cuts
the run; an attended late night is never split. The reference implementation is
`scripts/measure_boundaries.py` and the design is `docs/session-boundaries.md`.

**Pooling by the repository each record's `cwd` resolves to.** Claude Code stamps the
shell's CURRENT cwd on every record. One 2,231-record sitting `cd`'d between home and the
repo 332 times; all 15 prompts landed in one pool and 833 tool calls (19 commits) in
another, and the phone showed a 1h09m session with "Prompts you typed 0". A session is one
human's sitting and the repo is an attribute of it: pool by transcript lineage and fold every
record of one native session id into that id's dominant pool (v3, `fold_by_session_lineage`).

**Fitting the idle threshold to record gaps.** The Halfaker (WWW 2015) two-mode fit on
record gaps is confidently bimodal — between the harness's millisecond flush and the agent's
3 s tool cadence, valley 0.1 s, which the clamp would have turned into 300 s. The fit runs
on presence-to-presence intervals, needs 200 of them and a real dip inside [95 s, 11,400 s],
and otherwise falls back to 900 s. `make measure-gaps` prints both fits, labelled.
`docs/research/session-boundaries-research.md` has the literature and the numbers.

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
- Remote sessions (Claude Code web/phone) stamp human prompts as `promptSource: "sdk"` with
  `origin.kind: "human"`, never `"typed"`. MEASURED: 9 of 9 on a remote transcript. A
  typed-only rule counts zero prompts and files the entire sitting as unattended.

## Later findings, same rule

**A policy predicate that reads another RLS-protected table sees it through the viewer's
own eyes.** `sessions_public` checked repo exclusion with a NOT EXISTS against
`repo_visibility`, which is itself RLS-protected — so an anonymous viewer's subquery
returned zero rows, NOT EXISTS came back true, and an excluded repo's shared sessions
stayed public. The policy read as though it enforced exclusion and enforced nothing,
failing OPEN with no error anywhere. Route any such check through a SECURITY DEFINER
function with a fixed search_path.

**A negative test that cannot reach the code it is trying to violate passes for the wrong
reason.** The write-isolation test resolved the victim's device inside the restricted
connection, where `devices` is also RLS-protected, so the SELECT matched nothing, the
INSERT wrote zero rows, and the assertion held without ever exercising WITH CHECK.

**"Today" is not the calendar date.** At 00:20, mid-session, the menu bar read "0s active
today" — technically correct and completely wrong. `Tuning.dayBoundaryHour = 4` applies
identically in ingest, derivation and the graph; three different definitions of "day"
would disagree about streaks in ways that are very hard to see and impossible to explain.

**Rank sessions by duration alone and the winner is a robot.** The longest session in the
corpus had ZERO typed prompts — an autonomous run about to become the headline personal
record. `unattended` sessions count toward hours and can never win a record or fire a
notification.

**Backfill must be silent.** First launch finalized 71 historical sessions at once and
announced every one. An alert is only meaningful if it is news, so anything older than
twice the session threshold is recorded as notified without being delivered.

**A verification command that cries leak on correct output is worse than none.** The
published privacy check walked every scalar path and compared it against a flat list of
top-level field names, so `tokens.input` and `strip_marks[].ms` came back as "sent but not
declared". They are the insides of declared fields — but the first person to run it
publicly would have concluded the claim was false.

**Two counters for one number is the same bug as a wrong number.** `analysis/digest.py`
has read `cat > path <<'EOF'` writes since it was written; the INGEST parser
(`ClaudeCodeParser`) only ever read Edit's `structuredPatch` and Write's created content.
So one session was described to the analyst as "agent lines +2450" and rendered on the card
as nothing. MEASURED on this repository's container corpus, 17 root transcripts: 2,452 of
2,458 attributable lines came through the shell, so the card was showing 0.2% of the work.
The rule now lives once, in `BuilderParse.ShellFileEffect`, and `SessionDigest` forwards to
it. `sed -i` names a path and returns NO count: the file was touched, the magnitude is not
in the command, and inventing one would feed `attribution` a guess it would treat as
measured.

**The density floor is a design constant, not a detail.** At 0.45 the identity amber
rendered as muddy brown across most of a real strip, because a 71-minute session is 4.2s
per column and most columns land in the lowest bucket. Density should modulate the colour,
not dilute it.

## Commands

```bash
make gen          regenerate from the three specs
make test         the ground-truth regression suite
make scan         parse everything and report
make watch        daemon: watch, sessionize, notify on completion
make doctor       diagnostics, records, rollups
make share        render the last notable session to a PNG

./scripts/make_app.sh          assemble Builder.app
cd mobile && bun test          strip conformance, same fixtures as Swift
cd server && pytest            contract, RLS, boot guard, auth bootstrap
make measure                   boundary rules over your corpus, read-only
make analyze T=<jsonl>         digest + your own Claude Code -> SessionAnalysis
python -m analysis probe DIR   what record shapes a foreign transcript store holds
make capture-test              the cloud uploader against the boundary fixtures and the contract
python -m capture sync --dry-run   what a cloud container would upload, without sending
curl $SERVER/v1/ingest/hook.sh   the Claude Code hook: nothing installed, sessions on the phone (docs/hooks-capture.md)
```

Design notes worth reading before touching the corresponding code: `docs/session-boundaries.md`
(when a session ends, two clocks, three cuts), `docs/analysis.md` (the digest rules and the
prompt), `docs/integrations.md` (where every tool keeps its transcripts), `docs/social.md`
(the layer that is deliberately small).

## What each suite is actually for

| suite | n | protects |
|---|---|---|
| `swift test` | 136 | the measured ground truth, that a shell-written file reaches the card, the strip fixtures, the boundary fixtures (v3: lineage pooling, the threshold fitter against the Python fit), the Codex and Gemini fixtures, the live-path fixtures, digest parity with the Python reference, the analysis scheduler's retry rules |
| `bun test` | 378 | that the phone decodes the strip identically to the Mac; the Api refresh/retry rules; the cache's live→final rules; the social helpers and the upload flow; the notification-tap routing; the mascot's frames and motion tables; the eight-animal pack's frames, palette recipes and per-frame change ceiling |
| `pytest` | 118 | that undeclared fields cannot be stored, that RLS is real (as builder_app, through the routes), auth bootstrap, contract v2/v3, social, capture keys and their scope, the notification horizon, the hook channel's parity with capture |
| `unittest` (analysis/) | 120 | the Codex, Gemini, Cline, opencode and Aider loaders against their synthetic fixtures AND the real writers' output; Claude Code stats unchanged |
| `make capture-test` | 56 | boundary parity of the cloud uploader (v3 pooling), contract conformance (nested walk), refresh-on-401 rotation, capture-key auth |
| CI `reference` job | — | the boundary fixtures are what `scripts/measure_boundaries.py` produces |

CI runs on `main`, on `claude/**` branches and on demand. The macOS job is the only Swift
compiler an autonomous session has; push small and read the log.
