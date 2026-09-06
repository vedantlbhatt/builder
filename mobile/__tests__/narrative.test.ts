import { describe, expect, test } from 'bun:test';

import type { BuilderNarrative } from '../src/generated/narrative';
import { archetypeSentence, narrativeView, provenanceLine } from '../src/profile/narrative';

/**
 * The page is written and checked on the machine that has the transcripts, so the phone's
 * only job is deciding what an EMPTY field means. `verify` blanks a field rather than
 * rejecting the document when it takes a claim back, which makes "" a normal state and a
 * heading over blank space the failure to avoid.
 */
function doc(overrides: Partial<BuilderNarrative> = {}): BuilderNarrative {
  return {
    archetype_line: 'You run 5.75 tests an hour against a 3.0 bar.',
    how_you_work: ['You front-load: 340 characters, then 188.'],
    strengths: [{ text: 'You verify.', evidence: '13 of 14 edit bursts.' }],
    watch_outs: [{ text: 'You correct often.', evidence: 'steer_rate 0.433.' }],
    one_experiment: 'Check in at 9 tool calls.',
    narrative_version: 1,
    model: 'sonnet',
    generated_at: '2026-09-06T03:06:25Z',
    dashes_rewritten: 0,
    invented_numbers_dropped: 0,
    ...overrides,
  };
}

describe('narrativeView', () => {
  test('passes every sentence through exactly as it was written', () => {
    const v = narrativeView(doc())!;
    expect(v.paragraphs).toEqual(['You front-load: 340 characters, then 188.']);
    expect(v.strengths.map((s) => s.evidence)).toEqual(['13 of 14 edit bursts.']);
    expect(v.experiment).toBe('Check in at 9 tool calls.');
  });

  test('a document whose every claim was taken back renders nothing at all', () => {
    const v = narrativeView(
      doc({ how_you_work: [], strengths: [], watch_outs: [], one_experiment: '' })
    );
    expect(v).toBeNull();
  });

  test('one surviving claim is still a page', () => {
    const v = narrativeView(
      doc({ how_you_work: [], strengths: [], watch_outs: [], one_experiment: 'Try this.' })
    );
    expect(v).not.toBeNull();
    expect(v!.experiment).toBe('Try this.');
  });

  test('a blanked paragraph is dropped, not printed as an empty line', () => {
    const v = narrativeView(doc({ how_you_work: ['', '   ', 'Real one, 340 characters.'] }))!;
    expect(v.paragraphs).toEqual(['Real one, 340 characters.']);
  });

  test('a claim with no text is dropped even when it still carries evidence', () => {
    const v = narrativeView(
      doc({ strengths: [{ text: '', evidence: '13 of 14 edit bursts.' }] })
    )!;
    expect(v.strengths).toEqual([]);
  });

  test('null and undefined are the same as no page', () => {
    expect(narrativeView(null)).toBeNull();
    expect(narrativeView(undefined)).toBeNull();
  });

  test('a server that sent no arrays at all does not crash the screen', () => {
    // A field the spec requires can still be missing off the wire; the screen must render
    // rather than throw, because a blank profile tab is how a person concludes the app is
    // broken.
    const partial = { one_experiment: 'Try this.', model: 'sonnet' } as unknown as BuilderNarrative;
    const v = narrativeView(partial)!;
    expect(v.paragraphs).toEqual([]);
    expect(v.experiment).toBe('Try this.');
  });
});

describe('provenanceLine', () => {
  test('names the model and says the check exists', () => {
    const line = provenanceLine(doc());
    expect(line).toContain('sonnet');
    expect(line).toContain('deleted before it gets here');
    expect(line.endsWith('.')).toBe(true);
  });

  test('a page with nothing dropped does not mention a count', () => {
    expect(provenanceLine(doc())).not.toContain('this time');
  });

  test('one dropped claim is reported, in the singular', () => {
    expect(provenanceLine(doc({ invented_numbers_dropped: 1 }))).toContain('1 was this time');
  });

  test('several dropped claims are reported, in the plural', () => {
    expect(provenanceLine(doc({ invented_numbers_dropped: 3 }))).toContain('3 were this time');
  });

  test('a missing model name does not print "undefined" at a person', () => {
    expect(provenanceLine(doc({ model: '' }))).toContain('by a model on your machine');
  });

  test('never claims a dash was rewritten or a number invented that was not', () => {
    // The em dash ban applies to this string like every other user-facing one.
    expect(provenanceLine(doc())).not.toMatch(/[—–―−]/);
  });
});

describe('archetypeSentence', () => {
  test('is the sentence that says what the label means for this person', () => {
    expect(archetypeSentence(doc())).toBe('You run 5.75 tests an hour against a 3.0 bar.');
  });

  test('a line the check took back is null, never an empty quote', () => {
    expect(archetypeSentence(doc({ archetype_line: '' }))).toBeNull();
    expect(archetypeSentence(doc({ archetype_line: '   ' }))).toBeNull();
  });

  test('no narrative is no sentence', () => {
    expect(archetypeSentence(null)).toBeNull();
  });
});
