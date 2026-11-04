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
 *
 * window.__rxEmit(ref, emoji, agentId) sends a stream event as if that agent had reacted (a live update).
 */
import { StrictMode } from 'react';
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
const sceneItems = q.get('scene') === 'tools' ? TOOLS : ITEMS;

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
  private ls: Listener[] = [];
  constructor(url: string) { this.url = url; sources.add(this); }
  addEventListener(type: string, f: Listener) { if (type === 'reaction') this.ls.push(f); }
  close() { sources.delete(this); }
  emit(c: ReactionChange) { this.ls.forEach((f) => f({ data: JSON.stringify(c) })); }
}
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
void Promise.all([import('./components/chat/TranscriptChatView'), import('@tanstack/react-query')]).then(([chat, rq]) => {
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
  draw(sceneItems, null);
});
