/**
 * Render primitives for the transcript-driven chat grammar:
 *  - UserBubble       (a real user/the operator prompt; system spawn-prompt collapses)
 *  - ThinkingCard     (collapsed by default)
 *  - ToolCard         (tool_use + paired result, collapsible; long output scrolls)
 * Assistant prose is rendered by <Markdown/>.
 */
import { useState } from 'react';
import Markdown from './Markdown';
import { toolSummary, type ToolBlock, type RenderNode } from '../../lib/transcript';
import { parseMessageSegments, hasPasteMarkers } from '../../lib/pastedText';
import { hasVoiceCallMarker, parseVoiceSegments } from '../../lib/voiceCall';
import VoiceCallCard from './VoiceCallCard';

const RESULT_COLLAPSE_CHARS = 600;

// A large paste embedded in a message → collapsible "Pasted text #n" card,
// rendered AT its position in the message flow (pasted-text grammar, read end).
export function PastedCard({ n, lines, content }: { n: number; lines: number; content: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-neutral-700 bg-neutral-900/60 overflow-hidden max-w-[85%]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-neutral-800/50"
      >
        <span className="text-[11px] text-neutral-400">📋</span>
        <span className="text-[11px] font-medium text-neutral-300">Pasted text #{n}</span>
        <span className="text-[10px] text-neutral-500">· {lines} lines</span>
        <span className="text-[10px] text-neutral-600 ml-auto">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <pre className="px-3 py-1.5 border-t border-neutral-800 bg-neutral-950 overflow-auto max-h-72 text-[11px] font-mono text-neutral-400 whitespace-pre-wrap break-words">
          {content}
        </pre>
      )}
    </div>
  );
}

// Same-message visual grouping (the operator-approved): bubbles + paste cards of ONE
// message read as one utterance — tight intra-message spacing + a thin
// sender-side connector rail (no inline placeholder chip; row order carries
// position, the card's #n anchors identity).
function MessageGroup({ text, sender }: { text: string; sender: 'user' | 'assistant' }) {
  const segs = parseMessageSegments(text);
  const isUser = sender === 'user';
  const rail = isUser ? 'bg-blue-500/25' : 'bg-neutral-700/60';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex ${isUser ? 'flex-row-reverse' : 'flex-row'} gap-1.5 max-w-[92%]`}>
        {/* connector rail spanning the whole group */}
        <div className={`w-[2.5px] rounded-full shrink-0 ${rail}`} />
        {/* tight-spaced rows (space-y-1 ≈ ⅓ of the ~space-y-2.5 between messages) */}
        <div className="min-w-0 flex-1 space-y-1">
          {segs.map((seg, i) =>
            seg.kind === 'prose' ? (
              isUser ? (
                <div key={i} className="flex justify-end">
                  <div className="rounded-2xl rounded-br-sm bg-blue-600/90 px-3.5 py-2 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words">
                    {seg.text}
                  </div>
                </div>
              ) : (
                <div key={i}><Markdown text={seg.text} /></div>
              )
            ) : (
              <div key={i} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                <PastedCard n={seg.n} lines={seg.lines} content={seg.content} />
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}

// Text glyphs (no emoji — emoji fall back to tofu boxes where no emoji font).
const TOOL_GLYPH: Record<string, string> = {
  Bash: '$', Read: '≡', Edit: '✎', Write: '✎', NotebookEdit: '✎',
  Glob: '⌕', Grep: '⌕', Task: '⛁', Agent: '⛁',
  WebFetch: '↗', WebSearch: '⌕', Skill: '✦', ToolSearch: '⌕',
};

// Relative "Xm ago" from an ISO timestamp (queued-native-render P1; P2 adds
// the viewer's local wall-clock alongside).
export function relativeAgo(ts?: string): string {
  if (!ts) return '';
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return '';
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 45) return 'just now';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

export function UserBubble({ text, isSystem, queued }: { text: string; isSystem?: boolean; queued?: boolean }) {
  const [open, setOpen] = useState(false);
  if (isSystem) {
    return (
      <div className="flex justify-center my-1">
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-[10px] text-neutral-500 hover:text-neutral-300 px-2 py-0.5 rounded-full border border-neutral-800 bg-neutral-900/40"
        >
          {open ? 'Hide' : 'Show'} session prompt ({Math.round(text.length / 100) / 10}k chars)
        </button>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600/90 px-3.5 py-2 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words">
        {text}
      </div>
      {queued && (
        <span className="mt-0.5 text-[9px] uppercase tracking-wide text-amber-400/90 px-1.5 py-[1px] rounded-full border border-amber-500/40 bg-amber-500/10">
          queued
        </span>
      )}
    </div>
  );
}

// B2 processed-queue batch: collapsed-by-default expandable div (tool-call-style),
// entries newest->oldest, each = agent · how-long-ago · body.
export function QueuedBatchCard({ count, entries }: { count: number; entries: { agent: string; sent_ts: string; body: string }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] w-full rounded-lg border border-neutral-800 bg-neutral-900/40 overflow-hidden">
        <button
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-neutral-800/40"
        >
          <span className="text-[11px] w-4 text-center text-neutral-400">⇊</span>
          <span className="text-[11px] font-semibold text-neutral-300">
            {count} queued message{count === 1 ? '' : 's'} processed
          </span>
          <span className="text-[10px] text-neutral-600 ml-auto">{open ? '▾' : '▸'}</span>
        </button>
        {open && (
          <div className="border-t border-neutral-800 divide-y divide-neutral-800/60">
            {entries.map((e, i) => (
              <div key={i} className="px-3 py-1.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-[11px] font-semibold text-neutral-300">{e.agent || 'unknown'}</span>
                  <span className="text-[10px] text-neutral-500">{relativeAgo(e.sent_ts)}</span>
                </div>
                <div className="mt-0.5 text-[12px] leading-relaxed text-neutral-400 whitespace-pre-wrap break-words">
                  {e.body}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function SystemPromptBody({ text }: { text: string }) {
  return (
    <pre className="mt-1 max-h-64 overflow-auto rounded-md border border-neutral-800 bg-neutral-950 p-2 text-[11px] font-mono text-neutral-400 whitespace-pre-wrap">
      {text}
    </pre>
  );
}

export function ThinkingCard({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const firstLine = text.split('\n').find((l) => l.trim())?.slice(0, 80) || 'Thinking';
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] w-full">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-[11px] text-neutral-500 hover:text-neutral-300 italic"
        >
          <span>{open ? '▾' : '▸'}</span>
          <span>💭 {open ? 'Thinking' : firstLine + '…'}</span>
        </button>
        {open && (
          <div className="mt-1 pl-4 border-l-2 border-neutral-800 text-[12px] leading-relaxed text-neutral-400 whitespace-pre-wrap break-words">
            {text}
          </div>
        )}
      </div>
    </div>
  );
}

export function ToolCard({ block }: { block: ToolBlock }) {
  const summary = toolSummary(block.tool, block.input);
  const result = block.result || '';
  const resultLong = result.length > RESULT_COLLAPSE_CHARS;
  const [open, setOpen] = useState(false); // input open
  const [resOpen, setResOpen] = useState(!resultLong && result.length > 0);
  const glyph = TOOL_GLYPH[block.tool] || '•';

  const inputStr = JSON.stringify(block.input, null, 2);
  const inputBig = inputStr.length > 200;

  return (
    <div className="flex justify-start">
      <div className={`max-w-[92%] w-full rounded-lg border ${block.isError ? 'border-red-800/60' : 'border-neutral-800'} bg-neutral-900/40 overflow-hidden`}>
        {/* header */}
        <button
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-neutral-800/40"
        >
          <span className="text-[11px] w-4 text-center text-neutral-400">{glyph}</span>
          <span className="text-[11px] font-semibold text-neutral-300">{block.tool}</span>
          <span className="text-[11px] font-mono text-neutral-500 truncate flex-1">{summary}</span>
          {block.isError && <span className="text-[10px] text-red-400">error</span>}
          {inputBig && <span className="text-[10px] text-neutral-600">{open ? '▾' : '▸'}</span>}
        </button>

        {/* expanded input */}
        {open && inputBig && (
          <pre className="px-3 py-1.5 border-t border-neutral-800 bg-neutral-950 overflow-x-auto text-[11px] font-mono text-neutral-400 whitespace-pre">
            {inputStr}
          </pre>
        )}

        {/* result */}
        {result.length > 0 && (
          <div className="border-t border-neutral-800">
            {!resOpen ? (
              <button
                onClick={() => setResOpen(true)}
                className="w-full text-left px-3 py-1 text-[10px] text-neutral-500 hover:text-neutral-300"
              >
                ▸ {block.isError ? 'Error output' : 'Result'} · {result.split('\n').length} lines
              </button>
            ) : (
              <div>
                {resultLong && (
                  <button
                    onClick={() => setResOpen(false)}
                    className="w-full text-left px-3 py-1 text-[10px] text-neutral-500 hover:text-neutral-300 border-b border-neutral-800/60"
                  >
                    ▾ Collapse result
                  </button>
                )}
                <pre className={`px-3 py-1.5 overflow-auto text-[11px] font-mono whitespace-pre ${block.isError ? 'text-red-300' : 'text-neutral-400'} ${resultLong ? 'max-h-72' : ''}`}>
                  {result}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Dispatch a RenderNode to the right primitive. */
export function RenderNodeView({ node }: { node: RenderNode }) {
  switch (node.kind) {
    case 'user': {
      if (node.isSystem) return <UserSystem text={node.text} />;
      // [voice-call:] markers ride USER rows (the proxy injects call summaries as
      // composer input). Split them out first; prose segments keep the paste-aware
      // user path. Render voice cards left-aligned (call artifact, not a bubble).
      if (hasVoiceCallMarker(node.text)) {
        const segs = parseVoiceSegments(node.text);
        return (
          <div className="flex flex-col items-stretch gap-1">
            {segs.map((s, i) =>
              s.kind === 'voicecall' ? (
                <div key={i} className="flex justify-start"><VoiceCallCard callId={s.callId} /></div>
              ) : hasPasteMarkers(s.text) ? (
                <MessageGroup key={i} text={s.text} sender="user" />
              ) : (
                <UserBubble key={i} text={s.text} />
              )
            )}
          </div>
        );
      }
      return hasPasteMarkers(node.text)
        ? <MessageGroup text={node.text} sender="user" />
        : <UserBubble text={node.text} queued={node.queued} />;
    }
    case 'queued_batch':
      return <QueuedBatchCard count={node.count} entries={node.entries} />;
    case 'assistant':
      return hasPasteMarkers(node.text)
        ? <MessageGroup text={node.text} sender="assistant" />
        : (
          <div className="flex justify-start">
            <div className="max-w-[92%] w-full">
              <Markdown text={node.text} />
            </div>
          </div>
        );
    case 'thinking':
      return <ThinkingCard text={node.text} />;
    case 'tool':
      return <ToolCard block={node} />;
    default:
      return null;
  }
}

function UserSystem({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-1">
      <div className="flex justify-center">
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-[10px] text-neutral-500 hover:text-neutral-300 px-2 py-0.5 rounded-full border border-neutral-800 bg-neutral-900/40"
        >
          {open ? 'Hide' : 'Show'} session prompt ({Math.round(text.length / 100) / 10}k chars)
        </button>
      </div>
      {open && <SystemPromptBody text={text} />}
    </div>
  );
}
