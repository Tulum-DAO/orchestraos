/**
 * useAgentTree: a seat's live subagent tree. Thin wrapper over lib/agentTreeStream (tested there):
 * SSE /api/agents/:id/tree/stream, falling back to GET /tree polling; a 404 (no Claude session on
 * this box) yields a null tree and no further requests. `now` ticks every second so ages keep
 * counting while a frozen parent waits on its helper.
 */
import { useEffect, useReducer, useRef, useState } from 'react';
import { initialTreeStream, pollLane, reduceTreeStream, type TreeStreamState } from '../lib/agentTreeStream';
import { treeUrl } from '../lib/transcriptUrl';
import { workingCount, type AgentTreeNode } from '../lib/agentTree';

export interface AgentTreeHook {
  tree: AgentTreeNode | null;
  /** The API honours ?all=1 (it echoes all_finished), so a "show finished" toggle may show. */
  supportsAll: boolean;
  mode: TreeStreamState['mode'];
  now: number;
}

export function useAgentTree(agentId: string, { all = false, enabled = true }: { all?: boolean; enabled?: boolean } = {}): AgentTreeHook {
  const [state, dispatch] = useReducer(reduceTreeStream, undefined, initialTreeStream);
  const esRef = useRef<EventSource | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // The request: a new seat or ?all starts over. The poll lane below acts only on state for THIS request, so a
  // stale 'polling'/'gone' from the previous seat can never close the stream just opened for this one.
  const key = `${agentId}|${all ? 1 : 0}|${enabled ? 1 : 0}`;

  // The stream lane.
  useEffect(() => {
    dispatch({ type: 'reset', key });
    if (!enabled) return;
    if (typeof EventSource === 'undefined') { dispatch({ type: 'end' }); return; }
    const es = new EventSource(treeUrl(agentId, { stream: true, all }));
    esRef.current = es;
    es.addEventListener('snapshot', (e) => {
      try { dispatch({ type: 'snapshot', tree: JSON.parse((e as MessageEvent).data) as AgentTreeNode | null }); } catch { /* bad frame: the next one heals */ }
    });
    es.addEventListener('end', () => { es.close(); dispatch({ type: 'end' }); });
    es.onopen = () => dispatch({ type: 'open' });
    es.onerror = () => dispatch({ type: 'error' });
    return () => { es.close(); if (esRef.current === es) esRef.current = null; };
  }, [agentId, all, enabled, key]);

  // The poll lane: once polling (or gone), the stream is closed and one GET runs per seq.
  const { mode, seq, pollMs } = state;
  const lane = pollLane(state, key, enabled);
  useEffect(() => {
    if (lane === 'off') return;
    esRef.current?.close();
    esRef.current = null;
    if (lane === 'stop') return;
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const r = await fetch(treeUrl(agentId, { all }));
        const tree = r.ok ? ((await r.json()) as AgentTreeNode | null) : undefined;
        if (!cancelled) dispatch({ type: 'poll', status: r.status, tree });
      } catch {
        if (!cancelled) dispatch({ type: 'poll', status: 0 });
      }
    }, pollMs);
    return () => { cancelled = true; clearTimeout(t); };
  }, [agentId, all, lane, seq, pollMs]);

  // The 1 s clock, only while something below the seat is running or waiting.
  const live = !!state.tree && workingCount(state.tree) > 0;
  useEffect(() => {
    if (!live) return;
    const iv = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(iv);
  }, [live]);

  return { tree: state.tree, supportsAll: state.supportsAll, mode, now };
}
