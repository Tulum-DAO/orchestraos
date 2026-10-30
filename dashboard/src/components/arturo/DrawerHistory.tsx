/**
 * DrawerHistory — the drawer's History section as cards: what is running beside the agent's own turn, what waits on
 * the operator, and what was resolved and how (lib/drawerHistory.ts holds the rules).
 *
 * A waiting card is the chat's own card (DecisionBubble), opened in place, so an answer from here goes through the
 * same route as one from the chat, and the chat shows it at once: both read the same query
 * (['agent-decisions', id]). A resolved card is compact: how it ended, THE ANSWER (the pick, the words, every part of
 * a multi-part card with its skips), who, where and when; expanding it shows the chat's answered card. "Go to card"
 * closes the drawer and scrolls the chat to it (lib/decisions focusDecision).
 *
 * Running: Arturo's background jobs in the open conversation, each with Cancel (the jobs' own route); an agent's
 * subagents are only counted, and have no cancel route, so none is shown.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { LoaderCircle } from 'lucide-react';
import DecisionBubble from '../chat/DecisionBubble';
import { AnsweredQuestionCard } from '../chat/MultiPartCard';
import { fetchDecisions, focusDecision } from '../../lib/decisions';
import { fetchBatchRecords } from '../../lib/batchRecords';
import { cancelJob, elapsed, fetchJobs, type ArturoJob } from '../../lib/arturoJobs';
import { currentThread } from '../../lib/arturoCurrent';
import { listThreads, loadThread } from '../../lib/arturoThreads';
import { choiceAnswerFromText } from '../../lib/multiPart';
import { choiceParts } from '../../lib/choiceCard';
import {
  historyItems, groupHistory, applyFilter, filterChips, pageOf, batchAnswers, runningRows, waitingLine, resolvedLine,
  answerText, plainSummary, KIND_LABEL, OUTCOME_LABEL, NO_FILTER, PAGE_SIZE,
  type HistoryItem, type HistoryFilter,
} from '../../lib/drawerHistory';
import { CardSurface, useScreenCard } from '../../lib/threadKit/useScreenCard';
import type { ScreenCard } from '../../lib/screenCards';
import './drawerHistory.css';

// The drawer slides out in 200ms; the chat's card is scrolled to once the drawer's copy of it is gone.
const AFTER_CLOSE_MS = 260;
const JOBS_RUNNING_MS = 2000;
const JOBS_IDLE_MS = 8000;

export function DrawerHistory({ agentId, isArturo, name, subagents, onClose }: {
  agentId: string; isArturo: boolean; name: string;
  /** An agent's subagents running now (its status row's count). */
  subagents?: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const decisionsQ = useQuery({
    queryKey: ['agent-decisions', agentId], queryFn: () => fetchDecisions(agentId), refetchInterval: 10_000, staleTime: 5_000,
  });
  const batchesQ = useQuery({
    queryKey: ['agent-menu-batches', agentId], queryFn: () => fetchBatchRecords(agentId),
    enabled: !isArturo, refetchInterval: 15_000, staleTime: 5_000,
  });
  // Arturo's choice cards live in his open conversation, each with its answer as the server keeps it.
  const thread = isArturo ? currentThread() : null;
  const threadQ = useQuery({
    queryKey: ['arturo-thread-cards', thread], queryFn: () => loadThread(thread!), enabled: !!thread,
    refetchInterval: 10_000, staleTime: 5_000,
  });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['agent-decisions', agentId] }); };
  const goTo = (id: string) => {
    onClose();
    window.setTimeout(() => {
      // One of Arturo's choice cards: answered on his page, where it sits in the conversation.
      const choice = document.getElementById(`choice-${id}`);
      if (choice) { choice.scrollIntoView({ block: 'center', behavior: 'smooth' }); return; }
      focusDecision(agentId, id);
    }, AFTER_CLOSE_MS);
  };

  const items = useMemo(() => historyItems(decisionsQ.data ?? [], batchesQ.data ?? [], agentId, threadQ.data?.turns ?? [], name),
    [decisionsQ.data, batchesQ.data, agentId, threadQ.data, name]);
  const [filter, setFilter] = useState<HistoryFilter>(NO_FILTER);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const { waiting, resolved } = groupHistory(applyFilter(items, filter));
  const page = pageOf(resolved, limit);
  const chips = filterChips(items, filter);
  const pick = (f: HistoryFilter) => { setFilter(f); setLimit(PAGE_SIZE); };
  // "waiting 3h" counts on: re-read the clock while shown.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(iv);
  }, []);

  // Every card drawn here reports itself as on the drawer's History (Arturo's cards-on-screen context).
  return (
    <CardSurface.Provider value="history">
    <div className="dh" data-drawer-history-section>
      <h3 className="arturo-panel-section">Running</h3>
      {isArturo ? <ArturoRunning /> : <AgentRunning subagents={subagents} name={name} />}

      {decisionsQ.isLoading ? <p className="arturo-panel-empty">Loading…</p>
        : decisionsQ.isError && !decisionsQ.data ? <p className="arturo-panel-empty" data-drawer-history>Couldn't read {name}'s cards.</p>
        : items.length === 0 ? (
          <>
            <h3 className="arturo-panel-section">Waiting</h3>
            <p className="arturo-panel-empty" data-drawer-history>Nothing is waiting for you.</p>
          </>
        ) : (
          <div data-drawer-history>
            <div className="dh-filters" role="group" aria-label="Filter cards">
              <div className="dh-chips" role="radiogroup" aria-label="State">
                {chips.states.map((c) => (
                  <button key={c.id} type="button" role="radio" aria-checked={filter.state === c.id} data-history-chip={`state-${c.id}`}
                          className={clsx('dh-chipbtn', filter.state === c.id && 'is-on')} onClick={() => pick({ ...filter, state: c.id })}>
                    {c.label}<span className="dh-count">{c.count}</span>
                  </button>
                ))}
              </div>
              {chips.kinds.length > 0 && (
                <div className="dh-chips" role="radiogroup" aria-label="Kind">
                  {chips.kinds.map((c) => (
                    <button key={c.id} type="button" role="radio" aria-checked={filter.kind === c.id} data-history-chip={`kind-${c.id}`}
                            className={clsx('dh-chipbtn', filter.kind === c.id && 'is-on')} onClick={() => pick({ ...filter, kind: c.id })}>
                      {c.label}<span className="dh-count">{c.count}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {filter.state !== 'resolved' && (
              <>
                <h3 className="arturo-panel-section">Waiting{waiting.length ? ` · ${waiting.length}` : ''}</h3>
                {waiting.length === 0 ? <p className="arturo-panel-empty">Nothing is waiting for you.</p> : (
                  <div className="dh-list" data-history-group="waiting">
                    {waiting.map((it, i) => (
                      <WaitingCard key={it.id} item={it} now={now} startOpen={i === 0} onAnswered={refresh} onGoTo={goTo} />
                    ))}
                  </div>
                )}
              </>
            )}

            {filter.state !== 'waiting' && (
              <>
                <h3 className="arturo-panel-section">Resolved{resolved.length ? ` · ${resolved.length}` : ''}</h3>
                {resolved.length === 0 ? <p className="arturo-panel-empty">Nothing resolved yet.</p> : (
                  <div className="dh-list" data-history-group="resolved">
                    {page.shown.map((it) => <ResolvedCard key={it.id} item={it} now={now} onAnswered={refresh} onGoTo={goTo} />)}
                  </div>
                )}
                {page.more > 0 && (
                  <button type="button" className="dh-more" data-history-more onClick={() => setLimit((n) => n + PAGE_SIZE)}>
                    Show {page.more} more
                  </button>
                )}
              </>
            )}
          </div>
        )}

      {isArturo && <ArturoConversations />}
    </div>
    </CardSurface.Provider>
  );
}

/** A History card as Arturo's cards-on-screen context sees it while compact (opened, the chat's own card reports). */
function historyScreenCard(item: HistoryItem): ScreenCard {
  const o = item.outcome;
  const state = o === null ? 'waiting' : o === 'dismissed' || o === 'expired' || o === 'withdrawn' ? o : o === 'replaced' ? 'replaced' : 'answered';
  return {
    id: item.id, kind: item.kind, from: item.agent, state, question: item.question,
    ...(item.summary ? { summary: plainSummary(item.summary) } : {}),
    ...(item.answer.length ? { answer: answerText(item.answer) } : {}),
  };
}

function WaitingCard({ item, now, startOpen, onAnswered, onGoTo }: {
  item: HistoryItem; now: number; startOpen: boolean; onAnswered: () => void; onGoTo: (id: string) => void;
}) {
  const [open, setOpen] = useState(startOpen);
  const summary = plainSummary(item.summary);
  const frame = useRef<HTMLElement>(null);
  useScreenCard(frame, historyScreenCard(item), () => { setOpen(true); return 'The card was closed: it is open now, so ask again.'; });
  return (
    <article ref={frame} className={`dh-card dh-kind-${item.kind}`} data-history-item={item.id} data-history-state="waiting" data-history-kind={item.kind}>
      <div className="dh-head">
        <span className="dh-chip">{KIND_LABEL[item.kind]}</span>
        <span className="dh-meta">{waitingLine(item, now)}</span>
      </div>
      <p className="dh-q">{item.question}</p>
      {summary && summary !== item.question && <p className="dh-sum">{summary}</p>}
      <div className="dh-actions">
        {/* A choice card is answered in Arturo's conversation (its answer is his next message): Go to card. */}
        {!item.choice && (
          <button type="button" className="dh-act is-primary" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide' : 'Answer here'}
          </button>
        )}
        {item.inChat && <button type="button" className="dh-act" data-history-goto onClick={() => onGoTo(item.id)}>Go to card</button>}
      </div>
      {open && item.decision && (
        <div className="dh-full" data-history-full>
          <DecisionBubble decision={item.decision} onAnswered={onAnswered} />
        </div>
      )}
    </article>
  );
}

function ResolvedCard({ item, now, onAnswered, onGoTo }: {
  item: HistoryItem; now: number; onAnswered: () => void; onGoTo: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const outcome = item.outcome ?? 'answered';
  const multi = item.answer.length > 1 || !!item.answer[0]?.question;
  // A terminal menu's record is one line in the chat too: there is no fuller card to open.
  const expandable = item.kind !== 'menu';
  const frame = useRef<HTMLElement>(null);
  useScreenCard(frame, historyScreenCard(item));
  return (
    <article ref={frame} className={`dh-card dh-kind-${item.kind} is-resolved`} data-history-item={item.id} data-history-state="resolved"
             data-history-kind={item.kind} data-history-outcome={outcome}>
      <div className="dh-head">
        <span className={`dh-badge is-${outcome}`}>{OUTCOME_LABEL[outcome]}</span>
        <span className="dh-chip">{KIND_LABEL[item.kind]}</span>
      </div>
      <p className="dh-q is-compact">{item.question}</p>
      {item.answer.length > 0 && (
        multi ? (
          <ol className="dh-answer dh-parts" aria-label={`Answer: ${answerText(item.answer)}`} data-history-answer>
            {item.answer.map((l, i) => (
              <li key={i} className={clsx(l.skipped && 'is-skipped')}>
                {l.question && <span className="dh-partq">{l.question}</span>}
                <span className="dh-parta">{l.text}</span>
              </li>
            ))}
          </ol>
        ) : <p className="dh-answer" data-history-answer>{item.answer[0].text}</p>
      )}
      <p className="dh-meta dh-who">{resolvedLine(item, now)}</p>
      {(expandable || item.inChat) && (
        <div className="dh-actions">
          {expandable && (
            <button type="button" className="dh-act" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {open ? 'Less' : 'Show the card'}
            </button>
          )}
          {item.inChat && <button type="button" className="dh-act" data-history-goto onClick={() => onGoTo(item.id)}>Go to card</button>}
        </div>
      )}
      {open && (
        <div className="dh-full" data-history-full>
          {item.decision ? <DecisionBubble decision={item.decision} onAnswered={onAnswered} />
            : item.batch ? <AnsweredQuestionCard title={item.agent} parts={item.batch.parts} answers={batchAnswers(item.batch)}
                report={{ id: item.id, kind: 'multipart', from: item.agent }} />
            : item.choice ? <ChoiceAnswered item={item} />
            : null}
        </div>
      )}
    </article>
  );
}

/** One of Arturo's choice cards, answered or closed, read-only: the card as his page draws it. */
function ChoiceAnswered({ item }: { item: HistoryItem }) {
  const c = item.choice!;
  const parts = choiceParts(c.options, c.multi, c.exclusive, item.question);
  return (
    <AnsweredQuestionCard title={item.agent} parts={parts}
      answers={[c.answered ? choiceAnswerFromText(parts[0], c.answered) : { ns: [], text: '', skipped: true }]}
      report={{ id: item.id, kind: 'choice', from: item.agent, state: item.outcome === 'dismissed' ? 'dismissed' : 'answered' }} />
  );
}

/** An agent's subagents: counted by its status row; they have no cancel route, so there is no Cancel. */
function AgentRunning({ subagents, name }: { subagents?: number; name: string }) {
  const rows = runningRows({ subagents, name });
  if (!rows.length) return <p className="arturo-panel-empty" data-history-running>Nothing running beside {name}'s own turn.</p>;
  return (
    <ul className="arturo-panel-jobs" data-history-running>
      {rows.map((r) => (
        <li key={r.id}>
          <span className="dh-running"><LoaderCircle size={13} className="animate-spin" aria-hidden />{r.title}</span>
        </li>
      ))}
    </ul>
  );
}

/** Arturo's background jobs in the open conversation, polled while one runs; each can be cancelled. */
function ArturoRunning() {
  const thread = currentThread();
  const [jobs, setJobs] = useState<ArturoJob[] | null>(null);
  const [, setTick] = useState(0);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (!thread) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const got = await fetchJobs(thread);
      if (!alive) return;
      if (got) setJobs(got); else setJobs((j) => j ?? []);
      timer = setTimeout(poll, got?.some((j) => j.status === 'running') ? JOBS_RUNNING_MS : JOBS_IDLE_MS);
    };
    void poll();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [thread]);
  const rows = runningRows({ jobs: jobs ?? [], name: '', elapsed: (j) => elapsed(j) });
  useEffect(() => {                       // the elapsed clock ticks while something runs
    if (!rows.length) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [rows.length]);
  if (!thread) return <p className="arturo-panel-empty" data-history-running>No conversation open.</p>;
  if (jobs === null) return <p className="arturo-panel-empty">Loading…</p>;
  if (!rows.length) return <p className="arturo-panel-empty" data-history-running>Nothing running in this conversation.</p>;
  const cancel = async (id: string) => {
    setCancelling(id); setProblem(null);
    const ok = await cancelJob(thread, id);
    setCancelling(null);
    if (ok) setJobs((cur) => (cur ?? []).filter((j) => j.id !== id));
    else setProblem("Couldn't cancel that job. Try again.");
  };
  return (
    <>
      <ul className="arturo-panel-jobs" data-history-running>
        {rows.map((r) => (
          <li key={r.id}>
            <span className="dh-running"><LoaderCircle size={13} className="animate-spin" aria-hidden /><span className="truncate">{r.title}</span></span>
            <span className="dh-running-end">
              <span className="arturo-panel-meta tabular-nums">{r.detail}</span>
              {r.cancellable && (
                <button type="button" className="arturo-panel-link" disabled={cancelling === r.id} aria-label={`Cancel ${r.title}`}
                        onClick={() => void cancel(r.id)}>Cancel</button>
              )}
            </span>
          </li>
        ))}
      </ul>
      {problem && <p className="arturo-panel-error" role="alert">{problem}</p>}
    </>
  );
}

const dayLabel = (ms: number) => new Date(ms).toLocaleDateString([], { month: 'short', day: 'numeric' });

/** Arturo's latest conversations, as History showed them before it held cards. */
function ArturoConversations() {
  const [rows, setRows] = useState<{ key: string; label: string; value: string }[] | null>(null);
  useEffect(() => {
    let alive = true;
    listThreads(8).then((ts) => ts.map((t) => ({ key: t.id, label: t.title || 'Conversation', value: dayLabel(t.updated * 1000) })))
      .then((r) => { if (alive) setRows(r); }).catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, []);
  return (
    <>
      <h3 className="arturo-panel-section">Latest conversations</h3>
      {rows === null ? <p className="arturo-panel-empty">Loading…</p>
        : rows.length === 0 ? <p className="arturo-panel-empty">No conversations yet.</p>
        : (
          <div className="arturo-panel-list arturo-panel-list-tight" data-history-conversations>
            {rows.map((r) => (
              <div key={r.key} className="arturo-panel-history-row">
                <span className="arturo-panel-history-label">{r.label}</span>
                <span className="arturo-panel-history-value">{r.value}</span>
              </div>
            ))}
          </div>
        )}
    </>
  );
}
