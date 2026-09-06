import type { BuilderNarrative, NarrativeClaim } from '../generated/narrative';

/**
 * Display decisions for the "how you work" page, pure so `bun test` can run them.
 *
 * Nothing here rewords a sentence. Every string was checked against the measurements it
 * came from on the machine that wrote it, and a claim citing a number those measurements
 * did not contain was deleted before it was uploaded (analysis/narrative.py). Paraphrasing
 * on the phone would put an unverified sentence in front of a person.
 *
 * What this file does decide is what an EMPTY field means. `verify` blanks a field rather
 * than dropping the document when it takes a claim back, so "" is a normal state, and the
 * screen must render nothing rather than a heading with nothing under it.
 */

export interface NarrativeView {
  paragraphs: string[];
  strengths: NarrativeClaim[];
  watchOuts: NarrativeClaim[];
  experiment: string;
  provenance: string;
}

function nonEmpty(s: string | null | undefined): boolean {
  return typeof s === 'string' && s.trim().length > 0;
}

/**
 * The provenance line, said out loud rather than left for a person to wonder about.
 *
 * The count is included when it is not zero, because a page that dropped a claim is a page
 * whose model tried to invent a figure, and hiding that would make the guarantee sound
 * like a promise rather than a check that runs.
 */
export function provenanceLine(n: BuilderNarrative): string {
  const who = nonEmpty(n.model) ? n.model : 'a model';
  const head =
    `Written from your own measurements by ${who} on your machine. Any sentence citing ` +
    'a number your sessions did not produce is deleted before it gets here';
  const dropped = Number.isFinite(n.invented_numbers_dropped) ? n.invented_numbers_dropped : 0;
  if (dropped <= 0) return `${head}.`;
  return `${head}; ${dropped} ${dropped === 1 ? 'was' : 'were'} this time.`;
}

/**
 * What the screen should show, or null when there is nothing to show.
 *
 * Null rather than an empty section: a document whose every claim was taken back is a
 * document with nothing in it, and a heading over blank space reads as a bug.
 */
export function narrativeView(n: BuilderNarrative | null | undefined): NarrativeView | null {
  if (!n) return null;
  const paragraphs = (n.how_you_work ?? []).filter(nonEmpty);
  const strengths = (n.strengths ?? []).filter((s) => nonEmpty(s?.text));
  const watchOuts = (n.watch_outs ?? []).filter((s) => nonEmpty(s?.text));
  const experiment = nonEmpty(n.one_experiment) ? n.one_experiment.trim() : '';
  if (!paragraphs.length && !strengths.length && !watchOuts.length && !experiment) return null;
  return {
    paragraphs,
    strengths,
    watchOuts,
    experiment,
    provenance: provenanceLine(n),
  };
}

/**
 * The archetype line, when the narrative has one that survived the check.
 *
 * It is separate from the rest because it belongs beside the archetype, not under the
 * heading: it is the sentence that says what "quality guardian" means for THIS person,
 * and a label with no sentence under it is a horoscope.
 */
export function archetypeSentence(n: BuilderNarrative | null | undefined): string | null {
  return n && nonEmpty(n.archetype_line) ? n.archetype_line.trim() : null;
}
