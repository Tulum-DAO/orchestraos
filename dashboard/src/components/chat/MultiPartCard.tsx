/**
 * MultiPartCard — the one question card every question is answered with, one part at a time: an agent's multi-part
 * menu, its single menus, the decision cards agents post (Approvals, Inbox, the chat), questionnaires, and Arturo's
 * choice cards. A single question is a card of one part. lib/multiPart.ts holds the rules; each caller says how the
 * answers are sent (its own route) through `onSubmit`.
 *
 * The card is its own frame: one small header line (who is asking, and the ✕), any context the caller adds (a
 * decision's text, a questionnaire's summary), the part on show at full width (the parts slide; no neighbour peeks
 * in), then progress dots on the left and pill buttons on the right.
 *
 *  - A click on an option answers a single-select part and slides to the next; a multi-select part takes ticks and
 *    its own Next.
 *  - Skip leaves the part unanswered and slides on. Back shows the part before with its answer still marked;
 *    picking again replaces it.
 *  - Moving on from the last part sends every answer at once (no confirm step: that last pick is the confirm).
 *  - Once sent, the card stays, dimmed, showing only what was chosen per part, paged with Back and Next.
 *
 * Height: every part is laid out in the same grid cell, so the card is as tall as its TALLEST part (no jump between
 * parts, no space below the tallest), and grows with the words field. It is capped so the whole card fits the
 * viewport; only then does a part scroll inside.
 *
 * MultiPartAnswer is the agent's multi-part menu: the gateway reads every part first (/agent-menu-capture: Right
 * only, never Enter or a digit, and only when the question is on screen and on its first part), then takes the
 * answers bound to the card they were drawn from (/menu-submit). AnsweredMultiPart draws an answered
 * AskUserQuestion in the transcript, so the card is still in the thread after the live question is gone.
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { clsx } from 'clsx';
import { Check, ListChecks, Loader2, PenLine } from 'lucide-react';
import { DismissButton } from './DecisionCard';
import { withRefs } from '../kit/RefText';
import {
  answered, back, canNext, canSkip, captureProblem, cardKey, cardNote, choose, letter, next, nothingAnswered, outcome, partRows, setWords,
  skipPart, startState, submitAnswers, takesWords, type CardState, type MenuPart, type PartAnswer, type Step,
} from '../../lib/multiPart';
import { answerStep, partView, type ScreenCard } from '../../lib/screenCards';
import { useScreenCard } from '../../lib/threadKit/useScreenCard';
import type { RefCardKind, RefCardState } from '../../lib/cardRefs';

const PILL = 'hit-44 text-xs font-medium px-3 py-1 rounded-full border transition-colors disabled:opacity-40';
const PILL_QUIET = `${PILL} border-border text-muted-foreground hover:bg-muted hover:text-foreground`;
const PILL_MAIN = `${PILL} border-info bg-info text-info-foreground hover:bg-info/90`;

/** What a card is, for the cards-on-screen context Arturo reads (lib/screenCards.ts): its id, kind and who asked.
 *  The card adds its state, the part on show and what is picked, typed or answered. */
export interface CardReport {
  id: string;
  kind: RefCardKind;
  from?: string | null;
  /** The card's question when it is not its first part's (a questionnaire's title). */
  question?: string;
  summary?: string | null;
  /** An answered card that is not answered (dismissed, expired…). */
  state?: RefCardState;
}

/** An answered card's answer in one line: each part's picks and words, parts joined by "; ". */
function answerLine(parts: MenuPart[], answers: PartAnswer[]): string {
  return parts.map((p, i) => {
    const o = outcome(p, answers[i]);
    const a = o.skipped ? 'Skipped' : [o.labels.join(', '), o.words].filter(Boolean).join(' — ');
    return parts.length > 1 ? `${p.question}: ${a}` : a;
  }).join('; ');
}

function screenCard(r: CardReport, parts: MenuPart[], state: RefCardState, view: CardState, answer?: string | null): ScreenCard {
  return {
    id: r.id, kind: r.kind, from: r.from ?? null, state, question: (r.question || parts[0]?.question || '').trim(),
    ...(r.summary ? { summary: r.summary } : {}), ...(answer ? { answer } : {}),
    ...(parts.length ? { part: partView(parts, view) } : {}),
  };
}

/** Space kept free above and below the card when it is capped at the viewport. */
const VIEWPORT_MARGIN = 24;

interface FrameProps {
  /** Who is asking: the header line. */
  title: string;
  /** Next to the title, quieter: the kind of card, its risk. */
  meta?: ReactNode;
  onDismiss?: () => Promise<string | null>;
  dimmed?: boolean;
  /** Above the parts, for every part: a decision's text, a questionnaire's summary. */
  context?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  id?: string;
  highlighted?: boolean;
  /** The frame's element, for the card's keys and its height cap. */
  frameRef?: React.Ref<HTMLDivElement>;
}

/** The frame: one small header line, the context, the parts, and the footer (dots left, pills right). */
function Frame({ title, meta, onDismiss, dimmed, context, children, footer, id, highlighted, frameRef }: FrameProps) {
  return (
    <div ref={frameRef} id={id} data-decision-card data-multipart data-state={dimmed ? 'answered' : 'open'}
         data-focused={highlighted || undefined} tabIndex={0}
         className={clsx('rounded-2xl border bg-card text-card-foreground overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-info/50',
           highlighted && 'ring-2 ring-info/60', dimmed ? 'border-border' : 'border-info/40')}>
      <div className="touch-compact flex items-center gap-1.5 pl-3.5 pr-2 pt-2 min-h-[2rem]">
        <ListChecks size={13} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 truncate text-xs font-medium text-muted-foreground">{title}</span>
        {meta && <span className="min-w-0 truncate text-[11px] text-muted-foreground/80">{meta}</span>}
        <span className="flex-1" />
        {onDismiss && <DismissButton onDismiss={onDismiss} />}
      </div>
      {context && <div className={clsx('px-3.5 pt-1 pb-1.5 max-h-[30vh] overflow-y-auto', dimmed && 'opacity-60')} data-context>{context}</div>}
      <div className={clsx(dimmed && 'opacity-60')}>{children}</div>
      {footer && <div className="touch-compact flex items-center gap-2 px-3.5 pb-3 pt-1">{footer}</div>}
    </div>
  );
}

/** The tallest a part may be: what is left of the viewport once the rest of the card is drawn. Re-measured when
 *  the window resizes and when anything in the card changes size (the words field growing, context opening). */
function usePartCap(frame: React.RefObject<HTMLDivElement | null>, parts: React.RefObject<HTMLDivElement | null>): number | null {
  const [cap, setCap] = useState<number | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const f = frame.current; const p = parts.current;
      if (!f || !p) return;
      const rest = f.getBoundingClientRect().height - p.getBoundingClientRect().height;
      // The viewport, or the scrolling area the card sits in when that is smaller (a chat under a top bar).
      let vh = window.visualViewport?.height ?? window.innerHeight;
      for (let el = f.parentElement; el; el = el.parentElement) {
        // A floating box that grows with its card up to a cap (a chip's popover): the cap is the room, not its
        // current height, which is the card's own and would shrink it.
        if (el.dataset.capRoot !== undefined) {
          const mh = parseFloat(getComputedStyle(el).maxHeight);
          if (mh > 0) vh = Math.min(vh, mh);
          break;
        }
        const oy = getComputedStyle(el).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && el.clientHeight > 0) { vh = Math.min(vh, el.clientHeight); break; }
      }
      const next = Math.max(120, Math.floor(vh - VIEWPORT_MARGIN * 2 - rest));
      setCap((c) => (c === next ? c : next));
    };
    // The observer reports the frame's size once on observe, then on every change.
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (ro && frame.current) ro.observe(frame.current);
    window.addEventListener('resize', measure);
    window.visualViewport?.addEventListener('resize', measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', measure);
      window.visualViewport?.removeEventListener('resize', measure);
    };
  }, [frame, parts]);
  return cap;
}

/** Every part in the same grid cell, so the row is as tall as the tallest part; the one on show slides in, the rest
 *  sit beside it, clipped, and can be neither seen nor reached. */
function Slides({ parts, at, render, partsRef, cap }: {
  parts: MenuPart[]; at: number; render: (p: MenuPart, i: number) => ReactNode;
  partsRef: React.Ref<HTMLDivElement>; cap: number | null;
}) {
  return (
    <div ref={partsRef} className="overflow-hidden" data-parts>
      <div className="grid">
        {parts.map((p, i) => (
          <div key={p.index} data-part={i} data-current={i === at || undefined} inert={i !== at} aria-hidden={i !== at || undefined}
               className="[grid-area:1/1] min-w-0 flex flex-col px-3.5 pt-1 pb-1 overflow-y-auto overscroll-contain transition-transform duration-300 ease-out motion-reduce:transition-none"
               style={{ transform: `translateX(${(i - at) * 100}%)`, maxHeight: cap ? `${cap}px` : 'calc(100dvh - 12rem)' }}>
            {render(p, i)}
          </div>
        ))}
      </div>
    </div>
  );
}

function Dots({ answers, at }: { answers: PartAnswer[]; at: number }) {
  if (answers.length < 2) return null;
  return (
    <div className="flex items-center gap-1.5" aria-label={`Part ${at + 1} of ${answers.length}`} role="img" data-dots>
      {answers.map((a, i) => (
        <span key={i} className={clsx('h-1.5 rounded-full transition-all',
          i === at ? 'w-4 bg-info' : answered(a) ? 'w-1.5 bg-info/50' : 'w-1.5 bg-muted-foreground/30')} />
      ))}
    </div>
  );
}

function Question({ part }: { part: MenuPart }) {
  return (
    <div className="pb-2">
      <div className="text-[14.5px] font-semibold leading-snug">{withRefs(part.question)}</div>
      {part.select === 'multi' && <div className="text-[11px] text-muted-foreground mt-0.5">Pick any</div>}
    </div>
  );
}

function Badge({ i, on, multi }: { i: number; on: boolean; multi: boolean }) {
  return (
    <span className={clsx('mt-px w-[22px] h-[22px] shrink-0 border flex items-center justify-center text-[11px] font-semibold',
      multi ? 'rounded-md' : 'rounded-full', on ? 'border-info bg-info text-info-foreground' : 'border-border text-muted-foreground')}>
      {multi && on ? <Check size={12} strokeWidth={3} /> : letter(i)}
    </span>
  );
}

const CHOSEN_ROW = 'flex items-start gap-2.5 rounded-xl border border-info/40 bg-info/5 px-2.5 py-2 text-[13.5px]';

/** Typed words in an answered card: drawn as a chosen row, as plain text (never in quotes). */
function WordsRow({ words }: { words: string }) {
  return (
    <div data-chosen data-words className={CHOSEN_ROW}>
      <span className="mt-px w-[22px] h-[22px] shrink-0 rounded-full border border-info bg-info text-info-foreground flex items-center justify-center">
        <PenLine size={11} strokeWidth={2.5} aria-label="your words" />
      </span>
      <span className="font-medium leading-snug whitespace-pre-wrap break-words min-w-0">{words}</span>
    </div>
  );
}

/** The own-words field: grows with what is typed (the card re-measures), Return moves on, Shift+Return breaks. */
function WordsField({ value, onChange, onEnter, label, disabled, placeholder }: {
  value: string; onChange: (v: string) => void; onEnter: () => void; label: string; disabled?: boolean; placeholder: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const t = ref.current;
    if (!t) return;
    t.style.height = 'auto';
    t.style.height = `${t.scrollHeight}px`;
  }, [value]);
  return (
    <textarea ref={ref} rows={1} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onEnter(); } }}
              placeholder={placeholder} aria-label={label}
              className="block w-full resize-none overflow-hidden rounded-xl border border-border bg-transparent px-3 py-2 text-sm leading-snug placeholder:text-muted-foreground focus:outline-none focus:border-info disabled:opacity-50" />
  );
}

export interface QuestionCardProps {
  title: string;
  meta?: ReactNode;
  context?: ReactNode;
  parts: MenuPart[];
  /** Where the card opens (a questionnaire's saved draft). */
  initial?: PartAnswer[];
  /** Sends every answer through the card kind's own route: null once sent, else what went wrong (the card stays
   *  open with it). Called once per card unless it fails. */
  onSubmit: (answers: PartAnswer[]) => Promise<string | null>;
  onDismiss?: () => Promise<string | null>;
  dismissed?: boolean;
  /** The card cannot send yet; why, shown under the parts (picks on earlier parts still work). */
  waiting?: string | null;
  /** A standing hint under the parts. */
  hint?: string | null;
  /** Shown under an answered card: who answered, where, when. */
  answeredNote?: ReactNode;
  id?: string;
  highlighted?: boolean;
  /** Reports the card into the cards-on-screen context (lib/screenCards.ts). */
  report?: CardReport;
}

/** The answer area, framed: takes the answers one part at a time and sends them all at the end. */
export function QuestionCard({ title, meta, context, parts, initial, onSubmit, onDismiss, dismissed, waiting, hint,
  answeredNote, id, highlighted, report }: QuestionCardProps) {
  const [s, setS] = useState<CardState>(() => ({ ...startState(parts), ...(initial ? { answers: initial } : {}) }));
  const [phase, setPhase] = useState<'answer' | 'sending' | 'done'>('answer');
  const [problem, setProblem] = useState<string | null>(null);
  const sent = useRef(false);
  const frame = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const cap = usePartCap(frame, box);

  const send = async (state: CardState) => {
    if (sent.current || waiting) return;
    sent.current = true;
    setPhase('sending'); setProblem(null);
    const err = await onSubmit(state.answers);
    if (!err) { setS({ ...state, at: 0 }); setPhase('done'); return; }
    sent.current = false;
    setProblem(err); setPhase('answer');
  };
  const step = (r: Step) => { setS(r.state); setProblem(null); if (r.submit) void send(r.state); };
  const open = phase === 'answer';

  // On screen for Arturo: the part on show and what is picked or typed; an answer he hands back takes the same step
  // the taps would (so it sends through this card's own route, with the same send states).
  useScreenCard(frame, report && phase !== 'done' ? screenCard(report, parts, dismissed ? 'dismissed' : 'waiting', s) : null,
    (a) => {
      if (phase !== 'answer' || dismissed) return 'That card is already being answered.';
      if (waiting && s.at === parts.length - 1) return waiting;
      const r = answerStep(parts, s, a);
      if ('error' in r) return r.error;
      step(r);
      return null;
    });

  // Keys while the card has focus: A–Z pick on the part on show, Return moves on.
  const latest = useRef({ open, parts, s, step });
  useLayoutEffect(() => { latest.current = { open, parts, s, step }; });
  useEffect(() => {
    const card = frame.current;
    if (!card) return;
    const onKey = (e: KeyboardEvent) => {
      const { open: o, parts: ps, s: st, step: go } = latest.current;
      if (!o) return;
      const t = e.target as HTMLElement;
      const r = cardKey(ps, st, e.key, { modifier: e.metaKey || e.ctrlKey || e.altKey,
        inField: t.tagName === 'INPUT' || t.tagName === 'TEXTAREA', onButton: t.tagName === 'BUTTON' });
      if (r) { e.preventDefault(); go(r); }
    };
    card.addEventListener('keydown', onKey);
    return () => card.removeEventListener('keydown', onKey);
  }, [phase, dismissed]);

  if (dismissed) {
    return (
      <Frame title={title} meta={meta} dimmed id={id} highlighted={highlighted} frameRef={frame}>
        <div className="px-3.5 pb-3 pt-1 text-xs text-muted-foreground" data-testid="decision-outcome">Dismissed</div>
      </Frame>
    );
  }
  if (phase === 'done') {
    return <AnsweredQuestionCard title={title} meta={meta} context={context} parts={parts} answers={s.answers}
                                 note={answeredNote} id={id} highlighted={highlighted} report={report} />;
  }

  const part = parts[s.at];
  const last = s.at === parts.length - 1;
  const a = s.answers[s.at];
  // Skip on the last part with nothing answered would send nothing; the card says so instead.
  const emptyNote = last && a?.skipped && nothingAnswered(s) ? 'Nothing to send yet: answer a part, or dismiss the question.' : null;
  const note = cardNote({ phase, problem, emptyNote, waiting, hint });
  const one = parts.length === 1;

  return (
    <Frame title={title} meta={meta} context={context} onDismiss={onDismiss} id={id} highlighted={highlighted} frameRef={frame} footer={
      <>
        <Dots answers={s.answers} at={s.at} />
        <span className="flex-1" />
        {phase === 'sending' ? (
          <span className="text-xs text-muted-foreground flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" />Sending</span>
        ) : (
          <>
            {s.at > 0 && <button type="button" className={PILL_QUIET} onClick={() => step({ state: back(s), submit: false })}>Back</button>}
            {canSkip(parts, s) && <button type="button" className={PILL_QUIET} disabled={!!waiting && last} onClick={() => step(skipPart(parts, s))}>Skip</button>}
            {(part.select === 'multi' || canNext(s) || (one && takesWords(part))) && (
              <button type="button" className={PILL_MAIN} disabled={!canNext(s) || (last && !!waiting)} onClick={() => step(next(parts, s))}>
                {last ? 'Send' : 'Next'}
              </button>
            )}
          </>
        )}
      </>
    }>
      <Slides parts={parts} at={s.at} partsRef={box} cap={cap} render={(p, i) => {
        const ans = s.answers[i];
        const multi = p.select === 'multi';
        const rows = partRows(p);
        return (
          <>
            <Question part={p} />
            <div role={multi ? 'group' : 'radiogroup'} aria-label={p.question} className="space-y-1">
              {rows.map((o, k) => {
                const on = ans.ns.includes(o.n);
                return (
                  <button key={o.n} type="button" role={multi ? 'checkbox' : 'radio'} aria-checked={on} data-option={o.n}
                          disabled={phase !== 'answer' || (!!waiting && i === parts.length - 1 && !multi)}
                          onClick={() => step(choose(parts, { ...s, at: i }, o.n))}
                          className={clsx('w-full flex items-start gap-2.5 rounded-xl border px-2.5 py-2 text-left text-[13.5px] transition-colors disabled:cursor-default',
                            on ? 'border-info/60 bg-info/10' : 'border-border hover:bg-muted/60')}>
                    <Badge i={k} on={on} multi={multi} />
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium leading-snug break-words">
                        {o.label}
                        {p.lean === o.n && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">· agent leans</span>}
                      </span>
                      {o.detail && <span className="block text-xs text-muted-foreground mt-0.5">{o.detail}</span>}
                    </span>
                  </button>
                );
              })}
              {takesWords(p) && (
                <WordsField value={ans.text} disabled={phase !== 'answer'}
                            onChange={(v) => setS((x) => setWords(parts, { ...x, at: i }, v))}
                            onEnter={() => step(next(parts, { ...s, at: i }))}
                            placeholder={rows.length ? 'Or type your own answer' : 'Your answer'}
                            label={`Your own answer to ${p.question}`} />
              )}
            </div>
          </>
        );
      }} />
      {note && <div className={clsx('px-3.5 pt-1 text-[11px]', problem ? 'text-warning' : 'text-muted-foreground')} data-note>{note}</div>}
    </Frame>
  );
}

/** An answered question card: dimmed, only what was chosen per part ("Skipped" where nothing was), paged with Back
 *  and Next. Typed words are drawn like a chosen option, never in quotes. */
export function AnsweredQuestionCard({ title, meta, context, parts, answers, note, id, highlighted, elsewhere, report }: {
  title: string; meta?: ReactNode; context?: ReactNode; parts: MenuPart[]; answers: PartAnswer[]; note?: ReactNode;
  id?: string; highlighted?: boolean;
  /** The answer is shown elsewhere (a message in the chat), so the card says where instead of repeating it. */
  elsewhere?: string | null;
  report?: CardReport;
}) {
  const [at, setAt] = useState(0);
  const frame = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const cap = usePartCap(frame, box);
  useScreenCard(frame, report ? screenCard(report, parts, report.state ?? 'answered', { answers, at },
    elsewhere ? null : answerLine(parts, answers)) : null);
  const paged = parts.length > 1;
  return (
    <Frame title={title} meta={meta} context={context} dimmed id={id} highlighted={highlighted} frameRef={frame}
      footer={paged || note ? (
        <>
          <Dots answers={answers} at={at} />
          {note && <span className="min-w-0 truncate text-[11px] text-muted-foreground" data-answered-note>{note}</span>}
          <span className="flex-1" />
          {paged && (
            <>
              <button type="button" className={PILL_QUIET} disabled={at === 0} onClick={() => setAt(at - 1)}>Back</button>
              <button type="button" className={PILL_QUIET} disabled={at === parts.length - 1} onClick={() => setAt(at + 1)}>Next</button>
            </>
          )}
        </>
      ) : undefined}>
      <Slides parts={parts} at={at} partsRef={box} cap={cap} render={(p, i) => {
        const out = outcome(p, answers[i]);
        const rows = partRows(p);
        return (
          <div data-testid="decision-outcome" className="contents">
            <Question part={p} />
            <div className={clsx('space-y-1', !paged && !note && 'pb-2.5')}>
              {elsewhere ? (
                <div className="text-[13px] italic text-muted-foreground px-1" data-elsewhere>{elsewhere}</div>
              ) : out.skipped ? (
                <div className="text-[13px] italic text-muted-foreground px-1" data-skipped>Skipped</div>
              ) : (
                <>
                  {rows.map((o, k) => answers[i].ns.includes(o.n) && (
                    <div key={o.n} data-chosen className={CHOSEN_ROW}>
                      <Badge i={k} on multi={p.select === 'multi'} />
                      <span className="font-medium leading-snug break-words min-w-0">{o.label}</span>
                    </div>
                  ))}
                  {out.words && <WordsRow words={out.words} />}
                </>
              )}
            </div>
          </div>
        );
      }} />
    </Frame>
  );
}

/** An answered multi-part AskUserQuestion, as the transcript draws it. */
export function AnsweredMultiPart({ agentName, parts, answers }: { agentName: string; parts: MenuPart[]; answers: PartAnswer[] }) {
  return <AnsweredQuestionCard title={agentName} parts={parts} answers={answers} />;
}

/** What the gateway answers a walk or a submit with (the fields this card reads). */
interface GatewayReply {
  ok?: boolean; reason?: string; error?: string; detail?: string; walk_complete?: boolean; parts?: MenuPart[];
}

async function postJson(url: string, body: unknown): Promise<{ status: number; body: GatewayReply }> {
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  } catch {
    return { status: 0, body: {} };
  }
}

function submitProblem(status: number, body: GatewayReply): string {
  if (status === 0) return 'Couldn’t reach OrchestraOS. Nothing was sent.';
  const r = body?.reason;
  if (r === 'card_mismatch' || r === 'instance_mismatch') return 'This question was replaced by a newer one. Check the agent’s screen.';
  if (r === 'no_durable_row') return 'The question isn’t on the server yet. Try again in a moment.';
  if (r === 'free_text_not_armed') return 'Typing your own words is switched off on this server.';
  return `Couldn’t send: ${body?.error || body?.detail || r || `error ${status}`}.`;
}

/** An agent's multi-part menu: reads every part, then the shared card takes the answers. */
export function MultiPartAnswer({ agentId, agentName, cardId, onDismiss, dismissed, id, highlighted, onAnswered }: {
  agentId: string;
  /** After a send went through (a chip's popover closes). */
  onAnswered?: () => void;
  agentName?: string | null;
  /** The server's card for this question: the answers are bound to it. Null until it arrives. */
  cardId: string | null;
  onDismiss?: () => Promise<string | null>;
  dismissed?: boolean;
  id?: string;
  highlighted?: boolean;
}) {
  const [parts, setParts] = useState<MenuPart[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [tries, setTries] = useState(0);
  const agent = agentName || agentId;

  useEffect(() => {
    let alive = true;
    void postJson(`/api/agents/${encodeURIComponent(agentId)}/menu-capture`, {}).then(({ status, body }) => {
      if (!alive) return;
      if (status === 200 && body.ok && body.walk_complete && Array.isArray(body.parts) && body.parts.length) {
        setParts(body.parts); setProblem(null);
      } else {
        setProblem(status === 0 ? 'Couldn’t reach OrchestraOS.' : captureProblem(body?.reason));
      }
    });
    return () => { alive = false; };
  }, [agentId, tries]);

  // DECISION: no confirm step. The person's last pick (or Next/Send on the last part) IS the confirmation, so the
  // card always sends `confirm: true`; the gateway's 428 two-step stays for clients that want it.
  const submit = async (answers: PartAnswer[]): Promise<string | null> => {
    const { status, body } = await postJson(`/api/agents/${encodeURIComponent(agentId)}/menu-submit`,
      { answers: submitAnswers(parts!, answers), card_id: cardId, confirm: true });
    if (status === 200 && body.ok !== false) { onAnswered?.(); return null; }
    return submitProblem(status, body);
  };

  if (!parts || dismissed) {
    return (
      <Frame title={agent} onDismiss={dismissed ? undefined : onDismiss} dimmed={dismissed} id={id} highlighted={highlighted}>
        {dismissed ? <div className="px-3.5 pb-3 pt-1 text-xs text-muted-foreground" data-testid="decision-outcome">Dismissed</div>
          : !problem
          ? <div className="px-3.5 pb-3.5 pt-1 text-xs text-muted-foreground flex items-center gap-2"><Loader2 size={13} className="animate-spin" />Reading every part…</div>
          : (
            <div className="px-3.5 pb-3.5 pt-1 text-xs text-warning">
              {problem}{' '}
              <button type="button" className="underline text-info" onClick={() => { setProblem(null); setTries((n) => n + 1); }}>Try again</button>
            </div>
          )}
      </Frame>
    );
  }
  return (
    <QuestionCard key={tries} title={agent} parts={parts} onSubmit={submit} onDismiss={onDismiss} id={id} highlighted={highlighted}
      waiting={cardId ? null : 'You can answer in a moment, once this question reaches the server.'}
      report={{ id: cardId ?? `menu:${agentId}`, kind: 'multipart', from: agent }} />
  );
}

/** A multi-part question as a card of its own. */
export default MultiPartAnswer;
