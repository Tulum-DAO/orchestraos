/**
 * CardChip — a card id in a message, drawn as a chip (lib/cardRefs.ts holds the rules): the kind's icon, the
 * question's first words and the card's live state ("Which size? · Answered: Circle", "Deploy the drawer? ·
 * Waiting"). Never the id.
 *
 * A click opens the card itself in a popover beside the chip, at the width and with the look of the card in a chat,
 * on the same page (even in the conversation the card was answered in):
 *   - waiting: the live card, answered exactly as on the Approvals page (PendingApprovalCard: a multi-part menu part
 *     by part, a question card, Approve and Deny) or the questionnaire's own form; once sent the popover closes, the
 *     chip turns Answered and the page stays where it was;
 *   - answered (or dismissed, expired…): the chat's own answered card, read-only, the pick shown, who, where, when.
 * "Go to card" in the popover goes to where the card lives; it is all the popover offers when the card cannot be
 * read. While open, the popover's card is Arturo's focus (lib/screenCards.ts, surface "popover").
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ClipboardList, KeyRound, Layers, ListChecks, ShieldCheck, SquareStack, X } from 'lucide-react';
import ObjectChip, { type ChipTone } from './ObjectChip';
import type { Skin } from './Markdown';
import { chipLabel, describeCard, placeholderLabel, type CardSummary, type RefCardKind } from '../../lib/cardRefs';
import { cardKey, fetchUnified, loadCard, UNIFIED_KEY, type CardLoad } from '../../lib/cardLookup';
import { approvalsFocusPath, answeredByLine, focusDecision } from '../../lib/decisions';
import { batchAnswers } from '../../lib/drawerHistory';
import { CardSurface } from '../../lib/threadKit/useScreenCard';
import { isViewHidden } from '../../lib/features';
import PendingApprovalCard from '../chat/PendingApprovalCard';
import DecisionBubble from '../chat/DecisionBubble';
import { AnsweredQuestionCard } from '../chat/MultiPartCard';
import QuestionnaireForm from '../QuestionnaireForm';
import './kit.css';

const ICON: Record<RefCardKind, typeof ListChecks> = {
  decision: ListChecks, menu: ListChecks, approval: ShieldCheck, multipart: Layers, questionnaire: ClipboardList,
  permission: KeyRound, choice: ListChecks,
};

function toneOf(s: CardSummary | null | undefined): ChipTone {
  if (!s) return 'plain';
  return s.state === 'waiting' ? 'waiting' : s.state === 'answered' ? 'done' : 'closed';
}

export default function CardChip({ id, skin = 'bubbles', inert }: { id: string; skin?: Skin;
  /** Words only (inside another control): the card's words and state, no popover. */
  inert?: boolean }) {
  const [open, setOpen] = useState(false);
  const chip = useRef<HTMLButtonElement>(null);
  const q = useQuery({
    queryKey: cardKey(id),
    queryFn: () => loadCard(id),
    staleTime: 15_000,
    // A waiting card is watched, so the chip turns Answered when it is answered anywhere.
    refetchInterval: (query) => (query.state.data?.summary.state === 'waiting' ? 15_000 : false),
  });
  const s = q.data?.summary;
  const Icon = s ? ICON[s.kind] : SquareStack;
  const label = s ? chipLabel(s) : placeholderLabel(id);
  return (
    <>
      <ObjectChip ref={chip} skin={skin} tone={toneOf(s)} label={label} expanded={open}
        icon={<Icon size={12} strokeWidth={2.2} />}
        title={s ? `${describeCard(s)} Open the card.` : `${label}: open it`}
        onClick={() => setOpen((v) => !v)} data={{ 'card-ref': id, 'card-state': s?.state ?? 'unknown' }} inert={inert} />
      {open && !inert && <CardPopover id={id} load={q.data ?? null} loading={q.isLoading} anchor={chip} onClose={() => setOpen(false)} />}
    </>
  );
}

const GAP = 8;
const EDGE = 16;
const ROOM = 420;

/** Beside the chip: below it when a card fits there, else above, else the screen's full height; never off the screen
 *  (a phone uses a bottom sheet). */
function usePlace(anchor: React.RefObject<HTMLElement | null>): CSSProperties {
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' });
  useLayoutEffect(() => {
    const place = () => {
      const a = anchor.current?.getBoundingClientRect();
      if (!a) return;
      const vw = window.innerWidth, vh = window.visualViewport?.height ?? window.innerHeight;
      const w = Math.min(480, vw - EDGE * 2);
      const left = Math.max(EDGE, Math.min(a.left, vw - w - EDGE));
      const below = vh - a.bottom - GAP - EDGE;
      const above = a.top - GAP - EDGE;
      // Room for a whole card (about 420px) below, else above; else the full height, over the chip.
      setStyle(below >= ROOM ? { left, top: a.bottom + GAP, maxHeight: below }
        : above >= ROOM ? { left, bottom: vh - a.top + GAP, maxHeight: above }
          : { left, top: EDGE, maxHeight: vh - EDGE * 2 });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [anchor]);
  return style;
}

function CardPopover({ id, load, loading, anchor, onClose }: {
  id: string; load: CardLoad | null; loading: boolean; anchor: React.RefObject<HTMLElement | null>; onClose: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  const style = usePlace(anchor);
  const s = load?.summary;
  const waiting = s?.state === 'waiting';
  // The waiting card is drawn from the same list, with the same key, as the Approvals page.
  const unified = useQuery({ queryKey: UNIFIED_KEY, queryFn: fetchUnified, enabled: waiting && !load?.questionnaire });
  const row = waiting ? unified.data?.items.find((i) => i.id === id) : undefined;

  // Closes on Escape and on a press outside it (not on its own chip, whose click toggles it).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      // A card opened from a chip inside this one's card is still this card's work.
      if (!t || box.current?.contains(t) || anchor.current?.contains(t) || (t as Element).closest?.('.kit-card-pop')) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown, true);
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('pointerdown', onDown, true); };
  }, [onClose, anchor]);

  // Sent (or dismissed): close, and let every chip and list showing this card read it again. The page stays put.
  const answered = () => {
    onClose();
    void qc.invalidateQueries({ queryKey: cardKey(id) });
    void qc.invalidateQueries({ queryKey: UNIFIED_KEY });
    if (s?.from) void qc.invalidateQueries({ queryKey: ['agent-decisions', s.from] });
    if (load?.questionnaire) void qc.invalidateQueries({ queryKey: ['questionnaire', id] });
  };

  const goTo = () => {
    onClose();
    // On this page already (the chat or the Approvals page draws it): scroll to it there.
    const here = [...document.querySelectorAll<HTMLElement>(`[id="decision-${id}"], [id="approval-${id}"]`)]
      .find((el) => !el.closest('.kit-card-pop'));
    if (here) { here.scrollIntoView({ block: 'center', behavior: 'smooth' }); here.focus({ preventScroll: true }); return; }
    // A page this deployment hides is not offered: the agent's chat, else nowhere.
    if (load?.questionnaire || id.startsWith('qnr_')) { if (!isViewHidden('/questionnaires')) navigate('/questionnaires'); return; }
    if ((waiting || !s?.from) && !isViewHidden('/approvals')) { navigate(approvalsFocusPath(id)); return; }
    if (!s?.from) return;
    focusDecision(s.from, id);
    navigate(`/agent/${encodeURIComponent(s.from)}`);
  };

  let body: React.ReactNode;
  if (loading) body = <p className="kit-card-pop-note">Loading the card…</p>;
  else if (!load) body = <p className="kit-card-pop-note" data-card-unavailable>This card couldn’t be loaded here.</p>;
  else if (load.questionnaire) body = <QuestionnaireForm id={id} onAnswered={answered} />;
  else if (row) body = <PendingApprovalCard item={row} onChanged={answered} id={`card-pop-${id}`} />;
  else if (waiting && unified.isLoading) body = <p className="kit-card-pop-note">Loading the card…</p>;
  else if (load.card?.batch && load.card.batch.parts.length) {
    body = <AnsweredQuestionCard title={load.card.from_agent} parts={load.card.batch.parts} answers={batchAnswers(load.card.batch)}
      note={answeredByLine(load.card)} report={{ id, kind: 'multipart', from: load.card.from_agent }} />;
  } else if (load.card) body = <DecisionBubble decision={load.card} onAnswered={answered} />;
  else body = <p className="kit-card-pop-note" data-card-unavailable>This card couldn’t be loaded here.</p>;

  return createPortal(
    <div ref={box} className="kit-card-pop" data-cap-root style={style} role="dialog" aria-label={s ? `Card: ${s.question}` : 'Card'} data-card-pop={id}>
      <CardSurface.Provider value="popover">{body}</CardSurface.Provider>
      <div className="kit-card-pop-bar">
        <span className="grow" />
        <button type="button" className="kit-card-pop-btn" onClick={goTo} data-card-goto>Go to card</button>
        <button type="button" className="kit-card-pop-btn" onClick={onClose} aria-label="Close"><X size={12} /></button>
      </div>
    </div>,
    document.body,
  );
}
