import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';

import { ApiError, type PostMedia } from '../data/api';
import { api } from '../data/client';
import { downscaleTarget, type MediaJob, PHOTO_JPEG_QUALITY, type PhotoJob } from './media';

/**
 * One media upload, the three-step way the server designed it: presign → PUT the bytes
 * straight at object storage → attach the key to the post. The server never proxies
 * bytes, so a failure in step two is between the phone and the bucket and step three
 * never runs — nothing half-attached can appear on a post.
 */

export type UploadState =
  | { phase: 'queued' }
  | { phase: 'uploading' }
  | { phase: 'done'; media: PostMedia }
  | { phase: 'failed'; message: string; unconfigured: boolean };

/** Bytes for a local uri. RN's fetch reads `file://` and `content://` and yields a Blob. */
async function readBlob(uri: string): Promise<Blob> {
  const res = await fetch(uri);
  if (!res.ok) throw new Error(`could not read the file (${res.status})`);
  return res.blob();
}

/**
 * A photo over `PHOTO_LONG_EDGE` becomes a JPEG at that edge and `PHOTO_JPEG_QUALITY`
 * (docs/social.md). The manipulator writes a new file in the cache directory and leaves
 * the original alone, and the job that comes back describes the NEW file — its type,
 * size and dimensions are what the presign signs and what `attach` records. A photo
 * already within the edge is returned as-is, in whatever format the picker handed over.
 */
export async function downscalePhoto(job: PhotoJob): Promise<PhotoJob> {
  const target = downscaleTarget(job.width, job.height);
  if (!target) return job;
  const context = ImageManipulator.manipulate(job.uri);
  try {
    // One edge only: the native side keeps the aspect from the decoded bitmap, which is
    // the truth even when the picker's reported dimensions were swapped by EXIF rotation.
    const longer = job.width >= job.height ? { width: target.width } : { height: target.height };
    const image = await context.resize(longer).renderAsync();
    try {
      const saved = await image.saveAsync({ compress: PHOTO_JPEG_QUALITY, format: SaveFormat.JPEG });
      return {
        ...job,
        uri: saved.uri,
        content_type: 'image/jpeg',
        width: Math.round(saved.width),
        height: Math.round(saved.height),
        oversized: false,
      };
    } finally {
      image.release();
    }
  } finally {
    context.release();
  }
}

export async function uploadJob(postId: string, original: MediaJob): Promise<PostMedia> {
  const job = original.kind === 'photo' && original.oversized ? await downscalePhoto(original) : original;
  const blob = await readBlob(job.uri);
  // The presign wants the byte count up front and signs the content type into the URL,
  // so the PUT must send exactly that type — not whatever the Blob believes it is.
  const presign = await api.presignMedia(postId, {
    kind: job.kind,
    content_type: job.content_type,
    bytes: blob.size,
  });
  const put = await fetch(presign.upload_url, {
    method: presign.method ?? 'PUT',
    headers: { ...(presign.headers ?? {}), 'Content-Type': job.content_type },
    body: blob,
  });
  if (!put.ok) throw new Error(`storage refused the upload (${put.status})`);
  return job.kind === 'photo'
    ? api.attachMedia(postId, {
        object_key: presign.object_key,
        kind: 'photo',
        width: job.width,
        height: job.height,
      })
    : api.attachMedia(postId, {
        object_key: presign.object_key,
        kind: 'audio',
        duration_ms: job.duration_ms,
      });
}

/** True for the presign's "object storage is not set up on this server" answer. */
export function isUnconfigured(e: unknown): boolean {
  return e instanceof ApiError && e.status === 503;
}

/**
 * Run the jobs in order, reporting each state change. Sequential rather than parallel:
 * a phone on cellular gains nothing from six concurrent PUTs, and one presign 503 should
 * stop the run rather than produce six copies of the same alert. After a 503 every
 * remaining job is marked failed-unconfigured without being attempted.
 */
export async function runUploads(
  postId: string,
  jobs: readonly MediaJob[],
  onState: (i: number, state: UploadState) => void
): Promise<void> {
  let unconfigured = false;
  for (let i = 0; i < jobs.length; i++) {
    const job = jobs[i]!;
    if (unconfigured) {
      onState(i, { phase: 'failed', message: 'not configured', unconfigured: true });
      continue;
    }
    onState(i, { phase: 'uploading' });
    try {
      const media = await uploadJob(postId, job);
      onState(i, { phase: 'done', media });
    } catch (e) {
      unconfigured = isUnconfigured(e);
      onState(i, {
        phase: 'failed',
        message: e instanceof Error ? e.message : 'upload failed',
        unconfigured,
      });
    }
  }
}
