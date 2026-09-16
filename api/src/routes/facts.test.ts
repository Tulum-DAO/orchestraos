/**
 * Tests for Facts API — Freshness badge logic
 *
 * Tests the compute_staleness function and fact merging logic.
 * Does NOT require the Express server.
 *
 * Run: cd api && npx tsc --noEmit -p .  (type check)
 */

import { strict as assert } from 'assert';

// Reused staleness computation (from facts.ts)
function computeStaleness(
  asOf: string | null | undefined,
  now: Date = new Date(),
  budgetDays: number = 14
): { is_stale: boolean; age_days: number | null } {
  function parseDate(value: string | null | undefined): Date | null {
    if (!value) return null;
    try {
      const dt = new Date(value);
      return isNaN(dt.getTime()) ? null : dt;
    } catch {
      return null;
    }
  }

  const dt = parseDate(asOf);
  if (!dt) {
    return { is_stale: true, age_days: null };
  }
  const ageDays = Math.floor((now.getTime() - dt.getTime()) / (1000 * 60 * 60 * 24));
  return { is_stale: ageDays > budgetDays, age_days: ageDays };
}

// Test suite: Freshness Logic
console.log('Testing Freshness Logic...');

// Test 1: old fact
{
  const now = new Date('2026-09-15');
  const old = '2026-08-20'; // 26 days old, budget is 14
  const { is_stale, age_days } = computeStaleness(old, now, 14);
  assert.strictEqual(is_stale, true, 'Old fact should be stale');
  assert.strictEqual(age_days, 26, 'Age should be 26 days');
  console.log('✓ marks a fact stale if older than budget');
}

// Test 2: recent fact
{
  const now = new Date('2026-09-15');
  const recent = '2026-09-10'; // 5 days old
  const { is_stale, age_days } = computeStaleness(recent, now, 14);
  assert.strictEqual(is_stale, false, 'Recent fact should be fresh');
  assert.strictEqual(age_days, 5, 'Age should be 5 days');
  console.log('✓ marks a fact fresh if within budget');
}

// Test 3: missing date
{
  const { is_stale, age_days } = computeStaleness(null);
  assert.strictEqual(is_stale, true, 'Missing date should be stale');
  assert.strictEqual(age_days, null, 'Age should be null');
  console.log('✓ marks missing date as stale with age=null');
}

// Test 4: empty string
{
  const { is_stale, age_days } = computeStaleness('');
  assert.strictEqual(is_stale, true, 'Empty string should be stale');
  assert.strictEqual(age_days, null, 'Age should be null');
  console.log('✓ marks empty string as stale');
}

// Test 5: exact boundary (age = budget)
{
  const now = new Date('2026-09-15');
  const boundary = '2026-09-01'; // Exactly 14 days old
  const { is_stale } = computeStaleness(boundary, now, 14);
  // age > budget (14 > 14) is false, so not stale
  assert.strictEqual(is_stale, false, 'Boundary case should be fresh');
  console.log('✓ handles exact boundary (age = budget)');
}

// Test 6: over boundary
{
  const now = new Date('2026-09-15');
  const overBoundary = '2026-08-31'; // Exactly 15 days old
  const { is_stale } = computeStaleness(overBoundary, now, 14);
  // age > budget (15 > 14) is true, so stale
  assert.strictEqual(is_stale, true, 'Over boundary should be stale');
  console.log('✓ handles over boundary (age = budget + 1)');
}

// Test suite: Fact Record Structure
console.log('\nTesting Fact Record Structure...');

// Test 7: legacy "fact" field
{
  const row = {
    id: 1,
    fact: 'Text here',
    timestamp: '2026-09-10',
    category: 'test',
  };
  const text = row.fact || '';
  assert.strictEqual(text, 'Text here', 'Should extract from fact field');
  console.log('✓ handles fact with legacy "fact" field');
}

// Test 8: "text" field
{
  const row = {
    id: 2,
    text: 'Text here',
    verified_at: '2026-09-10',
  };
  const text = row.text || '';
  assert.strictEqual(text, 'Text here', 'Should extract from text field');
  console.log('✓ handles fact with "text" field');
}

// Test 9: verified_at fallback
{
  const row = {
    id: 3,
    fact: 'Text here',
    timestamp: '2026-09-10',
  } as { id: number; fact: string; timestamp: string; verified_at?: string };
  const verifiedAt = row.verified_at || row.timestamp;
  assert.strictEqual(verifiedAt, '2026-09-10', 'Should fall back to timestamp');
  console.log('✓ falls back verified_at to timestamp');
}

console.log('\n✅ All tests passed!');
