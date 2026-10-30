/**
 * Under the chat styles in an agent's panel, for a headless seat only: what Dev costs (lib/headlessDev.ts). A tmux
 * seat's Dev is its terminal as always, so it gets no note.
 */
import { useEffect } from 'react';
import { DEV_NOTE } from '../../lib/headlessDev';
import { loadDevInfo, useDevInfoStore } from '../../hooks/useHeadlessDev';

export function HeadlessDevNote({ agentId }: { agentId: string }) {
  const headless = useDevInfoStore((s) => s.info[agentId]?.headless);
  useEffect(() => { if (headless === undefined) void loadDevInfo(agentId); }, [agentId, headless]);
  if (!headless) return null;
  return <p className="arturo-panel-note" data-headless-dev-note>{DEV_NOTE}</p>;
}
