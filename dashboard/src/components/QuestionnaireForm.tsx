/**
 * QuestionnaireForm: a LIVE (QuestionnaireStore) questionnaire answered natively on the web.
 *
 * Reads GET /api/questionnaires/:id (questions[] with their menus and any saved draft) and sends
 * POST /api/questionnaires/:id/answers, which the API forwards to the gateway's own submit: the
 * same validation and the same resume of the asking agent as an answer from the phone or watch.
 * Legacy file questionnaires keep their iframe (pages/Questionnaires.tsx, pages/Inbox.tsx).
 */
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Loader2 } from 'lucide-react';
import { answerQuestionnaire, fetchQuestionnaire } from '../lib/api';
import {
  answerableOptions, buildAnswers, missingRequired, initialAnswers,
  type Drafts, type LiveQuestion,
} from '../lib/questionnaireForm';

interface LiveDetail {
  id: string;
  title: string;
  status: string;
  questions: LiveQuestion[];
}

interface SubmitResult { ok?: boolean; applied?: boolean; missing?: number[]; status?: string; error?: string }

export default function QuestionnaireForm({ id, onDone }: { id: string; onDone?: () => void }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['questionnaire', id],
    queryFn: () => fetchQuestionnaire<LiveDetail>(id),
  });
  const [drafts, setDrafts] = useState<Drafts>({});
  const [highlight, setHighlight] = useState<number[]>([]);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);

  useEffect(() => { if (data?.questions) setDrafts(initialAnswers(data.questions)); }, [data?.id]);

  if (isLoading) return <p className="text-xs text-neutral-500 p-4">Loading questions…</p>;
  if (error || !data?.questions) {
    return <p className="text-xs text-red-400 p-4">Could not load this questionnaire. Answer it on your phone or watch meanwhile.</p>;
  }
  if (data.status !== 'pending' || result?.applied) {
    return (
      <p className="flex items-center gap-2 text-sm text-green-400 p-4" data-testid="qnr-done">
        <CheckCircle2 size={16} /> {result?.applied ? 'Answers sent.' : `This questionnaire is ${data.status}.`}
      </p>
    );
  }

  const set = (n: number, patch: { option_n?: string; answer_text?: string }) =>
    setDrafts((d) => ({ ...d, [n]: { ...(d[n] ?? {}), ...patch } }));

  async function submit() {
    const missing = missingRequired(data!.questions, drafts);
    setHighlight(missing);
    if (missing.length) { setResult({ missing }); return; }
    setSending(true);
    try {
      const r = await answerQuestionnaire<SubmitResult>(id, buildAnswers(drafts));
      setResult(r);
      if (r.missing) setHighlight(r.missing);
      if (r.applied) {
        queryClient.invalidateQueries({ queryKey: ['questionnaires'] });
        onDone?.();
      }
    } catch (e: any) {
      setResult({ error: e?.message || 'send failed' });
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-4 p-4 bg-neutral-950" data-testid="qnr-form">
      {data.questions.map((q) => {
        const d = drafts[q.n] ?? {};
        const opts = answerableOptions(q);
        const picked = opts.find((o) => o.n === d.option_n);
        const missing = highlight.includes(q.n);
        return (
          <div key={q.n} role="group" aria-label={`Question ${q.n}`}
               className={`space-y-2 rounded-lg border p-3 ${missing ? 'border-amber-500/70' : 'border-neutral-800'}`}
               data-testid={`qnr-q-${q.n}`}>
            <p className="text-sm text-neutral-100">
              {q.n}. {q.prompt}{q.required ? <span className="text-amber-400"> *</span> : null}
            </p>
            {opts.map((o) => (
              <label key={o.n} className="flex items-start gap-2 text-sm text-neutral-300 cursor-pointer">
                <input type="radio" name={`${id}-q${q.n}`} className="mt-1" checked={d.option_n === o.n}
                       onChange={() => set(q.n, { option_n: o.n })} />
                <span>
                  {o.label}
                  {o.detail ? <span className="block text-xs text-neutral-500">{o.detail}</span> : null}
                </span>
              </label>
            ))}
            {/* Every question has a text box (text alone is an answer), so a saved draft's text is
                always on screen, even on a question whose options are all hidden. */}
            {(
              <textarea
                rows={q.kind === 'free_text' ? 3 : 2}
                value={d.answer_text ?? ''}
                onChange={(e) => set(q.n, { answer_text: e.target.value })}
                placeholder={q.kind === 'free_text' ? 'Your answer'
                  : picked?.input_kind === 'free_text' ? `Say more for "${picked.label}"` : 'Or type your own answer'}
                className="w-full rounded-md border border-neutral-700 bg-black px-2 py-1.5 text-sm text-neutral-100"
              />
            )}
            {missing ? <p className="text-xs text-amber-400">This question needs an answer.</p> : null}
          </div>
        );
      })}
      <div className="flex items-center gap-3">
        <button onClick={submit} disabled={sending}
                className="rounded-md bg-amber-500 px-3 py-1.5 text-sm font-medium text-black hover:bg-amber-400 disabled:opacity-50">
          {sending ? <Loader2 size={14} className="animate-spin" /> : 'Send answers'}
        </button>
        {result?.error ? <span className="text-xs text-red-400">{result.error}</span> : null}
        {result && !result.error && !result.applied && result.missing?.length
          ? <span className="text-xs text-amber-400">Answer the marked questions first.</span> : null}
      </div>
    </div>
  );
}
