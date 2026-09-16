/**
 * VoiceCallCard — renders a [voice-call:] marker as a gold-bordered call card
 * (spec 2026-08-09-arturo-voice-mode-design §5; iOS parity commits 6fc8c9d +
 * c48ab22). Collapsed by default: header only. Expand fetches the transcript via
 * GET /api/voice/call?call_id= (gateway-proxied) and shows the model summary +
 * diarized turns (user right/green, arturo left) + tool rows reusing the
 * ToolCard visual grammar.
 *
 * Failure modes: unfetchable (404/503/network) → plain "transcript unavailable"
 * chip; a live call older than 4h renders "stale", not "live".
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import { Loader2 } from 'lucide-react';
import { fetchVoiceCall, type VoiceCall, type VoiceTurn } from '../../lib/api';
import { fmtCallDuration, isStaleLive } from '../../lib/voiceCall';

const TOOL_GLYPH: Record<string, string> = {
  Bash: '$', Read: '≡', Edit: '✎', Write: '✎', Grep: '⌕', Glob: '⌕',
  Task: '⛁', Agent: '⛁', WebFetch: '↗', WebSearch: '⌕', Skill: '✦', knowledge: '✦',
};

function summarize(turns: VoiceTurn[]) {
  const tool = turns.filter((t) => t.role === 'tool').length;
  const talk = turns.length - tool;
  return { talk, tool };
}

function ToolRow({ t }: { t: VoiceTurn }) {
  const [open, setOpen] = useState(false);
  const glyph = TOOL_GLYPH[t.tool || ''] || '•';
  const isErr = t.status === 'error';
  const inputStr = t.input != null ? JSON.stringify(t.input, null, 2) : '';
  const mark = t.status === 'done' ? '✓' : isErr ? '✕' : '…';
  return (
    <div className={clsx('rounded-md border bg-neutral-900/40 overflow-hidden', isErr ? 'border-red-800/50' : 'border-neutral-800')}>
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-2 px-2 py-1 text-left hover:bg-neutral-800/40">
        <span className="text-[11px] w-3 text-center text-neutral-400">{glyph}</span>
        <span className="text-[11px] font-semibold text-neutral-300">{t.tool || 'tool'}</span>
        <span className="text-[11px] font-mono text-neutral-500 truncate flex-1">
          {inputStr.replace(/\s+/g, ' ').slice(0, 80)}
        </span>
        <span className={clsx('text-[10px]', isErr ? 'text-red-400' : t.status === 'done' ? 'text-green-400' : 'text-neutral-500')}>{mark}</span>
      </button>
      {open && (inputStr || t.result) && (
        <div className="border-t border-neutral-800 px-2.5 py-1.5 space-y-1">
          {inputStr && <pre className="overflow-x-auto text-[10px] font-mono text-neutral-400 whitespace-pre">{inputStr}</pre>}
          {t.result && <pre className={clsx('overflow-auto max-h-40 text-[10px] font-mono whitespace-pre-wrap', isErr ? 'text-red-300' : 'text-neutral-400')}>{t.result}</pre>}
        </div>
      )}
    </div>
  );
}

function Turn({ t }: { t: VoiceTurn }) {
  if (t.role === 'tool') return <ToolRow t={t} />;
  const isUser = t.role === 'user';
  return (
    <div className={clsx('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={clsx(
        'max-w-[85%] rounded-2xl px-3 py-1.5 text-[12px] leading-relaxed whitespace-pre-wrap break-words',
        isUser ? 'rounded-br-sm bg-blue-600/80 text-white' : 'rounded-bl-sm bg-neutral-800 text-neutral-200'
      )}>
        {t.text}
      </div>
    </div>
  );
}

export default function VoiceCallCard({ callId }: { callId: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [call, setCall] = useState<VoiceCall | null>(null);
  const [failed, setFailed] = useState(false);

  const load = async () => {
    if (call || loading) return;
    setLoading(true); setFailed(false);
    try {
      const r = await fetchVoiceCall(callId);
      if (r.status === 200 && r.ok && r.call) setCall(r.call);
      else setFailed(true);
    } catch { setFailed(true); }
    finally { setLoading(false); }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) load();
  };

  // Unfetchable → plain chip (only once we've tried and failed, collapsed).
  if (failed && !open) {
    return (
      <div className="inline-flex items-center gap-1.5 rounded-full border border-neutral-700 bg-neutral-900/60 px-2.5 py-1 text-[11px] text-neutral-500">
        ☎ Voice call · transcript unavailable
      </div>
    );
  }

  const counts = call?.turns ? summarize(call.turns) : null;
  const dur = call ? fmtCallDuration(call.started_at, call.ended_at) : null;
  const stale = call ? isStaleLive(call.status, call.started_at) : false;
  const live = call?.status === 'live' && !stale;

  return (
    <div className="rounded-lg border border-amber-500/45 bg-amber-500/5 overflow-hidden max-w-[92%]">
      <button onClick={toggle} className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-amber-500/10">
        <span className="text-[12px] text-amber-300">☎</span>
        <span className="text-[12px] font-medium text-neutral-200">Voice call</span>
        {counts && dur && (
          <span className="text-[11px] text-neutral-400">· {dur} · {counts.talk} turns · {counts.tool} tool runs</span>
        )}
        {live && <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400">live</span>}
        {stale && <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-700 text-neutral-400">stale</span>}
        <span className="text-[10px] text-neutral-600 ml-auto">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="border-t border-amber-500/20 px-2.5 py-2 space-y-2">
          {loading && (
            <div className="flex items-center gap-2 text-[11px] text-neutral-500">
              <Loader2 size={13} className="animate-spin" /> loading transcript…
            </div>
          )}
          {failed && !loading && (
            <div className="text-[11px] text-amber-400/90">Transcript unavailable — try again shortly.</div>
          )}
          {call && !loading && (
            <>
              {call.summary && (
                <div className="text-[11px] text-neutral-400 border-l-2 border-neutral-700 pl-2 whitespace-pre-wrap">
                  {call.summary}
                </div>
              )}
              <div className="space-y-1.5">
                {(call.turns || []).map((t, i) => <Turn key={i} t={t} />)}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
