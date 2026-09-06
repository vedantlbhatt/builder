/**
 * Where a build talks to, decided at CONFIG time.
 *
 * The default is a localhost the phone can never reach. That is correct for a simulator
 * and catastrophic in a store build: the app installs, opens, and fails every request
 * with a network error, because nothing about it is wrong except the address. Nobody
 * would guess that from the symptom, and a review rejection is the cheap outcome.
 *
 * So an EAS build without `BUILDER_API_URL` fails HERE, before anything is compiled,
 * with the fix in the message. `EAS_BUILD` is set by every EAS build and by nothing else,
 * so a local `expo start` or `expo run:ios` keeps the localhost default untouched.
 *
 * Lives in its own module rather than inside `app.config.ts` so `bun test` can exercise
 * it: a guard that has never been run is a guard nobody should trust.
 */

export const LOCAL_DEFAULT = 'http://localhost:8000';

/**
 * An environment. The index signature is what makes node's `ProcessEnv` assignable
 * without a cast: the three names below are the ones this rule reads, and everything else
 * in the environment is simply along for the ride.
 */
export interface BuildEnv {
  readonly BUILDER_API_URL?: string;
  readonly EAS_BUILD?: string;
  readonly EAS_BUILD_PROFILE?: string;
  readonly [key: string]: string | undefined;
}

export function resolveApiBaseUrl(env: BuildEnv): string {
  const url = env.BUILDER_API_URL;
  if (url) return url;
  if (env.EAS_BUILD === 'true') throw new Error(missingUrlMessage(env.EAS_BUILD_PROFILE));
  return LOCAL_DEFAULT;
}

export function missingUrlMessage(profile: string | undefined): string {
  const name = profile ?? 'unknown';
  return (
    `BUILDER_API_URL is not set for the "${name}" EAS build. A build without it ships ` +
    `pointing at ${LOCAL_DEFAULT} and fails every request on a real phone with no error a ` +
    'person could act on. Set it as an EAS secret: ' +
    'eas secret:create --name BUILDER_API_URL --value https://<your-server>'
  );
}
