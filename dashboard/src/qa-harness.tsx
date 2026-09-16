/**
 * Isolated QA harness for the transcript-driven chat renderer.
 * Renders TranscriptChatView against a captured fixture so the puppeteer loop
 * can render→screenshot→critique without any live service. Not shipped.
 *
 * URL params: ?fixture=<name>&state=<liveState>&w=<px>&stranded=<text>
 */
import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import TranscriptChatView, { type LiveState } from './components/chat/TranscriptChatView';
import ChatInput from './components/chat/ChatInput';
import type { ChatItem } from './lib/transcript';

const q = new URLSearchParams(location.search);

// ?input=<case> — mount ChatInput with a stubbed /inject fetch to verify the
// 409/force banner UI without any live injection.
const inputCase = q.get('input');
if (inputCase) {
  const CANNED: Record<string, { status: number; body: any }> = {
    ok: { status: 200, body: { injected: true, output: [] } },
    active: { status: 409, body: { injected: false, busy: true, reason: 'busy', state: 'working', activity: 'Active turn' } },
    composer: { status: 409, body: { injected: false, busy: true, reason: 'busy', state: 'idle', activity: 'Composer has unsubmitted text', composer_text: 'wait, let me check the migration first' } },
    stranded: { status: 409, body: { injected: false, busy: true, reason: 'busy', state: 'stranded', activity: 'has unsent typed input in its composer', stranded: { text: 'tmux inject into orchestra-builder', age_s: 4200 } } },
    unverified: { status: 502, body: { injected: false, reason: 'unverified_submit', state: 'idle' } },
  };
  const origFetch = window.fetch.bind(window);
  window.fetch = ((input: any, init?: any) => {
    const url = typeof input === 'string' ? input : input?.url || '';
    if (url.includes('/inject')) {
      const forced = init?.body && JSON.parse(init.body).force;
      const pick = forced ? CANNED.ok : (CANNED[inputCase] || CANNED.active);
      return Promise.resolve(new Response(JSON.stringify(pick.body), { status: pick.status, headers: { 'Content-Type': 'application/json' } }));
    }
    return origFetch(input, init);
  }) as typeof window.fetch;
}
const fixture = q.get('fixture') || 'gm';
const state = (q.get('state') || 'idle') as LiveState;
const width = parseInt(q.get('w') || '0') || 0;
const stranded = q.get('stranded') || undefined;
const live = q.get('live') === '1'; // fetch live transcript from qa-server :8791
const QA_SERVER = 'http://127.0.0.1:8791';

function Harness() {
  const [items, setItems] = useState<ChatItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const url = live
      ? `${QA_SERVER}/api/agents/${encodeURIComponent(fixture)}/transcript?limit=200`
      : `/qa-fixtures/${fixture}.transcript.json`;
    const load = () =>
      fetch(url)
        .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
        .then((d) => setItems(live ? d.items : d))
        .catch((e) => setErr(String(e)));
    load();
    if (live) { const iv = setInterval(load, 3000); return () => clearInterval(iv); }
  }, []);

  const frame = width
    ? { width: `${width}px`, height: '844px', margin: '16px auto' }
    : { width: '820px', height: '900px', margin: '16px auto' };

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#e5e5e5' }}>
      <div style={{ padding: '8px 16px', fontSize: 12, color: '#888', fontFamily: 'monospace' }}>
        harness · fixture={fixture} · state={state} · w={width || 'wide'}
      </div>
      <div
        style={{
          ...frame,
          display: 'flex',
          flexDirection: 'column',
          border: '1px solid #262626',
          borderRadius: 12,
          overflow: 'hidden',
          background: '#0a0a0a',
        }}
      >
        {err ? (
          <div style={{ padding: 16, color: '#f87171' }}>fixture error: {err}</div>
        ) : items ? (
          <TranscriptChatView agentId={fixture} fixtureItems={items} state={state} strandedText={stranded} />
        ) : (
          <div style={{ padding: 16, color: '#888' }}>loading fixture…</div>
        )}
        {inputCase && (
          <ChatInput agentId="qa-test" placeholder={`inject case: ${inputCase} (click Inject)`} />
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById('qa-root')!).render(
  <StrictMode>
    <Harness />
  </StrictMode>
);
