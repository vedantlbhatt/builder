# Hooks: sessions on the phone with nothing installed

The transcripts Claude Code writes live only on the machine (or the cloud container)
running it; Anthropic offers no API to read them. So the lightest possible capture is
Claude Code's **own hooks**: one entry in `settings.json` runs a 30-line shell script that
POSTs the transcript tail to your Builder server, and the server cuts the sessions with the
same rules `python -m capture` and the Mac app use. No app, no daemon, no Python on the
machine — `bash`, `curl` and `sed`.

## Install (one time, per machine or per cloud environment)

1. In the phone app: Settings → Cloud capture → **New key**. Copy the key (shown once).
2. On the machine:

   ```bash
   mkdir -p ~/.builder
   cat > ~/.builder/env <<'EOF'
   BUILDER_URL=https://<your builder server>
   BUILDER_CAPTURE_KEY=bck_…
   EOF
   chmod 600 ~/.builder/env
   curl -fsSL "$BUILDER_URL/v1/ingest/hook.sh" -o ~/.builder/hook.sh
   ```

3. Add to `~/.claude/settings.json` (merge into an existing `hooks` block):

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "bash ~/.builder/hook.sh" }] }],
       "Stop":             [{ "hooks": [{ "type": "command", "command": "bash ~/.builder/hook.sh" }] }],
       "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "bash ~/.builder/hook.sh" }] }]
     }
   }
   ```

That is the whole install. The next prompt you type appears on the phone as a live
session; when Claude Code exits, the session finalises and the "Session finished" push
fires like any other.

**claude.ai/code (web and phone):** set `BUILDER_URL` and `BUILDER_CAPTURE_KEY` as
environment variables in the cloud environment's settings, add the same three hooks plus a
`SessionStart` hook that runs the `curl -o ~/.builder/hook.sh` line above, and every cloud
session lands on the phone with no computer involved at all.

## What happens on each hook

| hook | the script sends | the server does |
|---|---|---|
| `UserPromptSubmit` | the tail since the last upload | appends the chunk, re-cuts the transcript; the session shows as **live** |
| `Stop` (end of a turn) | the tail | same — a Stop is not the end of a sitting; the idle rule decides |
| `SessionEnd` (Claude Code exiting) | the tail | appends, cuts with the session **finalised**, retires the raw bytes |

The script reads `session_id`, `transcript_path` and `hook_event_name` from the hook's
stdin JSON, keeps the last acknowledged byte offset per session under
`~/.builder/offsets/`, and sends only `tail -c +offset`. The server answers with
`next_offset`; on a `409` (the server holds less than the script thinks — e.g. a
restored database) it asks `/v1/ingest/transcript/<id>/offset` and the next hook resends
from there. The script always exits 0: an upload failure can never block Claude Code.

A session whose process was killed with no `SessionEnd` still finishes: the next hook from
any of your sessions re-cuts transcripts whose newest chunk is older than 30 minutes, and
the idle rule makes them final.

## Server side

`POST /v1/ingest/transcript` (capture key or device token) with headers
`X-Builder-Session-Id`, `X-Builder-Project-Dir`, `X-Builder-Offset`, `X-Builder-Hook`,
`X-Builder-Tz-Offset-Minutes`; body: the raw JSONL tail (gzip accepted); at most 64 MB.
Chunks are stored by byte offset in `transcript_chunks` (owner-only RLS). Each request
reassembles the session's bytes and runs **the uploader's own pipeline** —
`capture.sessions.load_source` → `measure_boundaries` v3 (lineage pooling, presence
intervals, the idle rule) → `build_payload` → the contract model → the same sanity gate
and upsert as `POST /v1/sync/sessions:batch`. `client_session_id` is derived from the
first event with the machine slot fixed to `capture`, so a session that arrives by hook
and the same session synced later by `python -m capture` or the Mac dedupe into one row.

The server image must contain `capture/`, `analysis/`, `scripts/measure_boundaries.py`
and `spec/strip.v1.json`: build from the repository root (`./Dockerfile`,
`./railway.json`) rather than from `server/`. A server-only image still boots; this route
then answers with the reason.

## Privacy

This channel sends the **raw transcript** — your prompts and the tool output — over TLS,
not the contract's summary fields. `privacy/upload-contract.json` still describes what
`python -m capture` and the Mac send; the hook is a separate, opt-in path you turn on by
installing it. The server keeps only what the contract describes plus the analysis
digest, and deletes the raw bytes as soon as the session is final (a zero-length marker
keeps the byte offset so a later tail still lands), or after 7 days for a session that
never finalises. `DELETE /v1/ingest/transcript/<id>` drops them now. Revoke the key in
Settings and the channel is closed the same second.

Not yet: server-side analysis. The Claude-powered analysis runs `claude -p` on the
machine that has your subscription; the server has no such credential. A hook-fed
session gets every number and the strip, and its analysis when a machine with `claude`
runs `python -m capture sync --analyze` against the same transcript.
