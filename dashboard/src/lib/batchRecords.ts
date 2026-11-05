/**
 * batchRecords.ts — an agent's durable multi-part answer records (GET /api/agents/:id/menu-batches), handed to the
 * transcript's answered AskUserQuestion cards through a context, so a card draws what was actually recorded
 * (skips included) instead of parsing the tool result's text.
 */
import { createContext, useContext } from 'react';
import type { BatchRecord } from './multiPart';

export const BatchRecordsContext = createContext<BatchRecord[] | undefined>(undefined);

export function useBatchRecords(): BatchRecord[] | undefined {
  return useContext(BatchRecordsContext);
}

export async function fetchBatchRecords(agentId: string): Promise<BatchRecord[]> {
  const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/menu-batches`, { signal: AbortSignal.timeout(12_000) });
  if (!res.ok) throw new Error(`menu-batches: ${res.status}`);
  const json = await res.json();
  return Array.isArray(json.records) ? json.records : [];
}
