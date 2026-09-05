# Session analysis: how a session becomes a reading of how you build

`spec/analysis.v1.json` defines the output. `analysis/` is the reference pipeline. This is
the reasoning behind both, and the numbers from running it.

## What it is for

The engine already knows *what happened*: minutes, prompts, tool calls, lines, commits,
tokens. It cannot say *what got built* or *how you worked*. That needs something that reads
the transcript. The analysis is a model's structured reading of one session — the features
that landed, how the work was planned and steered, where time was lost, the moves you make
when directing an agent, and a score on the five dimensions Paxel made familiar (steering,
execution, engineering, product instinct, planning), per session rather than per person, so
a profile can be an honest aggregate rather than one run's impression.

It runs **on your machine, through your own Claude Code** (`claude -p`), so it costs
nothing beyond the subscription you already have, needs no API key, and sends the digest
nowhere except to Anthropic under the agreement you already accepted. Nothing goes to a
third party for summarisation. That is the difference from Paxel's data path, and it is why
the analysis field is the one deliberate exception in the privacy contract — declared,
opt-in, private under RLS, and visible to anyone else only when you share the session.

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

## Running it

```sh
python -m analysis digest <transcript.jsonl>          # print the digest
python -m analysis stats  <transcript.jsonl>          # deterministic numbers only
python -m analysis run    <transcript.jsonl> --out a.json
python -m analysis probe  ~/.codex/sessions           # read-only: what shapes are in there
```

`run` calls `claude -p` with a replaced system prompt, `--tools ""` and the generated
`analysis/schema.json`. The default model is `sonnet` (`BUILDER_ANALYSIS_MODEL` overrides).
Measured on a 45-minute, 212-event session: 33 KB digest, five internal turns, 150 s,
$0.33 at list price — covered by a subscription. Two CLI facts that cost an hour to learn:
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

## What the numbers are not

The dimension scores are one model's reading of one digest. They are shown with their
rationale and the confidence the model reported, never as a bare number, and the profile
aggregates them over many sessions. A session under about fifteen minutes gets no
archetype. If that ever stops being enough — if people start optimising for the score — the
right response is to remove the number, not to tune it.
