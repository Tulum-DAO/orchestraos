/**
 * MenuAnswerBlock — the answer area of a decision or menu card (DecisionCard): one numbered row per option,
 * then an "Or answer in your own words" field and Send, always shown.
 *
 * Picking a row highlights it and shows "Send N ✓" on it; that, the Send button or Return sends. A long
 * option is cut short and shows in full once picked. A write-in option shows a text-cursor icon, and picking
 * it goes to the text field. Claude Code's built-in "Type something." / "Chat about this" rows are not drawn
 * (lib/decisionCard visibleOptions): the field does their job, and words alone are sent through them.
 * Keys while the card has focus: 1–9 pick, Return sends, e goes to the field.
 *
 * Contracts (DOCS/SURFACE_CONTRACTS.md), unchanged:
 *   A1. option_n OR answer_text alone is a valid answer.
 *   A2. option_n AND answer_text may both be submitted.
 *   A3. Selecting an option never wipes typed answer_text (the caller owns the state through menuAnswer.ts's
 *       selectOption/setText; this component never merges or clears the two).
 */
import { useEffect, useLayoutEffect, useRef } from 'react';
import { clsx } from 'clsx';
import { Loader2, TextCursorInput } from 'lucide-react';
import { canSubmit, type MenuAnswerState, type MenuOption } from '../../lib/menuAnswer';
import { canSendWords, isLongOption, isWriteIn, keyAction, visibleOptions } from '../../lib/decisionCard';

// The "(e)" key hint only where there is a keyboard to press it with.
const KEYS_HINTED = typeof window === 'undefined' || !window.matchMedia || window.matchMedia('(hover: hover) and (pointer: fine)').matches;

export interface MenuAnswerBlockProps {
  item: { id: string; menu?: { options?: MenuOption[]; question?: string; read_only?: boolean; notice?: string } | null };
  state: MenuAnswerState;
  onSelect: (n: string | null) => void;
  onText: (text: string) => void;
  onRespond: () => void;
  sending?: boolean;
  error?: string | null;
  /** Why words can't be sent right now (shown under the field, which stays usable with a picked option). */
  wordsNote?: string | null;
  /** The option the agent leans to, marked on its row. */
  lean?: string | null;
}

export default function MenuAnswerBlock({ item, state, onSelect, onText, onRespond, sending, error, wordsNote, lean }: MenuAnswerBlockProps) {
  const all = item.menu?.options ?? [];
  const rows = visibleOptions(all);
  const root = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const wordsOk = canSendWords(all) && !wordsNote;
  const picked = state.selectedN ? rows.find((o) => o.n === state.selectedN) : undefined;
  const ready = !sending && canSubmit(state) && (!!picked || wordsOk) && !(picked && isWriteIn(picked) && !state.text.trim());

  const send = () => { if (ready) onRespond(); };
  const pick = (n: string) => {
    const next = state.selectedN === n ? null : n;
    onSelect(next);
    const opt = rows.find((o) => o.n === n);
    if (next && opt && isWriteIn(opt)) setTimeout(() => field.current?.focus(), 0);
  };

  // The card's keys, on the card itself (DecisionCard takes focus when clicked), so they never reach the
  // chat box or another card on the page.
  const latest = useRef({ pick, send, rows });
  useLayoutEffect(() => { latest.current = { pick, send, rows }; });
  useEffect(() => {
    const card = root.current?.closest('[data-decision-card]') as HTMLElement | null;
    if (!card) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement;
      const inField = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable;
      if (!inField && t.tagName === 'BUTTON' && e.key === 'Enter') return; // a focused button's own Enter
      const a = keyAction(e.key, { inField, options: latest.current.rows });
      if (!a) return;
      e.preventDefault();
      if (a.kind === 'pick') latest.current.pick(a.n);
      else if (a.kind === 'send') latest.current.send();
      else field.current?.focus();
    };
    card.addEventListener('keydown', onKey);
    return () => card.removeEventListener('keydown', onKey);
  }, []);

  if (item.menu?.read_only) {
    return (
      <div ref={root} className="mx-4 mb-3 text-xs text-warning bg-warning/10 border border-warning/30 rounded-xl px-3 py-2">
        {item.menu?.notice || 'Multi-part menu — open the agent to answer.'}
      </div>
    );
  }

  return (
    <div ref={root}>
      <div role="listbox" aria-label="Options">
        {rows.map((opt) => {
          const on = state.selectedN === opt.n;
          const full = on;
          const cut = !full && isLongOption(opt);
          return (
            <div key={opt.n} role="option" aria-selected={on} data-option={opt.n}
                 onClick={() => { if (!sending) pick(opt.n); }}
                 className={clsx('touch-compact flex items-start gap-3 px-4 py-3 border-t border-border cursor-pointer select-none transition-colors',
                   on ? 'bg-info/10' : 'hover:bg-muted/60', sending && 'cursor-default')}>
              <span className={clsx('mt-px w-5 h-5 shrink-0 rounded-md border text-[11px] font-semibold flex items-center justify-center tabular-nums',
                on ? 'border-info text-info' : 'border-border text-muted-foreground')}>{opt.n}</span>
              <span className="min-w-0 flex-1">
                <span className={clsx('text-[14.5px] font-medium leading-snug', cut ? 'line-clamp-2' : 'block')}>
                  {opt.label}
                  {lean === opt.n && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">· agent leans</span>}
                </span>
                {opt.detail && <span className={clsx('text-xs text-muted-foreground mt-0.5', full ? 'block' : 'line-clamp-1')}>{opt.detail}</span>}
              </span>
              {isWriteIn(opt) && !on && <TextCursorInput size={15} className="mt-0.5 shrink-0 text-muted-foreground" aria-label="write-in" />}
              {on && (
                <button type="button" disabled={!ready} onClick={(e) => { e.stopPropagation(); send(); }}
                        className="hit-44 shrink-0 self-start -my-0.5 flex items-center gap-1 whitespace-nowrap rounded-full bg-info text-info-foreground px-2.5 py-1 text-xs font-semibold disabled:opacity-50">
                  {sending ? <Loader2 size={12} className="animate-spin" /> : null}Send {opt.n} ✓
                </button>
              )}
            </div>
          );
        })}
      </div>

      <div className="border-t border-border px-3 py-2.5">
        <div className="flex items-center gap-2">
          <input ref={field} type="text" value={state.text} disabled={sending}
                 onChange={(e) => onText(e.target.value)}
                 placeholder={KEYS_HINTED ? 'Or answer in your own words (e)' : 'Or answer in your own words'}
                 className="min-w-0 flex-1 rounded-xl border border-border bg-transparent px-2.5 py-2 text-sm placeholder:text-[13px] placeholder:text-muted-foreground focus:outline-none focus:border-info disabled:opacity-50" />
          <button type="button" onClick={send} disabled={!ready}
                  className="shrink-0 flex items-center gap-1.5 rounded-xl bg-info text-info-foreground px-3.5 py-2 text-sm font-semibold disabled:opacity-40">
            {sending && <Loader2 size={14} className="animate-spin" />}Send
          </button>
        </div>
        {state.text.trim() && !picked && !wordsOk && (
          <div className="text-[11px] text-muted-foreground mt-1.5">{wordsNote || 'Pick an option to send your words with it.'}</div>
        )}
        {error && <div className="text-[11px] text-warning mt-1.5">{error}</div>}
      </div>
    </div>
  );
}
