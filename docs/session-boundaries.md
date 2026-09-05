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

---

# v3 — a fitted threshold, lineage pooling, and two ends the human makes

v2 fixed the definition — a session is a human sitting — and cut it with four constants
chosen from one corpus's gap percentiles. v3 keeps the definition and the two clocks and
changes three things about how the cuts are found, each after measuring real inputs. The
research behind it, with citations and the numbers, is
`docs/research/session-boundaries-research.md`; the reference implementation is still
`scripts/measure_boundaries.py` and the Swift is still held to it by the fixtures.

## 1. Pooling: by lineage, not by where the shell was

**Finding (real input, this workspace).** Claude Code stamps the shell's *current* `cwd`
on every record. In one 2,231-record sitting the shell `cd`'d between `/home/user` and the
repository **332 times** (median gap at a change 0.42 s); all 15 human prompts carried
the home directory and 833 assistant records carried the repo. Pooled by the repository
each record resolved to — which is what `capture/` did and what the Mac's deriver does via
per-event `repo_id` — that one sitting uploaded as **two overlapping sessions**: one with
the prompts and no commits, one with 19 commits and "Prompts you typed 0". Nothing crashed.
The number on the phone was simply wrong.

**Rule.** A session is one human's sitting; the repository is an *attribute* of it, never
the partition key. Records are pooled by the transcript's lineage — the project directory
the file lives in (capture, the reference) or the repository the deriver resolved (the
Mac) — and then **every record of one native session id is folded into that id's dominant
pool**: most records wins, ties go to the pool of the id's earliest record, then to the
smaller key string, so three implementations agree (`fold_by_session_lineage`,
`Sessionizer.pool`). A record without a session id keeps its own key. Two conversations
back to back in one pool remain one sitting — v1's rule. The session's repository is then
the dominant resolvable cwd among its records (`capture.sessions.dominant_repo`; the Mac's
`repo_id_primary` already worked this way). Fixture: `cwd_interleaved_one_sitting` —
prompts under one cwd, tool calls and commits under another, seconds apart, ONE session.

Whether the Mac corpus has a cwd change inside a transcript: `Tuning.swift` and
`NormalizedEvent.swift` already record that it does (5 distinct cwds in one 30-minute
remote transcript; "it varies within a single file"), so the same split was available
there. How often it fired is unmeasured until the deriver is re-run on that corpus.

## 2. The idle threshold is fitted per person, with 900 s as the fallback

**Method.** Halfaker et al. (WWW 2015): inter-activity times are bimodal on a log scale,
and the session threshold belongs at the valley between the two modes, fitted per dataset.
v3 fits a two-component Gaussian mixture on log10 seconds by EM (`fit_tau`,
`ThresholdFitter`), finds the crossing of the two weighted densities by bisection, and
sets `tau = clamp(10^valley, 300 s, 3600 s)`.

**Finding (real input).** Run on **record** gaps, as the brief asked, the fit is
confidently bimodal on this container's corpus — and the two modes are the harness's
millisecond flush (records of one turn written 1–10 ms apart) and the agent's 1–10 s tool
cadence, with the valley at **0.1 s**. Between-sitting gaps are 0.9 % of record gaps and
cannot form a component. Clamping 0.1 s up to 300 s would have shipped a confident wrong
tau. Halfaker's events were human acts; a transcript writes thousands of machine records
per human act.

**Rule.** The sample is **presence-to-presence intervals** (`presence_gaps`,
`Sessionizer.presenceGaps`): the time between consecutive presence signals in a pool,
*including* the intervals that span idle gaps, over every pool of every harness the person
has. The fitted tau is then applied to record gaps by rule 1 — coherent, because a record
gap is never longer than the human interval containing it. The fit is used only if all of
these hold, else the fallback `Tuning.tauSessionSec = 900` stands:

| condition | constant | why |
|---|---|---|
| at least 200 intervals | `tauFitMinGaps` | five free parameters; the minor component needs ≥ 10 points |
| modes ≥ 0.8 decades apart | `tauFitMinSeparationDecades` | below that two log-normal humps show no dip at 0.1-decade bins |
| minor component ≥ 5 % | `tauFitMinComponentWeight` | else "the second mode" is a few outliers; on the reference corpus the between-sitting share is ~5.8 %, the number to watch |
| valley within half a decade of [300, 3600] s | `tauFitValleyMin/MaxLog10` | a valley at 0.1 s is a machine artefact; it is rejected, never clamped |
| the mixture dips at the crossing | (derived) | a crossing is only a valley if the density is lower there than at both modes |

Refit whenever the sample grows by 10 % or a day passes (`SessionThresholds.needsRefit`).
`scripts/measure_boundaries.py --tau auto` prints the fit; `make measure-gaps` prints the
histogram, both fits (the naive record-gap one, labelled as not used, and the v3 one) and
the session count at the fitted tau against 900 s. Fixtures: `auto_tau_bimodal` (239
presence intervals, modes 122 s and 54 min, valley 569 s, 25 sessions against 24 at
900 s — the extra cut is a planted 700 s mid-sitting silence) and `threshold_fit.json`
(a bare gap list; the Swift EM must match the Python to 1e-6).

On this container's corpus the answer is the fallback: 23 presence intervals. On the Mac
corpus (1,456 prompts, 84 sessions) the fit will run; whether it is bimodal by the rule
above is the first thing to look at when `make measure-gaps` is run there.

Capture uses `--tau auto` by default. **The Mac's deriver still passes the default
(fallback) tau** — `SessionThresholds` and `ThresholdFitter` exist and are tested, but
wiring the fit into `SessionDeriver` (fit over `presenceGaps`, store the fit, refit on the
policy above) is deliberately left for a change that can be run against the reference
corpus, because the ground-truth table in CLAUDE.md is stated at fixed taus and must not
move by accident.

## 3. Two ends the human makes on purpose

Both are announced exactly as `idle_gap` is — the work in that session stopped — and both
are final the moment they are derived, like a cut.

### `cleared` — the previous record was a typed `/clear`

The record shape: `type: user`, no `promptSource`, `<command-name>/clear</command-name>`
in the text (`Tuning.clearCommandMarker`). A human typed it, so it is also a presence
signal; it is not a prompt. The session ends there whatever the gap after it — silence
after a `/clear` is silence *after* the stop. Credit and `ended_at` are computed exactly
as for `idle_gap`; a `/clear` that is the last record of a pool leaves that session final
with no trailing credit rather than live. **Untested on real data**: the container corpus
holds zero `/clear` records (its only slash command was a skill invocation), and it is
possible that current Claude Code starts a new session id on `/clear` instead of writing
the marker, in which case the rule never fires. Fixture: `cleared_twice`.

### `switched_repo` — a human opened a new session in a different pool

If a native session id's *first* record anywhere is a presence signal, that instant is a
human session start in its pool. For every other pool, a start that falls at least
`Tuning.switchedRepoMinGapSec` (= `activeGapCapSec`, 120 s) after the pool's last record
and before its next ends the session there, credited as an idle end. Below 120 s the gap
is credited in full as continuous work anyway, and a person hopping between two repos
inside two minutes is one sitting on two repos. A `claude -p` run's first prompt is `sdk`
with no human origin and does not count (MEASURED: 7 of 7 headless runs in the container
corpus) — a robot starting elsewhere says nothing about where the person is. Fixtures:
`cross_pool/switched_repo_two_pools` and `cross_pool/headless_start_elsewhere_no_switch`
(two session ids, pooled per id).

Real count: one, and it is the rule's known limit — a sibling automated session started a
Codex run in another directory while the coordinator's sitting continued, and the Codex
loader (rightly, for Codex's own purposes) calls that first prompt a prompt. Within Claude
Code the `sdk`/`origin.kind` distinction is honoured; across harnesses a per-harness origin
field is needed before this rule is trusted between them.

## The v3 rules, in evaluation order per gap

| # | end reason | condition | credit / `ended_at` | announced | final on derivation |
|---|---|---|---|---|---|
| 0 | `cleared` | previous record is a `/clear` | as idle_gap | **yes** | yes |
| 1 | `idle_gap` | gap > tau (fitted or 900 s) | capped credit; last record + credit | yes | no (the clock finalizes it) |
| 2 | `switched_repo` | a human start in another pool ≥ 120 s after the last record and before the next | as idle_gap | **yes** | yes |
| 3 | `day_boundary` | 04:00 in the gap while autonomous | up to the boundary | no | yes |
| 4 | `human_returned` | presence after ≥ 2 h autonomy | as idle_gap, autonomous | no | yes |
| — | `still_running` | last session in the pool | none | — | this IS the live upload |

Notifications: `notify.NOTIFYING_END_REASONS = {idle_gap, cleared, switched_repo}` on the
server; on the Mac `DetectedSession.isStructuralEnd` sessions are final at once
(`isFinalOnDerivation`) and, unlike `isCut` ones, go through `pendingNotifications`. The
"Session finished" / "Agent run finished" split, the notable floor and the stale horizon
apply unchanged. Contract: `end_reason` gained the two values in
`privacy/upload-contract.json` (regenerated everywhere by `make gen`); Postgres migration
`0012_end_reasons_v3` widens the CHECK and, on downgrade, relabels v3 rows `idle_gap`
before restoring the four-value CHECK rather than deleting them. `sessionizer_version`
is 3.

## What stays, and what would move it

`tauAutonomousSec` (1800 s), `tauReturnSplitSec` (7200 s) and `dayBoundaryHour` (04:00)
are unchanged. `make measure-gaps` now prints the measurement each is waiting for:

* **1800 s** — the distribution of presence-to-presence intervals within continuous
  activity. If its upper hump sits well below 30 min the constant is too high.
* **7200 s** — the band histogram of intervals that crossed 30 min without an idle gap
  (30 m–1 h, 1–2 h, 2–4 h, ≥ 4 h). Returns inside afternoons populating 1–2 h say 2 h is
  right or low; an empty 1–2 h band and a populated ≥ 4 h one say it could come down.
  Here: one interval, 59 m 54 s.
* **04:00** — the local hour of every autonomous record and every idle-gap start. The
  boundary belongs where attended activity is rarest and autonomous runs are not
  systematically split. Here: one day of one person, not evidence.

## Things considered and not done, v3 additions

**Fitting tau on record gaps.** Measured; finds the machine (above). Reported by
`make measure-gaps` as "NOT what v3 uses" so the next person does not rediscover it.

**Compaction and `Stop` hooks as boundaries.** 1 and 35 in the real corpus, both mid-flow,
both harness events with nothing to say about the person.

**A commit followed by a long gap.** 43 commits; every long gap after one is already an
idle end. A strip mark, not a rule.

**Crop / split / merge after the fact.** Strava's second lesson. The right next step once
sessions are visible on the phone; nothing in v3 makes it harder.
