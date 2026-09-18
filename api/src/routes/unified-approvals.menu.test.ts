/**
 * gm-mine-menu-card seam 1 — RED-first: proves normalizeCanonical() round-trips
 * kind='menu'/menu/options through to the client, and that the W5 capability
 * gate (DOCS/SURFACE_CONTRACTS.md) rewrites an un-hydrated multipart row to a
 * read-only echo for a non-capable client — mirroring
 * scripts/test_menu_failsafe_detail.py's fixtures/assertions for the Python
 * gate this TS gate is the equivalent of.
 *
 * Run: npx tsx --test src/routes/unified-approvals.menu.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request } from 'express';
import {
  normalizeCanonical,
  gateMenuRowsForClient,
  clientHydratesMultipart,
} from './unified-approvals.js';
import type { CanonicalRow } from './_canonical-approvals.js';

function req(headers: Record<string, string> = {}): Request {
  return { headers } as unknown as Request;
}

// The apr_993fc651-shaped fixture (state/tasks.db, kind='menu'): a 3-option
// menu with one free_text write-in slot.
function menuRow(overrides: Partial<CanonicalRow> = {}): CanonicalRow {
  return {
    id: 'apr_demo0001_0000001',
    from_agent: 'pm-demo',
    question: 'Release demo — which build do I ship today?',
    op_key: 'demo-vertical-20260914',
    options: ['Oil change (OKC)', "Alzheimer's topic", 'Other / Write-in...'],
    status: 'pending',
    created_at: '2026-09-14T20:15:00Z',
    kind: 'menu',
    summary: null,
    risk_level: null,
    reversibility: null,
    feature: null,
    provider: null,
    menu: {
      question: 'Release demo — which build do I ship today?',
      options: [
        { n: '1', label: 'Oil change (OKC)', input_kind: 'direct' },
        { n: '2', label: "Alzheimer's topic", input_kind: 'direct' },
        { n: '3', label: 'Other / Write-in...', input_kind: 'free_text' },
      ],
    },
    ...overrides,
  };
}

test('normalizeCanonical round-trips kind/menu/options for a menu row (was dropped)', () => {
  const out = normalizeCanonical(menuRow());
  assert.equal(out.kind, 'menu');
  assert.ok(out.menu, 'menu must not be dropped');
  assert.equal(out.menu?.options?.length, 3);
  assert.deepEqual(
    out.menu?.options?.map((o) => o.n),
    ['1', '2', '3']
  );
  assert.equal(out.menu?.options?.[2].input_kind, 'free_text');
  assert.deepEqual(out.options, ['Oil change (OKC)', "Alzheimer's topic", 'Other / Write-in...']);
});

test('normalizeCanonical leaves a non-menu row untouched (kind/menu null)', () => {
  const out = normalizeCanonical(
    menuRow({ kind: null, menu: null, options: ['approve', 'deny', 'hold'] })
  );
  assert.equal(out.kind, null);
  assert.equal(out.menu, null);
});

test('W5 gate: absent X-Client-Capabilities header is non-capable (default SAFE)', () => {
  assert.equal(clientHydratesMultipart(req()), false);
});

test('W5 gate: hydrates-multipart token is capable', () => {
  assert.equal(clientHydratesMultipart(req({ 'x-client-capabilities': 'hydrates-multipart' })), true);
});

test('W5 gate: multipart && !walk_complete row -> read-only echo for a non-capable client', () => {
  const row = menuRow({
    menu: {
      multipart: true,
      walk_complete: false,
      question: 'Which surfaces?',
      options: [{ n: '1', label: 'iOS', input_kind: 'direct' }],
    },
    options: ['iOS'],
  });
  const [out] = gateMenuRowsForClient([row], req()); // absent header
  assert.equal(out.menu?.read_only, true, 'un-hydrated multipart MUST be read-only echo');
  assert.deepEqual(out.menu?.options, [], 'nothing tappable in the echo');
  assert.deepEqual(out.options, [], 'top-level options emptied too');
  assert.ok(out.menu?.notice);
});

test('W5 gate: capable client gets the raw multipart card', () => {
  const row = menuRow({
    menu: {
      multipart: true,
      walk_complete: false,
      question: 'Which surfaces?',
      options: [{ n: '1', label: 'iOS', input_kind: 'direct' }],
    },
    options: ['iOS'],
  });
  const [out] = gateMenuRowsForClient([row], req({ 'x-client-capabilities': 'hydrates-multipart' }));
  assert.equal(out.menu?.read_only, undefined);
  assert.equal(out.menu?.multipart, true);
});

test('W5 gate: a fully-walked (walk_complete) multipart row passes raw to ANY client', () => {
  const row = menuRow({
    menu: {
      multipart: true,
      walk_complete: true,
      question: 'Which surfaces?',
      options: [{ n: '1', label: 'iOS', input_kind: 'direct' }],
    },
    options: ['iOS'],
  });
  const [out] = gateMenuRowsForClient([row], req()); // absent header
  assert.equal(out.menu?.read_only, undefined, 'a fully-walked card is answerable — never echoed');
});

test('W5 gate: a single-part (non-multipart) menu row is untouched on any client', () => {
  const row = menuRow(); // apr_993fc651 shape — no `multipart` key at all
  const [out] = gateMenuRowsForClient([row], req());
  assert.equal(out.menu?.read_only, undefined);
  assert.equal(out.menu?.options?.length, 3, 'single-part options stay tappable');
});
