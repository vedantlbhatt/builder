#!/usr/bin/env node
/**
 * Record Bit moving on the web build, frame by frame.
 *
 * The `Animated` runtime is not unit-testable under bun, so this is the visual proof for
 * `mobile/src/pixel/motion.ts`: a burst of screenshots at a fixed cadence, clipped to the
 * first mascot on a screen, with the real capture time stamped on each so drift in
 * `page.screenshot` cannot be mistaken for drift in the animation. `scripts/e2e_contact_sheet.py`
 * lays the burst out as one PNG.
 *
 * Runs SIGNED OUT on purpose. The home screen shows Bit waving in the "browsing a sample
 * session" banner without a token, and the device-token file the full e2e uses carries a
 * rotating refresh pair — injecting it into a second browser context is token reuse and
 * revokes the device. Nothing here touches that file.
 *
 *   PLAYWRIGHT_DIR=<dir with node_modules/playwright> PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
 *   node scripts/e2e_mascot.mjs --out shots/ [--route /] [--frames 24] [--every 100] [--scale 4]
 *
 * Writes `mascot-NN.png` per frame plus `mascot-frames.json` (route, clip, per-frame
 * elapsed ms) into --out.
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
const ROUTE = arg('--route', '/');
const FRAMES = Number(arg('--frames', '24'));
const EVERY = Number(arg('--every', '100'));
const SCALE = Number(arg('--scale', '4'));
const APP = process.env.E2E_APP_URL ?? 'http://127.0.0.1:8081';
/** Padding around the sprite's box, in CSS px, so the clip shows it against its card. */
const PAD = 12;

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: SCALE,
  isMobile: true,
  hasTouch: true,
  colorScheme: 'dark',
  reducedMotion: 'no-preference',
});
const page = await context.newPage();
const noise = [];
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') noise.push(`[${m.type()}] ${m.text().slice(0, 300)}`);
});
page.on('pageerror', (e) => noise.push(`[pageerror] ${String(e).slice(0, 300)}`));

try {
  await page.goto(`${APP}${ROUTE}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  await page.waitForLoadState('networkidle', { timeout: 60_000 }).catch(() => {});

  // Every sprite is an <svg viewBox="0 0 16 16">; the first one on the screen is the one
  // we record. The clip is the svg's box plus padding, which is room enough for the
  // breathing scale (1.03 of 48 px is 1.4 px) and the one-pixel impact drop.
  const svg = page.locator('svg[viewBox="0 0 16 16"]').first();
  await svg.waitFor({ state: 'visible', timeout: 60_000 });
  const box = await svg.boundingBox();
  if (!box) throw new Error('mascot box not found');
  const clip = {
    x: Math.max(0, box.x - PAD),
    y: Math.max(0, box.y - PAD),
    width: box.width + 2 * PAD,
    height: box.height + 2 * PAD,
  };

  // Let the entrance settle land and one blink cadence begin before the burst, so the
  // sheet shows the steady state rather than the mount.
  await page.waitForTimeout(600);

  const frames = [];
  const t0 = Date.now();
  for (let i = 0; i < FRAMES; i++) {
    const due = t0 + i * EVERY;
    const wait = due - Date.now();
    if (wait > 0) await page.waitForTimeout(wait);
    const at = Date.now() - t0;
    const file = `mascot-${String(i).padStart(2, '0')}.png`;
    await page.screenshot({ path: path.join(OUT, file), clip, animations: 'allow', caret: 'hide' });
    frames.push({ file, ms: at });
  }

  fs.writeFileSync(
    path.join(OUT, 'mascot-frames.json'),
    JSON.stringify({ route: ROUTE, clip, scale: SCALE, everyMs: EVERY, frames, noise }, null, 1) + '\n'
  );
  console.log(`${frames.length} frames over ${frames[frames.length - 1].ms} ms -> ${OUT}`);
  const late = frames.filter((f, i) => f.ms - i * EVERY > EVERY / 2).length;
  if (late) console.log(`  ${late} frame(s) captured more than ${EVERY / 2} ms late; see mascot-frames.json`);
} finally {
  await browser.close();
}
