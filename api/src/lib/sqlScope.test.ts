import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tenantFilter, sortColumn } from './sqlScope.js';

test('admin scope adds no tenant clause and no params', () => {
  assert.deepEqual(tenantFilter({ isAdmin: true, clientScope: null, username: 'shaw' }, 'tenant_id'),
    { sql: '', params: [] });
});

test('a non-admin tenant reaches SQL only as a bound parameter, never in the SQL text', () => {
  const value = "o'brien";
  const f = tenantFilter({ isAdmin: false, clientScope: value, username: 'shaw' }, 'p.tenant_id');
  assert.equal(f.sql, ' AND p.tenant_id = ?');
  assert.deepEqual(f.params, [value]);
  assert.ok(!f.sql.includes(value), 'the tenant value must never appear in the SQL text');
});

test('without a client scope the username is the bound tenant', () => {
  const f = tenantFilter({ isAdmin: false, clientScope: null, username: 'kai' }, 'tenant_id');
  assert.deepEqual(f.params, ['kai']);
});

test('sortColumn: absent -> fallback, known -> itself, unknown -> null (the route answers 400)', () => {
  const allowed = new Set(['created_at', 'title']);
  for (const absent of [undefined, null, '']) assert.equal(sortColumn(absent, allowed, 'created_at'), 'created_at');
  assert.equal(sortColumn('title', allowed, 'created_at'), 'title');
  for (const unknown of ['nope', 'title desc', 'TITLE', 42, ['title']]) {
    assert.equal(sortColumn(unknown, allowed, 'created_at'), null, JSON.stringify(unknown));
  }
});
