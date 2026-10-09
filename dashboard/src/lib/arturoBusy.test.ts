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
