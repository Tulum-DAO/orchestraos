/**
 * GET /:id/transcript/stream carries `event: live_text` for a HEADLESS seat: the words so far from
 * its supervisor log, then '' once the finished line is in the transcript (sent after the delta that
 * brought it). A tmux seat, or a headless seat in Dev, never gets a frame.
 *
 * Run: npx tsx --test src/routes/transcript-stream.live.test.ts   (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, appendFileSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const HEADLESS = 'zz-live-demo';               // never a real tmux session on any box
const TMUX = 'zz-live-tmux-demo';
const SID = '44444444-2222-4333-8444-000000000001';
const SID2 = '44444444-2222-4333-8444-000000000002';
const MID = 'msg_example_live';

const root = mkdtempSync(join(tmpdir(), 'stream-live-'));
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
process.env.HEADLESS_SEATS_FILE = join(orch, 'state', 'headless-seats.json');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
writeFileSync(process.env.HEADLESS_SEATS_FILE, JSON.stringify({ seats: { [HEADLESS]: { headless: true, runtime: 'claude' } } }));
for (const [seat, sid] of [[HEADLESS, SID], [TMUX, SID2]]) {
  writeFileSync(join(orch, 'state', 'agents', `${seat}.json`), JSON.stringify({ session_id: sid, cwd: '/repo' }));
  writeFileSync(join(projects, `${sid}.jsonl`), JSON.stringify({ type: 'user', uuid: `u-${sid}`, timestamp: '2026-10-31T03:00:00Z', message: { role: 'user', content: 'hello' } }) + '\n');
}
const seatDir = (seat: string) => { const d = join(orch, 'state', 'headless', seat); mkdirSync(d, { recursive: true }); return d; };
const ev = (e: any) => JSON.stringify({ t: 0, wall: new Date().toISOString(), gen: 1, kind: 'out', ev: { parent_tool_use_id: null, ...e } });
const se = (event: any) => ev({ type: 'stream_event', event, api_message_id: MID });
const streamed = [
  se({ type: 'message_start', message: { id: MID, role: 'assistant', content: [] } }),
  se({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } }),
  se({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'Streaming words' } }),
];
writeFileSync(join(seatDir(HEADLESS), 'events.jsonl'), streamed.join('\n') + '\n');
writeFileSync(join(seatDir(TMUX), 'events.jsonl'), streamed.join('\n') + '\n');   // a log left behind: still not headless

const { default: router } = await import('./transcript-stream.js');
const express = (await import('express')).default;
const app = express();
app.use('/api/agents', router);
const server = app.listen(0);
after(() => { server.close(); rmSync(root, { recursive: true, force: true }); });
const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents`;

/** Read SSE frames from a seat's stream until `until` says stop (or the time is up). */
async function frames(seat: string, until: (f: { event: string; data: any }[]) => boolean, ms = 6000, during?: () => void) {
  const ctl = new AbortController();
  const res = await fetch(`${base}/${seat}/transcript/stream`, { signal: ctl.signal });
  const reader = res.body!.getReader();
  const got: { event: string; data: any }[] = [];
  let buf = '';
  const deadline = Date.now() + ms;
  let poked = false;
  // ONE read in flight: a read that loses the race to the timer still resolves later, with data.
  let pending: Promise<ReadableStreamReadResult<Uint8Array>> | null = null;
  try {
    while (Date.now() < deadline && !until(got)) {
      pending ??= reader.read();
      const r = await Promise.race([pending, new Promise<null>((ok) => setTimeout(() => ok(null), 300))]);
      if (r) pending = null;
      if (r && !r.done) buf += Buffer.from(r.value).toString('utf-8');
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const event = /^event: (.*)$/m.exec(block)?.[1];
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (event && data) got.push({ event, data: JSON.parse(data) });
      }
      if (!poked && during && got.some((f) => f.event === 'live_text')) { poked = true; during(); }
    }
  } finally {
    ctl.abort();
  }
  return got;
}

test('a headless seat: the words so far at once, then cleared when the finished line lands (after its delta)', async () => {
  const UUID = 'line-live-1';
  const got = await frames(HEADLESS, (f) => f.some((x) => x.event === 'live_text' && x.data.text === ''), 8000, () => {
    appendFileSync(join(orch, 'state', 'headless', HEADLESS, 'events.jsonl'),
      ev({ type: 'assistant', uuid: UUID, message: { id: MID, role: 'assistant', content: [{ type: 'text', text: 'Streaming words' }] } }) + '\n');
    appendFileSync(join(projects, `${SID}.jsonl`), JSON.stringify({
      type: 'assistant', uuid: UUID, timestamp: '2026-10-31T03:00:02Z',
      message: { id: MID, role: 'assistant', content: [{ type: 'text', text: 'Streaming words' }] },
    }) + '\n');
  });
  const live = got.filter((f) => f.event === 'live_text');
  assert.equal(live[0].data.text, 'Streaming words');
  assert.equal(live[0].data.agent_id, HEADLESS);
  assert.equal(live[0].data.message_id, MID);
  assert.equal(live[live.length - 1].data.text, '', 'cleared');
  const deltaAt = got.findIndex((f) => f.event === 'delta' && f.data.items.some((it: any) => it.uuid === UUID));
  const clearAt = got.findIndex((f) => f.event === 'live_text' && f.data.text === '');
  assert.ok(deltaAt >= 0 && deltaAt < clearAt, 'the finished line arrives first, then the live words go: no gap');
});

test('a tmux seat never gets a live_text frame, even with a supervisor log on disk', async () => {
  const got = await frames(TMUX, () => false, 2500);
  assert.ok(got.some((f) => f.event === 'snapshot'), 'its transcript stream works as before');
  assert.equal(got.filter((f) => f.event === 'live_text').length, 0);
});

test('a headless seat in Dev (the TUI owns the session) gets none either', async () => {
  const d = seatDir(HEADLESS);
  writeFileSync(join(d, 'events.jsonl'), streamed.join('\n') + '\n');
  writeFileSync(join(d, 'dev'), '');
  try {
    const got = await frames(HEADLESS, () => false, 2500);
    assert.equal(got.filter((f) => f.event === 'live_text' && f.data.text).length, 0);
  } finally {
    rmSync(join(d, 'dev'), { force: true });
  }
});
