/**
 * The pure half of posting, shared by the compose sheet and the recap. The plan is
 * `mediaPlan` exactly (media.test.ts pins its limits); this file pins what the two
 * sheets do around it — the upload lines, Retry's selection, the caption the recap
 * folds a title into, and the button labels.
 */

import { describe, expect, test } from 'bun:test';

import {
  allDone,
  CAPTION_MAX,
  detailAfterPost,
  initialRows,
  planMedia,
  primaryAction,
  publishCaption,
  retryableIndices,
  uploadStatus,
  uploadWhat,
  withRowState,
} from '../src/social/composeFlow';
import { mediaPlan, type PickedPhoto, type RecordedAudio } from '../src/social/media';

const photo = (i: number): PickedPhoto => ({ uri: `file:///p${i}.jpg`, width: 800, height: 600, mime: 'image/jpeg' });
const note: RecordedAudio = { uri: 'file:///n.m4a', durationMs: 42_000 };

describe('planMedia', () => {
  test('is mediaPlan, job for job — the compose sheet’s plan is unchanged', () => {
    const photos = [photo(1), photo(2)];
    const got = planMedia(photos, note);
    expect(got).toEqual({ ok: true, jobs: mediaPlan(photos, note) });
    if (got.ok) {
      expect(got.jobs.map((j) => j.kind)).toEqual(['photo', 'photo', 'audio']);
    }
  });
  test('turns the plan’s refusal into a sentence instead of a throw', () => {
    const got = planMedia(Array.from({ length: 7 }, (_, i) => photo(i)), null);
    expect(got.ok).toBe(false);
    if (!got.ok) expect(got.message).toMatch(/at most 6 photos/);
  });
  test('nothing chosen is an empty plan, not an error', () => {
    expect(planMedia([], null)).toEqual({ ok: true, jobs: [] });
  });
});

describe('upload rows', () => {
  const jobs = mediaPlan([photo(1), photo(2)], note);

  test('start queued, one per job, in plan order', () => {
    const rows = initialRows(jobs);
    expect(rows.map((r) => r.state.phase)).toEqual(['queued', 'queued', 'queued']);
    expect(rows.map((r) => uploadWhat(r.job))).toEqual(['Photo 1', 'Photo 2', 'Voice note · 0:42']);
  });

  test('withRowState touches exactly one line', () => {
    const rows = withRowState(initialRows(jobs), 1, { phase: 'uploading' });
    expect(rows.map((r) => r.state.phase)).toEqual(['queued', 'uploading', 'queued']);
  });

  test('Retry runs the failed lines and never a 503', () => {
    let rows = initialRows(jobs);
    rows = withRowState(rows, 0, { phase: 'failed', message: 'storage refused the upload (500)', unconfigured: false });
    rows = withRowState(rows, 1, { phase: 'done', media: {} as never });
    rows = withRowState(rows, 2, { phase: 'failed', message: 'not configured', unconfigured: true });
    expect(retryableIndices(rows)).toEqual([0]);
    expect(allDone(rows)).toBe(false);
  });

  test('allDone only when every line landed', () => {
    let rows = initialRows(jobs);
    for (let i = 0; i < rows.length; i++) rows = withRowState(rows, i, { phase: 'done', media: {} as never });
    expect(allDone(rows)).toBe(true);
    expect(retryableIndices(rows)).toEqual([]);
  });

  test('the status words', () => {
    expect(uploadStatus({ phase: 'queued' })).toBe('waiting');
    expect(uploadStatus({ phase: 'uploading' })).toBe('uploading…');
    expect(uploadStatus({ phase: 'done', media: {} as never })).toBe('done');
    expect(uploadStatus({ phase: 'failed', message: 'x', unconfigured: true })).toBe('not configured');
    expect(uploadStatus({ phase: 'failed', message: 'storage refused', unconfigured: false })).toBe(
      'failed · storage refused'
    );
  });
});

describe('publishCaption', () => {
  test('a changed title leads the caption; the session’s own title is not repeated', () => {
    expect(publishCaption({ title: 'Shipped the recap', sessionTitle: 'fix tests', caption: 'finally' })).toBe(
      'Shipped the recap\n\nfinally'
    );
    expect(publishCaption({ title: 'fix tests', sessionTitle: 'fix tests', caption: 'finally' })).toBe('finally');
    expect(publishCaption({ title: '  fix tests ', sessionTitle: 'fix tests', caption: '' })).toBeNull();
  });
  test('a title alone is a caption; nothing at all is null', () => {
    expect(publishCaption({ title: 'Night build', sessionTitle: null, caption: '   ' })).toBe('Night build');
    expect(publishCaption({ title: '', sessionTitle: null, caption: '' })).toBeNull();
  });
  test('stays within the server’s cap with the title first', () => {
    const got = publishCaption({ title: 'T', sessionTitle: null, caption: 'x'.repeat(CAPTION_MAX) });
    expect(got?.length).toBe(CAPTION_MAX);
    expect(got?.startsWith('T\n\n')).toBe(true);
  });
});

describe('primaryAction', () => {
  test('Post with a separate Save privately, unless private is already the choice', () => {
    expect(primaryAction('followers', false)).toEqual({ label: 'Post', showSavePrivately: true });
    expect(primaryAction('public', false)).toEqual({ label: 'Post', showSavePrivately: true });
    expect(primaryAction('private', false)).toEqual({ label: 'Save privately', showSavePrivately: false });
  });
  test('editing saves changes and offers nothing else', () => {
    expect(primaryAction('public', true)).toEqual({ label: 'Save changes', showSavePrivately: false });
  });
});

describe('detailAfterPost', () => {
  test('mirrors the server: a private post leaves is_shared down, the id says posted', () => {
    const s = { id: 's1', is_shared: false, post_id: null as string | null };
    expect(detailAfterPost(s, { id: 'p1', visibility: 'private' })).toEqual({ id: 's1', is_shared: false, post_id: 'p1' });
    expect(detailAfterPost(s, { id: 'p1', visibility: 'followers' })).toEqual({ id: 's1', is_shared: true, post_id: 'p1' });
  });
});
