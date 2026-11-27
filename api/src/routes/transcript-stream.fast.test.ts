/**
 * A tmux seat's stream hears its log grow within a quarter second (gm msg_e8195a72: the live status line changes within
 * 1.5 s of a tool call landing in the session log). The full beat (re-resolve the session, re-read) stays at 1 s; every
 * 250 ms the stream only stats the file it already follows, and runs the beat at once when it grew.
 *
 * Run: npx tsx --test src/routes/transcript-stream.fast.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, appendFileSync, rmSync } from 'fs';
import { join } from 'path';

const TMUX = 'zz-fast-tmux-demo';                 // never a real tmux session on any box
const SID = '44444444-2222-4333-8444-000000000003';

const root = testTmpDir('stream-fast-');
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
writeFileSync(process.env.HEADLESS_SEATS_FILE, JSON.stringify({ seats: {} }));
writeFileSync(join(orch, 'state', 'agents', `${TMUX}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));
const log = join(projects, `${SID}.jsonl`);
writeFileSync(log, JSON.stringify({ type: 'user', uuid: 'u-0', timestamp: '2026-11-22T17:40:00Z', message: { role: 'user', content: 'go' } }) + '\n');
const call = (n: number) => JSON.stringify({
  type: 'assistant', uuid: `a-${n}`, timestamp: '2026-11-22T17:40:01Z',
  message: { id: `msg_${n}`, role: 'assistant', content: [{ type: 'tool_use', id: `toolu_${n}`, name: 'Bash', input: { command: 'ls', description: `Step ${n}` } }] },
}) + '\n';

const { TranscriptTailer } = await import('../services/transcript-tail.js');
const { default: router, currentStep, probeForTests } = await import('./transcript-stream.js');
const { seatTmuxSession } = await import('./chat-transcript.js');
const express = (await import('express')).default;
const app = express();
app.use('/api/agents', router);
const server = app.listen(0);
after(() => { server.close(); rmSync(root, { recursive: true, force: true }); });
const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents`;

test('the tailer says when the file it follows has grown, without re-resolving it', () => {
  let resolves = 0;
  const t = new TranscriptTailer({ agentId: TMUX, limit: 50, resolve: () => { resolves++; return { path: log, sid: SID }; } });
  const off = t.subscribe(() => {});
  t.tick();
  const n = resolves;
  assert.equal(t.grew(), false);
  appendFileSync(log, call(90));
  assert.equal(t.grew(), true);
  assert.equal(resolves, n, 'a stat only');
  t.tick();
  assert.equal(t.grew(), false);
  off();
});

test('a call written to a tmux seat\'s log reaches the stream within a quarter second, not at the next 1 s beat', async () => {
  const ctl = new AbortController();
  const res = await fetch(`${base}/${TMUX}/transcript/stream`, { signal: ctl.signal });
  const reader = res.body!.getReader();
  let buf = '';
  const seen: { uuid: string; at: number }[] = [];
  // ONE read in flight across calls: a read left pending by the last call still takes the next chunk.
  let pending: Promise<ReadableStreamReadResult<Uint8Array>> | null = null;
  const read = async (until: () => boolean, ms: number) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline && !until()) {
      pending ??= reader.read();
      const r = await Promise.race([pending, new Promise<null>((ok) => setTimeout(() => ok(null), 50))]);
      if (!r) continue;
      pending = null;
      if (r.done) break;
      buf += Buffer.from(r.value).toString('utf-8');
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        if (/^event: delta$/m.test(block)) {
          const data = JSON.parse(/^data: (.*)$/m.exec(block)![1]);
          for (const it of data.items) seen.push({ uuid: it.uuid, at: Date.now() });
        }
      }
    }
  };
  const has = (u: string) => () => seen.some((s) => s.uuid === u);
  try {
    await read(() => false, 400);                                  // the snapshot
    // Each call is written just after the previous one arrived: on a 1 s beat alone that is a full second away.
    const lags: number[] = [];
    for (const n of [1, 2, 3]) {
      const t0 = Date.now();
      appendFileSync(log, call(n));
      await read(has(`a-${n}`), 2500);
      const got = seen.find((s) => s.uuid === `a-${n}`);
      assert.ok(got, `a-${n} arrived`);
      lags.push(got!.at - t0);
    }
    assert.ok(Math.max(...lags) < 600, `lags ${lags.join(', ')} ms`);
  } finally {
    ctl.abort();
  }
});

test('the agent list reads an open stream\'s step for that seat, and forgets it when the last client goes', async () => {
  const { streamStep } = await import('../services/live-step.js');
  const ctl = new AbortController();
  const res = await fetch(`${base}/${TMUX}/transcript/stream`, { signal: ctl.signal });
  const reader = res.body!.getReader();
  try {
    await Promise.race([reader.read(), new Promise((ok) => setTimeout(ok, 300))]);
    // No probe verdict for a seat with no pane: the stream's step is "none", and the list says the same.
    assert.equal(streamStep(TMUX), null);
  } finally {
    ctl.abort();
  }
  const deadline = Date.now() + 2000;
  while (streamStep(TMUX) !== undefined && Date.now() < deadline) await new Promise((ok) => setTimeout(ok, 50));
  assert.equal(streamStep(TMUX), undefined);
});

// Gate round 1 on #706 (fenced reviewer): the wiring itself, with a probe verdict (none: every step is null either way).
const busyProbe = () => new Map([[seatTmuxSession(TMUX)!, { state: 'thinking', state_age_s: 1 } as any]]);

test('a tmux seat\'s step is its transcript\'s: the running call\'s own description, not the probe\'s word', () => {
  probeForTests(busyProbe());
  try {
    appendFileSync(log, call(70));
    const t = new TranscriptTailer({ agentId: TMUX, limit: 50, resolve: () => ({ path: log, sid: SID }) });
    const off = t.subscribe(() => {});
    const s = currentStep(TMUX, { headless: false, tailer: t } as any);
    assert.deepEqual(s && { kind: s.kind, tool: s.tool, summary: s.summary, described: s.described },
      { kind: 'tool', tool: 'Bash', summary: 'Step 70', described: true });
    off();
  } finally {
    probeForTests(null);
  }
});

test('a tmux seat\'s step goes out within a quarter second of its call landing, not at the next 1 s beat', async () => {
  probeForTests(busyProbe());
  const ctl = new AbortController();
  try {
    const res = await fetch(`${base}/${TMUX}/transcript/stream`, { signal: ctl.signal });
    const reader = res.body!.getReader();
    let buf = '';
    const steps: { summary?: string; at: number }[] = [];
    let pending: Promise<ReadableStreamReadResult<Uint8Array>> | null = null;
    const read = async (until: () => boolean, ms: number) => {
      const deadline = Date.now() + ms;
      while (Date.now() < deadline && !until()) {
        pending ??= reader.read();
        const r = await Promise.race([pending, new Promise<null>((ok) => setTimeout(() => ok(null), 50))]);
        if (!r) continue;
        pending = null;
        if (r.done) break;
        buf += Buffer.from(r.value).toString('utf-8');
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          if (/^event: step$/m.test(block)) steps.push({ summary: JSON.parse(/^data: (.*)$/m.exec(block)![1]).summary, at: Date.now() });
        }
      }
    };
    await read(() => false, 400);
    const lags: number[] = [];
    for (const n of [81, 82, 83]) {
      const t0 = Date.now();
      appendFileSync(log, call(n));
      await read(() => steps.some((x) => x.summary === `Step ${n}`), 2500);
      const got = steps.find((x) => x.summary === `Step ${n}`);
      assert.ok(got, `the step for call ${n} arrived`);
      lags.push(got!.at - t0);
    }
    assert.ok(Math.max(...lags) < 600, `lags ${lags.join(', ')} ms`);
  } finally {
    ctl.abort();
    probeForTests(null);
  }
});
