import type { FeedbackNoteWire } from '../generated/contract';

/**
 * THE SENTENCE FOR EACH NOTE, written here rather than uploaded.
 *
 * The wire carries an id and two integers (contract v3). That is deliberate three times
 * over: the failing COMMAND and the FILE NAME the same module names on your own machine
 * are on the never-list and stay there, and the WORDING is not on the wire either — so
 * rewriting a note is a client release rather than a re-upload of everybody's history.
 *
 * The cost of that choice is this file: an id the client does not know renders nothing,
 * silently. `FeedbackNoteWire` is validated against the contract's own id list at the
 * server door, so an unknown id cannot be stored — but a note added to the contract
 * without a line added here would still be invisible, which is why `renderable` exists
 * and why the test asserts every declared id has a sentence.
 */

/** The three notes the contract declares, each with what its two integers mean. */
export const NOTE_IDS = ['went_nowhere', 'failed_in_a_row', 'one_file_over_and_over'] as const;

export type NoteId = (typeof NOTE_IDS)[number];

export interface Note {
  id: string;
  /** Second person, one sentence, with the numbers in it. */
  text: string;
  /** What it cost, in seconds. Used for ordering and for the badge. */
  seconds: number;
}

/**
 * Minutes the way a person says them. Never "0 minutes": a note about a stretch that
 * lasted under a minute would not have been worth writing, and printing zero would make
 * the sentence contradict its own existence.
 */
export function minutes(seconds: number): string {
  const m = Math.round(seconds / 60);
  if (m < 1) return 'under a minute';
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'}`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h}h ${String(rest).padStart(2, '0')}m` : `${h}h`;
}

/**
 * One note's sentence, or null when this client does not know the id.
 *
 * Null rather than a fallback string: "went_nowhere: 3" on a card is worse than nothing,
 * because it reads as a bug and tells the person less than silence does.
 */
export function sentence(n: FeedbackNoteWire): string | null {
  const t = minutes(n.seconds);
  switch (n.id) {
    case 'went_nowhere':
      return n.count === 1
        ? `A stretch of ${t} with nothing written, tested or committed.`
        : `${n.count} stretches with nothing written, tested or committed, ${t} in total.`;
    case 'failed_in_a_row':
      // The COMMAND is not on the wire. "The same thing" is what can be said honestly
      // from an id and two numbers, and it is still the useful half: a run of identical
      // failures is the moment to change approach rather than try again.
      return `${n.count} failures in a row on the same thing before anything changed, over ${t}.`;
    case 'one_file_over_and_over':
      // The FILE NAME is not on the wire either.
      return `One file was rewritten ${n.count} times across ${t}. A file on its fifth pass usually needs a decision, not another attempt.`;
    default:
      return null;
  }
}

/** Every note this client can actually render, most expensive first. */
export function renderable(notes: FeedbackNoteWire[] | null | undefined): Note[] {
  if (!notes) return [];
  const out: Note[] = [];
  for (const n of notes) {
    const text = sentence(n);
    if (text) out.push({ id: n.id, text, seconds: n.seconds });
  }
  return out.sort((a, b) => b.seconds - a.seconds);
}

/**
 * The one line above the notes, or null when there are none.
 *
 * It names the total so the section is a measurement rather than a scolding: "23 minutes
 * went here" is a fact about an hour the person was present for, and they can decide what
 * it was worth.
 */
export function heading(notes: Note[]): string | null {
  if (!notes.length) return null;
  const total = notes.reduce((sum, n) => sum + n.seconds, 0);
  if (total < 60) return 'One thing worth a look';
  return `${minutes(total)} of this session went here`;
}
