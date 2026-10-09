/**
 * VoiceSession.start()/stop() against fake browser primitives: what happens to the MICROPHONE
 * when a call is cancelled while the browser's permission prompt is still up, and whether a call
 * whose socket opens during that prompt ever goes live. NO real mic/WS/hardware.
 *   node --experimental-strip-types dashboard/src/lib/voiceSessionStart.test.mjs
 *
 * THE BUGS (found 2026-10-09 reading start()):
 *  1. stop() during the getUserMedia await had nothing to stop yet; the stream then resolved
 *     onto an idle session and nothing ever stopped it: the mic stayed on until the tab closed.
 *  2. The socket was opened BEFORE the permission await, its 'open' listener attached AFTER it.
 *     A socket that opened while the operator read the prompt fired 'open' to nobody, and the
 *     call sat in 'connecting' forever.
 */
import assert from 'node:assert';
import { VoiceSession } from './voiceSession.ts';

// ── fakes ─────────────────────────────────────────────────────────────────
class FakeTrack { stopped = false; stop() { this.stopped = true; } }
class FakeStream {
  tracks = [new FakeTrack()];
  getTracks() { return this.tracks; }
}
const sockets = [];
class FakeWS {
  static OPEN = 1;
  readyState = 0; closed = false; sent = []; binaryType = '';
  #l = {};
  constructor(url) {
    this.url = url;
    sockets.push(this);
    // Like a real socket: it opens on its own, asynchronously, whoever is listening.
    queueMicrotask(() => { if (!this.closed) { this.readyState = 1; this.#fire('open'); } });
  }
  addEventListener(t, fn) { (this.#l[t] ||= []).push(fn); }
  send(d) { this.sent.push(d); }
  close() { this.closed = true; this.readyState = 3; }
  /** The browser delivering 'close' later, as it does after close(). */
  deliverClose() { this.#fire('close'); }
  deliverJson(obj) { this.#fire('message', { data: JSON.stringify(obj) }); }
  #fire(t, ev = {}) { for (const fn of this.#l[t] || []) fn(ev); }
}
class FakeNode { connect() {} disconnect() {} }
class FakeAudioContext {
  sampleRate = 48000; destination = {};
  createMediaStreamSource() { return new FakeNode(); }
  createScriptProcessor() { return new FakeNode(); }
  createGain() { const g = new FakeNode(); g.gain = { value: 1 }; return g; }
  close() {}
}

// getUserMedia whose promise the TEST resolves, i.e. the permission prompt is up until we say so.
let pendingGum = [];
function installBrowser() {
  sockets.length = 0; pendingGum = [];
  globalThis.window = { WebSocket: FakeWS, AudioContext: FakeAudioContext, location: { protocol: 'http:', host: 'test' } };
  globalThis.WebSocket = FakeWS;
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { mediaDevices: { getUserMedia: () => new Promise((resolve) => pendingGum.push(resolve)) } },
  });
}
const tick = () => new Promise((r) => setTimeout(r, 0));

// ── 1. cancel while the permission prompt is up ─────────────────────────────
{
  installBrowser();
  const states = [];
  const s = new VoiceSession({ onStateChange: (st) => states.push(st) });
  const p = s.start({ route: '/agent/x' });
  await tick();
  assert.strictEqual(s.state, 'connecting');
  s.stop();                                   // operator cancels during the prompt
  const stream = new FakeStream();
  pendingGum.shift()(stream);                 // ...and THEN the browser grants the mic
  await p; await tick();
  assert.ok(stream.getTracks().every((t) => t.stopped), 'every mic track is stopped: the mic does not stay on');
  assert.strictEqual(s.state, 'idle', 'the cancelled call stays idle');
  assert.ok(!states.includes('live'), 'the cancelled call never goes live');
  assert.ok(sockets.every((w) => w.closed || w.sent.length === 0), 'no socket carries the cancelled call');
  console.log('PASS: stop() during the permission prompt stops the mic that arrives after it');
}

// ── 2. control: no cancel -> the stream is kept and the call goes live ─────────
{
  installBrowser();
  const s = new VoiceSession();
  const p = s.start({ route: '/agent/x' });
  await tick(); await tick();                 // a socket opened by now would have fired 'open' already
  const stream = new FakeStream();
  pendingGum.shift()(stream);
  await p; await tick(); await tick();
  assert.strictEqual(s.state, 'live', 'the call goes live even though the prompt took longer than the socket');
  assert.ok(stream.getTracks().every((t) => !t.stopped), 'the live call keeps its mic');
  assert.strictEqual(sockets.length, 1);
  assert.deepStrictEqual(JSON.parse(sockets[0].sent[0]), { route: '/agent/x', focusedEntity: null }, 'first message is the page context');
  s.stop();
  assert.ok(stream.getTracks().every((t) => t.stopped), 'stop() on a live call stops the mic');
  console.log('PASS: a granted, uncancelled call goes live, keeps its mic, and stop() releases it');
}

// ── 3. cancel, then call again, while the FIRST prompt is still pending ─────
{
  installBrowser();
  const s = new VoiceSession();
  const p1 = s.start({ route: '/a' });
  await tick();
  s.stop();
  const p2 = s.start({ route: '/b' });         // second call while the first prompt is unresolved
  await tick();
  const first = new FakeStream();
  const second = new FakeStream();
  pendingGum.shift()(first);                   // the stale grant lands first
  await p1; await tick();
  assert.ok(first.getTracks().every((t) => t.stopped), 'the stale grant is released');
  assert.strictEqual(s.state, 'connecting', 'and it does not hijack the second call');
  pendingGum.shift()(second);
  await p2; await tick(); await tick();
  assert.strictEqual(s.state, 'live', 'the second call goes live');
  assert.ok(second.getTracks().every((t) => !t.stopped), 'with its own mic');
  assert.strictEqual(JSON.parse(sockets.at(-1).sent[0]).route, '/b', 'on its own socket');
  s.stop();
  console.log('PASS: a stale grant after cancel + re-call is released and never hijacks the new call');
}

// ── 4. the OLD call's late 'close' must not tear down a NEW call ──────────────
{
  installBrowser();
  const ended = [];
  const s = new VoiceSession({ onCallEnded: (m) => ended.push(m) });
  const p1 = s.start({ route: '/a' });
  await tick(); pendingGum.shift()(new FakeStream()); await p1; await tick(); await tick();
  assert.strictEqual(s.state, 'live');
  const oldSocket = sockets[0];
  oldSocket.deliverJson({ event: 'connected', session_id: 'call-1' });
  s.stop();                                    // hang up; the browser delivers 'close' later
  const p2 = s.start({ route: '/b' });
  await tick();
  const second = new FakeStream();
  pendingGum.shift()(second); await p2; await tick(); await tick();
  assert.strictEqual(s.state, 'live', 'second call is live');
  sockets[1].deliverJson({ event: 'connected', session_id: 'call-2' });
  oldSocket.deliverClose();                    // ...the first call's close finally arrives
  await tick();
  assert.strictEqual(s.state, 'live', "the old socket's close does not end the new call");
  assert.ok(second.getTracks().every((t) => !t.stopped), 'and does not stop its mic');
  assert.strictEqual(ended.length, 1, 'the old call still reports that it ended');
  assert.match(ended[0], /call-1/, "with ITS OWN id, not the new call's");
  s.stop();
  sockets[1].deliverClose(); await tick();
  assert.strictEqual(ended.length, 2);
  assert.match(ended[1], /call-2/, 'and the new call reports its own when it ends');
  console.log("PASS: a stale socket's close leaves the current call alone and reports its own call");
}

// ── 5. the OLD call's 'close' arriving DURING the new call's permission prompt ──
{
  installBrowser();
  const ended = [];
  const s = new VoiceSession({ onCallEnded: (m) => ended.push(m) });
  const p1 = s.start({ route: '/a' });
  await tick(); pendingGum.shift()(new FakeStream()); await p1; await tick(); await tick();
  sockets[0].deliverJson({ event: 'connected', session_id: 'call-1' });
  s.stop();
  const p2 = s.start({ route: '/b' });         // new call: its prompt is up...
  await tick();
  sockets[0].deliverClose();                   // ...when the old socket's close lands
  await tick();
  assert.strictEqual(s.state, 'connecting', 'the new call is still connecting');
  const second = new FakeStream();
  pendingGum.shift()(second); await p2; await tick(); await tick();
  assert.strictEqual(s.state, 'live', 'and goes live once granted');
  assert.ok(second.getTracks().every((t) => !t.stopped), 'keeping its mic');
  assert.deepStrictEqual(ended.map((m) => /call-1/.test(m)), [true], 'the old call reported its end once');
  s.stop();
  console.log("PASS: the old call's close during the new call's prompt does not cancel the new call");
}
