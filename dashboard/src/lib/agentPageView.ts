/**
 * The agent page's view for ?sub=<agent_id>: the seat's own chat, or one subagent's transcript
 * (read-only: no composer, no menu answering). The seat node's agent_id is the Claude session UUID,
 * so selecting the root means "the seat" and never calls /subagents/<uuid>.
 */
import { findNode, type AgentTreeNode } from './agentTree';
import { SAFE_SUBAGENT_ID } from './transcriptUrl';

export interface AgentPageView {
  readOnly: boolean;
  title: string;
  /** The seat to go back to, when viewing a subagent. */
  backTo: string | null;
  subagentId?: string;
  parentLabel?: string;
  banner?: string;
}

function parentOf(tree: AgentTreeNode, id: string): AgentTreeNode | null {
  for (const c of tree.children) {
    if (c.agent_id === id) return tree;
    const p = parentOf(c, id);
    if (p) return p;
  }
  return null;
}

export function viewFor(seat: string, sub: string | null, tree: AgentTreeNode | null): AgentPageView {
  const seatView: AgentPageView = { readOnly: false, title: seat, backTo: null };
  if (!sub || (tree && sub === tree.agent_id) || !SAFE_SUBAGENT_ID.test(sub)) return seatView;
  const node = findNode(tree, sub);
  if (!node) {
    return { readOnly: true, title: sub, backTo: seat, subagentId: sub, banner: `Viewing ${sub} (a helper), read-only.` };
  }
  const parent = parentOf(tree!, sub);
  const parentLabel = !parent || parent.depth === 0 ? seat : parent.label;
  return {
    readOnly: true, title: node.label, backTo: seat, subagentId: sub, parentLabel,
    banner: `Viewing ${node.label} (helper of ${parentLabel}), read-only.`,
  };
}

/** The ?sub value after clicking a row: the root clears it. */
export function subParamFor(tree: AgentTreeNode, clickedId: string): string | null {
  return clickedId === tree.agent_id ? null : clickedId;
}

/** Whether ?sub should be dropped now: the tree once held that node (`seen`) and a later snapshot pruned it.
 *  A deep link to a node the tree has not shown yet (still loading, or older than the window) is kept.
 *  Returns the updated `seen` for the caller to keep across snapshots. */
export function subToDrop({ sub, tree, seen }: { sub: string | null; tree: AgentTreeNode | null; seen: string | null }):
  { drop: boolean; seen: string | null } {
  if (!sub || !tree) return { drop: false, seen };
  if (findNode(tree, sub)) return { drop: false, seen: sub };
  return { drop: seen === sub, seen };
}
