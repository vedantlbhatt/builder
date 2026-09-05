/**
 * Pure rules for post media: what may travel with a post, in what shape, and how the
 * phone names it. Nothing here touches the network, React, or a native module, so
 * `__tests__/media.test.ts` can pin the limits down in bun.
 *
 * The limits mirror `server/builder/routes/social.py` (`MAX_PHOTOS`, `MAX_AUDIO_MS`,
 * `PHOTO_TYPES`, `AUDIO_TYPES`). The server enforces them; the phone repeats them so that
 * a person hears "six photos" before the seventh upload, not after a 409.
 */

export const MAX_PHOTOS = 6;
export const MAX_AUDIO_MS = 90_000;

/**
 * The long edge docs/social.md asks the phone to downscale to, and the JPEG quality it
 * re-encodes at. The plan flags the photos over the edge (`PhotoJob.oversized`) and
 * `upload.ts` shrinks exactly those before the presign, so the byte count it signs is
 * the byte count it sends.
 */
export const PHOTO_LONG_EDGE = 2048;
export const PHOTO_JPEG_QUALITY = 0.85;

/**
 * The size a photo is resized to so its long edge is `longEdge`, or null when it already
 * fits. Aspect is kept; the short edge rounds to a whole pixel and never below 1, so a
 * 20000×1 banner still has a height.
 */
export function downscaleTarget(
  width: number,
  height: number,
  longEdge: number = PHOTO_LONG_EDGE
): { width: number; height: number } | null {
  const long = Math.max(width, height);
  if (!(long > longEdge)) return null;
  const scale = longEdge / long;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** Content types the server will presign, with the extension the object key receives. */
export const PHOTO_MIME_EXT: Readonly<Record<string, string>> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
  'image/heic': 'heic',
};

export const AUDIO_MIME_EXT: Readonly<Record<string, string>> = {
  'audio/mp4': 'm4a',
  'audio/m4a': 'm4a',
  'audio/aac': 'aac',
  'audio/mpeg': 'mp3',
};

/** The extension the server would stamp on an object for this mime, or null if unsigned. */
export function extensionForMime(mime: string): string | null {
  const key = normalizeMime(mime);
  return PHOTO_MIME_EXT[key] ?? AUDIO_MIME_EXT[key] ?? null;
}

/**
 * `image/jpg` is not a registered type but pickers emit it; parameters (`; charset`) and
 * case are noise. Anything the server does not list stays as-is so the caller can say so.
 */
export function normalizeMime(mime: string): string {
  const base = mime.split(';')[0]?.trim().toLowerCase() ?? '';
  if (base === 'image/jpg' || base === 'image/pjpeg') return 'image/jpeg';
  if (base === 'image/heif') return 'image/heic';
  if (base === 'audio/x-m4a') return 'audio/m4a';
  if (base === 'audio/mp3') return 'audio/mpeg';
  return base;
}

const EXT_MIME: Readonly<Record<string, string>> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  heic: 'image/heic',
  heif: 'image/heic',
  m4a: 'audio/m4a',
  mp4: 'audio/mp4',
  aac: 'audio/aac',
  mp3: 'audio/mpeg',
  caf: 'audio/x-caf',
};

/**
 * The mime a file's extension implies, or null when the extension is unknown. Query
 * strings and fragments are stripped first; `content://` uris without an extension give
 * null and the caller falls back to whatever the picker reported.
 */
export function mimeForUri(uri: string): string | null {
  const path = uri.split(/[?#]/)[0] ?? '';
  const dot = path.lastIndexOf('.');
  const slash = path.lastIndexOf('/');
  if (dot < 0 || dot < slash) return null;
  return EXT_MIME[path.slice(dot + 1).toLowerCase()] ?? null;
}

/**
 * The type a photo uploads as: the picker's own report when the server signs it, else
 * the extension, else JPEG — the picker re-encodes anything it compresses as JPEG and
 * `quality` is always set, so JPEG is the right guess for an unlabeled asset.
 */
export function photoMime(reported: string | null | undefined, uri: string): string {
  const fromPicker = reported ? normalizeMime(reported) : '';
  if (fromPicker in PHOTO_MIME_EXT) return fromPicker;
  const fromUri = mimeForUri(uri);
  if (fromUri && fromUri in PHOTO_MIME_EXT) return fromUri;
  return 'image/jpeg';
}

/**
 * The type a recording uploads as. `RecordingOptionsPresets.HIGH_QUALITY` writes `.m4a`
 * on both platforms, which the server signs as `audio/m4a`; anything else it does not
 * sign (a `.caf` from the LOW_QUALITY preset, for instance) comes back null so the caller
 * refuses before the presign 422s.
 */
export function audioMime(uri: string): string | null {
  const m = mimeForUri(uri);
  return m && m in AUDIO_MIME_EXT ? m : null;
}

/** "0:42", "1:30". Whole seconds, floored; anything unusable is "0:00". */
/** "Couldn't play" — what the chip says, briefly, when playback fails. */
export const AUDIO_FAILED_CAPTION = "Couldn't play";

/** How long the failure caption stays before the chip reads as playable again. */
export const AUDIO_FAILED_CAPTION_MS = 4000;

/**
 * expo-av reports a playback failure AFTER a successful load as `{ isLoaded: false,
 * error }` — the same `isLoaded: false` shape as "not loaded yet", with the error string
 * attached. A status handler that returns early on `!isLoaded` never sees it, so the chip
 * would stay in its playing state with nothing playing. Structural, so bun can pin it.
 */
export function isPlaybackError(st: { isLoaded: boolean; error?: string }): boolean {
  return !st.isLoaded && typeof st.error === 'string';
}

/**
 * The chip's text. Playing or paused mid-note shows "position / total"; at rest it shows
 * the note's length. The glyph is the STOP square while playing and the PLAY triangle
 * otherwise — after a failure too, because the next tap is a fresh play.
 */
export function audioChipLabel(a: { playing: boolean; positionMs: number; totalMs: number }): string {
  const midway = a.playing || a.positionMs > 0;
  const shown = midway ? a.positionMs : a.totalMs;
  return `${a.playing ? '■' : '▶'} ${formatClock(shown)}${midway ? ` / ${formatClock(a.totalMs)}` : ''}`;
}

export function formatClock(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0:00';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

// ---------------------------------------------------------------------- the plan

export interface PickedPhoto {
  uri: string;
  width: number;
  height: number;
  /** As the picker reported it; may be absent or a type the server does not sign. */
  mime?: string | null;
  bytes?: number | null;
}

export interface RecordedAudio {
  uri: string;
  durationMs: number;
  /** Resolved from the uri when absent. */
  mime?: string | null;
}

export interface PhotoJob {
  kind: 'photo';
  /** Index into the photos array the plan was made from; the UI keys progress on it. */
  index: number;
  uri: string;
  content_type: string;
  width: number;
  height: number;
  /**
   * Long edge over `PHOTO_LONG_EDGE`: `upload.ts` downscales it to a JPEG before the
   * presign. The server would accept the original (12 MB ceiling); the phone does not
   * make it. Content type, width and height above describe the ORIGINAL until then.
   */
  oversized: boolean;
}

export interface AudioJob {
  kind: 'audio';
  index: 0;
  uri: string;
  content_type: string;
  duration_ms: number;
}

export type MediaJob = PhotoJob | AudioJob;

export class MediaPlanError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MediaPlanError';
  }
}

/**
 * The uploads a post needs, photos first in the order chosen, then the voice note.
 *
 * Refuses more than `MAX_PHOTOS`, a note over `MAX_AUDIO_MS`, a photo without both
 * dimensions (attach requires them), and a recording in a type the server will not sign.
 * A note of exactly 90 000 ms is accepted: the recorder auto-stops at the cap and clamps,
 * so that is the number a full-length note arrives with.
 */
export function mediaPlan(photos: readonly PickedPhoto[], audio: RecordedAudio | null): MediaJob[] {
  if (photos.length > MAX_PHOTOS) {
    throw new MediaPlanError(`a post carries at most ${MAX_PHOTOS} photos`);
  }
  const jobs: MediaJob[] = photos.map((p, index) => {
    if (!(p.width > 0) || !(p.height > 0)) {
      throw new MediaPlanError('a photo needs its width and height');
    }
    return {
      kind: 'photo',
      index,
      uri: p.uri,
      content_type: photoMime(p.mime, p.uri),
      width: Math.round(p.width),
      height: Math.round(p.height),
      oversized: Math.max(p.width, p.height) > PHOTO_LONG_EDGE,
    };
  });
  if (audio) {
    if (!(audio.durationMs > 0)) throw new MediaPlanError('the voice note is empty');
    if (audio.durationMs > MAX_AUDIO_MS) {
      throw new MediaPlanError(`a voice note is at most ${formatClock(MAX_AUDIO_MS)}`);
    }
    const content_type = audio.mime ? normalizeMime(audio.mime) : audioMime(audio.uri);
    if (!content_type || !(content_type in AUDIO_MIME_EXT)) {
      throw new MediaPlanError('this recording is in a format the server does not accept');
    }
    jobs.push({
      kind: 'audio',
      index: 0,
      uri: audio.uri,
      content_type,
      duration_ms: Math.round(audio.durationMs),
    });
  }
  return jobs;
}

/**
 * How many more photos the picker may offer, never negative. `cap` is `MAX_PHOTOS` for a
 * new post and the remaining room when adding to one that already has photos.
 */
export function photoRoom(current: number, cap: number = MAX_PHOTOS): number {
  return Math.max(0, Math.min(cap, MAX_PHOTOS) - current);
}

/**
 * Merge a picker result into the current list: skip uris already chosen, cap at
 * `MAX_PHOTOS` (or a smaller `cap`). Returns the same array when nothing changed so
 * React state stays put.
 */
export function addPhotos<T extends { uri: string }>(
  current: readonly T[],
  picked: readonly T[],
  cap: number = MAX_PHOTOS
): T[] {
  const limit = Math.min(cap, MAX_PHOTOS);
  const seen = new Set(current.map((p) => p.uri));
  const next = [...current];
  for (const p of picked) {
    if (next.length >= limit) break;
    if (seen.has(p.uri)) continue;
    seen.add(p.uri);
    next.push(p);
  }
  return next.length === current.length ? (current as T[]) : next;
}
