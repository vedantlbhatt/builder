import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';

import { PRESIGN_UNCONFIGURED_MESSAGE, type FeedItem } from '../data/api';
import { api } from '../data/client';
import { allDone, initialRows, retryableIndices, withRowState, type UploadRow } from './composeFlow';
import type { MediaJob } from './media';
import { runUploads, type UploadState } from './upload';

/**
 * The upload phase of posting, shared by the compose sheet and the recap.
 *
 * The post row exists before this starts — the post is real the moment the server
 * answers, media or not — and this runs the plan against it one object at a time. A
 * failed line can be retried; a presign 503 (object storage not configured on this
 * server) is said once, in plain words, and the post stands without its media rather
 * than being rolled back. `onComplete` fires once every line has landed; anything less
 * waits for a tap so the failures are read rather than flashed.
 */
export function useUploadFlow(onComplete: (post: FeedItem) => void) {
  const [post, setPost] = useState<FeedItem | null>(null);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [busy, setBusy] = useState(false);
  const finishing = useRef(false);
  const saidUnconfigured = useRef(false);
  const rowsRef = useRef<UploadRow[]>([]);
  rowsRef.current = rows;

  const setRow = useCallback((i: number, state: UploadState) => {
    setRows((prev) => withRowState(prev, i, state));
  }, []);

  const run = useCallback(
    async (target: FeedItem, indices: number[], all: readonly UploadRow[]) => {
      setBusy(true);
      try {
        const jobs = indices.map((i) => all[i]!.job);
        await runUploads(target.id, jobs, (k, state) => {
          setRow(indices[k]!, state);
          if (state.phase === 'failed' && state.unconfigured && !saidUnconfigured.current) {
            saidUnconfigured.current = true;
            Alert.alert('Posted without media', PRESIGN_UNCONFIGURED_MESSAGE);
          }
        });
      } finally {
        setBusy(false);
      }
    },
    [setRow]
  );

  /** Begin uploading `jobs` against `target`. With no jobs, completes at once. */
  const start = useCallback(
    (target: FeedItem, jobs: readonly MediaJob[]) => {
      if (jobs.length === 0) {
        onComplete(target);
        return;
      }
      const all = initialRows(jobs);
      setPost(target);
      setRows(all);
      void run(
        target,
        all.map((_, i) => i),
        all
      );
    },
    [onComplete, run]
  );

  const retry = useCallback(() => {
    if (!post || busy) return;
    const failed = retryableIndices(rowsRef.current);
    if (failed.length === 0) return;
    void run(post, failed, rowsRef.current);
  }, [post, busy, run]);

  /** Done, or Done-with-failures: re-read so the caller's row carries the media urls. */
  const finish = useCallback(async () => {
    // Once. After the last row lands `busy` is false while the re-read below is in
    // flight, so the Done button is live for that window; a tap there must not call
    // onComplete (and route) a second time. Found by review.
    if (!post || finishing.current) return;
    finishing.current = true;
    let final = post;
    try {
      final = await api.post(post.id);
    } catch {
      // The post exists either way; the feed will show the media on its next refresh.
    }
    onComplete(final);
  }, [post, onComplete]);

  /** Back to the form. A sheet that stays mounted between openings calls this on open. */
  const reset = useCallback(() => {
    finishing.current = false;
    setPost(null);
    setRows([]);
    setBusy(false);
    saidUnconfigured.current = false;
  }, []);

  const uploading = post !== null;
  const complete = uploading && rows.length > 0 && allDone(rows);
  const retryable = retryableIndices(rows).length > 0;

  useEffect(() => {
    if (complete && !busy) void finish();
    // finish is stable for a given post; re-running on rows would double the read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [complete, busy]);

  return { uploading, rows, busy, retryable, start, retry, finish, reset };
}
