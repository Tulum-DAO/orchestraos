import { test } from 'node:test';
import assert from 'node:assert/strict';
import { arturoTurn } from './arturoStream.js';

// One operator send runs at most once, and the reply shown is THAT send's (#312 B1, #317 SF1, #319 review;
// DEC-1791518421640932). Every attempt of a send carries one turn id; when an attempt's outcome is unclear
// the page asks the server what became of that id, and never sends blind.

type Fate = { state: string; result?: Record<string, unknown> };

interface World {
  streams: unknown[];           // bodies POSTed to /text/stream
  texts: unknown[];             // bodies POSTed to /text (XHR)
  reads: string[];              // ?turn= read-back URLs
}

/** Fakes the three endpoints. `stream` answers /text/stream, `text` answers /text, `fates` are the read-backs
 *  in order (the last one repeats). The 2 s poll pause runs at once. */
async function withWorld(
  h: { stream?: (body: any) => Response; text?: (body: any) => { status: number; body: any }; fates?: Fate[] },
  run: (w: World) => Promise<void>,
) {
  const g = globalThis as any;
  const old = { fetch: g.fetch, xhr: g.XMLHttpRequest, timeout: g.setTimeout };
  const w: World = { streams: [], texts: [], reads: [] };
  let fateAt = 0;
  g.setTimeout = (fn: () => void, ms?: number) => old.timeout(fn, ms === 2000 ? 0 : ms);
  g.fetch = async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes('/api/arturo/text/stream')) {
      const body = JSON.parse(String(init?.body || '{}'));
      w.streams.push(body);
      return h.stream!(body);
    }
    if (u.includes('?turn=')) {
      w.reads.push(u);
      const fates = h.fates || [{ state: 'unknown' }];
      const f = fates[Math.min(fateAt++, fates.length - 1)];
      if (f.state === 'http502') return json(502, { ok: false, error: 'gateway unreachable' });   // the hop itself is down
      return json(200, { ok: true, turn: f });
    }
    throw new Error(`unexpected fetch ${u}`);
  };
  g.XMLHttpRequest = class {
    status = 0; responseText = ''; upload: any = {}; onload?: () => void; onerror?: () => void;
    open() {} setRequestHeader() {}
    send(raw: string) {
      const body = JSON.parse(raw);
      w.texts.push(body);
      const r = h.text ? h.text(body) : { status: 200, body: { ok: true, reply_text: 'whole' } };
      this.status = r.status; this.responseText = JSON.stringify(r.body);
      queueMicrotask(() => { this.upload.onload?.(); this.onload?.(); });
    }
  };
  try { await run(w); } finally { g.fetch = old.fetch; g.XMLHttpRequest = old.xhr; g.setTimeout = old.timeout; }
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** A stream that sends these frames, then (optionally) dies. */
function sse(frames: string[], die = true) {
  let i = 0;
  const body = new ReadableStream({ pull(c) {
    if (i < frames.length) c.enqueue(new TextEncoder().encode(frames[i++]));
    else if (die) c.error(new Error('connection reset'));
    else c.close();
  } });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

const START = 'event: turn.start\ndata: {}\n\n';

test('a busy conversation is not sent blind: the page asks what became of this send, then sends it with the same id', async () => {
  let n = 0;
  await withWorld({
    stream: () => json(409, { ok: false, error: 'busy' }),
    text: () => (++n === 1 ? { status: 409, body: { ok: false, error: 'busy' } } : { status: 200, body: { ok: true, reply_text: 'hi' } }),
    fates: [{ state: 'unknown' }],
  }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.ok, true);
    assert.equal(r.reply_text, 'hi');
    const ids = [...w.streams, ...w.texts].map((b: any) => b.turn_id);
    assert.ok(ids[0] && ids.every((id) => id === ids[0]), `one id for every attempt: ${ids}`);
    assert.ok(w.reads.every((u) => u.endsWith(`?turn=${ids[0]}`)));
  });
});

test('item 1: a dropped stream on a REPEATED message shows this send\'s reply, read by its id, never re-sent', async () => {
  await withWorld({
    stream: () => sse([START]),
    fates: [{ state: 'running' }, { state: 'running' }, { state: 'done', result: { ok: true, status: 200, reply_text: 'NEW', choices: { options: ['a'] } } }],
  }, async (w) => {
    const r = await arturoTurn('yes', 'web_c1');
    assert.equal(r.ok, true);
    assert.equal(r.reply_text, 'NEW');
    assert.deepEqual((r as any).choices, { options: ['a'] });          // nit 7: the cards come back too
    assert.equal(w.texts.length, 0);
    assert.equal(w.reads.length, 3);
  });
});

test('item 3: a started turn the server holds nothing of stops polling and says so, never re-sent', async () => {
  await withWorld({ stream: () => sse([START]), fates: [{ state: 'unknown' }] }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.ok, false);
    assert.equal(r.error, 'not_answered');
    assert.equal(w.reads.length, 3);                                    // unknown is final after 3 reads, not 150
    assert.equal(w.texts.length, 0);
  });
});

test('item 2: a server error after tools ran is the answer, never re-asked', async () => {
  await withWorld({
    stream: () => sse([START, 'event: error\ndata: {"code":"turn_failed"}\n\n'], false),
    fates: [{ state: 'done', result: { ok: false, status: 502, error: 'turn_failed', tools_called: ['send_telegram'] } }],
  }, async (w) => {
    const r = await arturoTurn('tell them', 'web_c1');
    assert.equal(r.ok, false);
    assert.deepEqual(r.tools_called, ['send_telegram']);
    assert.equal(w.texts.length, 0);
  });
});

test('a server error that ran nothing is asked once more, whole, with the same id', async () => {
  await withWorld({
    stream: () => sse([START, 'event: error\ndata: {"code":"turn_failed"}\n\n'], false),
    fates: [{ state: 'unknown' }],
  }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.ok, true);
    assert.equal(w.texts.length, 1);
    assert.equal((w.texts[0] as any).turn_id, (w.streams[0] as any).turn_id);
  });
});

test('a lost turn (it may have run) is never sent again', async () => {
  await withWorld({ stream: () => sse([START]), fates: [{ state: 'lost' }] }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.error, 'may_have_run');
    assert.equal(w.texts.length, 0);
  });
});

test('nit 6a: a /text that timed out at the gateway (504) is read back, not re-sent', async () => {
  await withWorld({
    text: () => ({ status: 504, body: { ok: false, error: 'timeout' } }),
    fates: [{ state: 'running' }, { state: 'done', result: { ok: true, status: 200, reply_text: 'slow but done' } }],
  }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1', null, { stream: false });
    assert.equal(r.reply_text, 'slow but done');
    assert.equal(w.texts.length, 1);
  });
});

test('nit 6b: a stream that failed before any response head waits for the stack, then goes again with the SAME id', async () => {
  // It may or may not have reached the server; the same id makes the resend safe either way (a send that
  // landed is replayed or answered busy, never run twice).
  await withWorld({
    stream: () => { throw new TypeError('Failed to fetch'); },
    fates: [{ state: 'unknown' }],
  }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1', null, { onStarting: async () => true });
    assert.equal(r.ok, true);
    assert.equal(w.texts.length, 1);
    assert.equal((w.texts[0] as any).turn_id, (w.streams[0] as any).turn_id);
  });
});

test('nit 8: an abort stops the read-back at once', async () => {
  const ac = new AbortController();
  await withWorld({ stream: () => sse([START]), fates: [{ state: 'running' }] }, async (w) => {
    setTimeout(() => ac.abort(), 5);
    const r = await arturoTurn('hello', 'web_c1', null, { signal: ac.signal });
    assert.equal(r.error, 'aborted');
    assert.ok(w.reads.length < 50);
  });
});

test('a replay (JSON 200 on the stream endpoint) is this send\'s answer', async () => {
  await withWorld({ stream: () => json(200, { ok: true, replayed: true, reply_text: 'again', pair_card: { device: 'iPhone' } }) }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.ok, true);
    assert.equal(r.reply_text, 'again');
    assert.deepEqual((r as any).pair_card, { device: 'iPhone' });
    assert.equal(w.reads.length, 0);
  });
});

test('a refusal is final: no read-back, no resend', async () => {
  await withWorld({ stream: () => json(403, { ok: false, error: 'onboarding_dashboard_only' }) }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.error, 'onboarding_dashboard_only');
    assert.equal(w.reads.length + w.texts.length, 0);
  });
});

test('nit 8: an abort that lands while a read-back is in flight wins over the answer that read brings', async () => {
  const ac = new AbortController();
  const g = globalThis as any;
  const oldFetch = g.fetch, oldTimeout = g.setTimeout;
  g.setTimeout = (fn: () => void, ms?: number) => oldTimeout(fn, ms === 2000 ? 0 : ms);
  let reads = 0;
  g.fetch = async (url: string) => {
    if (String(url).includes('/text/stream')) return sse([START]);
    reads++;
    if (reads === 2) ac.abort();                                       // the operator leaves mid-read
    return json(200, { ok: true, turn: reads < 2 ? { state: 'running' } : { state: 'done', result: { ok: true, status: 200, reply_text: 'late' } } });
  };
  try {
    const r = await arturoTurn('hello', 'web_c1', null, { signal: ac.signal });
    assert.equal(r.error, 'aborted');
  } finally { g.fetch = oldFetch; g.setTimeout = oldTimeout; }
});


// #319 delta B1: with the stack down, the read-back fails too. The page said nothing for 5 minutes and then
// blamed a send that never reached the server ("may have run"); the pill never showed "starting".
test('B1: a stack still starting is said at once, waited for, and the send then goes with the same id', async () => {
  let starting = 0, readsAtStart = -1;
  let n = 0;
  await withWorld({
    stream: () => json(502, { ok: false, error: 'gateway unreachable' }),
    text: () => ({ status: 200, body: { ok: true, reply_text: 'up now' } }),
    fates: [{ state: 'http502' }],
  }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1', null, {
      onStarting: async () => { starting++; readsAtStart = w.reads.length; n++; return true; },
    });
    assert.equal(starting, 1);
    assert.equal(readsAtStart, 0);                                       // said at once, not after a read-back
    assert.equal(r.ok, true);
    assert.equal(r.reply_text, 'up now');
    assert.equal((w.texts[0] as any).turn_id, (w.streams[0] as any).turn_id);
  });
});

test('B1: a stack that does not come up ends as "starting", never as "may have run"', async () => {
  await withWorld({ stream: () => json(502, { ok: false, error: 'gateway unreachable' }), fates: [{ state: 'http502' }] }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1', null, { onStarting: async () => false });
    assert.notEqual(r.error, 'may_have_run');
    assert.equal(r.status, 502);
    assert.equal(w.reads.length, 0);
    assert.equal(w.texts.length, 0);
  });
});

test('B1: a 504 whose read-back cannot reach the server stops after 3 reads as "starting", not after 5 minutes', { timeout: 5000 }, async () => {
  let starting = 0;
  await withWorld({
    text: () => ({ status: 504, body: { ok: false, error: 'timeout' } }),
    fates: [{ state: 'http502' }],
  }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1', null, { stream: false, onStarting: async () => { starting++; return false; } });
    assert.equal(w.reads.length, 3);
    assert.equal(starting, 1);
    assert.notEqual(r.error, 'may_have_run');
  });
});

// A 409 turn_lost comes back directly when a resend meets a send that started and was never finished (a
// restart): it may have run. It is an UNKNOWN, never "it did not go through" (ios-watch-dev, after #319).
test('a direct turn_lost is "may have run", never a raw error, and never re-sent', async () => {
  await withWorld({ stream: () => json(409, { ok: false, error: 'turn_lost' }) }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.error, 'may_have_run');
    assert.equal(w.texts.length + w.reads.length, 0);
  });
});

// 503 turn_mark_unavailable is the one FINAL 5xx: the server ran nothing. Not "starting", not unknown.
test('turn_mark_unavailable is final: nothing ran, no retry, no read-back', async () => {
  let starting = 0;
  await withWorld({ stream: () => json(503, { ok: false, error: 'turn_mark_unavailable' }) }, async (w) => {
    const r = await arturoTurn('hello', 'web_c1', null, { onStarting: async () => { starting++; return true; } });
    assert.equal(r.error, 'turn_mark_unavailable');
    assert.equal(starting, 0);
    assert.equal(w.texts.length + w.reads.length, 0);
  });
});

test('every send outcome has its own words, and a final 503 is never read as "starting"', async () => {
  const { sendOutcomeText, isStarting, NOT_RECORDED_TEXT, MAY_HAVE_RUN_TEXT, NOT_ANSWERED_TEXT } = await import('./arturo.js');
  const unrecorded = { ok: false, status: 503, error: 'turn_mark_unavailable' };
  assert.equal(isStarting(unrecorded), true);              // the trap: a status check alone calls it booting
  assert.equal(sendOutcomeText(unrecorded), NOT_RECORDED_TEXT);
  assert.equal(sendOutcomeText({ ok: false, error: 'may_have_run' }), MAY_HAVE_RUN_TEXT);
  assert.equal(sendOutcomeText({ ok: false, error: 'not_answered' }), NOT_ANSWERED_TEXT);
  assert.equal(sendOutcomeText({ ok: false, status: 502, error: 'gateway unreachable' }), null);
});
