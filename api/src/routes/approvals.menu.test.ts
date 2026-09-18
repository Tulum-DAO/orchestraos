/**
 * gm-mine-menu-card seam 1 — RED-first: proves the legacy GET /api/approvals
 * endpoint now round-trips kind='menu'/menu/options through to the client, and
 * that the W5 capability gate (DOCS/SURFACE_CONTRACTS.md) applies the same
 * multipart failsafe for non-capable clients — mirroring unified-approvals.menu.test.ts.
 *
 * Run: npx tsx --test src/routes/approvals.menu.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request } from 'express';
import { gateMenuRowsForClient } from './unified-approvals.js';
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

// Helper to simulate the legacy canonicalToLegacy transform + gating that the
// endpoint applies. This mirrors the GET / handler's flow.
function legacyTransform(row: CanonicalRow): any {
  return {
    id: row.id,
    agent_id: row.from_agent,
    action: row.question,
    question: row.question,
    created: row.created_at,
    type: row.kind || 'approval',
    status: row.status,
    op_key: row.op_key,
    options: row.options,
    source: 'canonical',
    kind: row.kind ?? null,
    menu: row.menu ?? null,
  };
}

test('legacy endpoint: kind=menu row round-trips kind/menu/options (was dropped)', () => {
  const row = menuRow();
  const gated = gateMenuRowsForClient([row], req());
  const out = legacyTransform(gated[0]);

  assert.equal(out.kind, 'menu', 'kind must pass through');
  assert.ok(out.menu, 'menu must not be dropped');
  assert.equal(out.menu?.options?.length, 3);
  assert.deepEqual(
    out.menu?.options?.map((o: any) => o.n),
    ['1', '2', '3']
  );
  assert.equal(out.menu?.options?.[2].input_kind, 'free_text');
  assert.deepEqual(out.options, ['Oil change (OKC)', "Alzheimer's topic", 'Other / Write-in...']);
});

test('legacy endpoint: W5 gate applies multipart failsafe to non-capable client', () => {
  const row = menuRow({
    menu: {
      multipart: true,
      walk_complete: false,
      question: 'Which surfaces?',
      options: [{ n: '1', label: 'iOS', input_kind: 'direct' }],
    },
    options: ['iOS'],
  });

  const gated = gateMenuRowsForClient([row], req()); // absent header = non-capable
  const out = legacyTransform(gated[0]);

  assert.equal(out.menu?.read_only, true, 'un-hydrated multipart MUST be read-only echo');
  assert.deepEqual(out.menu?.options, [], 'nothing tappable in the echo');
  assert.deepEqual(out.options, [], 'top-level options emptied too');
  assert.ok(out.menu?.notice);
});

test('legacy endpoint: W5 gate passes raw multipart to capable client', () => {
  const row = menuRow({
    menu: {
      multipart: true,
      walk_complete: false,
      question: 'Which surfaces?',
      options: [{ n: '1', label: 'iOS', input_kind: 'direct' }],
    },
    options: ['iOS'],
  });

  const gated = gateMenuRowsForClient(
    [row],
    req({ 'x-client-capabilities': 'hydrates-multipart' })
  );
  const out = legacyTransform(gated[0]);

  assert.equal(out.menu?.read_only, undefined, 'capable client gets raw card');
  assert.equal(out.menu?.multipart, true);
});
