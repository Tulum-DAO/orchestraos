/**
 * QuestionnaireForm: a LIVE (QuestionnaireStore) questionnaire answered natively on the web, as the one question card
 * (components/chat/MultiPartCard's QuestionCard): one question at a time, a pick moves on, Skip leaves an optional
 * question unanswered (a required one has no Skip), and answering the last question sends them all.
 *
 * Drawn on its own, as every other card is: no panel around it (pages/Questionnaires.tsx, pages/Inbox.tsx). The ✕
 * closes it unanswered (POST /api/questionnaires/:id/discard, the gateway's discard).
 *
 * Reads GET /api/questionnaires/:id (questions[] with their menus and any saved draft) and sends
 * POST /api/questionnaires/:id/answers, which the API forwards to the gateway's own submit: the same validation and
 * the same resume of the asking agent as an answer from the phone or watch. A skipped question goes as
 * {skipped: true} and is stored as skipped; at least one question must be answered. Legacy file questionnaires keep
 * their iframe (pages/Questionnaires.tsx, pages/Inbox.tsx).
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchQuestionnaire } from '../lib/api';
import { questionnaireSummary } from '../lib/questionnaireForm';
import { questionnaireAnswers, questionnaireBody, questionnaireParts, type PartAnswer, type QuestionnaireQuestion } from '../lib/multiPart';
import { AnsweredQuestionCard, QuestionCard } from './chat/MultiPartCard';
import Markdown from './chat/Markdown';

interface LiveDetail {
  id: string;
  title: string;
  summary?: string | null;
  status: string;
  from_agent?: string;
  questions: QuestionnaireQuestion[];
}

interface SubmitResult { ok?: boolean; applied?: boolean; missing?: number[]; status?: string; error?: string }

export default function QuestionnaireForm({ id }: { id: string }) {
  const [dismissed, setDismissed] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ['questionnaire', id],
    queryFn: () => fetchQuestionnaire<LiveDetail>(id),
  });

  if (isLoading) return <p className="text-xs text-muted-foreground px-1 py-2">Loading questions…</p>;
  if (error || !data?.questions) {
    return <p className="text-xs text-destructive px-1 py-2">Could not load this questionnaire. Answer it on your phone or watch meanwhile.</p>;
  }

  const parts = questionnaireParts(data.questions);
  const summary = questionnaireSummary(data);
  const context = summary ? <div className="text-[13px] leading-relaxed text-card-foreground/85" data-testid="qnr-summary"><Markdown text={summary} /></div> : null;
  const title = data.title || 'Questionnaire';
  const meta = data.from_agent ? <>· {data.from_agent}</> : null;

  if (data.status !== 'pending') {
    return (
      <div data-testid="qnr-done">
        <AnsweredQuestionCard title={title} meta={meta} context={context} parts={parts}
          answers={questionnaireAnswers(parts, data.questions)} note={`This questionnaire is ${data.status}.`} />
      </div>
    );
  }

  const submit = async (answers: PartAnswer[]): Promise<string | null> => {
    let r: SubmitResult = {};
    try {
      const res = await fetch(`/api/questionnaires/${encodeURIComponent(id)}/answers`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers: questionnaireBody(parts, answers) }),
      });
      r = await res.json().catch(() => ({}));
      if (!res.ok) return `Couldn’t send: ${r.error || `error ${res.status}`}.`;
    } catch {
      return 'Couldn’t reach OrchestraOS. Nothing was sent.';
    }
    // Sent: the answered card stays until the page's own poll moves the questionnaire out of the pending list.
    if (r.applied) return null;
    if (r.missing?.length) return `Question ${r.missing.join(', ')} still needs an answer. Go Back to it.`;
    return r.status ? `This questionnaire is ${r.status}.` : 'Couldn’t send. Try again.';
  };

  // The ✕: closed unanswered through the gateway's discard, which tells the asking agent not to keep waiting.
  const dismiss = async (): Promise<string | null> => {
    try {
      const res = await fetch(`/api/questionnaires/${encodeURIComponent(id)}/discard`, { method: 'POST' });
      const r: SubmitResult = await res.json().catch(() => ({}));
      if (!res.ok) return `Couldn’t dismiss: ${r.error || `error ${res.status}`}.`;
      setDismissed(true);
      return null;
    } catch {
      return 'Couldn’t reach OrchestraOS.';
    }
  };

  return (
    <div data-testid="qnr-form">
      <QuestionCard key={data.id} title={title} meta={meta} context={context} parts={parts}
        initial={questionnaireAnswers(parts, data.questions)} onSubmit={submit} onDismiss={dismiss} dismissed={dismissed} />
    </div>
  );
}
