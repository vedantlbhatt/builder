import { describe, expect, test } from 'bun:test';

import contract from '../../privacy/upload-contract.json';
import type { FeedbackNoteWire } from '../src/generated/contract';
import { heading, minutes, NOTE_IDS, renderable, sentence } from '../src/session/feedback';

const note = (over: Partial<FeedbackNoteWire> = {}): FeedbackNoteWire => ({
  id: 'went_nowhere',
  seconds: 1217,
  count: 1,
  ...over,
});

describe('every note the contract declares has a sentence here', () => {
  /**
   * The one way this design fails silently. The wire carries an id, the server checks it
   * against the contract's own list, and the WORDS live in this client — so a fourth note
   * added to the contract without a line in `sentence()` would validate, store, and
   * render nothing at all, on the screen people read most.
   */
  const declared: string[] = (
    contract.fields.find((f: { name: string }) => f.name === 'feedback') as {
      values: string[];
    }
  ).values;

  test('the contract and this client agree on the id list', () => {
    expect([...NOTE_IDS].sort()).toEqual([...declared].sort() as typeof NOTE_IDS[number][]);
  });

  test('each one renders a sentence with its numbers in it', () => {
    for (const id of declared) {
      const text = sentence(note({ id, count: 7, seconds: 480 }));
      expect(text).not.toBeNull();
      expect(text).toContain('7');
      expect(text).toContain('8 minutes');
    }
  });
});

describe('what the wire does not carry, the sentence does not invent', () => {
  test('the failing command is not named, because it never left the machine', () => {
    const text = sentence(note({ id: 'failed_in_a_row', count: 7, seconds: 64 }))!;
    expect(text).toContain('7 failures in a row');
    expect(text).toContain('the same thing');
  });

  test('the file is not named either', () => {
    const text = sentence(note({ id: 'one_file_over_and_over', count: 6, seconds: 720 }))!;
    expect(text).toContain('One file');
  });
});

describe('an id this build does not know', () => {
  test('renders nothing rather than a debug string', () => {
    expect(sentence(note({ id: 'burned_tokens' }))).toBeNull();
    expect(renderable([note({ id: 'burned_tokens' })])).toEqual([]);
  });

  test('and does not take the notes beside it down with it', () => {
    const got = renderable([note({ id: 'burned_tokens' }), note({ id: 'went_nowhere' })]);
    expect(got.length).toBe(1);
    expect(got[0]!.id).toBe('went_nowhere');
  });
});

describe('ordering and absence', () => {
  test('the most expensive note comes first', () => {
    const got = renderable([
      note({ id: 'failed_in_a_row', seconds: 64, count: 7 }),
      note({ id: 'went_nowhere', seconds: 1217, count: 1 }),
    ]);
    expect(got[0]!.id).toBe('went_nowhere');
  });

  test('null and an empty list are the same thing on this card', () => {
    expect(renderable(null)).toEqual([]);
    expect(renderable(undefined)).toEqual([]);
    expect(renderable([])).toEqual([]);
  });

  test('no notes means no heading, so the section never renders empty', () => {
    expect(heading([])).toBeNull();
  });

  test('the heading names what the notes cost in total', () => {
    expect(heading(renderable([note({ seconds: 600 }), note({ id: 'failed_in_a_row', seconds: 600, count: 5 })]))).toBe(
      '20 minutes of this session went here'
    );
  });
});

describe('minutes, the way a person says them', () => {
  test('a stretch under a minute is not zero minutes', () => {
    // A note about something that took no time would not have been written; printing "0
    // minutes" makes the sentence contradict its own existence.
    expect(minutes(20)).toBe('under a minute');
  });

  test('one is singular', () => {
    expect(minutes(60)).toBe('1 minute');
  });

  test('an hour reads as an hour', () => {
    expect(minutes(3600)).toBe('1h');
    expect(minutes(4500)).toBe('1h 15m');
  });
});

describe('the plural of a stretch', () => {
  test('one stretch says how long it was', () => {
    expect(sentence(note({ count: 1, seconds: 1217 }))).toBe(
      'A stretch of 20 minutes with nothing written, tested or committed.'
    );
  });

  test('several say how many and what they cost together', () => {
    expect(sentence(note({ count: 3, seconds: 2400 }))).toBe(
      '3 stretches with nothing written, tested or committed, 40 minutes in total.'
    );
  });
});
