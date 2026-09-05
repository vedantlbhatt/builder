/**
 * The pure rules behind Settings → Cloud capture. Formatting only: the server decides
 * what a key can do (`server/builder/routes/capture_keys.py`), the phone decides how a
 * key row reads. Kept out of the screen so `bun test` can hold the strings still.
 */

import type { CaptureKey } from './api';
import { relativeTime } from '../social/format';

/** The server's CHECK on `capture_keys.name`. */
export const CAPTURE_KEY_NAME_MAX = 64;

/** The server's cap on live keys per user; a 409 past it. */
export const CAPTURE_KEY_MAX_LIVE = 10;

/** What a new key is called when the person does not type a name: the place it is for. */
export const CAPTURE_KEY_DEFAULT_NAME = 'claude.ai/code';

/**
 * The one sentence under a freshly minted key. The variable name is the contract with
 * `capture/client.py` (`BUILDER_CAPTURE_KEY`), and the recipe it points at is the hook
 * block in docs/cloud-capture.md.
 */
export const CAPTURE_KEY_PASTE_HINT =
  'Run the setup below once on the machine, or set BUILDER_URL and BUILDER_CAPTURE_KEY in your claude.ai/code environment, and Claude Code posts each session itself with nothing to install (docs/hooks-capture.md).';

/** The three hook events that ship a session: the prompt (live), each turn, and exit. */
export const HOOK_EVENTS = ['UserPromptSubmit', 'Stop', 'SessionEnd'] as const;

/** The `hooks` block for ~/.claude/settings.json, as documented in docs/hooks-capture.md. */
export function hooksSettingsJson(): string {
  const entry = { hooks: [{ type: 'command', command: 'bash ~/.builder/hook.sh' }] };
  return JSON.stringify(
    { hooks: Object.fromEntries(HOOK_EVENTS.map((e) => [e, [entry]])) },
    null,
    2
  );
}

/**
 * Everything a person pastes into a terminal, with the key filled in: the env file, the
 * served script, and a merge of the hooks block into settings.json (python3 is on every
 * Mac and Linux box; it only rewrites the `hooks` keys). One paste, then forget it.
 */
export function hookInstallSnippet(baseUrl: string, key: string): string {
  const url = baseUrl.replace(/\/+$/, '');
  return [
    'mkdir -p ~/.builder ~/.claude',
    `printf 'BUILDER_URL=${url}\\nBUILDER_CAPTURE_KEY=${key}\\n' > ~/.builder/env && chmod 600 ~/.builder/env`,
    `curl -fsSL "${url}/v1/ingest/hook.sh" -o ~/.builder/hook.sh`,
    `python3 - <<'PY'
import json, os
p = os.path.expanduser('~/.claude/settings.json')
s = json.load(open(p)) if os.path.exists(p) else {}
h = s.setdefault('hooks', {})
for ev in ${JSON.stringify([...HOOK_EVENTS])}:
    h[ev] = [{'hooks': [{'type': 'command', 'command': 'bash ~/.builder/hook.sh'}]}]
json.dump(s, open(p, 'w'), indent=2)
PY`,
  ].join('\n');
}

/** Same rule as the server: trimmed, 1-64 characters. Null when fine. */
export function captureKeyNameProblem(raw: string): string | null {
  const t = raw.trim();
  if (!t) return 'Give the key a name — where it will live, like "claude.ai/code".';
  if (t.length > CAPTURE_KEY_NAME_MAX) return `At most ${CAPTURE_KEY_NAME_MAX} characters.`;
  return null;
}

/** "bck_a1b2…" — the prefix the server returns, with an ellipsis so it never reads as the key. */
export function keyLabel(prefix: string): string {
  return `${prefix}…`;
}

/**
 * "never used", "used just now", "used 4m ago", "used Aug 3". Rides on the feed's
 * `relativeTime` so the two screens agree about what "3h" means.
 */
export function lastUsedLabel(iso: string | null, now: number = Date.now()): string {
  if (!iso) return 'never used';
  const r = relativeTime(iso, now);
  if (!r) return 'never used';
  if (r === 'just now') return 'used just now';
  if (/^\d+[mhd]$/.test(r)) return `used ${r} ago`;
  return `used ${r}`;
}

/** Oldest first, as the server lists them; a fresh key goes at the end. */
export function appendKey(keys: CaptureKey[], next: CaptureKey): CaptureKey[] {
  return [...keys.filter((k) => k.id !== next.id), next];
}

export function withoutKey(keys: CaptureKey[], id: string): CaptureKey[] {
  return keys.filter((k) => k.id !== id);
}

/** True when the server would answer 409 to one more. */
export function atKeyCap(keys: CaptureKey[]): boolean {
  return keys.length >= CAPTURE_KEY_MAX_LIVE;
}
