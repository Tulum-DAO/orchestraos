/**
 * The card's ✕ for menus not on an agent's screen: lib/approval-dismiss.ts maps the gateway's discard
 * reply to the web's statuses, and changes nothing itself.
 *
 * Run: npx tsx --test src/lib/approval-dismiss.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dismissCard, type DismissDeps } from './approval-dismiss.js';

function deps(reply: { status: number; body: any } | Error, token = 'tok'): DismissDeps & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    token: () => token,
    base: () => 'http://gw.example',
    post: async (url, t) => {
      calls.push(`${url} ${t}`);
      if (reply instanceof Error) throw reply;
      return reply;
    },
  };
}

test('a pending card is discarded through the gateway', async () => {
  const d = deps({ status: 200, body: { ok: true, applied: true, id: 'apr_demo0001' } });
  const r = await dismissCard('apr_demo0001', d);
  assert.equal(r.status, 200);
  assert.equal(r.body.status, 'dismissed');
  assert.deepEqual(d.calls, ['http://gw.example/approvals/apr_demo0001/discard tok']);
});

test('a card already answered or closed is a 409, not a success', async () => {
  const r = await dismissCard('apr_demo0002', deps({ status: 200, body: { ok: true, applied: false } }));
  assert.equal(r.status, 409);
});

test('an unknown card is a 404', async () => {
  const r = await dismissCard('apr_demo0003', deps({ status: 404, body: { ok: false, error: 'not found' } }));
  assert.equal(r.status, 404);
});

test('the gateway\'s 423 (a menu on the agent\'s screen) keeps its words, why and agent; it is not a gateway fault nor a 409', async () => {
  const r = await dismissCard('apr_menu0001', deps({ status: 423, body: { ok: false, error: 'This menu is on codex-gm\'s screen: dismiss it on the pane (Escape), which closes this card too.', agent: 'codex-gm',
                                                                           why: 'the pane still shows this menu' } }));
  assert.equal(r.status, 423);
  assert.deepEqual(r.body, { error: 'This menu is on codex-gm\'s screen: dismiss it on the pane (Escape), which closes this card too.', id: 'apr_menu0001', why: 'the pane still shows this menu', agent: 'codex-gm' });
  const bare = await dismissCard('apr_menu0002', deps({ status: 423, body: {} }));
  assert.deepEqual([bare.status, bare.body.error], [423, 'This card cannot be dismissed here.']);
  const closed = await dismissCard('apr_menu0003', deps({ status: 409, body: { ok: false, error: 'x' } }));
  assert.equal(closed.status, 502, 'a 409 from the gateway is not a refusal the web knows; it stays a gateway fault');
});

test('no token or no gateway is a 502 and nothing is sent without a token', async () => {
  const noTok = deps({ status: 200, body: { ok: true, applied: true } }, '');
  assert.equal((await dismissCard('apr_demo0004', noTok)).status, 502);
  assert.equal(noTok.calls.length, 0);
  assert.equal((await dismissCard('apr_demo0004', deps(new Error('ECONNREFUSED')))).status, 502);
  assert.equal((await dismissCard('apr_demo0004', deps({ status: 401, body: { ok: false } }))).status, 502);
});

test('the id is escaped into the gateway path', async () => {
  const d = deps({ status: 200, body: { ok: true, applied: true } });
  await dismissCard('apr_x/../y', d);
  assert.equal(d.calls[0], 'http://gw.example/approvals/apr_x%2F..%2Fy/discard tok');
});

test('where and why ride to the gateway as the discard body (every card kind, one shape; never who)', async () => {
  const bodies: string[] = [];
  const d: DismissDeps & { calls: string[] } = {
    calls: [],
    token: () => 'tok',
    base: () => 'http://gw.example',
    post: async (url, _t, body) => { bodies.push(body ?? ''); return { status: 200, body: { ok: true, applied: true } }; },
  };
  const r = await dismissCard('apr_task0001', d, { surface: 'web', reason: 'old, remove' });
  assert.equal(r.status, 200);
  assert.deepEqual(JSON.parse(bodies[0]), { surface: 'web', reason: 'old, remove' });
  await dismissCard('apr_task0001', d);
  assert.equal(bodies[1], '{}', 'no fields: the body stays an empty object, as before');
});
