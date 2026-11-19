import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// The browser glue of a native call, against stand-ins for the socket, the mic, Web Audio and fetch: the order of
// the hang-up (journal, then disconnect, then close), the journal's shape, the call card's marker, captions,
// tools, refusals and the relay fallback.

type Listener = (ev: any) => void;
class FakeWS {
  static OPEN = 1;
  static last: FakeWS | null = null;
  readyState = 0;
  binaryType = '';
  sent: unknown[] = [];
  closedWith: [number?, string?] | null = null;
  private ls: Record<string, Listener[]> = {};
  constructor(public url: string) { FakeWS.last = this; }
  addEventListener(t: string, f: Listener) { (this.ls[t] ||= []).push(f); }
  send(d: unknown) { this.sent.push(d); }
  close(code?: number, reason?: string) { this.closedWith = [code, reason]; }
  fire(t: string, ev: any = {}) { for (const f of this.ls[t] || []) f(ev); }
  open() { this.readyState = 1; this.fire('open'); }
  msg(o: unknown) { this.fire('message', { data: JSON.stringify(o) }); }
  serverClose(code: number, reason = '') { this.readyState = 3; this.fire('close', { code, reason }); }
}

const node = () => ({ connect() {}, disconnect() {}, gain: { value: 1 }, fftSize: 0, onaudioprocess: null });
let ctxRate = 48000;
const ctxs: FakeCtx[] = [];
class FakeCtx {
  sampleRate = ctxRate; state = 'running'; currentTime = 0; destination = {}; closed = false; starts: number[] = [];
  constructor() { ctxs.push(this); }
  createMediaStreamSource() { return node(); }
  createGain() { return node(); }
  createAnalyser() { return node(); }
  createScriptProcessor() { return node(); }
  createBuffer() { return { duration: 0.1, copyToChannel() {} }; }
  createBufferSource() { const starts = this.starts; return { ...node(), start(at: number) { starts.push(at); }, stop() {}, onended: null, buffer: null }; }
  resume() { return Promise.resolve(); }
  close() { this.closed = true; return Promise.resolve(); }
}

let posts: Array<{ url: string; body: any; at: number }> = [];
let postAnswer: { status: number; body: unknown } = { status: 200, body: { ok: true, call_id: 'vc_client_0123456789ab' } };
let order: string[] = [];
const g = globalThis as any;
g.window = { location: { protocol: 'https:', host: 'example.test' }, WebSocket: FakeWS, AudioContext: FakeCtx };
g.WebSocket = FakeWS;
g.AudioContext = FakeCtx;
Object.defineProperty(globalThis, 'navigator', { configurable: true,
  value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } } });
g.fetch = async (url: string, init: any) => {
  order.push('post');
  posts.push({ url, body: JSON.parse(init.body), at: Date.now() });
  return new Response(JSON.stringify(postAnswer.body), { status: postAnswer.status });
};

const { NativeVoiceSession } = await import('./nativeVoiceSession.ts');

function session() {
  const ev: string[] = [];
  const s = new NativeVoiceSession({
    onStateChange: (st) => ev.push(`state ${st}`),
    onPartial: (t, r) => ev.push(`partial ${r} ${t}`),
    onFinal: (t, r) => ev.push(`final ${r} ${t}`),
    onUnavailable: (r) => ev.push(`note ${r}`),
    onConnected: (id) => ev.push(`connected ${id}`),
    onCallEnded: (m, id) => ev.push(`ended ${id} ${m}`),
    onTool: (e) => ev.push(`tool ${e.phase} ${e.callId} ${e.name}${e.ok === undefined ? '' : ` ${e.ok}`}`),
    onFallback: (r) => ev.push(`fallback ${r}`),
  });
  return { s, ev };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => { posts = []; order = []; postAnswer = { status: 200, body: { ok: true, call_id: 'vc_client_0123456789ab' } }; });

async function live() {
  const x = session();
  await x.s.start({ route: '/approvals' });
  const ws = FakeWS.last!;
  assert.equal(ws.url, 'wss://example.test/api/voice/native');
  ws.open();
  ws.msg({ type: 'state', mode: 'live', conversation_id: 'native-0123456789ab', principal: 'fleet' });
  return { ...x, ws };
}

test('open sends start first; state makes the call live and sends the page context once', async () => {
  const { ev, ws, s } = await live();
  assert.deepEqual(JSON.parse(ws.sent[0] as string), { type: 'start', settle: 'fast' });
  assert.ok(ev.includes('state live'));
  assert.ok(ev.includes('connected native-0123456789ab'));
  assert.deepEqual(JSON.parse(ws.sent[1] as string), { type: 'context', page: 'Approvals' });
  s.sendContext({ route: '/approvals' });            // an exact repeat is skipped
  s.sendContext({ route: '/settings' });             // a page the server drops is never sent
  s.sendContext({ route: '/agents' });
  const ctx = ws.sent.filter((d) => typeof d === 'string' && d.includes('"context"'));
  assert.equal(ctx.length, 2);
});

test('captions, tools and a hang-up: journal posted BEFORE disconnect, then the card marker', async () => {
  const { ev, ws, s } = await live();
  ws.msg({ type: 'transcript', who: 'user', text: 'what is up', new: true });
  ws.msg({ type: 'tools', calls: [{ call_id: 'c1', name: 'list_approvals', args: {}, ok: true }], refused: [], claims_blocked: [] });
  ws.msg({ type: 'tools', calls: [{ call_id: 'c1', name: 'list_approvals', ok: true }], refused: [], claims_blocked: [] });
  ws.msg({ type: 'transcript', who: 'arturo', text: 'Two approvals', new: true });
  ws.msg({ type: 'barge', cut: false });
  ws.msg({ type: 'some_future_frame' });
  const origSend = ws.send.bind(ws);
  ws.send = (d: unknown) => { if (typeof d === 'string' && d.includes('disconnect')) order.push('disconnect'); origSend(d); };
  s.stop();
  assert.ok(ev.includes('final user what is up'));
  assert.ok(ev.includes('final arturo Two approvals'));   // the open line is committed on hang-up
  assert.equal(ev.filter((e) => e.startsWith('tool start c1')).length, 1);
  assert.ok(ev.includes('tool done c1 list_approvals true'));
  assert.ok(ev.includes('state idle'));
  await tick(); await tick(); await tick();
  assert.deepEqual(order, ['post', 'disconnect']);
  assert.equal(posts[0].url, '/api/voice/call-ended');
  assert.equal(posts[0].body.conv_id, 'native-0123456789ab');
  assert.deepEqual(posts[0].body.turns.map((t: any) => [t.role, t.text]), [['user', 'what is up'], ['arturo', 'Two approvals']]);
  assert.ok(posts[0].body.started_at > 1e9 && posts[0].body.started_at < 1e11);   // epoch seconds
  assert.ok(ev.some((e) => e.startsWith('ended vc_client_0123456789ab [voice-call: vc_client_0123456789ab ')));
  ws.serverClose(1000, 'stopped');
  assert.equal(posts.length, 1);                      // journaled once
});

test('the server ends the call (idle): journaled, said, idle', async () => {
  const { ev, ws } = await live();
  ws.msg({ type: 'transcript', who: 'user', text: 'hello', new: true });
  ws.serverClose(1000, 'idle');
  await tick(); await tick();
  assert.ok(ev.some((e) => e.startsWith('note ') && e.includes('two minutes')));
  assert.ok(ev.includes('state idle'));
  assert.equal(posts.length, 1);
});

test('refused: the reason is shown, the call ends, no relay fallback', async () => {
  const { ev, ws } = await live();
  ws.msg({ type: 'refused', reason: 'Arturo is off for the night' });
  assert.deepEqual(ws.closedWith, [1000, 'refused']);
  ws.serverClose(1000, 'refused');
  assert.ok(ev.includes('note Arturo is off for the night'));
  assert.ok(!ev.some((e) => e.startsWith('fallback')));
  assert.ok(ev.includes('state idle'));
});

test('busy (4409) before live: the readable reason, no fallback, no journal', async () => {
  const { s, ev } = session();
  await s.start({ route: '/' });
  const ws = FakeWS.last!;
  ws.open();
  ws.serverClose(4409, 'busy: a call is already open');
  await tick();
  assert.ok(ev.includes('note busy: a call is already open'));
  assert.ok(!ev.some((e) => e.startsWith('fallback')));
  assert.equal(posts.length, 0);
});

test('native switched off (4503) before live: hands the call to the relay, says nothing itself', async () => {
  const { s, ev } = session();
  await s.start({ route: '/' });
  const ws = FakeWS.last!;
  ws.open();
  ws.serverClose(4503, 'unavailable: native voice is switched off (HTTP 503)');
  assert.ok(ev.includes('fallback unavailable: native voice is switched off (HTTP 503)'));
  assert.ok(!ev.some((e) => e.startsWith('note')));
  assert.ok(!ev.includes('state idle'));
});

test('a failed journal says so instead of a silent missing card', async () => {
  postAnswer = { status: 502, body: { ok: false, error: 'gateway unreachable' } };
  const { ev, ws, s } = await live();
  ws.msg({ type: 'transcript', who: 'user', text: 'x', new: true });
  s.stop();
  await tick(); await tick(); await tick();
  assert.ok(ev.includes("note the call's transcript was not saved (gateway unreachable)"));
  assert.ok(!ev.some((e) => e.startsWith('ended')));
});

test('the server stops the call with a reason: the caller is told it (but not for "you hung up")', async () => {
  const a = await live();
  a.ws.msg({ type: 'stopped', reason: 'Daily voice budget reached', report: {} });
  a.ws.serverClose(1000, 'stopped');
  assert.ok(a.ev.includes('note Daily voice budget reached'));
  const b = await live();
  b.ws.msg({ type: 'stopped', reason: 'you hung up', report: {} });
  b.ws.serverClose(1000, 'stopped');
  assert.ok(!b.ev.some((e) => e.startsWith('note')));
});

test('hang-up closes the call\'s playback AudioContext; the first chunk after silence starts ~80 ms ahead', async () => {
  ctxs.length = 0;
  const { ws, s } = await live();
  ws.fire('message', { data: new ArrayBuffer(3200) });
  ws.fire('message', { data: new ArrayBuffer(3200) });
  const play = ctxs.find((c) => c.starts.length > 0)!;
  assert.ok(play, 'a playback context was made');
  assert.ok(Math.abs(play.starts[0] - 0.08) < 1e-9, `first start ${play.starts[0]}`);
  assert.ok(Math.abs(play.starts[1] - 0.18) < 1e-9, `second start ${play.starts[1]} (gapless after the first)`);
  s.stop();
  assert.ok(ctxs.every((c) => c.closed), 'every AudioContext of the call is closed');
});

test('a mic context the resampler cannot use (8 kHz) ends the call loudly instead of a silent open call', async () => {
  ctxRate = 8000;
  try {
    const x = session();
    await x.s.start({ route: '/' });
    const ws = FakeWS.last!;
    ws.open();
    await tick(); await tick();
    assert.ok(x.ev.some((e) => e.startsWith("note the microphone can't be used for this call")), x.ev.join(' | '));
    assert.ok(x.ev.includes('state idle'));
  } finally { ctxRate = 48000; }
});

test('the start frame is exactly {"type":"start","settle":"fast"} and no frame of a call ever asks settle:"wait"', async () => {
  const { ws, s } = await live();
  assert.equal(ws.sent[0], '{"type":"start","settle":"fast"}');
  ws.msg({ type: 'transcript', who: 'user', text: 'hi', new: true });
  s.sendContext({ route: '/agents' });
  s.setMuted(true);
  s.stop();
  await tick(); await tick(); await tick();
  const texts = ws.sent.filter((d): d is string => typeof d === 'string');
  assert.equal(texts.filter((t) => t.includes('"start"')).length, 1);
  assert.ok(texts.every((t) => !/"settle"\s*:\s*"wait"/.test(t)), texts.join(' | '));
});
