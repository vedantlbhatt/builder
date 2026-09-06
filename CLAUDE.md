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

**An app that ships pointing at localhost does not crash.** `app.config.ts` defaulted
`apiBaseUrl` to `http://localhost:8000`, which is right for a simulator and fatal in a
store build: the app installs, opens, and fails every request against an address the phone
cannot reach, with no error a person could act on and nothing wrong with it except that.
An EAS build with no `BUILDER_API_URL` now fails at config time with the `eas secret:create`
line in the message. The rule lives in `mobile/src/config/apiBaseUrl.ts`, not inline in the
config, so `bun test` can run it: a guard nobody has ever executed is a guard nobody should
trust.

**One sitting in three containers is three sittings.** opencode keeps every session in a
SQLite database, and a machine upgraded from an older release still has the pre-SQLite
`storage/session/**.json` tree it was migrated from; an `opencode export` file may be
sitting beside both. All three hold the same sessions, so discovering "every session file
under the data directory" TRIPLES that person's hours with no error anywhere.
`capture/harnesses.py` keeps one container per session id, database first, and Gemini's
legacy whole-conversation `.json` is deduped against its `.jsonl` the same way.

**A contract enum value is always also a migration.** Adding `opencode` and `aider` to
`privacy/upload-contract.json` regenerates the Pydantic model, the Swift enum and the
TypeScript union, and all three accept the new value immediately. Postgres does not: the
`harness` TYPE is created in `0001` and grown by hand, so the first upload carrying a new
label is `invalid input value for enum harness` on the INSERT and a 500 the client cannot
act on. `test_every_contract_harness_exists_in_the_postgres_enum` reads the migrations
rather than trusting a comment.

**A correct per-session number can be an incorrect corpus total.** Every session asks git
what landed in its own window (plus the 30-minute attribution lookback), which is right on
its own card: "19 commits landed while you worked". Two sessions running AT ONCE in one
repository both get the same commits, so the SUM is not the number of commits. MEASURED on
this container: eleven sessions summed to 98 where `git log` over the same day counted 75,
because parallel agent sessions overlapped and one ran entirely inside another. The corpus
total is refused (`overlapping_session_windows`) rather than reported 31% high, and null is
not zero: the phone drops the row instead of claiming nothing was committed.

**A default root that guesses reads files nobody offered.** Every tool but one owns a
directory: `~/.codex`, `~/.gemini`, the extension's globalStorage, opencode's data dir.
Aider writes `.aider.chat.history.md` into the REPOSITORY you ran it in, and the first
version of `capture/harnesses.py` guessed `~/src`, `~/code`, `~/projects`, `~/work` and
walked them recursively. On a CI runner `~/work` IS the checkout, so discovery walked this
repository and reported its own Aider FIXTURES as the user's sessions. Aider now has no
default root at all; `discover(repo_roots=...)` reads only `<repo>/.aider.chat.history.md`
in repositories this machine's own transcripts already resolved to, which is knowledge
rather than a guess.

**A denominator that stops meaning anything is a wrong number with no error.** `Fanout.parallelism`
was agent-seconds over WALL seconds, which over one sitting reads correctly as "four hours of
agent work inside one hour of your life". Over a corpus it does not: MEASURED on this container,
53 agents did 11.9 hours of work inside a 19.3 hour stretch, and the ratio came out `0.61x` — a
concurrency figure below one, printed beside `max_concurrent: 8`, with nothing to tell a reader
which of the two was wrong. The denominator is now BUSY seconds, the union of the spans, so the
number is "while agents were running, this many were running" and can never fall below 1.0 when
anything ran. How much of the stretch had an agent in it is a different question and is reported
as one (`busy_share`).

**A rule copied into a second module is a definition that will drift.** `counted` decides
`visible` on the wire, which decides the population every server-side aggregate runs over. It was
written out twice — `capture/sessions.py` and `analysis/__main__.py` — and the uploader's own new
report path used neither, so it measured sittings the phone does not display and moved SEVEN of
this container's commits from "alone" to "assisted". `capture.sessions.is_counted` is the one
definition now, and a test asserts the constant appears exactly twice in that file: once imported,
once used.

**The density floor is a design constant, not a detail.** At 0.45 the identity amber
rendered as muddy brown across most of a real strip, because a 71-minute session is 4.2s
per column and most columns land in the lowest bucket. Density should modulate the colour,
not dilute it.

**Silence after the agent stopped is not the agent spinning.**
`patterns._runs_with_nothing_to_show` credited the trailing stretch to the session's
`ended_at` rather than to the last tool call, so every idle second after the work finished
counted as the agent going nowhere. A sitting whose last 50 calls finished in 100 seconds
and whose window ran two more hours reported a two hour stretch. The boundary rules
deliberately extend `endedAt` by the trailing gap, which is right for active time and wrong
for this: the corpus total was 2h11m and is 2h09m.

**The subagent sidecars are 12,236 records nothing had ever read.** Excluding them from
token counts is correct and it also made every agent invisible. `analysis/agents.py` reads
them and NEVER contributes a token, a line or a commit: it answers how many agents, of what
kind, running at once or one after another, and whether the delegation produced anything.
MEASURED: 52 agents in one sitting, up to 8 at the same moment, 712 agent-minutes inside a
1,159 minute stretch. Two rules that fail silently: discovery is an ALLOWLIST on path shape
(50 of 165 jsonl files here are in sibling `workflows/` and `tool-results/` directories),
and a handoff at the same instant is NOT two agents at once, or every consecutive chain
reads as parallel.

**A cost in tokens and the same cost in dollars can point opposite ways.** The sittings
that ended with no commit were 23% of the output TOKENS and 1% of the money, because the
quiet ones were cheap Sonnet sittings and every expensive Opus one shipped. The token
version was a true sentence pointing at waste that was not there. `analysis/pricing.py` is
the only place a price lives, it carries the day it was read, and the number is labelled
API LIST PRICES everywhere: most people are on a subscription and "you spent $12" is false
for them.

**One rule is one function, even when the arguments are in a different order.** The build
post migration first added a `repo_excluded_for_owner(repo, user)` beside the existing
`session_repo_excluded(user, repo)`. `can_view_post` COALESCEs the pair out of whichever of
the session or the post carries it and calls the one function.

**A `JOIN` written when a post could only be about one session.** `can_view_post` and the
feed query both INNER JOIN `sessions`, so a build post (0017) matched nothing: invisible to
everyone including its author, and absent from every feed, with no error. The exclusion
sweep had the same shape, deleting sessions and never reaching a post that has none.

## Commands

```bash
make gen          regenerate from the three specs
make lint         the server lint EXACTLY as CI runs it, pinned ruff
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
python -m analysis trends       how you build now against how you built before
python -m analysis agents       several agents at once: who ran, and what landed
python -m analysis rules --list failures that recurred across DIFFERENT sittings
python -m analysis playbook     your prompts that landed, against the ones that cost
python -m analysis contributions your commits, split by whether an agent was in the room
python -m analysis cards        what this corpus gives you to put in a feed
python -m analysis shipped      a build post: what you made, and what was hard
make capture-test              the cloud uploader against the boundary fixtures and the contract
python -m analysis report          the whole builder report, printed: trends, agents, commits, green
python -m capture report --dry-run what would be uploaded to the profile, without sending
python -m capture sync --dry-run   what a cloud container would upload, without sending
curl $SERVER/v1/ingest/hook.sh   the Claude Code hook: nothing installed, sessions on the phone (docs/hooks-capture.md)
```

Design notes worth reading before touching the corresponding code: `docs/session-boundaries.md`
(when a session ends, two clocks, three cuts), `docs/analysis.md` (the digest rules and the
prompt), `docs/integrations.md` (where every tool keeps its transcripts), `docs/social.md`
(the layer that is deliberately small), `docs/analysis-complete.md` (the two kinds of
analysis, every metric, and the nine things this codebase refuses to compute).

## What each suite is actually for

| suite | n | protects |
|---|---|---|
| `swift test` | 138 | the measured ground truth, that a shell-written file reaches the card, the strip fixtures, the boundary fixtures (v3: lineage pooling, the threshold fitter against the Python fit), the Codex and Gemini fixtures, the live-path fixtures, digest parity with the Python reference, the analysis scheduler's retry rules |
| `bun test` | 443 | that the phone decodes the strip identically to the Mac; the Api refresh/retry rules; the cache's live→final rules; the social helpers and the upload flow; the notification-tap routing; the mascot's frames and motion tables; the eight-animal pack's frames, palette recipes, per-frame change ceiling, that every one of them faces forward and that each loop moves at least three parts; the profile screen's archetype wording and its closest-rule fallback; that no refused block of the report renders as a zero |
| `pytest` | 159 | that undeclared fields cannot be stored, that RLS is real (as builder_app, through the routes), auth bootstrap, contract v2/v3, social, capture keys and their scope, the notification horizon, the hook channel's parity with capture, the corpus profile's server-side refusals, the report's door (nested extras, enums, string bounds) and that a null block survives the round trip |
| `unittest` (analysis/) | 490 | the Codex, Gemini, Cline, opencode and Aider loaders against their synthetic fixtures AND the real writers' output; Claude Code stats unchanged; every corpus metric's refusal reasons and the archetype rules; that the report's keys ARE the spec's keys at every level and that no field in it can carry free text |
| `make capture-test` | 74 | boundary parity of the cloud uploader (v3 pooling), contract conformance (nested walk), refresh-on-401 rotation, capture-key auth, and that every other harness discovers, dedupes and uploads |
| CI `reference` job | — | the boundary fixtures are what `scripts/measure_boundaries.py` produces |

CI runs on `main`, on `claude/**` branches and on demand. The macOS job is the only Swift
compiler an autonomous session has; push small and read the log.
