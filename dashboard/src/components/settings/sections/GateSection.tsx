/** Settings › (Server) Safety gate: the Claude Code safety gate's mode (Off / Observe only / On; admin, the install's
 *  own setting) and which agents it covers (agent → gate badge). On holds nothing until someone picks a stricter role
 *  or level; the kill switch is a file on the server. Writes: PUT /api/supervision/gate/mode (lib/supervision.ts). */
import { gateBadge, putGateMode, GATE_MODE_LABEL, type GateMode } from '../../../lib/supervision';
import { useInstallAdmin } from '../../../lib/myRole';
import { Card, Choice, PageIntro, SettingRow } from '../SettingsKit';
import { useSavedFlash } from '../settingsState';
import { useSupervision } from '../useSupervision';

const MODES: GateMode[] = ['off', 'observe', 'enforce'];
const MODE_DETAIL: Record<GateMode, string> = {
  off: 'Nothing is checked. Agents run every call.',
  observe: 'Calls are checked and logged, never held.',
  enforce: 'Calls your levels say should wait are held for you.',
};

export function GateSection() {
  const admin = useInstallAdmin();
  const { sup, error, write } = useSupervision({ grants: false });
  const [status, flash] = useSavedFlash();
  const id = 'supervision.gate.mode';
  return (
    <>
      <PageIntro>The safety gate checks each Claude Code agent's actions against its level before they run. Only an admin can change it.</PageIntro>
      {!sup && (error ? <p className="panel-error" role="alert">Couldn't load the safety gate: {error}</p> : <p className="panel-empty">…</p>)}
      {sup && <>
        <Card title="Safety gate">
          <SettingRow id={id} label="Gate" keywords={['gate', 'safety', 'hook', 'hold', 'off', 'observe', 'enforce']} locked={!admin}
            description={MODE_DETAIL[sup.gate_mode]} status={status}
            below={<p className="panel-note" data-gate-note>On holds nothing until you choose a stricter role or level. Turn it off instantly from the server with <code>orchestra gate mode off</code>, or create <code>state/attention/GATE_OFF</code> in the install's data directory.</p>}
            control={<Choice<GateMode> labelledBy={`setting-${id}`} value={sup.gate_mode} disabled={!admin}
                       options={MODES.map((m) => ({ value: m, label: GATE_MODE_LABEL[m] }))}
                       onChange={(m) => void write(putGateMode(m)).then((e) => flash(e ? { kind: 'error', text: e } : { kind: 'saved' }))} />} />
        </Card>
        <Card title="Coverage" description="Which agents the gate can hold.">
          {sup.agents.length ? sup.agents.map((a) => (
            <SettingRow key={a.agent} id={`supervision.gate.agent.${a.agent}`} label={a.agent} keywords={['gate', 'coverage', a.agent, a.runtime]}
              description={a.runtime || null} control={<span className="panel-rowvalue" data-gate-badge={a.agent}>{gateBadge(a.gate)}</span>} />
          )) : <div className="settings-row"><p className="settings-row-desc">No agents yet.</p></div>}
        </Card>
      </>}
    </>
  );
}
