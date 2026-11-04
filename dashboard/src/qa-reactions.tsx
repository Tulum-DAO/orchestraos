/**
 * Reactions QA harness (dev server only: qa-reactions.html; `vite build` builds index.html alone, so it never ships).
 * The agent chat (TranscriptChatView) on a fixed transcript whose items carry server refs, with /api/reactions, its
 * stream and /api/me answered here in memory, so a screenshot run needs no API, no agent and no network.
 *
 *   ?skin=bubbles|modern   the chat style (default bubbles)
 *   ?theme=dark|light      (default dark)
 *   ?scene=tools           a chat with tool runs (Bubbles' thin lines), a table reply and a code reply, and
 *                          window.__qaStream('mid' | 'done') to show a reply mid-stream (a tool still running, the live
 *                          overlay) and the frame right after its hand-off to the finished transcript row
 *   ?scene=stream          a reply streamed in on a timed fake stream (window.__qaPlay(), resolves when it is over):
 *                          realistic bursts of one to three words, a whole line at once, a long pause, two paragraphs,
 *                          a numbered list with bold, code and a link, a heading and a fenced code block, one tool run
 *                          between its two text blocks, each block handed off to its transcript row as the server does
 *                          (its uuid once it is closed, then its line). window.__qaArrivals: when each block's words
 *                          arrived (performance.now(), the length so far), window.__qaMarks: the hand-offs.
 *
 *   ?scene=replay          the chat on its LIVE path (no fixture items: the transcript stream, liveTextOf, the stable
 *                          block ids, "Typing…", the hand-off rise), fed a recorded server lane: window.__qaReplay(events)
 *                          with events [{ t (ms), type: snapshot|delta|live_text|state, data }] as the API sends them
 *                          (.rx-shots/replay/sim-server.ts builds them from a seat's real transcript and event log). Each
 *                          event is its own task, as an EventSource delivers them; resolves after the last one.
 *                          window.__qaMarks: when each was delivered (.rx-shots/replay/replay.mjs measures the scroller,
 *                          the rows and each reply per frame around its hand-off). ?dock=1 adds the agent page's working row slot
 *                          (its height in the column's bottom padding while the agent is typing or working).
 *
 * window.__rxEmit(ref, emoji, agentId) sends a stream event as if that agent had reacted (a live update).
 */
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { useAgentTyping } from './stores/agentTyping';
import { createRoot } from 'react-dom/client';
import './index.css';
import type { ChatItem } from './lib/transcript';
import type { ReactionChange, StoredReaction } from './lib/reactionState';
import type { LiveText } from './lib/threadKit/view';

const q = new URLSearchParams(location.search);
const AGENT = 'qa-agent';
const SID = 'qa-session';
const u = (n: number) => `11111111-2222-4333-8444-0000000000${String(n).padStart(2, '0')}`;
const ref = (n: number) => `${SID}~${u(n)}#0`;
const at = (m: number) => new Date(Date.UTC(2026, 10, 2, 10, m)).toISOString();
const me = { kind: 'user' as const, id: 'sam' };
const agent = { kind: 'agent' as const, id: AGENT };
const dev = { kind: 'agent' as const, id: 'dev' };

const say = (n: number, role: 'user' | 'assistant', text: string): ChatItem =>
  ({ kind: 'text', role, text, uuid: u(n), ref: ref(n), ts: at(n) }) as unknown as ChatItem;
const ITEMS: ChatItem[] = [
  say(1, 'user', 'Can you check the deploy finished?'),
  say(2, 'assistant', 'Yes: the deploy finished at 10:02 and every health check is green.'),
  say(3, 'user', 'Great, thanks'),
  say(4, 'assistant', 'Here is the short version of what changed:\n\n- the reactions store\n- the API and its stream\n- the server-stamped message refs'),
  say(5, 'user', 'Ship it'),
];

// ?scene=tools: tool runs between the bubbles.
const run = (n: number, tool: string, input: Record<string, unknown>, result?: string, isError = false): ChatItem[] => {
  const id = `toolu_qa${n}`;   // the same call in two frames (running, then done) keeps its id
  const use = { kind: 'tool_use', role: 'assistant', tool, input, summary: '', id, uuid: u(n), ts: at(n) };
  return result === undefined ? [use as unknown as ChatItem]
    : [use as unknown as ChatItem, { kind: 'tool_result', role: 'user', text: result, tool_use_id: id, is_error: isError, uuid: u(n + 50), ts: at(n + 1) } as unknown as ChatItem];
};
const TOOLS: ChatItem[] = [
  say(11, 'user', 'Is the deploy healthy? Check the box.'),
  say(12, 'assistant', 'Checking the box now.'),
  ...run(13, 'Bash', { command: 'D=/home/orchestra/orchestraos; cd $D && git log --oneline -3' }, 'f3662c9 fix(dashboard): no Typing or live bubble\n355a970 fix(reactions): the wake watcher snapshots\n49fd99e fix(arturo): ChatMirror never notes a reaction wake'),
  ...run(15, 'Read', { file_path: '/home/orchestra/orchestraos/dashboard/package.json' }, '{\n  "name": "dashboard",\n  "private": true,\n  "version": "0.0.0"\n}'),
  ...run(17, 'Bash', { command: 'curl -sf http://127.0.0.1:8080/health | jq .' }, '{\n  "ok": true,\n  "api": "up",\n  "web": "up"\n}'),
  say(19, 'assistant', 'All green. The summary:\n\n| check | state | note |\n|---|---|---|\n| api | ok | 12ms |\n| web | ok | served from dist |\n| git | clean | f3662c9 |'),
  say(20, 'user', 'And the last log lines?'),
  ...run(21, 'Bash', { command: 'tail -n 3 /var/log/orchestra/dashboard.log' }, '10:02:11 GET /api/agents 200 4ms\n10:02:12 GET /api/health 200 1ms\n10:02:14 SSE /api/agents/dev/stream open'),
  say(23, 'assistant', 'The last three lines:\n\n```log\n10:02:11 GET /api/agents 200 4ms\n10:02:12 GET /api/health 200 1ms\n10:02:14 SSE /api/agents/dev/stream open\n```\n\nNothing unusual.'),
];
const STREAM_USER = say(30, 'user', 'Run the tests and tell me where lint stands.');
const STREAM_LEAD = say(31, 'assistant', 'Running the suite.');
const STREAM_TEXT = 'The suite passed: 1045 tests, no failures.\n\nLint is at 274 problems, exactly the cap, so nothing new.';
const STREAM_LIVE: LiveText = { text: 'The suite passed: 1045 tests, no failures.\n\nLint is at 274 prob' };
const STREAM: Record<'mid' | 'done', { items: ChatItem[]; live: LiveText | null }> = {
  mid: { items: [...TOOLS, STREAM_USER, STREAM_LEAD, ...run(32, 'Bash', { command: 'cd dashboard && npm test' }, 'tests 1045\npass 1045\nfail 0'), ...run(34, 'Bash', { command: 'npx eslint . | tail -1' })], live: STREAM_LIVE },
  done: { items: [], live: null },
};
STREAM.done.items = [...TOOLS, STREAM_USER, STREAM_LEAD, ...run(32, 'Bash', { command: 'cd dashboard && npm test' }, 'tests 1045\npass 1045\nfail 0'),
  ...run(34, 'Bash', { command: 'npx eslint . | tail -1' }, '✖ 274 problems (260 errors, 14 warnings)'), say(36, 'assistant', STREAM_TEXT)];
// ?scene=stream: the timed fake stream.
const PLAY_USER = say(40, 'user', 'Did the deploy go through? And what actually changed?');
const PLAY_A = 'I checked the deploy on the box, and **everything is green**. Here is what I looked at, in order:\n\n'
  + '1. The API health route answered `200` in 12 ms, from the same process that served the last release.\n'
  + '2. The web build is the one from `f3662c9`, served from `dist` with the right cache headers.\n'
  + '3. The stream endpoint held its connection for a full minute without a reconnect.\n\n'
  + 'The details are in the [deploy notes](https://example.com/deploy) if you want to read them later.';
const PLAY_B = '## What changed\n\nTwo things moved in this release. The reveal queue now drives the agent chat as well as the '
  + 'call transcript, and the scroller follows the words as they appear instead of snapping to each burst.\n\n'
  + '```ts\nconst plan = new StreamPlan();\nplan.update(text, done);\npacer.setKnown(plan.units, done);\npacer.tick(performance.now());\n```\n\n'
  + 'That is the whole change. Nothing else in the thread moves while a reply streams, and an old message never animates.';
const PLAY_BASE: ChatItem[] = [...ITEMS, ...TOOLS, PLAY_USER];

/** The chunks a block arrives in: one to three words at a time (now and then half a word), 35–85 ms apart; `whole`:
 *  lines that arrive at once; `pauseBefore`: a pause before the chunk that starts with these words. */
function chunksOf(text: string, seed: number, whole: string[], pauseBefore: string, pauseMs: number): { at: number; len: number }[] {
  let x = seed;
  const rnd = () => { x = (x + 0x6D2B79F5) | 0; let t = Math.imul(x ^ (x >>> 15), 1 | x); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const out: { at: number; len: number }[] = [];
  let i = 0; let at = 0;
  while (i < text.length) {
    const line = whole.find((l) => text.startsWith(l, i));
    let len: number;
    if (line) len = line.length + 1;
    else {
      const words = text.slice(i).match(/\S+\s*/g) ?? [text.slice(i)];
      const n = Math.min(words.length, 1 + Math.floor(rnd() * 3));
      len = words.slice(0, n).join('').length;
      if (rnd() < 0.15 && words[n - 1].trim().length > 5) len -= Math.ceil(words[n - 1].trim().length / 2);   // half a word
    }
    at += text.startsWith(pauseBefore, i) ? pauseMs : 35 + Math.floor(rnd() * 50);
    i = Math.min(text.length, i + Math.max(1, len));
    out.push({ at, len: i });
  }
  return out;
}

const sceneItems = q.get('scene') === 'tools' ? TOOLS : q.get('scene') === 'stream' ? PLAY_BASE : ITEMS;

const store: Record<string, StoredReaction[]> = {
  [ref(2)]: [{ emoji: '👍', reactor: me, at: at(10) }, { emoji: '👍', reactor: dev, at: at(11) }, { emoji: '🎉', reactor: dev, at: at(12) }],
  [ref(3)]: [{ emoji: '❤️', reactor: agent, at: at(13) }],
};

// The stream: one fake EventSource the harness drives.
type Listener = (e: { data: string }) => void;
const sources = new Set<FakeEventSource>();
class FakeEventSource {
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  url: string;
  private ls = new Map<string, Listener[]>();
  constructor(url: string) { this.url = url; sources.add(this); }
  addEventListener(type: string, f: Listener) { this.ls.set(type, [...(this.ls.get(type) ?? []), f]); }
  close() { sources.delete(this); }
  emit(c: ReactionChange) { this.send('reaction', c); }
  send(type: string, data: unknown) { (this.ls.get(type) ?? []).forEach((f) => f({ data: JSON.stringify(data) })); }
}
/** ?scene=replay: the transcript stream the chat opened (its URL names the transcript stream). */
const transcriptSource = () => [...sources].find((x) => x.url.includes('/transcript/stream'));
let replaySnapshot: unknown[] = [];
(window as unknown as { EventSource: unknown }).EventSource = FakeEventSource;

function change(r: string, reactor: StoredReaction['reactor'], emoji: string): ReactionChange {
  const list = store[r] ?? [];
  const mine = list.find((x) => x.reactor.kind === reactor.kind && x.reactor.id === reactor.id);
  const next = mine?.emoji === emoji ? null : emoji;
  const rest = list.filter((x) => x !== mine);
  store[r] = next ? [...rest, { emoji: next, reactor, at: new Date().toISOString() }] : rest;
  const c = { surface: 'agent-chat' as const, conv: AGENT, ref: r, reactor, emoji: next, at: new Date().toISOString() };
  sources.forEach((s) => s.emit(c));
  return c;
}
(window as unknown as { __rxEmit: (r: string, e: string, id: string) => void }).__rxEmit =
  (r, e, id) => { change(r, { kind: 'agent', id }, e); };

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  if (url.startsWith('/api/me')) return json({ username: me.id, role: 'admin' });
  if (/^\/api\/agents\/[^/]+\/transcript\?/.test(url)) return json({ items: replaySnapshot });
  if (url.startsWith('/api/reactions') && (!init?.method || init.method === 'GET')) return json({ surface: 'agent-chat', conv: AGENT, reactions: store });
  if (url.startsWith('/api/reactions') && init?.method === 'PUT') {
    const b = JSON.parse(String(init.body));
    return json({ ok: true, ...change(b.ref, me, b.emoji) });
  }
  return json({ error: 'not in the harness' }, 404);
}) as typeof window.fetch;

document.documentElement.classList.toggle('dark', (q.get('theme') ?? 'dark') === 'dark');
try { localStorage.setItem('orchestra.agentPrefs', JSON.stringify({ [AGENT]: q.get('skin') === 'modern' ? 'modern' : 'bubbles' })); } catch { /* storage blocked */ }

// Loaded after the stubs and the saved style are in place (the prefs store reads them when its module loads).
// window.__qaStream draws the chat again on a stage of a reply's stream (scene=tools).
void Promise.all([import('./components/chat/TranscriptChatView'), import('@tanstack/react-query'), import('./lib/revealStore')]).then(([chat, rq, rs]) => {
  (window as unknown as { __revealPending: unknown }).__revealPending = rs.revealPending;
  const TranscriptChatView = chat.default;
  const client = new rq.QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(document.getElementById('qa-root')!);
  const draw = (items: ChatItem[], live: LiveText | null) => root.render(
    <StrictMode>
      <rq.QueryClientProvider client={client}>
        <div className="bg-background text-foreground" style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
          <TranscriptChatView agentId={AGENT} fixtureItems={items} state={live ? 'working' : 'idle'} hideStatusBar liveText={live} />
        </div>
      </rq.QueryClientProvider>
    </StrictMode>,
  );
  (window as unknown as { __qaStream: (stage: 'mid' | 'done') => void }).__qaStream = (stage) => draw(STREAM[stage].items, STREAM[stage].live);
  // The timed stream: each block grows chunk by chunk, gets its uuid once closed, then lands as its transcript line
  // (in the same draw the live words drop it, as liveTextOf does with the stream's own items).
  const w = window as unknown as { __qaPlay: () => Promise<void>; __qaArrivals: { t: number; id: string; len: number }[]; __qaMarks: { t: number; what: string }[] };
  w.__qaArrivals = []; w.__qaMarks = [];
  (window as unknown as { __qaTexts: Record<string, string> }).__qaTexts = { 'qa-A': PLAY_A, 'qa-B': PLAY_B };
  w.__qaPlay = async () => {
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    let items = PLAY_BASE;
    const stream = async (id: string, uuid: string, text: string, chunks: { at: number; len: number }[]) => {
      let t0 = 0;
      for (const c of chunks) {
        await sleep(c.at - t0); t0 = c.at;
        draw(items, { text: text.slice(0, c.len), blocks: [{ id, uuid: null, text: text.slice(0, c.len) }] });
        w.__qaArrivals.push({ t: performance.now(), id, len: c.len });
      }
      await sleep(120);
      draw(items, { text, blocks: [{ id, uuid, text }] });                         // closed: its uuid
      w.__qaMarks.push({ t: performance.now(), what: `${id} closed` });
      await sleep(450);
      items = [...items, say(Number(uuid.slice(-2)), 'assistant', text)];          // its line lands
      draw(items, null);
      w.__qaMarks.push({ t: performance.now(), what: `${id} landed` });
    };
    await sleep(300);
    await stream('qa-A', u(41), PLAY_A, chunksOf(PLAY_A, 7, ['2. The web build is the one from `f3662c9`, served from `dist` with the right cache headers.'], 'The details', 1200));
    await sleep(250);
    items = [...items, ...run(42, 'Bash', { command: 'cd dashboard && npm test' })];
    draw(items, null);
    await sleep(900);
    items = [...items.slice(0, -1), ...run(42, 'Bash', { command: 'cd dashboard && npm test' }, 'tests 1062\npass 1062\nfail 0')];
    draw(items, null);
    await sleep(350);
    await stream('qa-B', u(45), PLAY_B, chunksOf(PLAY_B, 11, ['const plan = new StreamPlan();\nplan.update(text, done);'], 'That is', 900));
    await sleep(1500);
  };
  // ?scene=replay: the live path, fed a recorded server lane.
  type ReplayEvent = { t: number; type: string; data: { items?: unknown[]; state?: string } };
  const replay = window as unknown as { __qaReplay: (events: ReplayEvent[]) => Promise<void> };
  const drawLive = (state: string) => root.render(
    <StrictMode>
      <rq.QueryClientProvider client={client}>
        <div className="bg-background text-foreground" style={{ height: '100vh', display: 'flex', flexDirection: 'column', position: 'relative' }}>
          <MemoryRouter><TranscriptChatView agentId={AGENT} state={state} hideStatusBar /></MemoryRouter>
        </div>
      </rq.QueryClientProvider>
    </StrictMode>,
  );
  replay.__qaReplay = async (events) => {
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    w.__qaMarks = [];
    replaySnapshot = events.find((e) => e.type === 'snapshot')?.data.items ?? [];
    let state = 'idle';
    // ?dock=1: the agent page's working row (pages/AgentPage.tsx), over the dock while the agent types or works: its
    // height (34 px: the avatar's 24 and the row's padding) in the thread's bottom space, a reader near the bottom kept
    // there, as the page does when it measures the row.
    let dockH = -1;
    const dock = () => {
      const h = useAgentTyping.getState().byAgent[AGENT] || state === 'working' ? 34 : 0;
      const sc = document.querySelector<HTMLElement>('[data-testid="transcript-scroll"]');
      if (q.get('dock') !== '1' || !sc || h === dockH) return;
      dockH = h;
      const pinned = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120;
      sc.style.setProperty('--composer-h', `${h}px`);
      if (pinned) sc.scrollTop = sc.scrollHeight;
    };
    useAgentTyping.subscribe(dock);
    drawLive(state);
    while (!transcriptSource()) await sleep(10);
    const t0 = performance.now();
    for (const e of events) {
      const wait = e.t - (performance.now() - t0);
      await sleep(Math.max(0, wait));                                  // each event its own task
      if (e.type === 'state') { state = e.data.state ?? state; drawLive(state); dock(); }
      else transcriptSource()?.send(e.type, e.data);
      w.__qaMarks.push({ t: performance.now(), what: e.type + (e.type === 'state' ? ` ${state}` : '') });
    }
    await sleep(1500);
  };
  if (q.get('scene') !== 'replay') draw(sceneItems, null);
});
