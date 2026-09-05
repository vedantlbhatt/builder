# When does a session end?

The question sounds like a threshold and is actually a definition. This document fixes
the definition, states every rule with the constant it reads, and says what is measured
and what is still a judgement call. The reference implementation is
`scripts/measure_boundaries.py`; the Swift sessionizer must reproduce it on the fixtures
under `spec/fixtures/boundaries/`, the same way the strip has a cross-language gate.

## The definition

**A session is a human sitting.** It is the unit a person would describe afterwards as
"I sat down and worked on X". The agent's activity is evidence of the sitting, not the
sitting itself. Everything below follows from taking that seriously now that agents run
for hours without anyone typing.

Two clocks run inside every session:

- **attended** — active time while a human is evidently present.
- **autonomous** — active time after the human has gone quiet for longer than
  `tauAutonomousSec` (30 min). The agent is working; nobody is steering it.

`active_seconds = attended + autonomous`, exactly as before. The split is new, and it is
the split that makes overnight runs describable instead of either a lie ("you worked
9 hours") or an omission ("nothing happened").

### Presence signals

Evidence a human is at the keyboard. Each one is a specific record shape, not a guess:

| signal | shape | note |
|---|---|---|
| typed prompt | `type: user`, `promptSource: "typed"`, `isMeta != true` | the existing rule |
| typed prompt (remote) | `type: user`, `promptSource: "sdk"`, `origin.kind: "human"` | **new.** Sessions driven from the Claude Code web/phone UI stamp prompts this way. MEASURED on a remote transcript: all 9 human prompts carried `sdk`/`human` and zero carried `typed`, so the old rule counted zero prompts and would have filed the whole session as unattended. |
| interrupt | `type: user`, text begins `[Request interrupted by user` | Escape, or "stop". Nobody presses stop from the other room. |
| human file edit | `attachment.type: edited_text_file` | the only true-positive human-authorship signal on disk |

Slash commands (`/model`, `/effort`) and `isMeta` injections remain non-prompts. They are
also non-presence: a `/compact` fired by the harness proves nothing about the person.

## The rules

Evaluated in this order, per gap between consecutive timestamped records in a pool.

### 1. `idle_gap` — nothing happened for `tauSessionSec` (900 s)

Unchanged, and still the boundary that ends 99.8% of sessions (MEASURED: 997 of 48,095
gaps exceed 60 s; the p99.9 gap is 32.5 min). This is the only end that means *the work
stopped*, and it is therefore the only end that fires a notification.

### 2. `human_returned` — presence after ≥ `tauReturnSplitSec` (2 h) of autonomy

You kick off a long task at 23:00 and go to bed. The agent runs until 03:10 and stops;
rule 1 ends the session at 03:25. Fine. But if it is a loop that never stops, and you sit
down at 09:00 and type, that prompt is not the ninth hour of last night's sitting. It is a
new one. The run is finalized at the instant of your prompt, and a new session begins
with it.

Why 2 h and not `tauAutonomousSec`: a 45-minute autonomous stretch inside an afternoon —
you asked for a refactor and went to lunch — is still your sitting when you come back to
check it. Two hours of absence is a different thing from a long lunch. UNMEASURED
JUDGEMENT CALL; the recipe to measure it is at the end.

### 3. `day_boundary` — 04:00 local falls inside a gap while autonomous

A robot running through the night credits its hours to each day it ran. The 04:00 rule
from `Tuning.dayBoundaryHour` is reused unchanged; the split happens only if, at 04:00,
the human has been absent for longer than `tauAutonomousSec`.

An **attended** session that crosses 04:00 is never split. That rule stands for the
reason it was written: splitting a late night manufactures a two-day streak out of one
sitting. The two rules do not conflict because autonomous time can never extend a
streak — nobody was there.

### Where `ended_at` lands, per end

Active time can never exceed elapsed — the server rejects a payload where it does — so each
end fixes `ended_at` explicitly. `idle_gap`: last record plus the capped credit for the
boundary gap, credited to the session (unchanged from v1). `human_returned`: the same,
credited as autonomous. `day_boundary`: exactly 04:00, with the gap credited up to the
boundary to the old session and the remainder of the capped credit to the new one, which
begins at 04:00. `still_running`: the last record, no trailing credit. The reference and
the Swift agree on all four; the fixtures pin the numbers.

### No hard cap

Rejected explicitly. A maximum length manufactures a boundary in the middle of real
work, and with rules 2 and 3 in place an attended session can only exceed a day if a
human was demonstrably present across it, which is a real sitting and should be
recorded as one.

## What each end means downstream

| end reason | notify | record-eligible | live upload replaced by final |
|---|---|---|---|
| `idle_gap` | yes — "Session finished" if attended, **"Agent run finished"** if unattended | attended sessions only | yes |
| `human_returned` | no (you are already here) | attended portion only | yes |
| `day_boundary` | no (the work continues) | no | yes |
| `still_running` | — | — | this IS the live upload |

`unattended` is redefined from "zero prompts" to **"zero presence signals"**. A session
with one kickoff prompt and eight autonomous hours is *not* unattended — the person did
start it — but its **record eligibility uses `attended_seconds`, never `active_seconds`.**
That closes the hole the old rule left open: the 5h40m "longest session" that was a
machine's record would now score its attended minutes.

Either notification is sent only if the session **ended** within `2 × tauSessionSec`
(1800 s) of being finalized; older ones are recorded as `suppressed_stale` and never sent,
so a first-launch backfill is silent. The horizon is measured from `ended_at` on both the
Mac (`SessionLifecycle.staleNotificationSeconds`) and the server (`notify.NOTIFY_HORIZON_SEC`),
never from `agent_observed_at`, which the client stamps at payload-build time and is
therefore always "now". An on-time final lands about 1000 s after the end (900 s gap +
30 s tick + 60 s sync pass), so 1800 s leaves ~13 minutes for a late tick.

"Agent run finished" is a new notification class. It is genuinely useful — it is the
moment you want to look at what happened — and it is exactly the case the old design
silenced because `notable` folded in `!unattended`. It does not count as a record and it
does not extend a streak.

## Live sessions

`open` and `idle` sessions are now uploaded as `state: "live"` snapshots, replaced in
place when they finalize (same `client_session_id`; the server upserts). This is what lets
the phone show *"Live · 2h 14m · 34 prompts · gt-transit"* while the Mac is still working,
and *"Running unattended for 3h 05m"* overnight.

Cadence: on any pass where the live payload's hash changed, at most once per
`liveUploadMinIntervalSec` (60 s). Live rows count toward today's hours on the phone and
toward nothing else until final.

## Analysis timing

The model-written analysis (`spec/analysis.v1.json`) runs when a session finalizes, for
every end reason. For a `human_returned` end that means the first thing you see when you
sit down is *what happened while you were away*. For live sessions in an autonomous run,
a checkpoint analysis runs every `analysisCheckpointSec` (2 h) so the phone can answer
"what has it done so far" at 3 a.m. without waking anyone.

## Remote and cloud sessions

Sessions started from the Claude Code web or mobile UI run in a cloud container. Their
transcript is written *there*, not under `~/.claude/projects` on the Mac, so the agent
never sees them unless they are synced back. The parser fix above makes them correct
when they do arrive; getting them to arrive is an integration (see
`docs/integrations.md`), not a boundary rule.

## What is measured, and what is not

From the 30-minute remote transcript this was developed against (`scripts/measure_boundaries.py`):

| quantity | value |
|---|---|
| presence → presence intervals during continuous activity | n = 10, p50 1m 33s, max 10m 53s |
| longest autonomous run | 0 (the session was attended throughout) |
| prompts counted by the old rule / the new rule | 0 / 9 |

That is one transcript and it is the wrong shape to calibrate `tauAutonomousSec` or
`tauReturnSplitSec` — it has no overnight run in it. The constants ship as judgement
calls with the measurement recipe attached:

```sh
scripts/measure_boundaries.py ~/.claude/projects
```

prints the presence-interval distribution over your whole corpus and a sensitivity grid
of session counts at `tauAutonomous ∈ {10, 20, 30, 60} min × tauReturnSplit ∈ {1, 2, 4} h`.
The number to look at is how many sessions rule 2 creates at each setting. If it is
creating them inside afternoons, `tauReturnSplitSec` is too low. If a run you remember
walking away from is still counted as attended, `tauAutonomousSec` is too high. When
those numbers exist for the reference corpus, they replace this paragraph.

## Things considered and not done

**Splitting on `sessionId`.** Two Claude Code conversations back to back in one repo
are one sitting to the person. Unchanged from v1.

**Ending a session when the agent says it is done.** There is no such record. An
assistant turn ending with `end_turn` happens hundreds of times per session.

**Using `sessionKind: "bg"` as the presence signal.** It marks background *turns*, not
absent *people*, and it appears on 2,508 records in the reference corpus — too coarse
and pointing at the wrong thing. `Tuning.unattendedBgFraction` is retired.

**Imputing presence from typing cadence in the prompt text.** Never read for this
purpose; the presence signals above are all record-shape checks.
