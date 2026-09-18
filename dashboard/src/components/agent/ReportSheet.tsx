/**
 * ReportSheet — the report button's sheet: the front door of the RED ALERT / ticket system
 * (operator, 2026-09-18: "accessible from a new button on screen … the gateway to
 * the ticket system"). Type + the user's words; evidence (screen, process state, logs) is
 * attached by the server (scripts/red_alert.py report). Crash/bug -> card + Telegram +
 * Arturo; improvement/suggestion -> Telegram + Arturo.
 */
import { useState } from 'react';
import { X } from 'lucide-react';
import { post } from '../../lib/api';

export type ReportKind = 'crash' | 'bug' | 'improvement' | 'suggestion';

const KINDS: Array<{ kind: ReportKind; label: string; hint: string }> = [
  { kind: 'crash', label: 'Crash', hint: 'It stopped working' },
  { kind: 'bug', label: 'Bug', hint: 'It did the wrong thing' },
  { kind: 'improvement', label: 'Improvement', hint: 'It could work better' },
  { kind: 'suggestion', label: 'Suggestion', hint: 'An idea' },
];

interface ReportSheetProps {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
}

export interface ReportResult { ok: boolean; id?: string; card_id?: string | null; severity?: string; error?: string }

export function ReportSheet({ isOpen, onClose, agentId }: ReportSheetProps) {
  const [kind, setKind] = useState<ReportKind>('bug');
  const [words, setWords] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReportResult | null>(null);

  if (!isOpen) return null;

  const submit = async () => {
    if (words.trim().length < 3 || busy) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await post<ReportResult>('/red-alert/report', { seat: agentId, kind, words: words.trim() });
      setResult(r);
      if (r?.ok) setWords('');
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : 'could not file the report' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" aria-label="Report a problem" className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60" onClick={onClose}>
      <div className="w-full sm:w-[28rem] bg-background text-foreground border border-border rounded-t-2xl sm:rounded-2xl p-4 space-y-3 safe-bottom" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-base font-semibold">Report a problem</div>
            <div className="text-xs text-muted-foreground">About <span className="font-mono">{agentId}</span> · the screen and logs are attached for you</div>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-2 rounded-lg hover:bg-muted"><X size={20} /></button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          {KINDS.map((k) => (
            <button
              key={k.kind}
              onClick={() => setKind(k.kind)}
              className={`text-left rounded-xl border px-3 py-2 min-h-[52px] transition-colors ${kind === k.kind ? 'border-red-500/60 bg-red-500/10' : 'border-border hover:bg-muted'}`}
            >
              <div className="text-sm font-medium">{k.label}</div>
              <div className="text-[11px] text-muted-foreground">{k.hint}</div>
            </button>
          ))}
        </div>

        <textarea
          value={words}
          onChange={(e) => setWords(e.target.value)}
          placeholder="What did you see? Your words go in verbatim."
          rows={4}
          className="w-full rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm outline-none focus:border-red-500/60"
        />

        {result && (
          <div className={`text-xs rounded-lg px-3 py-2 ${result.ok ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-400'}`}>
            {result.ok
              ? <>Filed <span className="font-mono">{result.id}</span>{result.card_id ? <> · card <span className="font-mono">{result.card_id}</span> is on your phone</> : <> · sent to Telegram and Arturo</>}. If nobody answers in 2 minutes the safe repairs run on their own.</>
              : <>Couldn't file it: {result.error || 'unknown error'}</>}
          </div>
        )}

        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="text-sm px-3 py-2 min-h-[44px] rounded-lg hover:bg-muted">Close</button>
          <button
            onClick={submit}
            disabled={busy || words.trim().length < 3}
            className="text-sm px-4 py-2 min-h-[44px] rounded-lg font-medium bg-red-500/20 text-red-300 disabled:opacity-40"
          >
            {busy ? 'Filing…' : 'File report'}
          </button>
        </div>
      </div>
    </div>
  );
}
