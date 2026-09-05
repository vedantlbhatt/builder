/**
 * What the camera may hand us, and what we are allowed to send to the server.
 *
 * A wrong guess burns a rate-limited approval attempt, so every ambiguous input is null.
 */
import { describe, expect, test } from 'bun:test';

import { parsePairingCode } from '../src/pairing/parse';

describe('parsePairingCode', () => {
  test('bare code', () => {
    expect(parsePairingCode('BCDF-GHJK')).toBe('BCDF-GHJK');
    expect(parsePairingCode('  BCDF-GHJK\n')).toBe('BCDF-GHJK');
  });

  test('bare code without the dash is normalised', () => {
    expect(parsePairingCode('BCDFGHJK')).toBe('BCDF-GHJK');
  });

  test('lowercase input is uppercased', () => {
    expect(parsePairingCode('bcdf-ghjk')).toBe('BCDF-GHJK');
    expect(parsePairingCode('https://builder.dev/pair/bcdf-ghjk')).toBe('BCDF-GHJK');
  });

  test('URL with the code as the last path segment', () => {
    expect(parsePairingCode('https://builder.dev/pair/BCDF-GHJK')).toBe('BCDF-GHJK');
    expect(parsePairingCode('https://builder.dev/pair/BCDF-GHJK/')).toBe('BCDF-GHJK');
    expect(parsePairingCode('builder://pair/BCDF-GHJK#x')).toBe('BCDF-GHJK');
  });

  test('URL with ?code=', () => {
    // Exactly what PairCommand.swift puts in the QR.
    expect(parsePairingCode('builder://pair?code=BCDF-GHJK')).toBe('BCDF-GHJK');
    expect(parsePairingCode('https://builder.dev/pair?code=BCDF-GHJK')).toBe('BCDF-GHJK');
    expect(parsePairingCode('https://builder.dev/pair?utm=x&code=bcdf-ghjk&y=1')).toBe('BCDF-GHJK');
    expect(parsePairingCode('https://builder.dev/pair?code=BCDF%2DGHJK')).toBe('BCDF-GHJK');
  });

  test('the query wins over a path segment that happens to look like a code', () => {
    expect(parsePairingCode('https://builder.dev/x/AAAA-BBBB?code=BCDF-GHJK')).toBe('BCDF-GHJK');
  });

  test('junk is null', () => {
    expect(parsePairingCode('')).toBeNull();
    expect(parsePairingCode('hello world')).toBeNull();
    expect(parsePairingCode('BCDF-GHJ')).toBeNull();
    expect(parsePairingCode('BCDF-GHJKL')).toBeNull();
    expect(parsePairingCode('https://example.com/')).toBeNull();
    expect(parsePairingCode('https://example.com/pair?code=nope')).toBeNull();
    expect(parsePairingCode('WIFI:S:home;T:WPA;P:secret;;')).toBeNull();
    // A bare host is not a code, even if it is eight characters.
    expect(parsePairingCode('https://ABCDEFGH')).toBeNull();
  });
});
