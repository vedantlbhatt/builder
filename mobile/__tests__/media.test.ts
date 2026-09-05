/**
 * The pure rules under post media: the plan that turns chosen photos and a recording into
 * uploads, the limits it enforces before the server has to, the clock a voice note is
 * labelled with, and the mime <-> extension mapping that must agree with
 * `server/builder/routes/social.py`. No picker, no recorder, no network.
 */

import { describe, expect, test } from 'bun:test';

import {
  addPhotos,
  AUDIO_MIME_EXT,
  audioMime,
  extensionForMime,
  formatClock,
  MAX_AUDIO_MS,
  MAX_PHOTOS,
  mediaPlan,
  MediaPlanError,
  mimeForUri,
  normalizeMime,
  PHOTO_MIME_EXT,
  photoMime,
  photoRoom,
  type PickedPhoto,
} from '../src/social/media';

const photo = (i: number, extra: Partial<PickedPhoto> = {}): PickedPhoto => ({
  uri: `file:///tmp/p${i}.jpg`,
  width: 1200,
  height: 900,
  ...extra,
});

describe('mediaPlan', () => {
  test('photos first, in the order chosen, then the voice note', () => {
    const jobs = mediaPlan([photo(1), photo(2, { uri: 'file:///tmp/p2.png', mime: 'image/png' })], {
      uri: 'file:///var/rec/abc.m4a',
      durationMs: 42_000,
    });
    expect(jobs.map((j) => j.kind)).toEqual(['photo', 'photo', 'audio']);
    expect(jobs[0]).toMatchObject({ index: 0, content_type: 'image/jpeg', width: 1200, height: 900 });
    expect(jobs[1]).toMatchObject({ index: 1, content_type: 'image/png' });
    expect(jobs[2]).toEqual({
      kind: 'audio',
      index: 0,
      uri: 'file:///var/rec/abc.m4a',
      content_type: 'audio/m4a',
      duration_ms: 42_000,
    });
  });

  test('empty in, empty out', () => {
    expect(mediaPlan([], null)).toEqual([]);
  });

  test('accepts exactly six photos and rejects the seventh', () => {
    const six = Array.from({ length: MAX_PHOTOS }, (_, i) => photo(i));
    expect(mediaPlan(six, null)).toHaveLength(6);
    expect(() => mediaPlan([...six, photo(99)], null)).toThrow(MediaPlanError);
    expect(() => mediaPlan([...six, photo(99)], null)).toThrow(/6 photos/);
  });

  test('accepts a note of exactly 90 s and rejects one millisecond more', () => {
    const at = (ms: number) => mediaPlan([], { uri: 'file:///r.m4a', durationMs: ms });
    expect(at(MAX_AUDIO_MS)).toHaveLength(1);
    expect(() => at(MAX_AUDIO_MS + 1)).toThrow(MediaPlanError);
    expect(() => at(MAX_AUDIO_MS + 1)).toThrow(/1:30/);
  });

  test('rejects an empty recording', () => {
    expect(() => mediaPlan([], { uri: 'file:///r.m4a', durationMs: 0 })).toThrow(MediaPlanError);
  });

  test('rejects a recording the server will not sign', () => {
    expect(() => mediaPlan([], { uri: 'file:///r.caf', durationMs: 5000 })).toThrow(/format/);
    expect(() => mediaPlan([], { uri: 'file:///r', durationMs: 5000, mime: 'audio/webm' })).toThrow(
      MediaPlanError
    );
  });

  test('a photo without dimensions cannot be attached, so it cannot be planned', () => {
    expect(() => mediaPlan([photo(1, { width: 0 })], null)).toThrow(/width and height/);
  });

  test('rounds fractional dimensions and durations to integers', () => {
    const jobs = mediaPlan([photo(1, { width: 1199.6, height: 899.4 })], {
      uri: 'file:///r.m4a',
      durationMs: 41_999.7,
    });
    expect(jobs[0]).toMatchObject({ width: 1200, height: 899 });
    expect(jobs[1]).toMatchObject({ duration_ms: 42_000 });
  });

  test('flags a long edge over 2048 without refusing it', () => {
    const jobs = mediaPlan([photo(1, { width: 4032, height: 3024 }), photo(2, { width: 2048, height: 1536 })], null);
    expect(jobs[0]).toMatchObject({ oversized: true });
    expect(jobs[1]).toMatchObject({ oversized: false });
  });

  test('falls back to JPEG for a picker type the server does not sign', () => {
    const jobs = mediaPlan([photo(1, { uri: 'content://media/1', mime: 'image/gif' })], null);
    expect(jobs[0]).toMatchObject({ content_type: 'image/jpeg' });
  });
});

describe('formatClock', () => {
  test('mm:ss, floored to whole seconds', () => {
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(999)).toBe('0:00');
    expect(formatClock(1000)).toBe('0:01');
    expect(formatClock(42_000)).toBe('0:42');
    expect(formatClock(42_999)).toBe('0:42');
    expect(formatClock(60_000)).toBe('1:00');
    expect(formatClock(90_000)).toBe('1:30');
    expect(formatClock(3_599_000)).toBe('59:59');
    expect(formatClock(3_600_000)).toBe('60:00');
  });

  test('anything unusable is 0:00', () => {
    expect(formatClock(-5)).toBe('0:00');
    expect(formatClock(Number.NaN)).toBe('0:00');
    expect(formatClock(Number.POSITIVE_INFINITY)).toBe('0:00');
  });
});

describe('mime <-> extension', () => {
  test('matches the server tables', () => {
    expect(PHOTO_MIME_EXT).toEqual({
      'image/jpeg': 'jpg',
      'image/png': 'png',
      'image/webp': 'webp',
      'image/heic': 'heic',
    });
    expect(AUDIO_MIME_EXT).toEqual({
      'audio/mp4': 'm4a',
      'audio/m4a': 'm4a',
      'audio/aac': 'aac',
      'audio/mpeg': 'mp3',
    });
  });

  test('extensionForMime tolerates case, parameters and the jpg alias', () => {
    expect(extensionForMime('image/jpeg')).toBe('jpg');
    expect(extensionForMime('IMAGE/JPG')).toBe('jpg');
    expect(extensionForMime('image/png; charset=binary')).toBe('png');
    expect(extensionForMime('audio/x-m4a')).toBe('m4a');
    expect(extensionForMime('audio/mp3')).toBe('mp3');
    expect(extensionForMime('image/gif')).toBeNull();
    expect(extensionForMime('')).toBeNull();
  });

  test('normalizeMime maps the aliases and leaves the rest alone', () => {
    expect(normalizeMime('image/heif')).toBe('image/heic');
    expect(normalizeMime('image/pjpeg')).toBe('image/jpeg');
    expect(normalizeMime('video/mp4')).toBe('video/mp4');
  });

  test('mimeForUri reads the extension and ignores query strings', () => {
    expect(mimeForUri('file:///a/b/photo.JPG')).toBe('image/jpeg');
    expect(mimeForUri('file:///a/b/photo.heic?x=1#frag')).toBe('image/heic');
    expect(mimeForUri('file:///a/b/rec.m4a')).toBe('audio/m4a');
    expect(mimeForUri('file:///a/b/rec.caf')).toBe('audio/x-caf');
    expect(mimeForUri('content://media/external/images/1')).toBeNull();
    expect(mimeForUri('file:///a.dir/noext')).toBeNull();
  });

  test('photoMime: picker type, then extension, then JPEG', () => {
    expect(photoMime('image/png', 'file:///x.jpg')).toBe('image/png');
    expect(photoMime(null, 'file:///x.webp')).toBe('image/webp');
    expect(photoMime('image/gif', 'file:///x.gif')).toBe('image/jpeg');
    expect(photoMime(undefined, 'content://media/1')).toBe('image/jpeg');
  });

  test('audioMime: the HIGH_QUALITY preset writes .m4a on both platforms', () => {
    expect(audioMime('file:///var/mobile/Containers/rec.m4a')).toBe('audio/m4a');
    expect(audioMime('file:///data/user/0/app/cache/rec.mp4')).toBe('audio/mp4');
    expect(audioMime('file:///rec.caf')).toBeNull();
  });
});

describe('photo room', () => {
  test('photoRoom never goes negative', () => {
    expect(photoRoom(0)).toBe(6);
    expect(photoRoom(4)).toBe(2);
    expect(photoRoom(6)).toBe(0);
    expect(photoRoom(9)).toBe(0);
  });

  test('addPhotos dedupes by uri and caps at six', () => {
    const cur = [photo(1), photo(2)];
    const next = addPhotos(cur, [photo(2), photo(3), photo(4), photo(5), photo(6), photo(7), photo(8)]);
    expect(next.map((p) => p.uri)).toEqual([1, 2, 3, 4, 5, 6].map((i) => `file:///tmp/p${i}.jpg`));
  });

  test('addPhotos returns the same array when nothing was added', () => {
    const cur = [photo(1)];
    expect(addPhotos(cur, [photo(1)])).toBe(cur);
    expect(addPhotos(cur, [])).toBe(cur);
  });
});
