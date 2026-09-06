# Session analysis: how a session becomes a reading of how you build

`spec/analysis.v1.json` defines the output. `analysis/` is the reference pipeline. This is
the reasoning behind both, and the numbers from running it.

There are TWO readings here and they must not be confused:

| | who writes it | scope | where |
|---|---|---|---|
| **session analysis** | a model, from a digest | one session | `spec/analysis.v1.json`, `analysis/prompt.py` |
| **builder profile** | nobody: it is arithmetic | the whole corpus | `analysis/profile.py` |

The profile is the punchy part (`planning_ratio`, `steer_rate`, `code_velocity`, an
archetype and a ranked list of one-line facts). It is COMPUTED, so it cannot be
hallucinated, and it is the reason the session analysis was allowed to get short.

## What it is for

The engine already knows *what happened*: minutes, prompts, tool calls, lines, commits,
tokens. It cannot say *what got built* or *how you worked*. That needs something that reads
the transcript. The analysis is a model's structured reading of one session: a headline,
two sentences, up to three highlights, the moves you make when directing an agent, and a
score on the five dimensions Paxel made familiar (steering, execution, engineering,
product instinct, planning), per session rather than per person, so a profile can be an
honest aggregate rather than one run's impression.

It runs **on your machine, through your own Claude Code** (`claude -p`), so it costs
nothing beyond the subscription you already have, needs no API key, and sends the digest
nowhere except to Anthropic under the agreement you already accepted. Nothing goes to a
third party for summarisation. That is the difference from Paxel's data path, and it is why
the analysis field is the one deliberate exception in the privacy contract — declared,
opt-in, private under RLS, and visible to anyone else only when you share the session.

## Short, not an essay

The first version returned five paragraphs per session: features, a work mix, pivots, a
friction list, a four-sentence summary. Nothing displayed most of it and nobody wanted to
read their own afternoon at that length. The shape now is

* `headline`, at most **70** characters,
* `summary`, **at most two sentences**,
* `highlights`, up to **three** one-line facts about the session, with a number in them
  where the digest gives one,
* the five `dimensions` and up to three `growth_edge` lines, unchanged,
* `build_style`, `prompting`, `decision_patterns` (now capped at 3), `tags`, `outcome`,
  `archetype`, `confidence`, which is what the profile aggregates.

`features`, `work_mix`, `pivots` and `friction` were REMOVED from the spec rather than
left for a model to fill: a field that exists gets written, and every one of them cost
the reader attention and the analyst tokens.

## No dashes, anywhere

The user's rule, and it is absolute: **no em dashes and no en dashes in any string a
person reads**. A comma, a full stop or a colon instead, and "5 to 10" rather than a
range written with a dash. Models write them constantly, so the rule is enforced three
times over:

1. `spec/analysis.v1.json` says "No dashes" in the doc of every prose field, and those
   docs become the JSON Schema descriptions the constrained decoder reads;
2. the analyst prompt states it as a rule, with examples, and the prompt obeys it;
3. `analysis/run.py` REWRITES any that survive (`dedash`), because the call costs minutes
   and money and a session with no analysis is worse than one with a comma where a dash
   was. A spaced dash becomes a comma; a dash between two digits becomes " to ".

The one exemption is `prompt_excerpt`: an excerpt is the user's own words, verbatim, and
rewriting it would both put words in their mouth and break the substring check that makes
it worth showing. `analysis/tests/test_prompt_style.py` holds all of this.

## The digest

The model never sees a transcript. It sees a digest, built by `analysis/digest.py`
(Claude Code) and `analysis/codex.py` (Codex), both producing the same event list.

Three rules, in priority order:

1. **Every human prompt, verbatim.** Prompts are the steering signal and there are few of
   them — 1,456 typed prompts against 23,838 tool calls in the reference corpus. They are
   never sampled out. Each is bounded at 1,400 characters.
2. **Every error.** Friction is the thing a person most wants explained.
3. **Everything else degrades under a budget** (60,000 characters, ~15k tokens): assistant
   text is truncated, then runs of tool calls collapse into one line each ("TOOLS ×12 over
   4.2m: Bash×7, Read×3, Edit×2 — edits: cache.ts +40/-3"), then the middle of the session
   is thinned evenly. `coverage` reports what fraction of lines survived and the model is
   told to lower its confidence accordingly.

Never read: thinking blocks (the model's scratch space; the bulk of the bytes), file
contents, full tool output. Masked before anything is written: API keys, tokens, JWTs,
private keys, `password=`-style assignments, email addresses. The digest is local, but the
analysis it produces can be uploaded, and a model will happily copy a token into a
"friction" note.

Two things found by running it on a real transcript rather than reasoning about it:

- A loose error heuristic flagged 20 tool results; 17 were successful commands whose
  output merely *mentioned* an error string. Only shapes that mean the command failed count
  (`Traceback`, `error:`, `fatal:`, `Exit code N`, the harness's `is_error` flag).
- A session run in a shell-first permission mode wrote every file with `cat > path
  <<'EOF'` and made zero Edit/Write calls, so agent lines read **+0** on a session that
  added 1,300. Heredoc and `sed -i` writes are credited, approximately and labelled.

## The prompt

`analysis/prompt.py`. The model is told what it is not seeing and asked to leave fields
null rather than fill gaps. Deterministic numbers are given in the digest header and
declared authoritative. Each dimension has behavioural anchors per band so two sessions a
week apart are scored on the same scale. Decision-pattern excerpts must be verbatim
substrings of a prompt; the runner checks each one against the digest and drops any that
is not (one of four was dropped on the first real run — the model had trimmed a quote).
The excerpt check runs BEFORE the dash rewrite, and excerpts are exempt from it, so a
dash the user typed survives in their own quoted words.

## Running it

```sh
python -m analysis profile [dir]                      # corpus metrics + ranked facts
python -m analysis digest <transcript.jsonl>          # print the digest
python -m analysis stats  <transcript.jsonl>          # deterministic numbers only
python -m analysis run    <transcript.jsonl> --out a.json
python -m analysis probe  ~/.codex/sessions           # read-only: what shapes are in there
```

`run` calls `claude -p` with a replaced system prompt, `--tools ""` and the generated
`analysis/schema.json`. The default model is `sonnet` (`BUILDER_ANALYSIS_MODEL` overrides).
Measured on a 45-minute, 212-event session: 33 KB digest, five internal turns, 150 s,
$0.33 at list price — covered by a subscription. Measured again on a 770-minute,
626-event remote session (Claude Code 2.1.261, 2026-09-05): 56 KB digest at coverage
1.0, 204 s, $0.46; the runner dropped 1 of 5 decision-pattern excerpts as not verbatim
and the 4 kept were checked by hand against the digest. A 5-event `claude -p` session
(one failing test run, one commit, no human prompt) took 83 s and $0.16, scored every
dimension 5–15 with confidence 0.35 and got no archetype — the floor behaves. Two CLI
facts that cost an hour to learn:
structured output needs several turns, so `--max-turns 1` fails silently with exit 1; and
the CLI's schema validator rejects a `$schema` header, so it is stripped.

## When it runs (agent)

For every session the lifecycle holds `final`, for every end reason — so after a
`human_returned` end the first thing you see when you sit down is what happened while you
were away. For a live session in an autonomous run, a checkpoint every
`Tuning.analysisCheckpointSec` (2 h), so the phone can answer "what has it done so far" at
3 a.m. Results are stored in the durable store (they cost money to regenerate), keyed by
session and digest hash, and attached to the next upload when analysis upload is on.

The final trigger is the *state*, not the transition into it. `AnalysisScheduler.consider`
runs on every tick and offers a final job to any `final` session that is worth analysing,
ended within `backfillHorizonSec` (24 h), has no final row — a surviving checkpoint row
does not count — and has not failed within `retryAfterFailureSec` (30 min). An earlier
version offered the job only on the tick where the lifecycle transition fired, so a
`claude -p` that failed on that tick (CLI not on a Finder-launched app's PATH, the 480 s
timeout, a locked database, a transient exit 1) was never retried, and the run's last
checkpoint went up on the final upload as if it were the reading of the whole session. Two
rules now hold: a failed final is re-offered once the backoff expires, at most
`backfillHorizonSec / retryAfterFailureSec` = 48 times before it is left to
`builder analyze --all-missing`; and `builder sync` attaches a checkpoint row only to a
`live` upload — a `final` upload without a final row goes up with no analysis rather than
the wrong one (`AnalysisSchedulerTests`).

The digest reads ROOT transcripts only. Subagent sidecars
(`<projectdir>/<uuid>/subagents/*.jsonl`) are ingested with the parent's cwd, repo and
timestamps, so a query by pool and window would hand the Swift digest two files where
`analysis/digest.py` reads one, and the two would never again agree on `digest_hash`.
`AnalysisJob.make` filters `is_sidechain = 0` on the event query and then applies
`ClaudeCodeParser.isRootTranscript` — the allowlist on path shape — to every resolved file,
recovering the path relative to the project directory from the source id
(`DigestTests.sidecarContributesNothingToTheDigest`).

## The builder profile: the corpus, computed

`analysis/profile.py` takes every session a person has and returns numbers, not prose.
Run it over your own transcripts, read-only:

```sh
python -m analysis profile                      # the whole JSON
python -m analysis profile --facts-only         # just the ranked one-liners
```

and the server serves the same object at `GET /v1/profile/builder` under `corpus`, built
from the stored sessions rather than from transcripts.

Every metric carries three things beside its value: the `basis` it was computed from, the
sample `n` it rests on, and a `reason` when the value is null. **A metric is never
estimated, defaulted or filled in.** The reasons are collected in `sample.missing` so the
phone can say why a number is blank instead of showing an empty card.

### The definitions that needed a decision

**`planning_ratio`** = prompts the agent answered with PROSE before touching a tool, over
prompts it answered by going straight to a tool. The obvious definition (two assistant
turns before the first EDIT tool, over prompts followed immediately by an edit) is
**15/0** on the container corpus, an undefined ratio: that harness writes files through
the shell, so there is 1 Edit/Write call in 714 tool calls and no prompt whose first agent
action is a write. Measured at the first TOOL instead, the same corpus splits **20 against
8** over 28 prompts, a ratio of 2.5, which is a number with a meaning.

**`steer_rate`** = (interrupts + corrective prompts) / prompts. A prompt is corrective
when a marker from a fixed list appears in its **first 25 words**. The first list, with
bare `not` and bare `stop` in it, flagged 14 of 29 real prompts and 2 of those were
plainly directives ("keep working do not stop until you have tested it", "whats done and
whats not"); both markers were narrowed and the list now flags 11, all hand-read. It
UNDER-counts (six prompts a human would call a redirect carry no marker, e.g. "Yes there
is man"), which is the safer direction for a sentence that says "you steer hard". An
interrupt and the redirect typed straight after it are one act of steering, not two.

**`code_velocity`** = agent lines per active hour, and a LOWER BOUND: edit-tool deltas
plus credited shell writes (heredoc, `sed -i`). It refuses to be a number at zero lines
("you wrote nothing" is a different and false claim) and when there is not enough behind
it: ten write events where writes can be counted, or 200 lines where they cannot. On the
server they cannot: since `ShellFileEffect`, both clients fold shell writes into one
`lines_added_agent` and the uploaded tool map cannot say which Bash call wrote a file, so
`write_events` there is None (unknown) rather than a count of the edit-tool names, which
would report zero writes for a session that wrote two thousand lines. MEASURED: the proof
database's rows carry 33 attributed lines over 4.4 hours, which reads as **7.6 lines an
hour**, while reading the same seven sittings from the transcripts credits 2,300 lines
through heredocs. 7.6 is not a small number, it is a wrong one.

**`night_share`** dices each session's span by local hour and gives each hour its share of
the session's active seconds, rather than filing a whole session under its start hour.
Days are cut at 04:00 local, the same boundary ingest and the graph use.

**Commits** are `git log` over the session window, the definition the uploader stores.
Counting `git commit` shell calls off the digest text is **3.5x low** (19 of 68 calls
survive the 160-character command truncation), and windows overlap, so the local runner
assigns each commit to the first session whose window contains it: summing per-session
counts reported 92 commits where the repository had 68.

### What the server cannot see

Prompt TEXT, interrupts, real tool NAMES and commit TIMES never leave the machine
(`privacy/upload-contract.json`), so on the server `avg_prompt_chars`,
`short_prompt_share`, `planning_ratio`, `steer_rate`, `night_commit_share`,
`test_runs_per_hour` and `tool_diversity` are null with a reason. Nothing is inferred to
fill the gap, and `tool_diversity` in particular is refused rather than computed from the
allowlisted map, which would undercount every corpus by construction.

The corpus archetype vocabulary is NOT the per-session `archetype` enum. The session enum
(spec/analysis.v1.json, what the phone's animal pack maps) is architect, velocity_machine,
quality_guardian, night_owl, explorer, firefighter; the computed one is architect,
velocity_machine, quality_guardian, night_owl, **director** and **skeptic**, because those
two are things you can measure (autonomy, steer rate) and "explorer" is not.

### The archetype

Six rules, each ONE metric crossing ONE threshold, all in `ARCHETYPE_RULES` with the
source of the threshold beside it: architect (planning_ratio 2.4), velocity_machine
(code_velocity 487/h), quality_guardian (test runs 3/h), night_owl (night_share 0.4),
director (autonomy 0.5), skeptic (steer_rate 0.4). Four are anchored on Paxel's published
example figures or on arithmetic (the six night hours are 0.25 of the clock);
quality_guardian's is an unmeasured judgement call and says so. A rule whose metric is
null does not score and cannot win, the winner must have MET its threshold, and the
response carries every score plus the two runners up so the UI can say "Architect, with a
streak of Night Owl". Confidence is the winner's margin over the runner up, damped by how
many sessions the corpus has.

### The facts

`headline_facts` turns the metrics into second-person one-liners with the number in the
sentence, ranked by distance from a documented baseline (`BASELINES`, each with its
source: Paxel's example report, this repository's reference corpus, or arithmetic). The
most unusual thing about you leads. Every fact carries `{id, text, value, unit}` so the UI
can style the number, and no fact is ever built on a metric that came back null.

On the container corpus (9 counted sessions, 5.1 active hours, 28 prompts):

```
[ 1.74] 38% of your build time runs without you
[ 1.09] 9% of your commits land after 10pm
[ 0.97] 24.2 tool calls per prompt, on average
[ 0.47] 7.4 different tools in the average session
[ 0.43] 39% of your prompts are under ten words
[ 0.31] 20% of your build time is between 10pm and 4am
[ 0.21] You default to Fable: 71% of output tokens
[ 0.19] Bash is 59% of every tool call you make
[ 0.14] You steer hard: 4 in 10 prompts stop or redirect the agent
[ 0.14] 450.9 lines an hour while the agent is running
```

## What the numbers are not

The dimension scores are one model's reading of one digest. They are shown with their
rationale and the confidence the model reported, never as a bare number, and the profile
aggregates them over many sessions. A session under about fifteen minutes gets no
archetype. If that ever stops being enough — if people start optimising for the score — the
right response is to remove the number, not to tune it.
