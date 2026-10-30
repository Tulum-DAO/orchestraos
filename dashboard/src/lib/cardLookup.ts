/**
 * cardLookup.ts — a card read by its id, for a reference chip and its popover (components/kit/CardChip.tsx).
 *
 *   apr_…  GET /api/approvals/unified/:id/card (any state: the decisions feed's shape, plus a multi-part answer's
 *          record); a waiting one is drawn from its row in GET /api/approvals/unified, as the Approvals page draws it,
 *          so it is answered the same way. When the card route is not there (an older API) or does not know the id,
 *          the waiting list alone still finds a waiting card.
 *   qnr_…  GET /api/questionnaires/:id (the questionnaire's detail; QuestionnaireForm draws and answers it).
 *
 * Null when the card cannot be read: the chip then shows only its kind, and the popover offers "Go to card".
 */
import type { Decision } from './decisions';
import type { BatchRecord } from './multiPart';
import type { UnifiedApproval } from '../components/chat/PendingApprovalCard';
import { summaryFromCard, summaryFromQuestionnaire, summaryFromUnified, type CardSummary, type QuestionnaireDetail } from './cardRefs';

export type LookedUpCard = Decision & { terminal?: boolean; batch?: BatchRecord | null };

export interface CardLoad {
  summary: CardSummary;
  /** The card row in any state (apr_ ids, when the API has the card route). */
  card?: LookedUpCard;
  /** A questionnaire: QuestionnaireForm reads and answers it by id. */
  questionnaire?: boolean;
}

/** The query key every chip and popover of one card share, so one answer refreshes them all. */
export const cardKey = (id: string) => ['card-ref', id] as const;
/** The same list (and key) the Approvals page polls. */
export const UNIFIED_KEY = ['unified-approvals'] as const;

export async function fetchUnified(): Promise<{ items: UnifiedApproval[]; total: number }> {
  const res = await fetch('/api/approvals/unified');
  if (!res.ok) throw new Error(`/approvals/unified: ${res.status}`);
  return res.json();
}

export async function loadCard(id: string, unified: () => Promise<{ items: UnifiedApproval[] }> = fetchUnified): Promise<CardLoad | null> {
  if (id.startsWith('qnr_')) {
    try {
      const res = await fetch(`/api/questionnaires/${encodeURIComponent(id)}`);
      if (!res.ok) return null;
      const q = await res.json() as QuestionnaireDetail;
      return q && q.id ? { summary: summaryFromQuestionnaire(q), questionnaire: true } : null;
    } catch { return null; }
  }
  try {
    const res = await fetch(`/api/approvals/unified/${encodeURIComponent(id)}/card`);
    if (res.ok) {
      const json = await res.json() as { card?: LookedUpCard };
      if (json.card?.id) return { summary: summaryFromCard(json.card), card: json.card };
    }
  } catch { /* fall back to the waiting list */ }
  try {
    const row = (await unified()).items.find((i) => i.id === id);
    return row ? { summary: summaryFromUnified(row) } : null;
  } catch { return null; }
}
