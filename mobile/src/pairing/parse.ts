/**
 * Read a pairing code out of whatever the camera saw.
 *
 * The Mac agent shows a user code as `XXXX-XXXX` and, next to it, a QR that may encode
 * either the bare code or a URL carrying it (`.../pair/XXXX-XXXX` or `...?code=XXXX-XXXX`).
 * Anything else is null — an unrecognised scan must never be sent to the server as a
 * guess, because a wrong guess burns a rate-limited approval attempt.
 */

const CODE = /^([A-Z0-9]{4})-?([A-Z0-9]{4})$/;

export function parsePairingCode(text: string): string | null {
  const raw = text.trim();
  if (!raw) return null;

  const bare = matchCode(raw);
  if (bare) return bare;

  // Not a bare code; try it as a URL. No URL class: React Native's differs from the web's
  // in exactly the accessors this needs, and the shape here is simple enough to split.
  const [beforeFragment = ''] = raw.split('#');
  const [pathPart = '', queryPart = ''] = beforeFragment.split('?');

  for (const pair of queryPart.split('&')) {
    const eq = pair.indexOf('=');
    if (eq < 0) continue;
    if (safeDecode(pair.slice(0, eq)) !== 'code') continue;
    const value = matchCode(safeDecode(pair.slice(eq + 1)));
    if (value) return value;
  }

  // Drop the scheme and host: `https://ABCDEFGH` is a host, not a code, however much it
  // looks like one. Only what follows the first slash after the authority is a path.
  const schemeAt = pathPart.indexOf('://');
  const afterAuthority =
    schemeAt >= 0 ? pathPart.slice(schemeAt + 3).split('/').slice(1).join('/') : pathPart;
  const segments = afterAuthority.split('/').filter((s) => s.length > 0);
  const last = segments[segments.length - 1];
  if (last !== undefined) {
    const value = matchCode(safeDecode(last));
    if (value) return value;
  }
  return null;
}

function matchCode(s: string): string | null {
  const m = CODE.exec(s.trim().toUpperCase());
  return m ? `${m[1]}-${m[2]}` : null;
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s.replace(/\+/g, ' '));
  } catch {
    return s;
  }
}
