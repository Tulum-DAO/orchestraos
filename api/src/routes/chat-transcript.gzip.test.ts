/**
 * The per-seat JSON routes are gzipped for a client that accepts it (lib/compress.ts, mounted by
 * server.ts): a transcript page with Accept-Encoding: gzip carries Content-Encoding: gzip and
 * decodes to the same JSON; the transcript SSE stream is gzipped too, every event flushed as it is
 * written, and its snapshot on connect is 40 items.
 *
 * Run: npx tsx --test src/routes/chat-transcript.gzip.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { gunzipSync } from 'node:zlib';
import type { IncomingHttpHeaders } from 'node:http';
import Database from 'better-sqlite3';

const SEAT = 'zz-gzip-seat';                   // never a real tmux session on any box
const SID = '55555555-2222-4333-8444-000000000001';

const root = testTmpDir('transcript-gzip-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
{
  const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
  db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(SEAT, '/repo');
  db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (1, ?, 1, ?, ?)').run(SEAT, SID, '2026-10-20T00:00:00Z');
  db.prepare("INSERT INTO canonical VALUES (?, 1, ?, 'online')").run(SEAT, SEAT);
  db.close();
}
const ts = (i: number) => new Date(Date.UTC(2026, 9, 22, 10, 0, i)).toISOString();
const lines: string[] = [];
// 200 items: longer than any default or explicit window under test.
for (let i = 0; i < 100; i++) {
  lines.push(JSON.stringify({ type: 'user', uuid: `u${i}`, timestamp: ts(i), message: { role: 'user', content: `question ${i}` } }));
  lines.push(JSON.stringify({ type: 'assistant', uuid: `a${i}`, timestamp: ts(i),
    message: { role: 'assistant', content: [{ type: 'text', text: `answer ${i} ${'words '.repeat(60)}` }] } }));
}
writeFileSync(join(projects, `${SID}.jsonl`), lines.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));

const { default: router } = await import('./chat-transcript.js');
const { default: streamRouter } = await import('./transcript-stream.js');
const { apiCompression, compressibleResponse } = await import('../lib/compress.js');
const { clearFirstPageCache } = await import('../services/transcript-first-page-cache.js');

async function withServer<T>(fn: (base: string) => Promise<T>): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use(apiCompression());
  a.use('/api/agents', router);
  a.use('/api/agents', streamRouter);
  const server = a.listen(0);
  try {
    return await fn(`http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}`);
  } finally {
    server.closeAllConnections();   // an SSE client that dropped mid-frame must not hold the process open
    server.close();
  }
}

test('a transcript page is gzipped for a client that accepts it, and decodes to the same JSON', { timeout: 10_000 }, async () => {
  clearFirstPageCache();
  await withServer(async (base) => {
    const plain = await fetch(`${base}/transcript`, { headers: { 'accept-encoding': 'identity' } });
    assert.equal(plain.headers.get('content-encoding'), null);
    const plainText = await plain.text();
    assert.ok(plainText.length > 1024, 'the fixture page is past the threshold');

    // Node's fetch would unzip for us: read the raw bytes to prove what was on the wire.
    const http = await import('node:http');
    const raw = await new Promise<{ headers: IncomingHttpHeaders; body: Buffer }>((resolve, reject) => {
      http.get(`${base}/transcript`, { headers: { 'accept-encoding': 'gzip' } }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve({ headers: res.headers, body: Buffer.concat(chunks) }));
      }).on('error', reject);
    });
    assert.equal(raw.headers['content-encoding'], 'gzip');
    assert.ok(raw.body.length < plainText.length / 2, `gzip saves bytes (${raw.body.length} vs ${plainText.length})`);
    assert.equal(gunzipSync(raw.body).toString('utf-8'), plainText, 'the same JSON, byte for byte');
    assert.ok(String(raw.headers.vary || '').toLowerCase().includes('accept-encoding'));
    // The cached page (second read) is gzipped too, and still the same JSON.
    const again = await fetch(`${base}/transcript`, { headers: { 'accept-encoding': 'gzip' } });
    assert.equal(again.headers.get('content-encoding'), 'gzip');
    assert.equal(await again.text(), plainText);
  });
});

test('the SSE stream is gzipped with a flush per event: the 40-item snapshot arrives on its own', { timeout: 10_000 }, async () => {
  // Raw http + a streaming gunzip: the snapshot must come out of the compressor on connect, with
  // no later event to push it (the tailer sends nothing while the log is still; the heartbeat is
  // 25 s away). Without res.flush() the frame sits in the gzip buffer and this fails at 2 s.
  await withServer(async (base) => {
    const http = await import('node:http');
    const zlib = await import('node:zlib');
    const got = await new Promise<{ headers: IncomingHttpHeaders; frames: string[] }>((resolve, reject) => {
      const req = http.get(`${base}/transcript/stream`, { headers: { 'accept-encoding': 'gzip' } }, (res) => {
        const gunzip = zlib.createGunzip();
        let text = '';
        const timer = setTimeout(() => { req.destroy(); reject(new Error(`no snapshot within 2 s; got ${JSON.stringify(text.slice(0, 200))}`)); }, 2000);
        gunzip.on('data', (c: Buffer) => {
          text += c.toString('utf-8');
          if (/^event: snapshot$/m.test(text) && text.endsWith('\n\n')) {
            clearTimeout(timer);
            req.destroy();
            resolve({ headers: res.headers, frames: text.split('\n\n').filter(Boolean) });
          }
        });
        gunzip.on('error', (e) => { clearTimeout(timer); reject(e); });
        res.pipe(gunzip);
      });
      req.on('error', (e: any) => { if (e.code !== 'ECONNRESET') reject(e); });
    });
    assert.equal(got.headers['content-type'], 'text/event-stream');
    assert.equal(got.headers['content-encoding'], 'gzip', 'the stream is gzipped on the wire');
    const snap = got.frames.find((f) => f.startsWith('event: snapshot'))!;
    const data = JSON.parse(snap.split('\n').find((l) => l.startsWith('data: '))!.slice(6));
    assert.equal(data.items.length, 40, 'the snapshot on connect is 40 items (was 150) on a 200-item log');
    assert.equal(data.items[data.items.length - 1].uuid, 'a99', 'the newest items');
  });
});

test('a client that does not accept gzip gets the same stream plain, and ?limit=150 (the live dashboard) snapshots 150', { timeout: 10_000 }, async () => {
  await withServer(async (base) => {
    const ctl = new AbortController();
    const deadline = setTimeout(() => ctl.abort(new Error('no snapshot within 5 s')), 5000);
    const r = await fetch(`${base}/transcript/stream?limit=150`, { headers: { 'accept-encoding': 'identity' }, signal: ctl.signal });
    try {
      assert.equal(r.status, 200);
      assert.equal(r.headers.get('content-type'), 'text/event-stream');
      assert.equal(r.headers.get('content-encoding'), null);
      const reader = r.body!.getReader();
      let text = '';
      while (!/^event: snapshot$/m.test(text) || !text.endsWith('\n\n')) {
        const { value, done } = await reader.read();
        if (done) break;
        text += Buffer.from(value).toString('utf-8');
      }
      const snap = text.split('\n\n').find((f) => f.startsWith('event: snapshot'))!;
      assert.equal(JSON.parse(snap.split('\n').find((l) => l.startsWith('data: '))!.slice(6)).items.length, 150, 'an explicit ?limit= is honoured exactly as before');
    } finally {
      clearTimeout(deadline);
      ctl.abort();
    }
  });
});

test('the filter: JSON yes; event streams and the media route no', { timeout: 10_000 }, () => {
  const req = (path: string) => ({ path, originalUrl: path, headers: {} }) as any;
  const res = (type: string, extra: Record<string, string> = {}, locals: Record<string, unknown> = {}) =>
    ({ locals, getHeader: (h: string) => ({ 'content-type': type, ...extra })[h.toLowerCase()] }) as any;
  assert.equal(compressibleResponse(req('/api/agents/x/transcript'), res('application/json; charset=utf-8')), true);
  assert.equal(compressibleResponse(req('/api/agents'), res('application/json; charset=utf-8')), true);
  assert.equal(compressibleResponse(req('/api/agents/x/transcript.md'), res('text/markdown; charset=utf-8')), true);
  assert.equal(compressibleResponse(req('/api/agents/x/transcript/stream'), res('text/event-stream')), false, 'an event stream that did not opt in');
  assert.equal(compressibleResponse(req('/api/agents/x/transcript/stream'), res('text/event-stream', {}, { gzipEventStream: true })), true, 'one that did (and flushes per event)');
  assert.equal(compressibleResponse(req('/api/status/stream'), res('application/json')), false, 'a stream path, whatever its type says');
  assert.equal(compressibleResponse(req('/api/activity/events'), res('application/json')), false);
  assert.equal(compressibleResponse(req('/api/activity/events'), res('text/event-stream')), false, 'the generic event routes stay plain');
  assert.equal(compressibleResponse(req('/api/media/abc'), res('image/png')), false);
  assert.equal(compressibleResponse(req('/api/media/abc'), res('application/json')), false, 'the media route is bytes, never recompressed');
  assert.equal(compressibleResponse(req('/api/agents/x/transcript'), res('application/json', { 'content-encoding': 'gzip' })), false, 'already encoded');
});

test('mounted the way server.ts mounts them: the media route and the activity events stay identity on the wire', { timeout: 10_000 }, async () => {
  // Inside a mounted router req.path is mount-relative ('/abc'); the filter must look at the full URL.
  const express = (await import('express')).default;
  const http = await import('node:http');
  const a = express();
  a.use(apiCompression());
  const media = express.Router();
  media.get('/:id', (req, res) => {
    // routes/media.ts's shape: a typed body with Content-Length, 206 + Content-Range for a Range header
    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Content-Length', '3000');
    if (req.headers.range) { res.status(206); res.setHeader('Content-Range', 'bytes 0-2999/6000'); }
    res.end('x'.repeat(3000));
  });
  const activity = express.Router();
  activity.get('/events', (_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${'y'.repeat(2000)}\n\n`);
    res.end();
  });
  a.use('/api/media', media);
  a.use('/api/activity', activity);
  const server = a.listen(0);
  const base = `http://127.0.0.1:${(server.address() as any).port}`;
  const raw = (path: string, headers: Record<string, string>) => new Promise<{ status: number; headers: IncomingHttpHeaders; body: Buffer }>((resolve, reject) => {
    http.get(`${base}${path}`, { headers }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body: Buffer.concat(chunks) }));
    }).on('error', reject);
  });
  try {
    const whole = await raw('/api/media/abc', { 'accept-encoding': 'gzip' });
    assert.equal(whole.status, 200);
    assert.equal(whole.headers['content-encoding'], undefined, 'media is bytes: never a Content-Encoding');
    assert.equal(whole.headers['content-length'], '3000', 'and its Content-Length survives');
    assert.equal(whole.body.length, 3000);
    const part = await raw('/api/media/abc', { 'accept-encoding': 'gzip', range: 'bytes=0-2999' });
    assert.equal(part.status, 206);
    assert.equal(part.headers['content-encoding'], undefined, 'a byte range is never compressed');
    assert.equal(part.headers['content-range'], 'bytes 0-2999/6000');
    assert.equal(part.headers['content-length'], '3000');
    const ev = await raw('/api/activity/events', { 'accept-encoding': 'gzip' });
    assert.equal(ev.headers['content-encoding'], undefined, 'the generic event route stays plain');
  } finally {
    server.closeAllConnections();
    server.close();
  }
});
