/**
 * OptionsMenuCard — an agent's live menu (detector `pending_menu`) at the bottom of TranscriptChatView, drawn
 * as the same card as every decision (DecisionCard + MenuAnswerBlock).
 *
 * Answering:
 *  - An option alone goes straight to the pane through the two-phase /agent-key route: picking the row is the
 *    first step, its "Send N ✓" the second.
 *  - Words (alone, or with an option) can only reach a live menu through the card the server keeps for it,
 *    which appears a few seconds after the menu does (lib/decisionCard linkedMenuCard); until then the field
 *    says so. A permission prompt takes an option only.
 *  - The ✕ refuses the menu (the gateway presses Escape on the checked menu), in two clicks.
 *  - 409 (menu vanished or replaced) and 403 come back as a notice on the card.
 *
 * The menu object arrives as a prop from the agents poll (≤10s stale), but the
 * gateway re-checks the LIVE detector on confirm, so a stale render can only
 * fail closed (409), never mis-send.
 */
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { post, sendAgentKey, dismissAgentMenu } from '../../lib/api';
import DecisionCard from './DecisionCard';
import MenuAnswerBlock from './MenuAnswerCard';
import { buildMenuAnswerPayload, initialMenuAnswerState, selectOption, setText, type MenuAnswerState } from '../../lib/menuAnswer';
import { linkedMenuCard, withInputKinds, type MenuCardRow } from '../../lib/decisionCard';

export interface PendingMenuOption { n: string; label: string; detail?: string }
export interface PendingMenu {
  kind: 'options' | 'permission' | 'yes_no';
  question: string;
  /** What the prompt is about (a permission prompt's command), from the detector. */
  context?: string | null;
  options: PendingMenuOption[];
  selected_n?: string | null;
  chrome?: string;
  captured_at?: number;
}

const GONE = 'Menu is gone — answered elsewhere.';
const REPLACED = 'This prompt was replaced by a newer one. Check the screen before answering.';

export default function OptionsMenuCard({ agentId, menu }: { agentId: string; menu: PendingMenu }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<MenuAnswerState>(initialMenuAnswerState);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  // A new menu (different question / capture) starts clean.
  const menuKey = useMemo(() => `${menu.question}|${menu.options.map((o) => o.n + o.label).join('|')}`, [menu]);
  useEffect(() => {
    setState(initialMenuAnswerState); setSending(false); setNotice(null); setDone(null); setDismissed(false);
  }, [menuKey]);

  const isPermission = menu.kind === 'permission';
  // The same list (and query key) the Approvals page polls, so the two share one request.
  const cardsQ = useQuery({
    queryKey: ['unified-approvals'],
    queryFn: async () => {
      const r = await fetch('/api/approvals/unified');
      if (!r.ok) throw new Error(`approvals: ${r.status}`);
      return r.json() as Promise<{ items: MenuCardRow[] }>;
    },
    refetchInterval: 5000,
    enabled: !isPermission && !done && !dismissed,
  });
  const card = isPermission ? undefined : linkedMenuCard(cardsQ.data?.items ?? [], agentId, menu.question);
  const wordsNote = isPermission ? 'A permission prompt takes an option only.'
    : card ? null : 'Your words can be sent in a moment, once this menu reaches the server.';
  const expect = { question: menu.question, context: menu.context ?? '' };
  const picked = state.selectedN ? menu.options.find((o) => o.n === state.selectedN) : undefined;

  const respond = async () => {
    if (sending) return;
    const payload = buildMenuAnswerPayload(state);
    if (!payload) return;
    setSending(true); setNotice(null);
    try {
      if (payload.answer_text) {
        if (!card) { setNotice(wordsNote); return; }
        await post(`/approvals/unified/${encodeURIComponent(card.id)}/answer`, payload);
        void queryClient.invalidateQueries({ queryKey: ['unified-approvals'] });
        setDone(payload.option_n || 'words');
        return;
      }
      const r = await sendAgentKey(agentId, payload.option_n!, true, expect);
      if (r.status === 200 && r.ok) setDone(payload.option_n!);
      else if (r.status === 409) setNotice(r.reason === 'instance_mismatch' ? REPLACED : GONE);
      else if (r.status === 403) setNotice(r.error || 'That key is not enabled yet.');
      else if (r.status === 428) setNotice(r.confirm_text || 'Confirmation required — send again.');
      else setNotice(r.error || 'Could not send — try again.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Network error — try again.');
    } finally {
      setSending(false);
    }
  };

  const dismiss = async (): Promise<string | null> => {
    try {
      const r = await dismissAgentMenu(agentId, expect);
      if (r.status === 200 && r.ok) { setDismissed(true); return null; }
      if (r.status === 409) return r.reason === 'instance_mismatch' ? REPLACED : GONE;
      return r.error || 'Could not dismiss — try again.';
    } catch {
      return 'Network error — try again.';
    }
  };

  const lean = menu.selected_n && menu.options.some((o) => o.n === menu.selected_n) ? menu.selected_n : null;
  const outcome = done === 'words' ? 'Sent your words'
    : done ? `Sent ${done}${picked ? ` · ${picked.label}` : ''}${state.text.trim() ? ' with your words' : ''}` : null;

  return (
    <div className="mt-1">
      <DecisionCard kind={isPermission ? 'approval' : 'decision'} question={menu.question || 'The agent is asking'}
        body={menu.context || null} tone={done ? 'answered' : 'pending'} open={!done} outcome={outcome}
        onDismiss={dismiss} dismissed={dismissed}>
        <MenuAnswerBlock item={{ id: `live-${agentId}`, menu: { options: withInputKinds(menu.options) } }} state={state}
          sending={sending} error={notice} wordsNote={wordsNote} lean={lean}
          onSelect={(n) => setState((s) => selectOption(s, n))}
          onText={(t) => setState((s) => setText(s, t))}
          onRespond={() => { void respond(); }} />
      </DecisionCard>
    </div>
  );
}
