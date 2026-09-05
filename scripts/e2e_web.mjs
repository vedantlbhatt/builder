#!/usr/bin/env node
/**
 * Drive the web build of the phone app with Playwright and screenshot every screen.
 *
 * What it needs running (see the task notes in scripts/e2e_mint_device.py for the server side):
 *   - the API on $E2E_API_URL (default http://127.0.0.1:8787)
 *   - `bunx expo start --web --offline --port 8081` with BUILDER_API_URL pointing at it
 *   - a device token file (`scripts/e2e_mint_device.py` output) at $E2E_TOKENS
 *
 * Sign-in on web is by injection: the web build's SecureStore stand-in reads localStorage
 * (`mobile/src/web/secureStore.web.ts`), so `addInitScript` sets `builder.access` and
 * `builder.refresh` before the first script runs and `Api.loadTokens` finds them. No auth
 * rule is weakened: the tokens came out of the real device flow, and if the access token
 * has expired the app refreshes it exactly as it would on a phone.
 *
 * REFRESH TOKENS ROTATE. A refresh burns the injected pair, and a second run that injected
 * the same pair would be "reuse" — the server revokes the device. So at the end of a run the
 * rotated pair is read back out of localStorage and written over the token file; never copy
 * that file into a second browser context.
 *
 *   PLAYWRIGHT_DIR=<dir with node_modules/playwright> E2E_TOKENS=web-device.json \
 *   E2E_SESSION_ID=<uuid> E2E_POST_ID=<uuid> E2E_FACTION_SLUG=night-shift \
 *   node scripts/e2e_web.mjs --out shots/ [--only 01,02]
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const requireFrom = createRequire(
  process.env.PLAYWRIGHT_DIR ? path.join(process.env.PLAYWRIGHT_DIR, 'package.json') : import.meta.url
);
const { chromium } = requireFrom('playwright');

const args = process.argv.slice(2);
const arg = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const OUT = path.resolve(arg('--out', 'shots'));
const ONLY = (arg('--only', '') || '').split(',').filter(Boolean);
const APP = process.env.E2E_APP_URL ?? 'http://127.0.0.1:8081';
const TOKENS = process.env.E2E_TOKENS;
const SESSION_ID = process.env.E2E_SESSION_ID;
const POST_ID = process.env.E2E_POST_ID;
const FACTION_SLUG = process.env.E2E_FACTION_SLUG ?? '';
const HEADLINE = process.env.E2E_ANALYSIS_HEADLINE ?? '';

if (!TOKENS) throw new Error('set E2E_TOKENS to the device token json');
const tokens = JSON.parse(fs.readFileSync(TOKENS, 'utf8'));
fs.mkdirSync(OUT, { recursive: true });

const consoleErrors = [];
const shots = [];

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  colorScheme: 'dark',
  permissions: ['camera'],
});
await context.addInitScript(
  ({ access, refresh }) => {
    try {
      localStorage.setItem('builder.access', access);
      localStorage.setItem('builder.refresh', refresh);
    } catch {}
  },
  { access: tokens.access_token, refresh: tokens.refresh_token }
);

const page = await context.newPage();
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') {
    const text = m.text();
    // Metro's own dev-server chatter is not the app's.
    if (/Download the React DevTools|source map/i.test(text)) return;
    consoleErrors.push(`[${m.type()}] ${text.slice(0, 300)}`);
  }
});
page.on('pageerror', (e) => consoleErrors.push(`[pageerror] ${String(e).slice(0, 300)}`));

const wants = (id) => ONLY.length === 0 || ONLY.includes(id);

async function settle() {
  await page.waitForLoadState('networkidle', { timeout: 60_000 }).catch(() => {});
}

/** Wait for a visible text (string or regex); on timeout fall through and shoot what is there. */
async function seen(text, timeout = 30_000) {
  try {
    await page.getByText(text, { exact: false }).first().waitFor({ state: 'visible', timeout });
    return true;
  } catch {
    consoleErrors.push(`[wait] did not see ${String(text)} on ${page.url()}`);
    return false;
  }
}

async function shoot(id, file, note, opts = {}) {
  if (!wants(id)) return;
  const out = path.join(OUT, file);
  await page.screenshot({ path: out, fullPage: false, ...opts });
  shots.push(`${file}  ${note}`);
  console.log(`  shot ${file}`);
}

async function go(route) {
  await page.goto(`${APP}${route}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  await settle();
}

try {
  // 01 — the sessions list, live block on top.
  if (wants('01') || wants('09')) {
    await go('/');
    await seen('LIVE NOW');
    await seen('private repo');
    // Let the sync's second pass (details) land and the sprite animate a frame.
    await settle();
    await shoot('01', '01-sessions.png', 'Sessions list: LIVE NOW block on top, then the notable finals with their strips');
    // Bit is on this screen as the live rows' sprite; also on every empty state.
    await shoot('09', '09-mascot.png', 'Bit (the pixel mascot) beside the live sessions');
  }

  // 02 — session detail and its analysis.
  if (SESSION_ID && (wants('02') || wants('02b') || wants('10'))) {
    await go(`/session/${SESSION_ID}`);
    await seen('Timeline');
    await seen('Numbers');
    await shoot('02', '02-session-detail.png', 'Session detail: recap card, share button, timeline strip and numbers');
    // The recap card is the first child of the scroll view; a clip of the top is the card.
    await shoot('10', '10-share-card.png', 'The recap (share) card as rendered on the detail screen', {
      clip: { x: 0, y: 0, width: 390, height: 420 },
    });
    const anchor = HEADLINE || 'Analysis';
    const el = page.getByText(anchor, { exact: false }).first();
    const found = await seen(anchor, 20_000);
    if (found) await el.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(400);
    await shoot('02b', '02b-session-analysis.png', found && HEADLINE ? `Detail scrolled to the analysis (headline: ${HEADLINE})` : 'Detail scrolled to the Analysis section');
  }

  // 03 — profile.
  if (wants('03')) {
    await go('/profile');
    await seen('How you build');
    await shoot('03', '03-profile.png', 'Profile: totals, contribution grid, "How you build" card');
  }

  // 04 — feed.
  if (wants('04')) {
    await go('/feed');
    await seen(/Wired the cloud capture|Follow a builder/);
    await shoot('04', '04-feed.png', 'Feed: the one public post with its strip');
  }

  // 05 — post.
  if (POST_ID && wants('05')) {
    await go(`/post/${POST_ID}`);
    await seen('COMMENTS');
    await shoot('05', '05-post.png', 'Post: caption, session card, kudos, comments');
  }

  // 06 — factions.
  if (wants('06')) {
    await go('/factions');
    await seen(/CREATE|JOIN BY CODE/);
    await shoot('06', '06-factions.png', 'Factions: membership, create and join-by-code');
  }

  // 07 — settings.
  if (wants('07')) {
    await go('/settings');
    await seen(/Scan code|vedant|Sign/);
    await shoot('07', '07-settings.png', 'Settings: account, devices, repo visibility, sign-out');
  }

  // 08 — pair (camera). Headless Chromium has no camera; the screen shows what it shows.
  if (wants('08')) {
    await go('/pair');
    await page.waitForTimeout(1500);
    await shoot('08', '08-pair.png', 'Connect your Mac: pairing screen (no camera device in headless Chromium)');
  }

  // 09 fallback — an empty faction feed shows Bit waving.
  if (wants('09') && FACTION_SLUG) {
    await go(`/feed?slug=${encodeURIComponent(FACTION_SLUG)}`);
    if (await seen('Nothing shared in this faction yet')) {
      await shoot('09', '09-mascot.png', 'Bit waving on the empty faction feed');
    }
  }
} finally {
  // Persist the rotated pair (see the header). Read from the page's origin.
  try {
    const rotated = await page.evaluate(() => ({
      access_token: localStorage.getItem('builder.access'),
      refresh_token: localStorage.getItem('builder.refresh'),
    }));
    if (rotated.access_token && rotated.refresh_token) {
      const changed = rotated.refresh_token !== tokens.refresh_token;
      fs.writeFileSync(TOKENS, JSON.stringify({ ...tokens, ...rotated }, null, 1) + '\n');
      if (changed) console.log('  refresh token rotated during the run; token file updated');
    } else {
      console.log('  WARNING: no tokens in localStorage at exit (signed out?) — mint a new device before the next run');
    }
  } catch (e) {
    console.log(`  could not read back tokens: ${e}`);
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'console-errors.txt'), consoleErrors.join('\n') + '\n');
  fs.writeFileSync(path.join(OUT, 'shots.txt'), shots.join('\n') + '\n');
  console.log(`\n${shots.length} shot(s) in ${OUT}`);
  console.log(`${consoleErrors.length} console error/warning line(s) -> ${path.join(OUT, 'console-errors.txt')}`);
}
