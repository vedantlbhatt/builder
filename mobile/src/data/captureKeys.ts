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
  'Paste it as BUILDER_CAPTURE_KEY in your Claude Code cloud environment settings; the Stop and SessionEnd hooks from docs/cloud-capture.md then upload every session from that environment, with no pairing.';

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
