import { test } from 'node:test';
import assert from 'node:assert/strict';
import { arturoTurn } from './arturoStream.js';
import { isBusy } from './arturoResume.js';

// Review of #312 (B1): a message sent while the conversation was mid-turn was re-sent over /text by the
// stream's fallback, queued behind the same turn, and then RAN TWICE. A busy conversation answers 409 at
// once; the turn must report it as busy (so the page waits and retries) and never fall back.

test('a busy conversation is reported as busy, and the message is not sent a second time', async () => {
  const g = globalThis as any;
  const oldFetch = g.fetch, oldXhr = g.XMLHttpRequest;
  let xhrOpened = 0;
  g.fetch = async () => new Response(JSON.stringify({ ok: false, error: 'busy' }),
    { status: 409, headers: { 'Content-Type': 'application/json' } });
  g.XMLHttpRequest = class { open() { xhrOpened++; } setRequestHeader() {} send() {} upload = {}; };
  try {
    const r = await arturoTurn('hello', 'web_c1');
    assert.equal(r.ok, false);
    assert.equal(isBusy(r), true);
    assert.equal(xhrOpened, 0);                       // no /text fallback
  } finally {
    g.fetch = oldFetch; g.XMLHttpRequest = oldXhr;
  }
});

// Review of #317 (SF1): the stream dropped AFTER turn.start, the server kept running the turn, and the
// fallback re-sent the message; the busy retry then made it run twice, reliably. A started turn is
// never re-sent: the page waits for the server to record it and reads the reply from the thread.
test('a turn that started and then lost its connection is read back, never sent again', async () => {
  const g = globalThis as any;
  const oldFetch = g.fetch, oldXhr = g.XMLHttpRequest, oldTimeout = g.setTimeout;
  let xhrOpened = 0, threadReads = 0;
  g.setTimeout = (fn: () => void) => oldTimeout(fn, 0);          // no real waiting in the test
  g.fetch = async (url: string) => {
    if (String(url).includes('/api/arturo/text/stream')) {
      let sent = false;
      const body = new ReadableStream({ pull(c) {                  // turn.start arrives, then the line drops
        if (!sent) { sent = true; c.enqueue(new TextEncoder().encode('event: turn.start\ndata: {}\n\n')); }
        else c.error(new Error('connection reset'));
      } });
      return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
    }
    threadReads++;
    const turns = threadReads < 3 ? [] : [{ role: 'user', content: 'B says hi' }, { role: 'assistant', content: 'Hi B.' }];
    return new Response(JSON.stringify({ ok: true, thread: { id: 'web_c1', title: '', turns } }),
      { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  g.XMLHttpRequest = class { open() { xhrOpened++; } setRequestHeader() {} send() {} upload = {}; };
  try {
    const r = await arturoTurn('[Onboarding: step=onboarding]\nB says hi', 'web_c1');
    assert.equal(r.ok, true);
    assert.equal(r.reply_text, 'Hi B.');
    assert.equal(xhrOpened, 0);                                    // never re-sent over /text
    assert.ok(threadReads >= 3);
  } finally {
    g.fetch = oldFetch; g.XMLHttpRequest = oldXhr; g.setTimeout = oldTimeout;
  }
});
