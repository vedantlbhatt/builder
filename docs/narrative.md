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

`profile.py` answers "how much, how fast, how often". `patterns.py` answers the question a
person actually asks, which is **when is it different?** Every finding compares two groups
of that person's own prompts or sessions and reports the gap, so the sentence carries its
own evidence.

Seven finders, each one comparison:

| id | compares |
|---|---|
| `short_prompts_get_corrected` | prompts under 10 words against longer ones, by whether the next human act was a correction |
| `the_opening_prompt` | the first prompt of a sitting against every prompt after it |
| `after_an_interrupt` | the prompt straight after an interrupt against every other prompt |
| `the_leash` | the longest hands-off run against the median one, in tool calls |
| `night_sessions` | sittings started after 22:00 against sittings started in daylight |
| `verification_habit` | edit bursts that ended in a test against ones that did not |
| `rework` | files written three or more times in one sitting against files written once or twice |

**Both bars, or nothing is said.** Both sides need `MIN_GROUP = 5` observations, and the
gap needs `MIN_LIFT = 1.4` (or `MIN_SHARE_GAP = 0.15` points, because 2% against 1% is a
2x lift and means nothing). Below either bar the finding is dropped, not softened. A
sentence that says "you slightly prefer" about three prompts against four reads as insight
and is noise, which is the exact failure this codebase is written to avoid.

MEASURED on a 17-session corpus: four of the seven cleared both bars. The other three were
refused for sample size, and that is the system working.

Everything here needs PROMPT TEXT, so it runs on the machine that has the transcripts. The
server never sees prompt wording (`privacy/upload-contract.json`) and therefore cannot
compute a single one of these.

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
