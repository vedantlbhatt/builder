/**
 * Decodes the 1024-byte strip the server sends.
 *
 * This file has one job: agree with `StripSpec.swift` exactly. Both read the ordinals
 * from `spec/strip.v1.json` through generated code, both resample with the same
 * nearest-neighbour rule, and `__tests__/strip.test.ts` decodes the same golden fixtures
 * the Swift suite does.
 *
 * That discipline exists because of a specific failure. Two independent designs of this
 * format had classes 1 and 2 swapped. A renderer fed the other spec's ordinals paints
 * every agent run in the prompt colour and produces a plausible-looking strip showing a
 * human who typed for three hours — it does not crash, it does not look empty, and it
 * survives code review. With a Swift renderer on the Mac and this one on the phone, that
 * risk exists twice over, and only a decoded-value assertion catches it.
 */

import { COLUMNS, StripClass, StripMarkKind, resample } from '../generated/strip';

export interface Column {
  klass: StripClass;
  density: number;
}

export interface Mark {
  ms: number;
  kind: StripMarkKind;
}

/** bits 0-1 class, bits 2-3 density, bits 4-7 reserved and MUST be zero. */
export function unpackByte(byte: number): Column {
  return { klass: (byte & 0b11) as StripClass, density: (byte >> 2) & 0b11 };
}

export class StripDecodeError extends Error {}

/**
 * base64 -> bytes.
 *
 * `Buffer` is not available in the React Native runtime, and `atob` mishandles anything
 * outside latin1 — but base64 is ASCII by definition, so `atob` plus charCodeAt is exact
 * here and avoids a polyfill dependency in the hot path.
 */
export function decodeBase64(b64: string): Uint8Array {
  const binary = globalThis.atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export function decodeStrip(b64: string): Uint8Array {
  const bytes = decodeBase64(b64);
  if (bytes.length !== COLUMNS) {
    throw new StripDecodeError(`expected ${COLUMNS} bytes, got ${bytes.length}`);
  }
  for (let i = 0; i < bytes.length; i++) {
    if ((bytes[i] ?? 0) >> 4) {
      // Reserved bits are the only expansion room the format has. A non-zero one means
      // a newer client is writing a field this build does not understand, and rendering
      // it as though nothing were wrong would silently misreport the session.
      throw new StripDecodeError(`reserved bits set at column ${i}`);
    }
  }
  return bytes;
}

export function decodeColumns(b64: string): Column[] {
  return Array.from(decodeStrip(b64), unpackByte);
}

/** Marks arrive as `[[ms, kind], ...]` — compact, and stable across languages. */
export function decodeMarks(raw: unknown): Mark[] {
  if (!Array.isArray(raw)) return [];
  const out: Mark[] = [];
  for (const entry of raw) {
    if (!Array.isArray(entry) || entry.length !== 2) continue;
    const [ms, kind] = entry;
    if (typeof ms !== 'number' || typeof kind !== 'number') continue;
    out.push({ ms, kind: kind as StripMarkKind });
  }
  return out.sort((a, b) => a.ms - b.ms);
}

/**
 * Resample to a render width. Delegates to the generated function so there is exactly
 * one implementation of the rule, including its tie-break.
 */
export function resampleColumns(bytes: Uint8Array, width: number): Column[] {
  return resample(bytes, width).map(unpackByte);
}

/**
 * Collapse marks that would land within `minPx` of one another.
 *
 * A prompt outranks a compaction marker on the same pixel: one is the person, the other
 * is bookkeeping.
 */
export function layoutMarks(
  marks: Mark[],
  spanMs: number,
  width: number,
  minPx: number
): { x: number; kind: StripMarkKind }[] {
  if (spanMs <= 0 || width <= 0) return [];
  const out: { x: number; kind: StripMarkKind }[] = [];
  for (const m of marks) {
    const x = (m.ms / spanMs) * width;
    const last = out[out.length - 1];
    if (last && x - last.x < minPx) {
      if (m.kind === StripMarkKind.prompt) last.kind = m.kind;
      continue;
    }
    out.push({ x, kind: m.kind });
  }
  return out;
}

/** Share of each class, for the accessibility label and the session detail breakdown. */
export function classShare(columns: Column[]): Record<StripClass, number> {
  const counts: Record<number, number> = { 0: 0, 1: 0, 2: 0, 3: 0 };
  for (const c of columns) counts[c.klass] = (counts[c.klass] ?? 0) + 1;
  const total = Math.max(columns.length, 1);
  return {
    [StripClass.idle]: (counts[StripClass.idle] ?? 0) / total,
    [StripClass.prompting]: (counts[StripClass.prompting] ?? 0) / total,
    [StripClass.agent]: (counts[StripClass.agent] ?? 0) / total,
    [StripClass.human_edit]: (counts[StripClass.human_edit] ?? 0) / total,
  } as Record<StripClass, number>;
}
