# The narrative: what the numbers mean about the person who made them

`spec/narrative.v1.json` defines the output. `analysis/patterns.py` and
`analysis/narrative.py` are the pipeline. This is the reasoning behind both, and the
numbers from running it on a real corpus.

There are now THREE readings on the profile and they must not be confused:

| | who writes it | scope | where |
|---|---|---|---|
| **session analysis** | a model, from a digest | one session | `spec/analysis.v1.json` |
| **builder profile** | nobody: it is arithmetic | the whole corpus | `analysis/profile.py` |
| **narrative** | a model, from the arithmetic | the whole corpus | `spec/narrative.v1.json` |

## Why it exists

The profile screen said `quality_guardian` and then a wall of numbers. A person reading
that has learned a label. They have not learned anything about themselves, and the label
without a sentence under it is a horoscope: it reads as insight and it is a lookup.

The narrative is the layer that says what the measurements MEAN for the person who made
them. It is deliberately the last step and the thinnest one. Everything the model is
allowed to say is already in the input as a measurement, and every claim carries the
number it rests on.

## Comparative findings, which are the interesting half

`profile.py` answers "how much, how fast, how often". `patterns.py` answers the only
question worth a paragraph on somebody's profile, which is **what would you change
tomorrow**.

Seven finders, each one comparison, each one with something on the end of it:

| id | says |
|---|---|
| `what_a_shipping_session_looks_like` | the sessions you opened with the most detail shipped a commit N of M times, the ones you opened shortest A of B |
| `the_spin` | the longest run with no file written, no test and no commit, and what those runs cost you in total |
| `stuck_in_a_loop` | consecutive failures before anything changed |
| `fighting_one_file` | the worst rewritten file, with the minutes it took |
| `when_the_work_lands` | lines an hour by time of day |
| `short_prompts_get_corrected` | your one-liners are the ones you take back, and each is a round trip paid for twice |
| `verification_habit` | you test after N% of your editing runs, and what that buys or costs |

### Three rules, and the first two are the ones a finding usually fails

**1. A FINDING NAMES A COST OR A MOVE.** "Your later prompts are shorter than your first"
is a true sentence with nothing on the other end of it. The same measurement becomes
useful the moment it is attached to whether the session shipped. If a comparison cannot be
finished with "so it cost you X" or "so do Y", it does not belong here. This is the rule
the first version of this module failed, and the page it produced was correct and useless.

**2. NO WORD THE READER WOULD HAVE TO LOOK UP.** Not "steer rate", not "front-loading",
not "autonomy score 0.361". Internal metric names live in `left`/`right`, where a screen
can show the working, and never in `text`. There is a test that fails if one leaks.

**3. BOTH SIDES BIG ENOUGH, GAP BIG ENOUGH, OR SAY NOTHING.** `MIN_GROUP = 5` a side and
`MIN_LIFT = 1.4` (or `MIN_SHARE_GAP = 0.15` points, because 2% against 1% is a 2x lift and
means nothing). Below either bar the finding is dropped, not softened.

### Four things found by running it

Each of these would have shipped a wrong number, or no number at all.

1. **A failure is the `result_error` event AFTER the call**, not a flag on the call.
   Walking both kinds and testing `ok` counts the call as a success and its own error as a
   separate failure, so a run never reaches two. The loop finder silently never fired on a
   corpus with 128 errors in it.
2. **"Nothing to show" cannot mean "no file written" alone.** An edit made by a script the
   agent wrote (`python3 - <<PY`) is a Bash call with no line count anywhere in it, so on
   this container 46 writes in 1,259 tool calls turned nearly every stretch into "nothing
   happened". A checkpoint is a write, a test OR a commit, and below one checkpoint per
   twenty calls the finding is REFUSED: the gaps would be measuring the parser's blind
   spots and reporting them as the person's wasted time.
3. **Splitting the opening prompts at their median** put every session on one side the
   moment the lengths were bimodal, which they are. It splits by rank now: this person's
   longer half against their shorter half. Requiring a follow-up prompt as well was worse:
   13 of 18 sessions have exactly one prompt.
4. **The token cost is a TOTAL and a share, never an average.** MEASURED: the sessions
   that shipped nothing averaged 36,214 output tokens each against 148,932 for the ones
   that did, so a sentence built on the averages would have said "your quiet sessions are
   the cheap ones" while the number a person cares about, a fifth of their spend producing
   nothing they kept, went unsaid. The average is the wrong statistic and the direction it
   points is worse than useless.

Token counts come from the ledger, never from summing `Ev.tok_out`, which reports 39,487
output tokens for 21 hours of real work. Absent is a refusal; zero would be a lie.

Everything here needs PROMPT TEXT, the tool results around it and the timings between, so
it runs on the machine that has the transcripts. The server never sees prompt wording
(`privacy/upload-contract.json`) and cannot compute a single one of these.

## The one rule the model is given, and the check that makes it real

The prompt's first two rules are EVERY CLAIM CARRIES ITS NUMBER and NEVER INVENT A NUMBER.
Rules in a prompt are a request. `verify` is the enforcement: it extracts every number
from every sentence the model wrote and deletes any sentence carrying one the input did
not contain.

This matters more here than anywhere else in the product. A fabricated number in a
paragraph about somebody's own habits is the most believable wrong thing the app can
print, because the reader has no way to check it and every reason to believe it.

### Two false positives, both found by running it

The check fired twice on CORRECT sentences before it was right, and each time it deleted a
true paragraph:

1. **"72% of your 1,211 tool calls"**, copied exactly from an input that read
   `total_tool_calls: 1211`. The extractor read `1` and `211`, could not find 211, and
   took the sentence. Thousands separators are now stripped from both sides.
2. **"44% of your commits"**, from an input that read `night_commit_share: 0.44`. A
   share's natural English is a percentage. Every known value in [0, 1] now also licenses
   its percentage, exact and rounded.

Both fixes carry their measurement in the code. The reason they are fixes rather than
accepted noise is the rule this repository already states about the privacy check: a
verification step that cries wolf on correct output is worse than no verification step,
because the first person to see it concludes the whole thing is false.

The second fix is a genuine widening and is labelled as one where it happens: allowing a
share's percentage lets a fabricated integer under 101 through. That is the right trade.
The claims worth catching are the ones with a magnitude nobody could check by eye (1,211
tool calls, 4,089 lines, a 484-call hands-off run), not the ones a reader could sanity
check in their head.

Every drop is logged with the claim that caused it. A count with nothing behind it cannot
tell you whether the ban is working or misfiring, which is how both of the above were
found.

## The order of the input is a stronger instruction than a rule in the prompt

FOUND BY READING THE OUTPUT. With the computed metrics at the top of the input, three of
four paragraphs were built out of them and read like this:

> Your prompts skew planning over execution 2.33 to 1. About 36.5% of your time is spent
> with it running on its own.

True, free of jargon after the ban, and still nothing a person can act on. The findings
are the only material in the input that is guaranteed to carry a cost or a move, so
`build_input` now leads with them under a heading that says to build the page out of them,
and demotes the metrics to a block labelled "support, never subject".

## Refusals travel WITH their reason

A metric the profile refused (`commits_total: null, overlapping_session_windows`) is
handed to the model as `REFUSED, <reason>` rather than dropped. A model shown nothing
concludes the question was never asked and guesses; a model shown the refusal can see it
was asked and answered "we cannot know". Rule 3 of the prompt then forbids mentioning its
absence as though it were a finding.

## Where it runs, and why the server does not write it

```
transcripts on your machine
  -> capture.sessionize_sources          the reference cut (v3 lineage pooling)
  -> analysis.profile.corpus_profile     arithmetic, every metric with its basis
  -> analysis.patterns.findings          comparisons, both bars enforced
  -> analysis.narrative.write            claude -p, then verify, then dedash
  -> PUT /v1/profile/narrative           validated against the same spec, stored
  -> the phone                           printed verbatim
```

Two commands do this and they must agree:

```bash
python -m analysis narrative --findings-only   # the findings, no model call, no cost
python -m capture  narrative --dry-run         # the whole page, printed, nothing sent
python -m capture  narrative                   # generated and uploaded
```

`--dry-run` exists because this document is prose about a person, written from their own
transcripts. Reading it before uploading it should not require a server.

Commit attribution is shared between the two commands (`profile.attribute_commits`, git
run by `capture.repo.commits_in`): a commit goes to the FIRST session whose window
contains it, because windows overlap and the per-session counts otherwise add up to more
commits than the repository has. Two commands that disagreed about how many commits a
person landed would be the same bug as one wrong number.

## Storage

`0016_builder_narrative`, one row per user, upserted. There is no history: a narrative
describes the corpus as it stands, and a stale one is not evidence of anything.

RLS ENABLE + FORCE, owner-only, with **no public policy at all**. A shared session is a
session; this is a statement about a person.

The PUT is on `current_uploader`, so a headless container's capture key can write it, for
the reason `0011` exists: the device flow's refresh token rotates and a fleet of
containers would revoke each other. It is the same trust class as the `analysis` prose a
key already uploads with every session. The GET stays on `current_device`, so the property
that matters is unchanged and tested with its controls: **a key can write the page and
still get a 401 reading it back.**

`invented_numbers_dropped` is lifted out of the body into its own column, so finding the
rows where the check had to fire is a WHERE clause rather than a scan of the prose.

## What the phone does with it

Nothing, on purpose. Every string is printed exactly as stored, because it was already
checked against the measurements it came from on the machine that wrote it; rewording it
would put an unverified sentence in front of a person.

The one thing the phone decides is what an EMPTY field means. `verify` blanks a field
rather than rejecting the document when it takes a claim back, so `""` is a normal state,
and a document whose every claim was taken back renders nothing at all rather than a
heading over blank space. Those decisions live in `mobile/src/profile/narrative.ts` so
`bun test` can run them.

The provenance line says the check exists and reports what it caught. Hiding a non-zero
count would make the guarantee sound like a promise rather than something that runs.
