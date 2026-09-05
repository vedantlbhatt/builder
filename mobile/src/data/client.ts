import Constants from 'expo-constants';

import { Api, type SessionDetail } from './api';

/** Where this build talks to. Settings prints it in the hook recipe. */
export const API_BASE_URL =
  (Constants.expoConfig?.extra as { apiBaseUrl?: string } | undefined)?.apiBaseUrl ??
  'http://localhost:8000';

export const api = new Api(API_BASE_URL);

/**
 * The sample session.
 *
 * Exists so the app renders something before sign-in and in App Review, where nobody has
 * a Mac agent paired. The strip is `spec/fixtures/strip_realistic.json` — the same 1024
 * columns the Swift and TypeScript conformance suites decode — with its marks scaled from
 * the fixture's 71-minute span to this session's 6h48m so they land in the same places.
 */
const SAMPLE_STARTED_MS = Date.parse('2026-08-29T13:12:00Z');
const SAMPLE_SPAN_MS = 24_480_000; // 6h48m
const SAMPLE_ENDED_MS = SAMPLE_STARTED_MS + SAMPLE_SPAN_MS;

export const SAMPLE_SESSION: SessionDetail = {
  id: 'sample',
  client_session_id: 'sample',
  harness: 'claude_code',
  repo_name: 'gt-transit',
  started_at: new Date(SAMPLE_STARTED_MS).toISOString(),
  ended_at: new Date(SAMPLE_ENDED_MS).toISOString(),
  active_seconds: 19020,
  idle_seconds: 5460,
  local_date: '2026-08-29',
  title: 'Wire live vehicle positions into the guidance map',
  title_source: 'harness',
  notable: true,
  unattended: false,
  timeline_fidelity: 'full',
  is_shared: false,
  post_id: null,
  strip: {
    cols:
      'DQ0NDQ0NDQ0KDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKBwcHBwcHBwcHBwcHBwcHBwcHBwcAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0NDQ0NDQ0NCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCg4KCgcHBwcHBwcHBwcHBwcHBwcHBwcHAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAANDQ0NDQ0NDQoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoOCgoHBwcHBwcHBwcHBwcHBwcHBwcHBwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADQ0NDQ0NDQ0KDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKDgoKBwcHBwcHBwcHBwcHBwcHBwcHBwcAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==',
    marks: [[0, 0], [6120000, 0], [12240000, 2], [24474000, 0]],
    t0_ms: SAMPLE_STARTED_MS,
    t1_ms: SAMPLE_ENDED_MS,
  },
  stats: {
    tokens_reported: true,
    tok_in: 2_100_000,
    tok_out: 410_000,
    tok_cache_read: 190_000_000,
    tok_cache_w5m: 1_600_000,
    tok_cache_w1h: 0,
    models: [{ model_id: 'claude-opus-5', output_token_share: 1 }],
    model_state: 'known',
    human_prompt_count: 52,
    prompt_count_basis: 'typed_promptsource',
    files_touched: 38,
    lines_added_agent: 2101,
    commit_count: 7,
    agent_line_bucket: 'nine_in_ten',
    attrib_confidence: 'high',
  },
};
