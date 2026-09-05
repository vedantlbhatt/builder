# Cloud capture: getting web and phone sessions onto the phone

Sessions started from claude.ai/code — in the browser or from the Claude app — run in a
cloud container. Claude Code writes their transcript there, under the container's
`~/.claude/projects`, in exactly the JSONL the Mac agent parses; the file simply never
reaches a machine the agent runs on. `docs/integrations.md` has carried that row as
"capture path missing" since the remote prompt shape was first measured.

`capture/` closes it from the other side. It is a Python package that runs where the
transcript is — the container, or any dev box — and speaks to the server exactly as the
Mac does: the same contract v2 payload, the same endpoints, and either a capture key or
the same device-pairing flow. Standard library only, Python 3.11+, because a fresh
container has nothing installed.

```
BUILDER_CAPTURE_KEY=bck_… python -m capture sync --live           # a cloud container: no pairing
python -m capture pair --server https://api.builder.example      # a machine you keep: once per device
python -m capture sync --dry-run                                  # print what would be sent; send nothing
```

## What it shares with the engine, and what it does not

Capture does not carry a second copy of any rule. It imports the Python reference
implementations the Swift engine is itself held to:

| concern | where the rule lives | how capture uses it |
|---|---|---|
| session boundaries — idle gap, two clocks, `human_returned`, `day_boundary`, presence signals | `scripts/measure_boundaries.py` (the fixtures in `spec/fixtures/boundaries` are generated from it) | imported; `sessionize` is called unchanged |
| tool calls, edit-tool line deltas, human edits, compactions | `analysis/digest.py` | imported (`load_claude_code_events`) |
| the strip's ordinals, weights, density thresholds | `spec/strip.v1.json` | read at import; `capture/strip.py` is a port of `StripBuilder.swift` that hand-types no integer |
| what may leave the machine | `privacy/upload-contract.json` | every payload key is checked against it, nested fields included, by `capture/tests/test_contract.py` |
| root-transcript allowlist, message-id token dedupe, repo hashing, `counted`/`notable`/`unattended` | `Packages/BuilderKit` | mirrored, each with its source named in the module docstring |

`make capture-test` runs the parity test over every boundary fixture: attended, autonomous,
presence, prompts, end reason and `unattended` must equal the fixture's expected values,
which are the numbers the Mac produces.

Where the two clients knowingly differ:

- **`client_session_id`.** The engine's id is
  `sha256("builder-session-v1|claude_code|<Mac hardware UUID hash>|<first event uid>")`.
  A container cannot know the Mac's hardware UUID, so the same transcript synced from both
  places cannot share an id under the engine's own rule. Capture keeps the exact chain
  (`source_id` from the path relative to the projects root, `event_uid` from the first
  record's uuid — both byte-identical to the engine's) and fills the machine slot with the
  literal `capture`. The id is then a pure function of the transcript: a remote session
  resumed in a fresh container keeps its identity, and every hook re-run upserts. A
  per-container id would have given one sitting a new identity every time the container
  was rebuilt. `capture/identity.py` states this in full.
- **Every branch counts as live.** The engine drops records off the surviving DAG branch
  from lines, tool counts and the strip, and reports their tokens as
  `abandoned_branch_tokens`. Capture reports 0 there and counts everything. MEASURED on a
  remote transcript (harness 2.1.261) that was never rewound: 51 fork points, 40 of them a
  tool result and the next assistant record both parented to the same `tool_use` record,
  11 a `system`/assistant pair. A single-chain walk from the newest leaf classed 35 of 599
  assistant records, 14 tool calls and 18 of 216 authoritative usage records (8%) as
  abandoned work. Subtracting real work from ordinary sessions to catch rare rewinds is the
  larger lie; the field is labelled accordingly.
- **Anonymous mode only.** `repo_name`, `title` and `title_source` are public-repo fields
  and which repositories are public is a Mac-side setting. Capture sends `repo_hash` (the
  same HMAC under the same non-secret pepper) so cloud sessions fold into the right project
  arc, and never a name. Likewise the Mac's `excluded` list is invisible here:
  `BUILDER_CAPTURE_EXCLUDE=github.com/acme/secret,github.com/acme/other` is the only
  exclusion capture honours, and a session in an excluded repository produces no upload.
- **`files_created` is 0**, as the Mac uploads it today. **Line deltas count edit tools
  only** (`Edit`'s `structuredPatch`, `Write`'s content), as the engine does; shell heredoc
  writes are not counted. **Density** counts one event per timestamped record where the
  engine counts one per normalized event — the class channel, which the server checks
  against `active_seconds`, is computed identically.
- **The day boundary needs a time zone.** A container is UTC. Set `BUILDER_TZ` (or pass
  `--tz America/New_York`) to your home zone, or an overnight robot's hours are split at
  04:00 UTC and land on the wrong day. `tz_offset_minutes` is stamped from the same zone.

## The open session

The last session in a pool is `still_running`. Capture follows the Mac:

- younger than `tauSessionSec` (900 s): it is **live**, uploaded only with `--live`, as
  `state: live`, `end_reason: still_running`, `ended_at` at the last record with no
  trailing credit, at most once per `liveUploadMinIntervalSec` (60 s) per session and only
  when its content hash changed. The server replaces it in place when the final arrives.
- older: nobody was there to observe the idle gap, so it is finalized the way the reference
  finalizes an idle-gap cut — the boundary gap credited, capped at `activeGapCapSec`
  (120 s), to whichever clock was running at the last record, and `ended_at` extended by
  the same amount so active can never exceed elapsed.
- `--finalize` applies the second rule to a young session too. It exists for `SessionEnd`:
  the container will not be there in fifteen minutes to see the gap, and a live row that
  is never finalized would say "Live" on the phone forever.

## Credentials: a capture key, or pairing

There are two ways for capture to prove whose sessions it is uploading. They differ in
exactly one property — whether the credential rotates — and that property decides which
one a cloud container can use.

### A capture key

A capture key is a `bck_…` string minted on the phone (Settings → Cloud capture → New
key). The server stores its sha256 and returns the plaintext once; the phone shows it
once. Set it as `BUILDER_CAPTURE_KEY` (or pass `--key`) and `sync` sends it as the bearer
on every request, reads no credentials file, and never calls `/v1/auth/refresh`. Because it
does not rotate, any number of containers may hold the same one — which is the whole point,
and the reason it exists (`server/alembic/versions/0011_capture_keys.py`).

The server accepts a key on `POST /v1/sync/sessions:batch` and `GET /v1/sync/known` and on
NOTHING else — not the session list, not the profile, not the feed, not the key routes
themselves (`server/tests/test_capture_keys.py` presents a live key to every other route
family and checks the 401 against a device token that gets through). Unknown and revoked
keys receive the same 401. The phone's list shows each key's name, prefix and when it last
uploaded (touched at most once a minute); Revoke sets `revoked_at` and revokes the device
row the key uploads as. A user may hold ten live keys; one per cloud environment is the
expected number.

**Threat model, in three sentences.** A leaked key lets its holder upload sessions into
your account and read back the content hashes it already knows — it cannot read a session,
a profile or a post, cannot mint or revoke keys, and cannot pair a device, so the worst
outcome is fabricated rows under your name. You revoke it in Settings → Cloud capture, and
the next request from anything holding it is a 401 (capture prints one line naming the
key's prefix and exits 5, and never retries). Nothing about a key's use rotates or extends
it, so a copy that sat unused for a year is exactly as dead as the one you revoked.

### Pairing

`pair` runs the RFC 8628 device flow against `/v1/auth/device/start` and `/device/poll`
with the same body the Mac sends. It prints the user code, the verification URL and the
`builder://pair?code=…` deep link, polls at the server's interval until the phone approves
(or the 15-minute grant expires), and writes `~/.builder/credentials.json` with mode 0600:
server, `machine_id`, label, access token, refresh token.

`machine_id` is `sha256("builder-machine-v1|" + raw)` where `raw` is `BUILDER_MACHINE_ID`
if set, else `/etc/machine-id`, else a random UUID pinned by the credentials file. Set
`BUILDER_MACHINE_ID` on a box you re-pair so it stays one device on the server (`devices`
is unique on `(user, machine_id)`; re-pairing an existing id un-revokes the row instead of
adding one).

**Refresh tokens rotate, and a spent one presented again revokes the device.** Access
tokens live fifteen minutes; on the first 401 capture refreshes once, writes the rotated
pair to disk atomically before retrying, and never refreshes twice in one call
(`capture/tests/test_client.py` proves both against a real HTTP server). The consequence
for the cloud is not optional: a static copy of `credentials.json` handed to several
containers works exactly once — the second container presents a token the first already
spent, the server treats that as a leak and revokes the whole chain, and the user is back
to pairing. `BUILDER_CREDENTIALS_JSON` (the file's contents inline) exists for a single
long-lived box whose disk is not persistent, not for a fleet. For a fleet, use a key.
Pairing remains the right credential for a machine you keep: it can be revoked per device
and its tokens expire on their own.

When both are present, the key wins and the credentials file is not read; `sync`'s summary
line ends with `auth: capture key bck_xxxx… (no pairing needed)` so a hook log says which
path ran.

## Hook recipes

### A dev box or a persistent machine

Pair once, then either a cron line:

```
*/5 * * * *  cd ~/src/builder && BUILDER_TZ=America/New_York python3 -m capture sync --live --quiet
```

or Claude Code hooks in `~/.claude/settings.json`, which run the moment something happens
rather than five minutes later:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "timeout": 60,
          "command": "cd ~/src/builder && python3 -m capture sync --live --quiet" } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "timeout": 60,
          "command": "cd ~/src/builder && python3 -m capture sync --finalize --quiet" } ] }
    ]
  }
}
```

`Stop` fires after every assistant turn, so the phone sees a live row while you work;
`SessionEnd` finalizes it. `--quiet` prints only rejections and errors, which land in the
hook's stderr.

### A Claude Code cloud container

Two things: a key in the environment, and hooks in the repository.

**In the cloud environment's settings** (claude.ai/code → the environment → variables),
four plain variables: `BUILDER_CAPTURE_KEY` (from Settings → Cloud capture; name the key
after the environment), `BUILDER_API_URL`, `BUILDER_TZ`, and — optional, cosmetic —
`BUILDER_MACHINE_ID`, any stable string, so the `machine_id` field in the payload does not
change with the container. Variables set there are secrets to the extent the platform
makes them so; a key that leaks is revoked in the same screen that minted it.

**In the repository's own `.claude/settings.json`**, because that is the only
configuration a cloud container inherits, three hooks:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "timeout": 120,
          "command": "test -d ~/.builder/src || git clone --depth 1 https://github.com/vedantlbhatt/builder ~/.builder/src" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "timeout": 60,
          "command": "cd ~/.builder/src && python3 -m capture sync --live --quiet" } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "timeout": 60,
          "command": "cd ~/.builder/src && python3 -m capture sync --finalize --quiet" } ] }
    ]
  }
}
```

How a session then lands on the phone:

1. `SessionStart` clones the builder checkout — capture imports the reference
   implementations from it. Nothing else: there is no code to approve and nothing to
   wait for.
2. `Stop` runs `sync --live` with the key. Every turn uploads a live snapshot,
   rate-limited to one per 60 s per session, so the phone shows a live row while the
   container works.
3. `SessionEnd` runs `sync --finalize`: the open session becomes `final` /
   `idle_gap` with the gap so far credited, and the phone's live row is replaced.

A revoked key does not break the session: the hook prints one line to its stderr
(`capture key bck_xxxx… was rejected …`) and exits 5, and Claude Code carries on. Paste a
new key into the environment and the next `Stop` uploads everything the root allowlist
finds, including the turns that ran while the old key was dead.

#### The alternative: pairing each container

Without a key, a fresh container can still pair itself, at the cost of one tap per
container. Replace the `SessionStart` command with

```
test -d ~/.builder/src || git clone --depth 1 https://github.com/vedantlbhatt/builder ~/.builder/src; cd ~/.builder/src && python3 -m capture sync --dry-run --quiet >/dev/null 2>&1; test -s ~/.builder/credentials.json || python3 -m capture pair --server "$BUILDER_API_URL" --no-wait
```

and set `BUILDER_MACHINE_ID` in the environment so every container is one device on the
server. `pair --no-wait` prints the code and the `builder://pair?code=…` link into the
hook's output, which Claude Code adds to the assistant's context — so the first thing the
assistant can tell you is "approve BCDF-GHJK in Builder to have this session on your
phone". The grant lives fifteen minutes; the pending device code is kept in
`~/.builder/pending-pair.json`, and the next `Stop` polls it once, stores the tokens and
continues. Sessions that end before you approve are not lost — the next `Stop` or
`SessionEnd` that runs paired uploads them — but sessions in a container destroyed before
any pairing are, which is the gap a key closes.

Add `--analyze` to the `SessionEnd` command to attach a model-written analysis
(`spec/analysis.v1.json`) produced by the container's own `claude -p` from a digest of the
transcript, exactly as the Mac's analysis-upload toggle does. It is opt-in for the reason
the contract gives: it is the one field that carries prose. Budget ~150 s for it; raise the
hook timeout accordingly.

## What leaves the machine

Exactly the fields in `privacy/upload-contract.json`, version 2, in anonymous mode: counts,
durations, the two clocks, a 1024-byte strip that carries no text by construction, token
buckets deduplicated on `message.id`, model labels, and an HMAC of the repository's
normalized origin. No prompt text, no assistant text, no tool input or output, no file
path or name, no branch, no commit message, no cwd, no hostname. The only prose is
`analysis`, and only when you pass `--analyze`. `python -m capture sync --dry-run` prints
every byte that would be sent and sends nothing — `capture/tests/test_dry_run.py` proves
the second half against a listening server — and `capture/tests/test_contract.py` walks
every nested key of every payload built from the fixtures against the contract.

## Measured on this container

The first end-to-end dry run, over the container in which capture was written:

```
dry run: 7 root transcript(s), 1,702 timestamped records, 10 session(s): 5 final, 0 live,
2 open (skipped; pass --live), 3 below the visibility floor.
```

All seven uploadable payloads validated against the server's `SessionUpload` model and
passed `sanity_gate`; the strip's non-idle seconds agreed with `active_seconds` to within
five seconds on each. Two of the root transcripts were open sessions of concurrent agents
working in this repository — the session that wrote this document among them.
