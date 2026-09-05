/**
 * Where a tapped notification goes. Pure: no expo, no React, so `__tests__/push.test.ts`
 * can pin every rule in bun. `push.ts` re-exports these beside the hook that uses them.
 */

/**
 * The push kinds the server sends (`server/builder/notify.py`). Both open the recap: a
 * finished session and a finished agent run are the same moment — "look at what
 * happened" — and the recap reads the headline off the session itself.
 */
export const RECAP_KINDS: ReadonlySet<string> = new Set(['session_finished', 'agent_run_finished']);

/** The route a recap opens on. `[id].tsx` reads `recap=1` and raises the sheet. */
export type SessionRoute = `/session/${string}` | `/session/${string}?recap=1`;

/**
 * Where a notification's data points, or null when it points nowhere.
 *
 * Pure so bun can pin it. The rules: the id comes from `session_id`, or from the recap
 * `url` the server also sends, or from the older `session` key that earlier builds of the
 * server wrote; without an id there is nothing to open. A known finish kind — or no kind
 * at all, because a payload without one only ever meant "finished" — opens the recap. Any
 * other explicit kind opens the plain detail, so a future push class never lands a
 * person in a compose sheet it did not ask for.
 */
export function routeForNotification(data: unknown): SessionRoute | null {
  if (typeof data !== 'object' || data === null) return null;
  const d = data as Record<string, unknown>;
  const id = sessionIdFrom(d);
  if (!id) return null;
  const kind = typeof d.kind === 'string' ? d.kind : null;
  const recap = kind === null || RECAP_KINDS.has(kind);
  return recap ? `/session/${id}?recap=1` : `/session/${id}`;
}

function sessionIdFrom(d: Record<string, unknown>): string | null {
  for (const key of ['session_id', 'session'] as const) {
    const v = d[key];
    if (typeof v === 'string' && isSafeId(v)) return v;
  }
  if (typeof d.url === 'string') return sessionIdFromUrl(d.url);
  return null;
}

/**
 * `builder://session/<id>?recap=1` → `<id>`. Only the one path shape the server builds
 * (`notify.recap_url`); anything else is not an id and returns null.
 */
export function sessionIdFromUrl(url: string): string | null {
  const m = /^[a-z][a-z0-9+.-]*:\/{2,3}session\/([^/?#]+)/i.exec(url.trim());
  const id = m?.[1] ? safeDecode(m[1]) : null;
  return id && isSafeId(id) ? id : null;
}

function safeDecode(s: string): string | null {
  try {
    return decodeURIComponent(s);
  } catch {
    return null;
  }
}

/** An id is what goes into a route segment: no slashes, no query syntax, not empty. */
function isSafeId(s: string): boolean {
  return s.length > 0 && s.length <= 128 && !/[/?#\s]/.test(s);
}

/**
 * The data object on a notification response, wherever this platform put it.
 *
 * expo-notifications on iOS hands a REMOTE notification's `userInfo["body"]` to JS as
 * `content.data` (EXNotificationSerializer.m), because that is where the Expo push service
 * nests custom data. The server sends raw APNs with `data` at the top level of userInfo,
 * so on iOS `content.data` is null for our own pushes and the whole userInfo — `aps`,
 * `session`, `unattended`, `data` — is on `trigger.payload`. Read the three places in
 * order of specificity; a local or Expo-service notification still resolves from the
 * first.
 */
export function dataFromResponse(response: unknown): unknown {
  const r = response as {
    notification?: {
      request?: {
        content?: { data?: unknown };
        trigger?: { payload?: unknown; remoteMessage?: { data?: unknown } } | null;
      };
    };
  } | null;
  const req = r?.notification?.request;
  if (!req) return null;
  const candidates: unknown[] = [
    req.content?.data,
    (req.trigger?.payload as { data?: unknown } | undefined)?.data,
    req.trigger?.payload,
    req.trigger?.remoteMessage?.data,
  ];
  for (const c of candidates) {
    if (routeForNotification(c) !== null) return c;
  }
  return req.content?.data ?? null;
}

