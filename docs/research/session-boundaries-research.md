# What is a session? — research notes behind boundaries v3

The v2 rules cut sessions with four fixed clocks: a 900 s idle gap, 1800 s of quiet before
the agent counts as autonomous, 7200 s of autonomy before a returning human starts a new
session, and 04:00 as the day boundary. All four were chosen from one corpus's gap
percentiles. The user's objection — "I don't like the way you handle sessions with
hard-set times … a session is a vague way of defining it; find out a better way and
research; actually test on real inputs" — is the brief for this document. It has three
parts: what the literature says, what the transcripts themselves offer as boundaries, and
what the real inputs in this workspace measured. The design that follows from it is
`docs/session-boundaries.md`, section "v3".

Citations are given as author, year, venue and the specific finding relied on. Where a
detail (page number, exact figure) is recalled rather than checked against the paper, it
is marked **approximate citation**.

## 1. The literature

### 1.1 The 30-minute convention is an accident of one 1994 study

Catledge, L. D. and Pitkow, J. E. (1995), "Characterizing browsing strategies in the
World-Wide Web", *Computer Networks and ISDN Systems* 27(6), 1065–1073 (the Third
International WWW Conference). They instrumented XMosaic for 107 users at Georgia Tech
over three weeks and found a mean time between user-interface events of **9.3 minutes**;
they then proposed a session cut-off of the mean plus 1.5 standard deviations, which came
to **25.5 minutes**. Web analytics rounded that to 30 minutes, and Google Analytics,
Adobe and most log-analysis tools still ship it as the default. Nothing about a modern
browsing population — let alone an agentic coding tool — was ever measured to justify it;
it is one campus in 1994, one summary statistic, one rule of thumb for the multiplier.
(Approximate citation for the 1.5-sigma detail; the 25.5-minute figure is the one usually
quoted.)

Two later strands showed how much the convention costs. Spiliopoulou, M., Mobasher, B.,
Berendt, B. and Nakagawa, M. (2003), "A framework for the evaluation of session
reconstruction heuristics in web-usage analysis", *INFORMS Journal on Computing* 15(2),
compared time-oriented heuristics (a fixed total-session length, a fixed page-stay gap)
against navigation-oriented ones on a server log where the true sessions were known, and
found that no single timeout is right for every user and that the choice of heuristic
changes the mined patterns materially (approximate citation for the exact comparison
table). Jones, R. and Klinkner, K. L. (2008), "Beyond the session timeout: automatic
hierarchical segmentation of search topics in query logs", *CIKM 2008*, showed on
labelled search logs that **a timeout of any length is a poor predictor of a task
boundary** — the best fixed timeout they tried was barely better than chance at separating
tasks — and that content and structure signals (query reformulation, result overlap) do
much better. Their conclusion is the one this document borrows: the clock is one feature,
not the definition.

### 1.2 Inter-activity time is bimodal on a log scale, and the valley is the threshold

Halfaker, A., Keyes, O., Kluver, D., Thebault-Spieker, J., Nguyen, T., Shores, K.,
Uduwage, A. and Warncke-Wang, M. (2015), "User Session Identification Based on Strong
Regularities in Inter-activity Time", *WWW 2015* (Florence), pp. 410–418. They took the
inter-activity times of individual users across very different systems — Wikipedia edits,
Stack Exchange, MovieLens, OpenStreetMap, Cyclopath, the AOL search log, and others — and
plotted them on a log scale. Every system showed the same shape: **two humps**, a
within-session hump (seconds to minutes: the pace of a person doing one thing) and a
between-session hump (hours to days: sleeping, working, living), with a clear valley
between them. The valley, not any convention, is the natural session threshold, and it
sat close to one hour across their systems — which is why they recommend fitting the
valley per dataset and, failing that, using about an hour rather than 30 minutes
(approximate citation for the exact per-system valley values). They fitted the humps
with a **two-component Gaussian mixture on log inter-activity time** and reported that
the fit is stable across users and time within a system.

The method, as v3 implements it (`scripts/measure_boundaries.fit_tau`,
`Packages/BuilderKit/Sources/BuilderIngest/ThresholdFitter.swift`):

1. Take every inter-activity interval $g_i$ in seconds and work with $x_i = \log_{10} g_i$.
   A log-normal hump is a Gaussian hump on this axis.
2. Model the sample as $p(x) = w_1\,\mathcal N(x;\mu_1,\sigma_1) + w_2\,\mathcal N(x;\mu_2,\sigma_2)$
   with $w_1 + w_2 = 1$, and fit the five parameters by EM: the E-step assigns each $x_i$
   a responsibility $\gamma_i = w_1 \mathcal N_1(x_i) / p(x_i)$; the M-step sets
   $w_1 = \bar\gamma$, $\mu_1 = \sum \gamma_i x_i / \sum \gamma_i$,
   $\sigma_1^2 = \sum \gamma_i (x_i-\mu_1)^2 / \sum\gamma_i$ and the mirror for component 2.
   Initialised from the means of the lower and upper halves of the sorted sample with one
   shared variance, it converges in a few dozen iterations; the whole thing is forty lines
   and needs nothing outside the standard library.
3. **The valley** is the $x$ between $\mu_1$ and $\mu_2$ where the two weighted densities
   cross, $w_1\mathcal N_1(x) = w_2\mathcal N_2(x)$; found by bisection. Below it an
   interval is more likely a within-session pause, above it more likely a between-session
   one. The threshold is $\tau = 10^{x_\text{valley}}$ seconds.
4. **Bimodality is a precondition, not an output.** A mixture will happily fit two
   Gaussians to one hump. v3 requires the modes at least 0.8 decades apart, the minor
   component at least 5 % of the sample, a genuine dip in the mixture density at the
   crossing, and — the lesson of §3 — a valley inside the range that could possibly be a
   session boundary. Otherwise the fit is discarded and the fallback stands.

Two related methods deserve a mention. Mehrzadi, D. and Feitelson, D. G. (2012), "On
extracting session data from activity logs", *SYSTOR 2012*, fitted per-user thresholds to
the log-gap histogram of job-submission logs and showed that the per-user threshold varies
by more than an order of magnitude across users of one system (approximate citation) —
the argument for fitting per user rather than per product. Montgomery, A. L. and
Faloutsos, C. (2001), "Identifying Web browsing trends and patterns", *IEEE Computer*
34(7), observed the same heavy-tailed, log-scale structure in browsing inter-arrival times
a decade before Halfaker made it a session method.

### 1.3 Strava: moving time, auto-pause, and the athlete's right to fix it afterwards

Strava is the product this one is modelled on, and it made the same distinction v2 made,
for the same reason. An activity has an **elapsed time** (first sample to last) and a
**moving time** (elapsed minus the stretches the device or the app judged the athlete
to be stationary). Moving time is what leads on the activity page and what the segment
leaderboards use for most sports; elapsed is what a race clock would say. The judgment is
made by **auto-pause**, a speed threshold rather than a clock: on the Strava app a run
pauses when the phone stops moving and a ride pauses below a walking-pace speed, and the
recording resumes on its own when motion returns. That is exactly the shape of
`attended` versus `active` versus elapsed here — a threshold on *evidence of the person*,
not on the calendar — with the important difference that a coding agent keeps moving
while the person is away, which is why v2 needed a second clock (autonomous) and v3 keeps
it.

The second thing Strava gets right is the **override**. The athlete can **crop** an
activity (trim the beginning or end — the drive home recorded by mistake), and Strava
supports **splitting** one recording into several activities on the same account; merging
two recordings into one activity is done through export and a third-party tool rather
than natively (approximate — the exact feature set moves between app versions). The
principle survives every version: the automatic boundary is a good default that the
athlete can correct after the fact, per activity, without changing the rule for everyone.
Builder's v3 carries the first half of that principle (a per-person fitted default) and
defers the second: a crop/split/merge affordance is the natural next step once the phone
shows sessions, and nothing in v3 makes it harder.

## 2. Boundaries the transcript offers without a clock

The v2 design already reads presence from record shapes rather than heuristics. The
question here is which other record shapes mark a boundary. Each is classified as a
**hard boundary** (the sitting ended, whatever the clock says), a **soft signal** (moves
the odds; combined with a gap it may end a session), or **noise** (says nothing about the
person), with its count in the real corpus described in §3 — 13 root transcripts, 2,506
timestamped records, one human sitting of about 13 hours wall time with 17 remote-human
prompts, plus seven headless `claude -p` runs.

| signal | record shape | class | count in the real corpus | reasoning |
|---|---|---|---|---|
| typed `/clear` | `type: user`, no `promptSource`, text contains `<command-name>/clear</command-name>` | **hard** | **0** | The human deliberately ended the conversation. Nobody clears from the other room, so it is also presence. Implemented as `cleared`; **untested on real data** — the container corpus has no `/clear` at all — and pinned by the `cleared_twice` fixture. It is possible that current Claude Code starts a new session id on `/clear` rather than writing the marker, in which case the rule simply never fires. |
| new session id in a **different** repo, opened by a human | first timestamped record of a session id is a presence signal, its pool differs | **soft → hard after 120 s** | 1 cross-pool human start (a sibling agent's Codex run, see below); the 7 `claude -p` starts in another directory are all non-human `sdk` prompts and do not count | If the person opened a fresh conversation somewhere else at least `activeGapCapSec` after this pool's last record, their attention moved. Below 120 s the gap is credited as continuous work anyway, and a person hopping between two repos inside two minutes is one sitting on two repos. Implemented as `switched_repo`. |
| new session id in the **same** repo | first record of a session id, same pool | **noise** | 7 (the `claude -p` runs, all in the builder directory) | Two conversations back to back in one repo are one sitting; v1's rule, unchanged. Splitting on it halves the length of every session on the card. |
| `cwd` change inside a conversation | `.cwd` differs from the previous record's | **noise** — and a trap | **332** runs of alternating cwd in one 2,231-record session; median gap at a change 0.42 s | The shell `cd`'d between `/home/user` and the repo; all 15 human prompts were stamped with the home directory and 833 assistant records with the repository. Pooled per record, this one sitting became two overlapping sessions — one with the prompts and no commits, one with the commits and "0 prompts typed". Not a boundary; a reason to pool by lineage (v3 §"pooling"). |
| compaction | `type: system`, `subtype: compact_boundary` | **noise** | 1 (auto-triggered, `preTokens` 784,796 → 10,743) | Fired by the harness on context size, mid-flow, while the person was working. Says nothing about them. Already a zero-duration marker in v2. |
| `Stop` hook fired | `type: system`, `subtype: stop_hook_summary` | **noise** | **35**, one per assistant turn | It marks the end of a *turn*, not of a sitting; there are dozens per session. A `SessionEnd` hook would be a hard boundary — the process exited — but it is written nowhere in the transcript; `capture/` reads it as a process event (`--finalize`), which is the right place. |
| `git commit` followed by a long gap | Bash `tool_use` whose command contains `git commit`, then silence | **soft** | 43 commits; the longest silence after one was the between-sitting gap already cut by rule 1 | A commit is a natural pause point, and a person who commits and then goes quiet has probably stopped — but rule 1 already ends that session at the same place, so the commit adds nothing the gap does not. It is worth showing on the card (it already is a strip mark), not worth a rule. |
| interrupt, human file edit, typed or remote-human prompt | as v2 | presence, not boundaries | 17 human prompts, 1 interrupt in the coordinator's session | Unchanged. |
| `end_turn` stop reason | assistant record | **noise** | hundreds | Considered and rejected in v2; still true. |

One limitation surfaced by the measurement and worth stating plainly: the rule cannot
tell a *human* opening a session in another repo from an *agent* doing so on a human's
behalf when the agent's harness stamps its prompt as human. The one `switched_repo` in
the real corpus was a sibling automated session starting a Codex run at 17:39 while the
coordinator's sitting continued. The Codex loader marks its first prompt as a prompt,
because for Codex's own transcripts that is what it is. Cross-harness presence deserves a
per-harness origin field before `switched_repo` is trusted across harnesses; within
Claude Code the `sdk`/`origin.kind` distinction already exists and is honoured.

## 3. What the real inputs said

`scripts/measure_gap_distribution.py` (run as `make measure-gaps`) does the measurement.
Two corpora were available in this container, and neither is the user's Mac corpus (84
sessions at tau 900), which the script is written to run on:

* **REAL — this container's `~/.claude/projects`.** 13 root transcripts, 2,506
  timestamped records after lineage folding into 3 pools, 2,260 positive record gaps
  (243 zero-length gaps excluded — records of one turn share a timestamp). One human
  sitting (the coordinator's, 17 remote-human prompts over ~13 h wall) plus seven
  headless `claude -p` runs and the automated sessions those spawned. Small, and almost
  entirely one sitting: evidence about *record cadence*, barely any about *sittings*.
* **REAL — other harnesses** left under the scratchpad `real-sessions/` tree by a
  sibling session (checked at the start and again before writing this): one Codex rollout
  (10 lines, 1 event), one opencode database (2 events) and one Aider history (2 events)
  from tool runs that mostly failed on network policy; Gemini and Cline left nothing. Too
  little to fit anything, reported as such.
* **SYNTHETIC — `spec/fixtures/boundaries/*.jsonl`.** The generator's transcripts, run
  through the same script and labelled SYNTHETIC in its output. They exercise the rules
  and say nothing about people.

The measurement that changed the design:

**The naive Halfaker fit on record gaps finds the machine, not the person.** On the real
corpus the two-Gaussian fit over the 2,260 record gaps is confidently bimodal — modes at
$10^{-2.12}$ s ≈ 7 ms (w 0.43) and $10^{0.53}$ s ≈ 3.4 s (w 0.57), 2.6 decades apart, a
clean valley at $10^{-0.96}$ ≈ 0.1 s. Those are the harness flushing several records of
one turn within milliseconds, and the agent's tool-call cadence. The between-sitting
gaps — the ones a session threshold is *about* — are 20 of 2,260 (0.9 %) and cannot form
a component. Clamping the 0.1 s valley to the 300 s floor, as the brief's
`clamp(valley, 300, 3600)` would, produces a confident, wrong tau of 300 s: the failure
CLAUDE.md warns about. On the Mac corpus the same shape is already documented in
`Tuning.swift` ("a dense sub-minute mass, then a thin tail"), with 2.07 % of gaps over
60 s: a record-gap fit there would find the same machine valley.

The reason is the unit. Halfaker's events were *human acts* — an edit, a query — so the
between-session interval was a large fraction of the sample. A Claude Code transcript
writes thousands of machine records per human act. So v3 fits on **presence-to-presence
intervals** (typed or remote-human prompt, interrupt, human file edit, `/clear`), taken
across idle gaps so that the between-sitting mode is in the sample, and applies the
resulting tau to record gaps. That is coherent: a record gap is never longer than the
human interval containing it, so a record gap past the human's within-sitting valley is
at least as strong a "the sitting ended" signal. It also adds two safety checks the brief
did not have: a valley must fall within half a decade of the clamp range or the fit is
rejected outright (never clamped), and the mixture must actually dip at the crossing.

**On the real corpus the honest answer is the fallback.** 23 presence intervals — far
below the 200 the fit requires — so tau stays 900 s and the script says so. The other
numbers it prints for the record: presence-to-presence during activity p50 2 m 14 s, p90
10 m 53 s, max 59 m 54 s, one interval that crossed 30 min without an idle gap (the case
`tauReturnSplitSec` exists for) and none over 2 h; autonomous records by local hour peak
at 12:00 and 17:00 UTC, none near 04:00 in any zone that matters here; sessions at 900 s:
10, of which 7 idle_gap and 3 still running; cross-pool with the other harnesses'
transcripts included, one `switched_repo`.

**On the synthetic bimodal fixture the fit does what Halfaker describes.** 239 presence
intervals, modes at $10^{2.09}$ ≈ 122 s (w 0.90) and $10^{3.51}$ ≈ 54 min (w 0.10),
1.4 decades apart, valley at 569 s; 25 sessions at the fitted tau against 24 at 900 s —
the extra cut is the 700 s mid-sitting silence the fixture plants between the two. The
bare-gap fixture `threshold_fit.json` (360 gaps, modes 1.99 and 3.79 decades, valley
2.985, tau 966 s) is what holds the Swift EM to the Python one at 1e-6.

**What would justify changing the other three constants** — stated as measurements the
script now prints, so the next person with a real corpus can read them off:

* `tauAutonomousSec` (1800 s): the distribution of presence-to-presence intervals *within*
  continuous activity. If its upper mode (the "went to lunch" hump) sits well below
  30 min, the constant is too high and autonomous time is under-counted; here the max was
  59 m 54 s on n = 23, which is too little to say.
* `tauReturnSplitSec` (7200 s): the band histogram the script prints for intervals that
  crossed `tauAutonomousSec` without an idle gap — 30 m–1 h, 1–2 h, 2–4 h, ≥ 4 h. If
  the 1–2 h band is populated by returns *inside* afternoons, 2 h is right or low; if it
  is empty and the ≥ 4 h band is not, the line could move down. Here: one interval, in
  the 30 m–1 h band.
* `dayBoundaryHour` (04:00): the local hour of every autonomous record and of every idle
  gap's start. The right boundary is the hour at which *attended* activity is rarest and
  autonomous activity is not systematically split — if idle gaps cluster at 02:00 and
  autonomous records run through 04:00, 04:00 is doing its job; if the person's own
  sittings routinely straddle 04:00, it is not. Here the histogram is one day of one
  person in UTC and says nothing yet.

## 4. Conclusion, and the proposal

A session for a builder is **a sitting**: one human's continuous attention, on one or
more repositories, as evidenced by the records their tools write — not a fixed number of
quiet minutes, and not a partition of the log by whatever directory the shell happened to
be in. The literature says the quiet-minutes threshold should be read off the person's own
inter-activity distribution (Halfaker et al. 2015) and that the 30-minute convention is a
1994 accident (Catledge & Pitkow 1995); the real inputs say the reading must be taken on
the human's acts, not the machine's records, or it measures the harness. So v3 (a) pools
by the transcript's lineage and treats the repository as an attribute of the session, (b)
fits the idle threshold per person on presence intervals, clamped to [300 s, 3600 s], with
900 s as the fallback until there are 200 intervals and a genuinely bimodal fit, (c) makes
two things the human does on purpose — `/clear`, and opening a new conversation in another
repo after two quiet minutes — first-class ends that are announced like silence, and (d)
leaves the autonomy, return-split and day-boundary constants where they are, with the
measurements that would move them now printed by `make measure-gaps`. Strava's remaining
lesson — let the person crop, split or merge a session afterwards — is the next step, not
this one.
