/**
 * The one build-time rule that decides whether a shipped app can reach anything.
 *
 * `app.config.ts` calls this before Expo writes a single file. A build that gets it wrong
 * does not crash: it installs, opens, and fails every request against a localhost the
 * phone cannot reach, which is a symptom nobody would trace back to a missing variable.
 */
import { describe, expect, test } from 'bun:test';

import { LOCAL_DEFAULT, missingUrlMessage, resolveApiBaseUrl } from '../src/config/apiBaseUrl';

describe('resolveApiBaseUrl', () => {
  test('an explicit URL always wins', () => {
    expect(resolveApiBaseUrl({ BUILDER_API_URL: 'https://api.example' })).toBe('https://api.example');
    expect(
      resolveApiBaseUrl({
        BUILDER_API_URL: 'https://api.example',
        EAS_BUILD: 'true',
        EAS_BUILD_PROFILE: 'production',
      })
    ).toBe('https://api.example');
  });

  test('local development keeps the localhost default', () => {
    // No EAS_BUILD: a simulator, a dev client, `expo start`. localhost is correct here and
    // failing the build would make the repo unusable.
    expect(resolveApiBaseUrl({})).toBe(LOCAL_DEFAULT);
    expect(resolveApiBaseUrl({ EAS_BUILD_PROFILE: 'production' })).toBe(LOCAL_DEFAULT);
  });

  test('an EAS build with no URL fails, and says how to fix it', () => {
    expect(() => resolveApiBaseUrl({ EAS_BUILD: 'true', EAS_BUILD_PROFILE: 'production' })).toThrow(
      /BUILDER_API_URL is not set for the "production" EAS build/
    );
    // Every profile, not just production: a preview build handed to a tester is just as
    // useless pointed at their laptop's localhost.
    expect(() => resolveApiBaseUrl({ EAS_BUILD: 'true', EAS_BUILD_PROFILE: 'preview' })).toThrow(
      /"preview"/
    );
    expect(() => resolveApiBaseUrl({ EAS_BUILD: 'true' })).toThrow(/"unknown"/);
  });

  test('the message names the command that fixes it', () => {
    // A build failure that does not say what to type is a build failure somebody works
    // around by deleting the check.
    expect(missingUrlMessage('production')).toContain('eas secret:create --name BUILDER_API_URL');
    expect(missingUrlMessage('production')).toContain(LOCAL_DEFAULT);
  });

  test('an empty string is not a URL', () => {
    // `env: { "BUILDER_API_URL": "" }` in eas.json is the shape of this mistake.
    expect(() => resolveApiBaseUrl({ BUILDER_API_URL: '', EAS_BUILD: 'true' })).toThrow();
    expect(resolveApiBaseUrl({ BUILDER_API_URL: '' })).toBe(LOCAL_DEFAULT);
  });
});
