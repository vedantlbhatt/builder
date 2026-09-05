/**
 * The pure half of posting a session, shared by the compose sheet and the recap.
 *
 * Both screens do the same three things: turn the chosen media into a plan, create (or
 * patch) the post row, then push the plan through `runUploads` one line at a time and
 * show each line's state. The rules that decide what those lines say and when the run is
 * finished live here, with no React and no network, so `__tests__/composeFlow.test.ts`
 * can pin them in bun. The effectful loop is `useUploadFlow.ts`.
 */

import type { FeedItem, Visibility } from '../data/api';
import { formatClock, mediaPlan, type MediaJob, type PickedPhoto, type RecordedAudio } from './media';
import type { UploadState } from './upload';

/** One line per planned upload while a sheet is in its upload phase. */
export type UploadRow = { job: MediaJob; state: UploadState };

/** The plan, or the one sentence that explains why there is none. */
export type Planned = { ok: true; jobs: MediaJob[] } | { ok: false; message: string };

/** `mediaPlan` without the throw: the sheets show the message, they do not catch it. */
export function planMedia(photos: readonly PickedPhoto[], audio: RecordedAudio | null): Planned {
  try {
    return { ok: true, jobs: mediaPlan(photos, audio) };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : 'check the media' };
  }
}

export function initialRows(jobs: readonly MediaJob[]): UploadRow[] {
  return jobs.map((job) => ({ job, state: { phase: 'queued' } }));
}

export function withRowState(rows: readonly UploadRow[], i: number, state: UploadState): UploadRow[] {
  return rows.map((r, k) => (k === i ? { ...r, state } : r));
}

/** Every line landed. Vacuously true for no lines, which the sheets never reach. */
export function allDone(rows: readonly UploadRow[]): boolean {
  return rows.every((r) => r.state.phase === 'done');
}

/**
 * The lines Retry would run again: failed, and not because storage is unconfigured. A
 * 503 is a fact about the server, and retrying it produces the same 503.
 */
export function retryableIndices(rows: readonly UploadRow[]): number[] {
  return rows
    .map((r, i) => (r.state.phase === 'failed' && !r.state.unconfigured ? i : -1))
    .filter((i) => i >= 0);
}

/** "Photo 2" / "Voice note · 0:42" — what an upload line is about. */
export function uploadWhat(job: MediaJob): string {
  return job.kind === 'photo' ? `Photo ${job.index + 1}` : `Voice note · ${formatClock(job.duration_ms)}`;
}

/** The right-hand word on an upload line. */
export function uploadStatus(state: UploadState): string {
  switch (state.phase) {
    case 'queued':
      return 'waiting';
    case 'uploading':
      return 'uploading…';
    case 'done':
      return 'done';
    case 'failed':
      return state.unconfigured ? 'not configured' : `failed · ${state.message}`;
  }
}

// ------------------------------------------------------------------- the caption

export const CAPTION_MAX = 1000;

/**
 * The caption a recap posts, with the edited title folded in.
 *
 * The server has no title field on a post and no PATCH on a session's title
 * (`routes/sessions.py` is read-only; `posts` carries `caption` and `visibility`), so a
 * title the person changes on the recap lives in the caption as its first line — the way
 * a Strava activity's name is the first thing on its card. A title left as the session's
 * own is not repeated: the card already shows it. Whitespace-only input is nothing, and
 * the whole thing stays within `CAPTION_MAX` with the title taking precedence.
 */
export function publishCaption(input: {
  title: string;
  sessionTitle: string | null;
  caption: string;
}): string | null {
  const title = input.title.trim();
  const caption = input.caption.trim();
  const own = (input.sessionTitle ?? '').trim();
  const lead = title && title !== own ? title : '';
  const joined = [lead, caption].filter(Boolean).join('\n\n');
  if (!joined) return null;
  return joined.length > CAPTION_MAX ? joined.slice(0, CAPTION_MAX) : joined;
}

/**
 * What the primary button does, from the visibility chosen. A private choice IS "save
 * privately", so the secondary button has nothing left to offer and is hidden.
 */
export function primaryAction(visibility: Visibility, editing: boolean): {
  label: string;
  showSavePrivately: boolean;
} {
  if (editing) return { label: 'Save changes', showSavePrivately: false };
  if (visibility === 'private') return { label: 'Save privately', showSavePrivately: false };
  return { label: 'Post', showSavePrivately: true };
}

/**
 * The detail's fields after a post lands, mirroring the server's `_set_shared`: a private
 * post leaves `is_shared` down; the post id is what says "posted" for every visibility.
 */
export function detailAfterPost<T extends { is_shared: boolean; post_id?: string | null }>(
  session: T,
  post: Pick<FeedItem, 'id' | 'visibility'>
): T {
  return { ...session, is_shared: post.visibility !== 'private', post_id: post.id };
}
