/**
 * Render primitives for the transcript-driven chat grammar:
 *  - UserBubble       (a real user/the operator prompt; system spawn-prompt collapses)
 *  - ThinkingCard     (collapsed by default)
 *  - ToolCard         (tool_use + paired result, collapsible; long output scrolls)
 *  - ToolGroupCard    (two or more consecutive calls folded to one line per turn)
 * Assistant prose is rendered by <Markdown/>.
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import { ChevronRight, ChevronDown, FileText } from 'lucide-react';
import Markdown from './Markdown';
import CopyButton from '../CopyButton';
import ToolCalls, { type ToolRunEntry } from './ToolCalls';
import { AnsweredMultiPart } from './MultiPartCard';
import { fromAskUserQuestion, matchBatchRecord, type BatchRecord } from '../../lib/multiPart';
import { useBatchRecords } from '../../lib/batchRecords';
import { fromBlock } from '../../lib/toolCalls';
import { splitAttachments } from '../../lib/markdownBlocks';
import { type ToolBlock, type RenderNode } from '../../lib/transcript';
import { type GroupedNode, type ToolGroupNode } from '../../lib/toolGroups';
import { parseMessageSegments, hasPasteMarkers } from '../../lib/pastedText';
import { parseSystemMessage, type SystemMessage, type MachineKind } from '../../lib/systemMessages';
import AgentMessage from './AgentMessage';
import { hasVoiceCallMarker, parseVoiceSegments } from '../../lib/voiceCall';
import VoiceCallCard from './VoiceCallCard';
import { type ToolDensity } from '../../lib/toolCardDetail';

/**
 * The disclosure control for every expandable card here.
 *
 * It was a text glyph (`▸` / `▾`) at `text-[10px]`, which renders about SIX PIXELS of
 * actual ink and is both hard to see and far below any sane touch target. An icon at 14px
 * is the smallest thing that reads as a control rather than as punctuation. It is
 * `aria-hidden` because the control it belongs to always carries `aria-expanded`, so
 * announcing it again would be noise.
 */
function Caret({ open }: { open: boolean }) {
  const Icon = open ? ChevronDown : ChevronRight;
  return <Icon size={14} aria-hidden className="shrink-0 text-neutral-500" />;
}

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
        <span className="ml-auto"><Caret open={open} /></span>
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


/**
 * When a message happened. Shaw: "No timestamps anywhere. You can't tell whether 'Hello? Are you
 * still there?' was sent 5 seconds or 5 hours ago, and that is the one question this page has to
 * answer." Clock time for anything older than a minute, "just now" under it, and the full local
 * datetime on hover. Absent ts renders NOTHING rather than a guessed time.
 */
export function MessageTime({ ts, align = 'left' }: { ts?: string; align?: 'left' | 'right' }) {
  if (!ts) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  const ageS = (Date.now() - d.getTime()) / 1000;
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const label = ageS < 60 ? 'just now' : hhmm;
  return (
    <span
      className={`block mt-0.5 text-[10px] text-neutral-600 ${align === 'right' ? 'text-right' : 'text-left'}`}
      title={d.toLocaleString()}
    >
      {label}
    </span>
  );
}

export function UserBubble({ text, isSystem, queued, ts }: { text: string; isSystem?: boolean; queued?: boolean; ts?: string }) {
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
  // Attachments the composer sent with it ("[IMAGE: …] words"): an uploaded image as a thumbnail above the bubble,
  // any other file as a chip, rather than the raw marker.
  const { attachments, text: words } = splitAttachments(text);
  return (
    <div className="flex flex-col items-end">
      {attachments.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1.5 mb-1 max-w-[85%]" data-attachments>
          {attachments.map((a, i) => a.kind === 'image' && a.url ? (
            <a key={i} href={a.url} target="_blank" rel="noopener noreferrer" title={a.name}>
              <img src={a.url} alt={a.name} loading="lazy"
                   className="h-24 max-w-[12rem] rounded-xl object-cover border border-border bg-muted" />
            </a>
          ) : (
            <span key={i} className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border bg-muted text-[11px] text-foreground/80">
              <FileText size={12} />{a.name}
            </span>
          ))}
        </div>
      )}
      {(words || !attachments.length) && (
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600/90 px-3.5 py-2 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words">
          {words}
        </div>
      )}
      {queued && (
        <span className="mt-0.5 text-[9px] uppercase tracking-wide text-blue-300/80 px-1.5 py-[1px] rounded-full border border-blue-500/30 bg-blue-500/10">
          queued
        </span>
      )}
      <MessageTime ts={ts} align="right" />
    </div>
  );
}

/** A line a machine wrote into the agent's pane (lib/systemMessages): a decision or questionnaire
 *  answer, an approval, a closed human task, another agent's message, a finished background job.
 *  Never the operator's bubble: a compact dark card on the left, outlined by what it is (blue for an
 *  answered decision or questionnaire, green approved, red denied or failed, grey for agent traffic
 *  and jobs, amber for an urgent relay). Collapsed it shows the gist; expanded, the line verbatim, so
 *  nothing the trim hides is out of reach. */
export function SystemMessageCard({ msg, raw }: { msg: SystemMessage; raw: string }) {
  const [open, setOpen] = useState(false);
  const tone = msg.outcome === 'deny' || msg.outcome === 'cant' || msg.outcome === 'failed' ? 'red'
    : msg.outcome === 'approve' ? 'green'
      : msg.kind === 'relay' ? (msg.urgent ? 'amber' : 'grey')
        : msg.kind === 'task' || msg.kind === 'system' ? 'grey' : 'blue';
  const border = { red: 'border-red-500/50', green: 'border-green-500/50', amber: 'border-amber-500/60',
    grey: 'border-neutral-700', blue: 'border-blue-500/50' }[tone];
  const accent = { red: 'text-red-300', green: 'text-green-300', amber: 'text-amber-300',
    grey: 'text-neutral-400', blue: 'text-blue-300' }[tone];
  const glyph = msg.kind === 'relay' ? '⇄' : msg.kind === 'task' ? (msg.outcome === 'failed' ? '!' : '✓')
    : msg.kind === 'questionnaire' ? '≡' : tone === 'red' ? '✕' : '✓';
  return (
    <div className="flex justify-start" data-testid="system-message" data-machine={msg.kind} data-tone={tone}>
      <div className={clsx('max-w-[85%] min-w-0 rounded-xl border bg-neutral-950/80 px-2.5 py-1.5', border)}>
        <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-1.5 text-left">
          <span className={clsx('text-[11px] w-3.5 text-center shrink-0', accent)} aria-hidden>{glyph}</span>
          <span className="text-[11px] font-semibold text-neutral-200 truncate">{msg.headline}</span>
          {msg.urgent && <span className="text-[10px] text-amber-300">· {msg.detail}</span>}
          <span className="ml-auto pl-2"><Caret open={open} /></span>
        </button>
        {open ? (
          <div className="mt-1 max-h-80 overflow-y-auto text-[11px] leading-relaxed text-neutral-400 whitespace-pre-wrap break-words select-text">{raw}</div>
        ) : (
          <div className="mt-0.5 pl-5 space-y-0.5 select-text">
            {msg.subject && <div className="text-[12px] text-neutral-200 break-words">{msg.subject}</div>}
            {msg.detail && !msg.urgent && msg.kind !== 'task' && msg.kind !== 'approval' && (
              <div className={clsx('text-[12px] font-medium break-words', accent)}>→ {msg.detail}</div>
            )}
            {msg.note && <div className="text-[11px] text-neutral-500 break-words">{msg.note}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

/** A just-sent message, until its own transcript row replaces it (lib/pendingSends). Faded while
 *  the agent has it but has not logged it; a "queued" chip when it landed mid-turn and waits in the
 *  agent's input queue; a dimmer blue when the server holds or stores it for later delivery. */
export function PendingBubble({ text, kind }: { text: string; kind: 'sent' | 'queuedInTurn' | 'held' | 'queued' }) {
  const waiting = kind === 'held' || kind === 'queued';
  return (
    <div className="flex flex-col items-end" data-testid="pending-bubble" data-pending-kind={kind}>
      <div className={clsx('max-w-[85%] rounded-2xl rounded-br-sm px-3.5 py-2 text-[13px] leading-relaxed whitespace-pre-wrap break-words',
        waiting ? 'bg-blue-600/25 text-white/70' : 'bg-blue-600/50 text-white/85')}>
        {text}
      </div>
      {kind !== 'sent' && (
        <span className="mt-0.5 text-[9px] uppercase tracking-wide text-blue-300/80 px-1.5 py-[1px] rounded-full border border-blue-500/30 bg-blue-500/10">
          {kind === 'queuedInTurn' ? 'queued' : kind === 'held' ? 'held for its next turn' : 'saved to its inbox'}
        </span>
      )}
    </div>
  );
}

// B2 processed-queue batch: collapsed-by-default expandable div (tool-call-style),
// entries newest->oldest, each = agent · how-long-ago · body.
export function QueuedBatchCard({ count, entries }: { count: number; entries: { agent: string; sent_ts: string; body: string; machine?: string }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex justify-start">
      <div className="w-full rounded-lg border border-neutral-800 bg-neutral-900/40 overflow-hidden">
        <button
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-neutral-800/40"
        >
          <span className="text-[11px] w-4 text-center text-neutral-400">⇊</span>
          <span className="text-[11px] font-semibold text-neutral-300">
            {count} queued message{count === 1 ? '' : 's'} processed
          </span>
          <span className="ml-auto"><Caret open={open} /></span>
        </button>
        {open && (
          <div className="border-t border-neutral-800 divide-y divide-neutral-800/60">
            {entries.map((e, i) => {
              // Another agent's message reads the same here as in the chat: the "Message from" line and its body.
              const relay = parseSystemMessage(e.body, (e.machine as MachineKind | undefined) ?? null);
              if (relay?.kind === 'relay') return <div key={i} className="px-3 py-2"><AgentMessage msg={relay} /></div>;
              return (
              <div key={i} className="px-3 py-1.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-[11px] font-semibold text-neutral-300">{e.agent || 'unknown'}</span>
                  <span className="text-[10px] text-neutral-500">{relativeAgo(e.sent_ts)}</span>
                </div>
                <div className="mt-0.5 text-[12px] leading-relaxed text-neutral-400 whitespace-pre-wrap break-words">
                  {e.body}
                </div>
              </div>
              );
            })}
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
      <div className="w-full">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-[11px] text-neutral-500 hover:text-neutral-300 italic"
        >
          <Caret open={open} />
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


/** An answered AskUserQuestion stays in the thread as its answered card (what was chosen per part), so it outlives
 *  the live question: drawn from the gateway's durable record of the answer where one matches, else from the call's
 *  own result. Null for any other call, or one not answered yet. */
function answeredQuestion(block: ToolBlock, records?: BatchRecord[]) {
  if (block.tool !== 'AskUserQuestion' || block.isError) return null;
  const qs = Array.isArray(block.input?.questions) ? (block.input.questions as { question?: unknown }[]) : [];
  const record = matchBatchRecord(records, qs.map((q) => String(q?.question ?? '')));
  const r = fromAskUserQuestion(block.input, block.result, record);
  if (!r) return null;
  return (
    <div className="flex justify-start" key={block.key}>
      <div className="w-full max-w-[min(92%,480px)]"><AnsweredMultiPart agentName="Your answers" parts={r.parts} answers={r.answers} /></div>
    </div>
  );
}

export function ToolCard({ block, density = 'normal' }: { block: ToolBlock; density?: ToolDensity }) {
  const records = useBatchRecords();
  const asked = answeredQuestion(block, records);
  if (asked) return asked;
  // The one tool card (components/chat/ToolCalls.tsx), shared with Arturo's page.
  return (
    <div className="flex justify-start">
      <ToolCalls calls={[fromBlock(block)]} density={density} />
    </div>
  );
}

/**
 * A turn's tool calls folded to ONE line, e.g. "Worked for 1m 8s — edited 2 files, ran 3 commands". Opening it lists
 * each call (with the thinking between them), and each call opens again to its input and output.
 */
export function ToolGroupCard({ group, interrupted = false, density = 'normal' }: { group: ToolGroupNode; interrupted?: boolean; density?: ToolDensity }) {
  // Answered questions are drawn as their cards under the folded line, not folded into it.
  const records = useBatchRecords();
  const asked = group.children.flatMap((c) => (c.kind === 'tool' ? [answeredQuestion(c, records)] : [])).filter(Boolean);
  const children = asked.length ? group.children.filter((c) => !(c.kind === 'tool' && answeredQuestion(c, records))) : group.children;
  const entries: ToolRunEntry[] = children.map((c) => c.kind === 'tool'
    ? { kind: 'call', call: fromBlock(c) }
    : { kind: 'note', key: c.key, node: <RenderNodeView node={c} density={density} /> });
  const calls = entries.flatMap((e) => (e.kind === 'call' ? [e.call] : []));
  return (
    <>
      {calls.length > 0 && (
        <div className="flex justify-start">
          <ToolCalls calls={calls} entries={entries} interrupted={interrupted} density={density} />
        </div>
      )}
      {asked}
    </>
  );
}

/** Like RenderNodeView, plus the folded tool group. */
export function GroupedNodeView({ node, interrupted, density = 'normal' }: { node: GroupedNode; interrupted?: boolean; density?: ToolDensity }) {
  return node.kind === 'tool_group'
    ? <ToolGroupCard group={node} interrupted={interrupted} density={density} />
    : <RenderNodeView node={node} density={density} />;
}

/** Dispatch a RenderNode to the right primitive. */
export function RenderNodeView({ node, density = 'normal' }: { node: RenderNode; density?: ToolDensity }) {
  switch (node.kind) {
    case 'user': {
      if (node.isSystem) return <UserSystem text={node.text} />;
      // A machine-written line (the server's tag, or the same prefixes from an older server) is a
      // card, never the operator's bubble. One that does not parse is drawn as before.
      const sys = parseSystemMessage(node.text, (node.machine as MachineKind | undefined) ?? null);
      if (sys?.kind === 'relay') return <AgentMessage msg={sys} />;
      if (sys) return <SystemMessageCard msg={sys} raw={node.text} />;
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
        : <UserBubble text={node.text} queued={node.queued} ts={node.ts} />;
    }
    case 'queued_batch':
      return <QueuedBatchCard count={node.count} entries={node.entries} />;
    case 'assistant':
      return hasPasteMarkers(node.text)
        ? <MessageGroup text={node.text} sender="assistant" />
        : (
          <div className="flex justify-start group">
            <div className="w-full">
              <Markdown text={node.text} />
              {/* The reply's time, and a copy button (the words, as written) that shows on hover, or always on touch. */}
              <div className="flex items-center gap-1">
                <MessageTime ts={node.ts} />
                <CopyButton text={node.text} label="Copy reply" size={12}
                            className="mt-0.5 text-neutral-500 hover:text-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity" />
              </div>
            </div>
          </div>
        );
    case 'thinking':
      return <ThinkingCard text={node.text} />;
    case 'tool':
      return <ToolCard block={node} density={density} />;
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
