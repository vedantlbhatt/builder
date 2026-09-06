# The analysis, completely: what gets measured, what gets refused, and why

Two documents about one person, and confusing them is how this got useless twice.

| | **1. The session** | **2. The builder profile** |
|---|---|---|
| scope | one sitting | the whole corpus, over time |
| answers | what happened just now | how you build, and whether it is changing |
| written by | a model, from a digest | arithmetic, then a model for the prose |
| shape | an overlay you read once | a page you come back to |
| lives in | `spec/analysis.v1.json` | `analysis/profile.py`, `analysis/trends.py`, `spec/report.v1.json` |
| feedback | yes, about THIS sitting | yes, about the shape of your months |

Everything below is measured on this container's own corpus unless it says otherwise.

---

## 0. The rule every number here obeys

**A refused number beats a wrong one.** Every metric carries `value`, `unit`, `n`, `basis`
and `reason`. `value: null` with a reason means the data could not support it, and null is
never rendered as zero: the phone drops the row rather than claim nothing happened.

Nine things this codebase has already had to refuse, each one found by running it:

| refused | because |
|---|---|
| corpus commit totals | two sessions in one repo both counted the overlap: 98 against git's 75 |
| per-session token sums | one JSONL record per content block, identical usage on each: 1.878x |
| `Ev.tok_out` as a token count | reports 39,487 output tokens for 21 real hours |
| a model with no published price | a total missing an unknown amount reads as complete |
| the spin finding on a blind corpus | 46 detectable writes in 1,259 calls measures the parser |
| a trend from zero | dividing by zero, or calling it an infinite rise |
| a trend on a metric either window refused | `None - None` is not "flat" |
| an archetype from one session | `docs/analysis.md`: never from one run |
| commits credited to a model | 3 models summed to 134 where git counted 85 |

---

## 1. The session analysis

One sitting, read once, closed. It answers *what just happened and how did I work*.

### What is measured, no model involved

- **two clocks**: `attended` (within `tauAutonomousSec` of a presence signal) and
  `autonomous`. **Attended time decides records**: the longest session in the corpus was
  5h40m with zero typed prompts and was about to become a personal best.
- **the strip**: 1024 columns, `idle` / `prompting` / `agent` / `human_edit`, the shape of
  the sitting at a glance.
- prompts you typed, interrupts, tool calls by name, lines the agent added (shell writes
  included: **2,452 of 2,458** attributable lines came through the shell here), commits in
  the window plus a 30 minute lookback, tokens by model, and cost at list prices.

### What a model adds

`headline`, `summary`, up to three `highlights`, `outcome`, `build_style`, five
`dimensions` with a rationale each, `decision_patterns` grounded in a verbatim prompt
excerpt, `prompting`, `growth_edge`, `tags`, `confidence`.

### The feedback half, which is new

Four measured findings, each of which names a cost rather than a fact:

| finding | on this corpus |
|---|---|
| `the_spin` | 12 runs of 25+ calls with no write, test or commit, **2h 11m** total, worst 59 calls |
| `stuck_in_a_loop` | 2 runs of 4+ consecutive failures, worst **7 in a row** |
| `fighting_one_file` | the worst file, with the minutes it consumed |
| `what_the_quiet_sessions_cost` | sittings that shipped nothing, **in dollars** |

That last one is the correction that matters most: in **tokens** the sittings that shipped
nothing were 23% and read as waste worth chasing; in **dollars** they are 1%, because the
quiet ones were cheap Sonnet sittings and every expensive Opus one shipped. Same data,
opposite conclusion.

### Several agents at once

`analysis/agents.py` reads the subagent sidecars that every other tool skips, and **never**
contributes a token, a line or a commit to any total. Measured here:

```
52 agents in one sitting, up to 8 running at the same moment
712 agent-minutes inside a 1,159 minute stretch
51 of 53 delegations produced something
biggest: "Builder profile metrics engine", 46 minutes, 106 things landed
```

**Consecutive and concurrent are different questions.** A chain of handoffs is a sequence
of decisions. Overlapping agents are what turn one hour of wall clock into four hours of
work, and `parallelism` (agent-seconds over wall seconds) is the honest statement of it.
Two rules that fail silently: sidecar discovery is an **allowlist on path shape** (50 of
165 jsonl files here are in sibling `workflows/` and `tool-results/` directories a denylist
would wave through), and a handoff at the same instant is **not** two agents at once, or
every consecutive chain reads as parallel.

---

## 2. The builder profile

The corpus, over time. It answers *how do I build, and is it changing*.

### The computed half

`planning_ratio`, `steer_rate`, `autonomy_score`, `code_velocity`, `test_runs_per_hour`,
`ships_rate`, `tool_diversity`, `iteration_depth`, `night_share`, `peak_hour`,
`short_prompt_share`, `default_model`, `shipping_day`, `session_rank`, `spend_usd`,
`spend_per_hour_usd`, `spend_without_a_commit_usd`, plus a `model_costs` table.

### The money, which is the only unit strangers share

**Not a bill.** Most people are on a subscription. This is what the tokens would cost on
the API at list prices, and the label travels with the number everywhere.

```
$306 of API usage over 21 hours, $41 an hour
Sonnet 5   $6.04    12 commits over 7 sittings it wrote    $0.50 a commit
Fable 5.1  $143.14  59 commits over 4 sittings it wrote    $1.61 a commit
Opus 5     $157.22  13 commits over 1 sitting it wrote    $10.09 a commit
```

Commits are credited only to a model that wrote **80%+** of a sitting's output. Crediting
them to every model in the session summed those three to 134 where git counted 85.

### Trends: you against you

There is no cohort, and "top decile of what" is a question a percentile cannot answer
honestly here. Two **equal** windows back to back, never a window against all of history.

```
up   152%  lines an hour                     527 -> 1329   the way you want it
up    70%  cost an hour                     28.5 -> 48.4   worth a look
down  62%  one line prompts                 0.50 -> 0.19   the way you want it
steady 5%  how often you test                4.6 -> 4.8
```

Four bars: both windows need 4 sessions; a move under 15% is **steady**, not an arrow; a
metric either window refused has no trend; and **direction is not virtue** — six metrics
carry which way is good and everything else gets a change with no verdict, because more
hours is not better and a night owl is not broken.

### The things you can work on

- **`rules`** — failures that recurred across **different sittings**. One sitting is
  debugging; three is something nobody wrote down. Here: `digest_hash` rejected by the same
  regex in **4 sittings, 20 times, over 21 hours**. One CLAUDE.md line kills all of it.
- **`playbook`** — your own prompts in two piles: landed cleanly vs cost a round trip.
  **22.6% clean** here, 7 of 31. A long run that lands is a good prompt; a long run with
  nothing landing is a stall. Getting that backwards filed the best prompt in the corpus
  (44 things landed over 484 calls) under "cost a round trip".

### The prose

`analysis/narrative.py` turns the measurements into paragraphs. Every claim carries its
number, `verify` deletes any sentence citing one the input did not contain, and the
jargon is banned by name with a replacement for each. Two false positives fixed by
running it: a thousands separator (`1,211` read as `1` and `211`) and a share written as a
percentage (`0.44` said as `44%`). Both deleted **correct** sentences.

---

## 3. How it reaches the phone

Everything above was, for a while, a set of commands that printed to a terminal. A
measurement nobody sees is a measurement that did not happen, so there is now one document
that carries all of it: `spec/report.v1.json`.

```
python -m capture report  ->  PUT /v1/profile/report  ->  GET /v1/profile/builder
```

**The server computes none of it and cannot.** Each block rests on something the upload
contract does not put on the wire:

| block | needs | which is |
|---|---|---|
| `trends` | the metrics over two windows | recomputable, kept here so one document describes one moment |
| `agents` | the subagent SIDECAR transcripts | files on your machine, uploaded by nothing |
| `quality` | shell command TEXT | a `pytest` from a `git status`; the server has a count of Bash calls |
| `prompting` | prompt TEXT | never leaves; only the two counts do |
| `contributions` | commit TIMES against session windows | the server has a count per session, no timestamps |

**What is deliberately NOT in it.** `analysis/rules.py` writes CLAUDE.md lines quoting
error text, which carries paths and file names, so it stays on the machine and writes to a
file there. The playbook holds prompt text on purpose — that is what it prints locally —
and `report._prompting` is the only function that reads attempts and is also uploaded. It
reads five numbers and no words.

**Every string in the document comes from a fixed table in this repository**: a metric key,
a label from `trends.LABEL`, a subagent type the harness named, an ISO date, or a refusal
reason a module wrote. A test asserts that set, so adding a field that carries an excerpt
means arguing for it there first.

The three documents under `GET /v1/profile/builder` are different kinds of thing and the
route says so: `builder_profile` aggregates the model-written session analyses,
`corpus` is computed server-side from stored sessions, and `report` is measured on the
machine. A block that is null does not render. A refused rate renders its reason.

---

## Where each thing runs

```
transcripts on your machine
  -> capture.sessionize_sources        the reference cut, v3 lineage pooling
  -> analysis.profile / patterns       arithmetic, every metric with its basis
  -> analysis.agents                   the sidecars, never counted
  -> analysis.trends                   this window against the last
  -> analysis.quality / playbook       time to green, and prompts that landed clean
  -> analysis.report                   one document, all of it, numbers only
  -> analysis.narrative / shipped      claude -p, then verify, then dedash
  -> the server                        validated against the same spec, stored
  -> the phone                         printed verbatim
```

Prompt text, commit messages, file names and error output **never leave the machine**
(`privacy/upload-contract.json`). Everything that needs them runs where they are.

```bash
python -m analysis profile        the computed half
python -m analysis trends         you against you
python -m analysis agents         who ran, at once or in a chain
python -m analysis rules --list   what keeps going wrong
python -m analysis playbook       your prompts, both piles
python -m analysis cards          what is postable
python -m analysis shipped        a build post
python -m analysis narrative      the prose
python -m analysis report         all of the above as one document, exactly as uploaded
python -m capture report          measure it and put it on the profile
```
