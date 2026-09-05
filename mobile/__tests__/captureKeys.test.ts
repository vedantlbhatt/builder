/**
 * The strings Settings → Cloud capture shows for a key. The name rule is the server's
 * (`capture_keys.name` CHECK, 1-64), the prefix is shown with an ellipsis so it never reads
 * as a usable key, and "last used" rides on the feed's relativeTime so the two agree.
 */

import { describe, expect, test } from 'bun:test';

import type { CaptureKey } from '../src/data/api';
import {
  HOOK_EVENTS,
  hookInstallSnippet,
  hooksSettingsJson,
  appendKey,
  atKeyCap,
  CAPTURE_KEY_MAX_LIVE,
  CAPTURE_KEY_NAME_MAX,
  CAPTURE_KEY_PASTE_HINT,
  captureKeyNameProblem,
  keyLabel,
  lastUsedLabel,
  withoutKey,
} from '../src/data/captureKeys';

const NOW = Date.parse('2026-09-05T12:00:00Z');

function key(id: string, lastUsed: string | null = null): CaptureKey {
  return {
    id,
    name: `k-${id}`,
    key_prefix: 'bck_a1b2',
    created_at: '2026-09-01T00:00:00Z',
    last_used_at: lastUsed,
  };
}

describe('name rule', () => {
  test('mirrors the server: trimmed, 1-64 characters', () => {
    expect(captureKeyNameProblem('claude.ai/code')).toBeNull();
    expect(captureKeyNameProblem('  x  ')).toBeNull();
    expect(captureKeyNameProblem('a'.repeat(CAPTURE_KEY_NAME_MAX))).toBeNull();
  });

  test('names the rule that failed', () => {
    expect(captureKeyNameProblem('')).toMatch(/name/);
    expect(captureKeyNameProblem('   ')).toMatch(/name/);
    expect(captureKeyNameProblem('a'.repeat(CAPTURE_KEY_NAME_MAX + 1))).toMatch(/64/);
  });
});

describe('display', () => {
  test('the prefix is shown with an ellipsis, never as a whole key', () => {
    expect(keyLabel('bck_a1b2')).toBe('bck_a1b2…');
  });

  test('last used reads as a sentence fragment in every band', () => {
    expect(lastUsedLabel(null, NOW)).toBe('never used');
    expect(lastUsedLabel('', NOW)).toBe('never used');
    expect(lastUsedLabel('2026-09-05T11:59:50Z', NOW)).toBe('used just now');
    expect(lastUsedLabel('2026-09-05T11:56:00Z', NOW)).toBe('used 4m ago');
    expect(lastUsedLabel('2026-09-05T09:00:00Z', NOW)).toBe('used 3h ago');
    expect(lastUsedLabel('2026-09-03T12:00:00Z', NOW)).toBe('used 2d ago');
    // Beyond a week relativeTime gives a date; the label does not say "ago" after a date.
    const old = lastUsedLabel('2026-08-01T12:00:00Z', NOW);
    expect(old.startsWith('used ')).toBe(true);
    expect(old).not.toMatch(/ago$/);
    expect(old).toMatch(/Aug/);
  });

  test('the paste hint names the variable the uploader reads and the recipe', () => {
    expect(CAPTURE_KEY_PASTE_HINT).toContain('BUILDER_CAPTURE_KEY');
    expect(CAPTURE_KEY_PASTE_HINT).toContain('docs/hooks-capture.md');
    expect(CAPTURE_KEY_PASTE_HINT.split('. ').length).toBe(1);
  });
});

describe('list edits', () => {
  test('a new key goes last, a duplicate id replaces in place', () => {
    const list = appendKey([key('a'), key('b')], key('c'));
    expect(list.map((k) => k.id)).toEqual(['a', 'b', 'c']);
    const again = appendKey(list, key('b', '2026-09-05T11:00:00Z'));
    expect(again.map((k) => k.id)).toEqual(['a', 'c', 'b']);
    expect(again[2]!.last_used_at).not.toBeNull();
  });

  test('revoke removes by id and leaves the rest untouched', () => {
    const list = [key('a'), key('b'), key('c')];
    expect(withoutKey(list, 'b').map((k) => k.id)).toEqual(['a', 'c']);
    expect(withoutKey(list, 'zz')).toEqual(list);
  });

  test('the cap is the server’s ten', () => {
    const nine = Array.from({ length: CAPTURE_KEY_MAX_LIVE - 1 }, (_, i) => key(String(i)));
    expect(atKeyCap(nine)).toBe(false);
    expect(atKeyCap([...nine, key('last')])).toBe(true);
  });
});

describe('hook recipe', () => {
  test('the settings block names the three events and one command', () => {
    const parsed = JSON.parse(hooksSettingsJson()) as { hooks: Record<string, unknown[]> };
    expect(Object.keys(parsed.hooks)).toEqual([...HOOK_EVENTS]);
    expect(JSON.stringify(parsed)).toContain('bash ~/.builder/hook.sh');
  });
  test('the install snippet carries the key and the server once each, trailing slash trimmed', () => {
    const s = hookInstallSnippet('https://b.example.com/', 'bck_abc');
    expect(s.split('bck_abc').length - 1).toBe(1);
    expect(s).toContain('https://b.example.com/v1/ingest/hook.sh');
    expect(s).not.toContain('example.com//');
    expect(s).toContain("~/.claude/settings.json");
  });
});
