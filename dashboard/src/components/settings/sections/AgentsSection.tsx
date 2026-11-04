/** Settings › Agents, in the OpenBot layout General uses: an intro, then cards of rows (a title, one grey line that says
 *  the scope in words, the control on the right).
 *    Defaults              the install defaults agents start with (Everyone, admin): default model, auto-compact, compact
 *                          at, show compaction messages. Under each compaction row, "N of M use their own": the agents
 *                          that keep their own value, listed on demand, each a link to that agent's panel. Default model
 *                          has no count: an agent keeps the model it was made with (api lib/agentOverrides.ts).
 *    Safety limits         read-only.
 *    Background processes  programs agents started and left running (a dev server): command, agent, start time in the
 *                          install's zone, ports, and Stop (admin, one confirm). The server picks what is listed and
 *                          re-checks a Stop (api lib/backgroundProcesses.ts). Helpers: lib/agentsPage.ts. */
import { useContext, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Lock, RefreshCw } from 'lucide-react';
import ModelPicker from '../../kit/ModelPicker';
import { COMPACT_MAX, COMPACT_MIN } from '../../../lib/seatSettings';
import { Card, ConfirmButton, PageIntro, SettingRow } from '../SettingsKit';
import { ServerNumberRow, ServerSwitchRow } from '../ServerRows';
import { useServerSetting } from '../useServerSetting';
import { useInstallTimeZone, useServerSettings } from '../../../stores/serverSettings';
import { mergeDefaultModel } from '../../../lib/settingsApi';
import { useAgents } from '../../../hooks/useAgents';
import { agentChoices } from '../../../lib/notificationsPage';
import { matchesQuery } from '../../../lib/settingsRoute';
import { ADMIN_LOCK_NOTE } from '../../../lib/myRole';
import {
  OPEN_AGENT_PANEL, agentPanelPath, fetchOverrides, fetchProcesses, overrideCount, overrideValueText, portsText, processLabel, shortCommand,
  startedText, stopConsequence, stopProcess, type BackgroundProcess, type Overrides,
} from '../../../lib/agentsPage';
import { SettingsQuery } from '../settingsState';
import './agentsPage.css';

const OVERRIDES_KEY = ['settings', 'agents', 'overrides'];
const PROCESSES_KEY = ['settings', 'agents', 'processes'];
const INSTALL_WIDE = 'For the whole install.';

/** Agent id -> the name the sidebar shows. */
function useAgentNames(): (id: string) => string {
  const { data } = useAgents();
  const names = new Map(agentChoices(data).map((a) => [a.id, a.name]));
  return (id) => names.get(id) ?? id;
}

/** Leave Settings for an agent's page with its panel open, where that agent's own settings live. */
function useOpenAgentPanel(): (id: string) => void {
  const navigate = useNavigate();
  return (id) => navigate(agentPanelPath(id), { state: { [OPEN_AGENT_PANEL]: true } });
}

function AgentLink({ id, name }: { id: string; name: string }) {
  const open = useOpenAgentPanel();
  return <button type="button" className="arturo-panel-link settings-agent-link" data-agent-link={id} onClick={() => open(id)}>{name}</button>;
}

/** "N of M use their own", right-aligned under the row; the chevron opens the list of those agents and their values. */
function OverrideCount({ k, data }: { k: string; data: Overrides | undefined }) {
  const [open, setOpen] = useState(false);
  const nameOf = useAgentNames();
  const list = data?.rows[k] ?? [];
  const c = overrideCount(data?.rows[k], data?.total ?? 0);
  if (!c) return null;
  const listId = `settings-overrides-${k}`;
  const words = <><strong>{c.of}</strong>{c.rest}</>;
  return (
    <div className="settings-overrides" data-overrides={k}>
      {c.n === 0 ? <span className="settings-overrides-count">{words}</span> : (
        <button type="button" className="settings-overrides-count settings-overrides-toggle" aria-expanded={open} aria-controls={listId}
                onClick={() => setOpen((o) => !o)}>
          {words}<ChevronDown size={14} aria-hidden className={open ? 'is-open' : undefined} />
        </button>
      )}
      {open && c.n > 0 && (
        <ul id={listId} className="settings-overrides-list">
          {list.map((o) => (
            <li key={o.id} data-override-agent={o.id}>
              <AgentLink id={o.id} name={nameOf(o.id)} />
              <span className="settings-overrides-value">{overrideValueText(k, o.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DefaultModelRow() {
  const { row, status, save, locked, loadError } = useServerSetting('agents.defaultModel');
  const models = (row?.value && typeof row.value === 'object' ? row.value : {}) as Record<string, string>;
  const [runtime, setRuntime] = useState('claude');
  return <SettingRow id="agents.defaultModel" label="Default model" keywords={['model', 'runtime', 'claude', 'codex', 'gemini', 'grok', 'new agents']}
    description={`The model a new agent starts on, for each runtime. An agent keeps the model it was made with. ${INSTALL_WIDE}`}
    stacked status={status} locked={locked} note={row?.note} detail={row?.detail ?? (loadError ? `Couldn't load settings: ${loadError}` : null)}
    changedBy={row?.changedBy} changedAt={row?.changedAt}
    control={row?.editable
      ? <ModelPicker variant="inline" value={{ providerId: runtime, modelId: models[runtime] ?? '' }}
          onValueChange={(p) => { if (!(p.providerId in models)) return; setRuntime(p.providerId); void save({ runtime: p.providerId, model: p.modelId }, mergeDefaultModel(models, p.providerId, p.modelId)); }} />
      : <span className="arturo-panel-rowvalue">{Object.entries(models).map(([r, m]) => `${r}: ${m}`).join(' · ') || '…'}</span>} />;
}

function SafetyLimitsRow() {
  const row = useServerSettings((s) => s.rows['agents.safetyLimits']);
  const loadError = useServerSettings((s) => s.error);
  const v = row?.value as { forced_compaction_pct?: number; context_ceiling_pct?: number } | null | undefined;
  return <SettingRow id="agents.safetyLimits" label="Safety limits" keywords={['safety', 'limit', 'guard', 'ceiling', 'context']}
    description="Fixed: these keep agents from running out of room. The same for every agent."
    detail={row?.detail ?? (!row && loadError ? `Couldn't load settings: ${loadError}` : null)}
    control={<span className="arturo-panel-rowvalue">{v && typeof v === 'object' ? `Forced compaction at ${v.forced_compaction_pct}% · context ceiling ${v.context_ceiling_pct}%` : '…'}</span>} />;
}

function ProcessRow({ p, canStop, onStopped }: { p: BackgroundProcess; canStop: boolean; onStopped: (text: string) => void }) {
  const zone = useInstallTimeZone();
  const nameOf = useAgentNames();
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = processLabel(p);
  const short = shortCommand(label);
  const cut = short !== label.replace(/\s+/g, ' ').trim();
  const stop = async () => {
    setBusy(true); setError(null);
    const r = await stopProcess(p);
    setBusy(false);
    if (r.ok) onStopped(r.warning ? `${r.warning}.` : `Stopped ${shortCommand(p.detail ?? p.program, 40)}${r.result === 'killed' ? " (it didn't stop by itself, so it was forced)" : ''}.`);
    else setError(r.error);
  };
  const meta = [startedText(p.startedAt, zone), portsText(p.ports)].filter(Boolean).join(' · ');
  return (
    <div className="settings-row" data-process={p.pid}>
      <div className="arturo-panel-field">
        <span className="settings-row-text">
          <span className="settings-row-titleline">
            <code className={`settings-proc-cmd${full ? ' is-full' : ''}`} title={label}>{full ? label : short}</code>
          </span>
          <span className="settings-row-desc">
            {p.agent ? <AgentLink id={p.agent} name={nameOf(p.agent)} /> : 'No agent'}{meta ? ` · ${meta}` : ''}
            {cut && <> · <button type="button" className="arturo-panel-link" aria-expanded={full} onClick={() => setFull((f) => !f)}>{full ? 'Show less' : 'Show all'}</button></>}
          </span>
        </span>
        {canStop && (
          <span className="settings-control">
            {busy ? <span className="arturo-panel-note">Stopping…</span>
              : <ConfirmButton label="Stop" consequence={stopConsequence(p)} onConfirm={() => void stop()} data={`stop-${p.pid}`} className="arturo-panel-link settings-proc-stop" />}
          </span>
        )}
      </div>
      {error && <p className="arturo-panel-error" role="alert" data-process-error>{error}</p>}
    </div>
  );
}

function BackgroundProcesses() {
  const q = useContext(SettingsQuery);
  const client = useQueryClient();
  const { data, isFetching, refetch } = useQuery({ queryKey: PROCESSES_KEY, queryFn: () => fetchProcesses(), refetchInterval: 15_000, staleTime: 5_000 });
  const [done, setDone] = useState<string | null>(null);
  if (!matchesQuery(q, 'Background processes', ['process', 'processes', 'running', 'dev server', 'server', 'port', 'stop', 'kill', 'background'])) return null;
  const list = data?.ok ? data.value : null;
  const refresh = () => { setDone(null); void refetch(); };
  const onStopped = (text: string) => { setDone(text); void client.invalidateQueries({ queryKey: PROCESSES_KEY }); };
  let body;
  if (!data) body = <p className="settings-row-desc">…</p>;
  else if (!data.ok) body = <p className="arturo-panel-error" role="alert">{`Couldn't read the running programs: ${data.error}`}</p>;
  else if (!data.value.supported) body = <p className="settings-row-desc">This server can't list its programs.</p>;
  else if (!data.value.processes.length) body = <p className="settings-row-desc" data-processes-empty>Nothing is running.</p>;
  return <>
    {list?.processes.map((p) => <ProcessRow key={`${p.pid}-${p.startTicks}`} p={p} canStop={list.canStop} onStopped={onStopped} />)}
    <div className="settings-row settings-proc-foot" data-setting="agents.backgroundProcesses">
      {body}
      {done && <p className="arturo-panel-note" role="status">{done}</p>}
      {list && !list.canStop && list.processes.length > 0 && <p className="arturo-panel-note settings-lock"><Lock size={12} aria-hidden /> {ADMIN_LOCK_NOTE}</p>}
      <button type="button" className="arturo-panel-link settings-proc-refresh" onClick={refresh} disabled={isFetching} data-processes-refresh>
        <RefreshCw size={14} aria-hidden /> Refresh
      </button>
    </div>
  </>;
}

export function AgentsSection() {
  const { data } = useQuery({ queryKey: OVERRIDES_KEY, queryFn: () => fetchOverrides(), staleTime: 10_000 });
  const overrides = data?.ok ? data.value : undefined;
  const count = (k: string) => <OverrideCount k={k} data={overrides} />;
  return (
    <>
      <PageIntro>Defaults every agent starts with, and what they leave running.</PageIntro>
      <Card title="Defaults" description="Agents follow these defaults unless you change them for an agent.">
        <DefaultModelRow />
        <ServerSwitchRow k="agents.autoCompact" label="Auto-compact" keywords={['compact', 'context', 'rotation']}
          description={`Compacts an agent's conversation on its own before it runs out of room. ${INSTALL_WIDE}`} below={count('agents.autoCompact')} />
        <ServerNumberRow k="agents.compactAt" label="Compact at" suffix="%" min={COMPACT_MIN} max={COMPACT_MAX} stepper keywords={['compact', 'percent', 'context']}
          description={`How full an agent's context gets before auto-compact runs. ${INSTALL_WIDE}`} below={count('agents.compactAt')} />
        <ServerSwitchRow k="agents.showCompaction" label="Show compaction messages" keywords={['compact', 'messages', 'notice']}
          description={`Whether a chat shows a note when its conversation was compacted. ${INSTALL_WIDE}`} below={count('agents.showCompaction')} />
      </Card>
      <Card title="Safety limits">
        <SafetyLimitsRow />
      </Card>
      <Card title="Background processes" description="Programs agents started and left running, such as dev servers. They keep running after the chat ends.">
        <BackgroundProcesses />
      </Card>
    </>
  );
}
