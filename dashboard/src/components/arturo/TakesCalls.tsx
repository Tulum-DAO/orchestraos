/** The agent panel's "Takes calls" (Setup): whether a call to this agent is with the agent itself. It affects everyone
 *  who calls it, so only an admin may change it; others see it locked. Saves on change, ✓ Saved, reverts with the reason. */
import { useEffect, useState } from 'react';
import { Check } from 'lucide-react';
import { fetchTakesCalls, refreshAgentCallsOn, saveTakesCalls } from '../../lib/agentCallsOn';
import { ADMIN_LOCK_NOTE, useInstallAdmin } from '../../lib/myRole';
import { useSavedFlash } from '../settings/settingsState';
import { Switch } from './PanelRows';

export function TakesCalls({ agentId }: { agentId: string }) {
  const [on, setOn] = useState<boolean | null>(null);
  const [status, flash] = useSavedFlash();
  const admin = useInstallAdmin();
  useEffect(() => {
    let alive = true;
    void fetchTakesCalls(agentId).then((v) => { if (alive) setOn(v); });
    return () => { alive = false; };
  }, [agentId]);
  const toggle = async () => {
    if (on === null) return;
    const before = on;
    setOn(!before);
    const r = await saveTakesCalls(agentId, !before);
    if (r.ok) { flash({ kind: 'saved' }); void refreshAgentCallsOn(agentId); }
    else { setOn(before); flash({ kind: 'error', text: r.error }); }
  };
  return (
    <div data-takes-calls>
      <div className="arturo-panel-field">
        <span id={`takes-calls-${agentId}`}>Takes calls</span>
        <span className="settings-control">
          {status.kind === 'saved' && <span className="settings-saved" role="status"><Check size={14} aria-hidden /> Saved</span>}
          <Switch on={on === true} labelledBy={`takes-calls-${agentId}`} disabled={on === null || !admin} onToggle={() => void toggle()} data="takes-calls" />
        </span>
      </div>
      {status.kind === 'error' && <p className="arturo-panel-error" role="alert">{status.text}</p>}
      {!admin && <p className="arturo-panel-note">{ADMIN_LOCK_NOTE}</p>}
      <p className="arturo-panel-note">When on, a call to this agent is with the agent itself. Applies to new calls.</p>
    </div>
  );
}
