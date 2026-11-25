/**
 * Outward-action ledger (docs/OUTWARD_ACTIONS.md, V7a): normalizeCanonical() hands a canonical
 * row's `evidence` through parsed, so the web dashboard reads evidence.action (the action's
 * state) from the same column the phone and watch already decode. Additive: a row without
 * evidence has no `evidence` key at all, and unparseable evidence is omitted, never a throw.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/unified-approvals.evidence.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeCanonical } from './unified-approvals.js';
import type { CanonicalRow } from './_canonical-approvals.js';

function row(overrides: Partial<CanonicalRow> = {}): CanonicalRow {
  return {
    id: 'apr_demo0002_0000002',
    from_agent: 'deploy-bot',
    question: 'Deploy build 42 to prod?',
    op_key: 'deploy:42',
    options: ['approve', 'deny', 'hold'],
    status: 'pending',
    created_at: '2026-11-25T10:00:00Z',
    kind: null,
    summary: null,
    risk_level: null,
    reversibility: null,
    feature: null,
    provider: null,
    menu: null,
    ...overrides,
  };
}

const ACTION = {
  hash: 'ab'.repeat(32),
  action: 'deploy',
  state: 'asked',
  reason: null,
  updated_at: '2026-11-25T10:00:01+00:00',
};

test('a canonical row with evidence {"action":{...}} comes out with evidence.action intact', () => {
  const out = normalizeCanonical(row({ evidence: JSON.stringify({ action: ACTION, due_ts: 'x' }) }));
  assert.deepEqual(out.evidence?.action, ACTION);
  assert.equal((out.evidence as any).due_ts, 'x');         // every other key rides along
  assert.equal(out.id, 'apr_demo0002_0000002');            // nothing else changed shape
  assert.equal(out.evidence_summary, '');
});

test('a row without evidence has no evidence key', () => {
  const out = normalizeCanonical(row());
  assert.equal('evidence' in out, false);
  const out2 = normalizeCanonical(row({ evidence: null }));
  assert.equal('evidence' in out2, false);
  const out3 = normalizeCanonical(row({ evidence: '' }));
  assert.equal('evidence' in out3, false);
});

test('unparseable or non-object evidence is omitted, never thrown', () => {
  assert.equal('evidence' in normalizeCanonical(row({ evidence: '{not json' })), false);
  assert.equal('evidence' in normalizeCanonical(row({ evidence: '[1,2]' })), false);
  assert.equal('evidence' in normalizeCanonical(row({ evidence: '"str"' })), false);
});
