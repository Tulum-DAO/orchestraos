/**
 * needsYou.ts — Overview's "Needs You" panel: who is waiting on the operator, read from the same feeds and counted
 * by the same rules as the notification bell (GET /api/approvals/unified grouped by waitingByAgent; questionnaires
 * by pendingQuestionnaires), so the two never disagree. A failed read says so; it never shows as calm.
 */
import { waitingByAgent } from './decisions.ts';
import { pendingQuestionnaires } from './openWaiting.ts';

export type NeedsYouRow =
  | { kind: 'agent'; key: string; agent: string; count: number; label: string }
  | { kind: 'questionnaires'; key: string; count: number; label: string };

export type NeedsYou =
  | { state: 'loading' }
  | { state: 'error' }
  | { state: 'empty' }
  | { state: 'rows'; rows: NeedsYouRow[] };

type Feed = { items?: { agent_id?: string | null; status?: string }[] } | undefined;
type Questionnaires = { pending?: number; questionnaires?: { status?: string }[] } | undefined;

export function needsYouView(feed: Feed, failed: boolean, questionnaires?: Questionnaires): NeedsYou {
  if (failed) return { state: 'error' };
  if (!feed) return { state: 'loading' };
  const rows: NeedsYouRow[] = waitingByAgent(feed.items ?? []).map(({ agent, count }) => ({
    kind: 'agent' as const, key: `agent:${agent}`, agent, count, label: `${count} card${count === 1 ? '' : 's'} waiting for you`,
  }));
  const q = pendingQuestionnaires(questionnaires);
  if (q > 0) rows.push({ kind: 'questionnaires', key: 'questionnaires', count: q, label: `${q} questionnaire${q === 1 ? '' : 's'} waiting for your input` });
  return rows.length ? { state: 'rows', rows } : { state: 'empty' };
}
