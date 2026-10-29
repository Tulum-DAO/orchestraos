/**
 * OptionsMenuCard — an agent's live menu (detector `pending_menu`) at the bottom of TranscriptChatView, drawn
 * as the one question card (MultiPartCard's QuestionCard; a single menu is a card of one part).
 *
 * Answering:
 *  - A pick goes straight to the pane through /agent-key, with its confirm in the same call: the pick is the
 *    confirmation, as on every question card.
 *  - Words go alone (a pick and words are exclusive), and only where the menu has a write-in. They can only reach a
 *    live menu through the card the server keeps for it, which appears a few seconds after the menu does
 *    (lib/decisionCard linkedMenuCard); until then the card says so. A permission prompt takes an option only.
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
import { MultiPartAnswer, QuestionCard } from './MultiPartCard';
import { canSendWords, linkedMenuCard, withInputKinds, type MenuCardRow } from '../../lib/decisionCard';
import { onePart, singleBody, type PartAnswer } from '../../lib/multiPart';

export interface PendingMenuOption { n: string; label: string; detail?: string; input_kind?: string }
export interface PendingMenu {
  kind: 'options' | 'permission' | 'yes_no';
  question: string;
  /** What the prompt is about (a permission prompt's command), from the detector. */
  context?: string | null;
  options: PendingMenuOption[];
  selected_n?: string | null;
  chrome?: string;
  captured_at?: number;
  /** Several questions on one screen (the detector sees only the first): answered part by part (MultiPartAnswer). */
  multipart?: boolean;
  part_count?: number;
}

const GONE = 'Menu is gone — answered elsewhere.';
const REPLACED = 'This prompt was replaced by a newer one. Check the screen before answering.';

export default function OptionsMenuCard({ agentId, menu }: { agentId: string; menu: PendingMenu }) {
  const queryClient = useQueryClient();
  const [done, setDone] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  // A new menu (different question / capture) starts clean.
  const menuKey = useMemo(() => `${menu.question}|${menu.options.map((o) => o.n + o.label).join('|')}`, [menu]);
  useEffect(() => { setDone(false); setDismissed(false); }, [menuKey]);

  const isPermission = menu.kind === 'permission';
  // The same list (and query key) the Approvals page polls, so the two share one request.
  const cardsQ = useQuery({
    queryKey: ['unified-approvals', 'live-menu'],
    queryFn: async () => {
      // hydrates-multipart: a multi-part menu's card comes back whole (answerable), not rewritten read-only.
      const r = await fetch('/api/approvals/unified', { headers: { 'X-Client-Capabilities': 'hydrates-multipart' } });
      if (!r.ok) throw new Error(`approvals: ${r.status}`);
      return r.json() as Promise<{ items: MenuCardRow[] }>;
    },
    refetchInterval: 5000,
    enabled: !isPermission && !done && !dismissed,
  });
  const found = isPermission ? undefined : linkedMenuCard(cardsQ.data?.items ?? [], agentId, menu.question);
  // The server's card for this menu, kept once seen: a send marks it answered before its reply comes back, and the
  // next refresh then no longer lists it as pending; the card being answered must not lose it mid-send.
  const [seen, setSeen] = useState<{ key: string; card: MenuCardRow } | null>(null);
  if (found && (seen?.key !== menuKey || seen.card.id !== found.id)) setSeen({ key: menuKey, card: found });
  const card = found ?? (seen?.key === menuKey ? seen.card : undefined);
  const expect = { question: menu.question, context: menu.context ?? '' };
  const options = withInputKinds<PendingMenuOption>(menu.options);
  // A permission prompt takes an option only; words go only where the menu has a write-in.
  const words = !isPermission && canSendWords(options);
  const lean = menu.selected_n && menu.options.some((o) => o.n === menu.selected_n) ? menu.selected_n : null;
  const parts = onePart(menu.question || 'The agent is asking', options, { words, lean });

  const submit = async (answers: PartAnswer[]): Promise<string | null> => {
    const body = singleBody(answers[0]);
    if (!body) return 'Pick an option, or type your answer.';
    try {
      if (body.answer_text) {
        if (!card) return 'Your words can be sent in a moment, once this menu reaches the server.';
        await post(`/approvals/unified/${encodeURIComponent(card.id)}/answer`, body);
        void queryClient.invalidateQueries({ queryKey: ['unified-approvals'] });
        setDone(true);
        return null;
      }
      const r = await sendAgentKey(agentId, body.option_n!, true, expect);
      if (r.status === 200 && r.ok) { setDone(true); return null; }
      if (r.status === 409) return r.reason === 'instance_mismatch' ? REPLACED : GONE;
      if (r.status === 403) return r.error || 'That key is not enabled yet.';
      if (r.status === 428) return r.confirm_text || 'Confirmation required — send again.';
      return r.error || 'Could not send — try again.';
    } catch (e) {
      return e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Network error — try again.';
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

  if (menu.multipart) {
    return (
      <div className="mt-1">
        <MultiPartAnswer agentId={agentId} cardId={card?.id ?? null} onDismiss={dismiss} dismissed={dismissed} />
      </div>
    );
  }

  return (
    <div className="mt-1">
      <QuestionCard key={menuKey} title={isPermission ? 'Permission' : 'The agent is asking'}
        context={menu.context ? <div className="text-[13px] font-mono break-words text-card-foreground/85">{menu.context}</div> : null}
        parts={parts} onSubmit={submit} onDismiss={dismiss} dismissed={dismissed} />
    </div>
  );
}
