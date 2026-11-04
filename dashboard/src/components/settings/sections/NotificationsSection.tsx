/** Settings › Notifications, in the OpenBot layout General uses: an intro, then cards of rows (a title, one grey line that
 *  says the scope in words, the control on the right).
 *    What you hear about  All / Needs me / Nothing (follows you), then "Choose for one agent": an agent picker that adds a
 *                         row with its own All / Needs me / Nothing and a remove button. An agent's own setting wins.
 *    Quiet hours          off, or from–to in the install's time zone (follows you). No exceptions list.
 *    This device          the dashboard shows no system notifications, so only a note saying where alerts come from.
 *  The rows are the person's own (scope user) in state/users/<you>/prefs.json; the server's push path reads the same
 *  file before it pushes a card (scripts/notify_policy.py, approval_notify.py). What each row can and cannot reach is
 *  said by the server (the level row's note and detail), not guessed here. Helpers: lib/notificationsPage.ts. */
import { useContext, useState } from 'react';
import { X } from 'lucide-react';
import { useAgents } from '../../../hooks/useAgents';
import { matchesQuery } from '../../../lib/settingsRoute';
import { useInstallTimeZone } from '../../../stores/serverSettings';
import {
  LEVEL_OPTIONS, DEFAULT_QUIET, THIS_DEVICE_NOTE, addableAgents, agentChoices, changeQuiet, overrideRows, overridesOf, quietOf,
  quietZoneLine, withOverride, withoutOverride, type NotifyLevel,
} from '../../../lib/notificationsPage';
import { Card, Choice, PageIntro, SettingRow } from '../SettingsKit';
import { ServerChoiceRow } from '../ServerRows';
import { Switch } from '../../arturo/PanelRows';
import { useServerSetting } from '../useServerSetting';
import { SettingsQuery, descIdOf } from '../settingsState';

const loading = <span className="arturo-panel-rowvalue">…</span>;
const OVERRIDES = 'notifications.agentOverrides';

/** "Choose for one agent", then one row per agent with its own setting. An agent picked here is listed at once with no
 *  choice made; picking All, Needs me or Nothing saves it (nothing is saved for an agent until a choice is made). */
function AgentOverrides() {
  const { row, status, save, loadError } = useServerSetting(OVERRIDES);
  const { data } = useAgents();
  const [adding, setAdding] = useState<string | null>(null);
  const [changed, setChanged] = useState<string | null>(null);
  const agents = agentChoices(data);
  const overrides = overridesOf(row?.value);
  const rows = overrideRows(overrides, agents);
  const pick = addableAgents(agents, overrides, adding);
  const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
  const set = (id: string, level: NotifyLevel) => { setChanged(id); if (id === adding) setAdding(null); void save(withOverride(overrides, id, level)); };
  const remove = (id: string) => { setChanged(OVERRIDES); void save(withoutOverride(overrides, id)); };
  const shown = adding ? [...rows, { id: adding, name: nameOf(adding), level: null }] : rows;
  return <>
    <SettingRow id={OVERRIDES} label="Choose for one agent" keywords={['agent', 'override', 'one agent', 'mute', 'per agent']}
      description="Give one agent its own setting. It wins over the one above."
      status={changed === OVERRIDES ? status : { kind: 'idle' }} detail={row?.detail ?? (loadError ? `Couldn't load settings: ${loadError}` : null)}
      control={row ? (
        <select className="arturo-panel-value" aria-labelledby={`setting-${OVERRIDES}`} aria-describedby={descIdOf(OVERRIDES)} value=""
                disabled={!row.editable || !pick.length} data-notify-add onChange={(e) => { if (e.target.value) { setChanged(null); setAdding(e.target.value); } }}>
          <option value="">{pick.length ? 'Add an agent…' : 'No other agents'}</option>
          {pick.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      ) : loading} />
    {shown.map((o) => {
      const id = `notifications.agent.${o.id}`;
      return <SettingRow key={o.id} id={id} label={o.name} keywords={['agent', o.id]} description="Only for this agent."
        status={changed === o.id ? status : { kind: 'idle' }}
        control={<>
          <Choice<NotifyLevel> labelledBy={`setting-${id}`} value={o.level} options={LEVEL_OPTIONS} disabled={!row?.editable} onChange={(v) => set(o.id, v)} />
          <button type="button" className="arturo-panel-link" aria-label={`Remove ${o.name}`} data-notify-remove={o.id} disabled={!row?.editable}
                  onClick={() => (o.id === adding ? setAdding(null) : remove(o.id))}><X size={16} aria-hidden /></button>
        </>} />;
    })}
  </>;
}

/** Off, or from–to. Turning it on saves 22:00–07:00; each time saves when it is a whole time and the two differ. */
function QuietHoursRow() {
  const k = 'notifications.quietHours';
  const { row, status, flash, save, loadError } = useServerSetting(k);
  const zone = useInstallTimeZone();
  const quiet = quietOf(row?.value);
  const time = (end: 'from' | 'to', label: string) => quiet && (
    <label className="settings-control" data-quiet-end={end}>
      <span>{label}</span>
      <input type="time" className="arturo-panel-input" value={quiet[end]} disabled={!row?.editable} aria-describedby={descIdOf(k)}
             onChange={(e) => {
               const r = changeQuiet(quiet, end, e.target.value);
               if (r.ok) void save(r.value); else if (r.error) flash({ kind: 'error', text: r.error });
             }} />
    </label>
  );
  return <SettingRow id={k} label="Quiet hours" keywords={['quiet', 'hours', 'night', 'sleep', 'do not disturb', 'silent', 'mute']}
    description="Nothing buzzes or pushes in these hours; cards still land in the app. Follows you to every device."
    status={status} detail={row?.detail ?? (loadError ? `Couldn't load settings: ${loadError}` : null)}
    note={quiet ? quietZoneLine(zone) : null}
    below={quiet && <div className="settings-control settings-quiet-times">{time('from', 'From')}{time('to', 'To')}</div>}
    control={row ? <Switch on={!!quiet} labelledBy={`setting-${k}`} describedBy={descIdOf(k)} disabled={!row.editable} data={k}
                           onToggle={() => void save(quiet ? null : DEFAULT_QUIET)} /> : loading} />;
}

function ThisDeviceNote() {
  const q = useContext(SettingsQuery);
  if (!matchesQuery(q, 'This device', ['browser', 'device', 'pop-up', 'popup', 'desktop', 'sound', 'watch'])) return null;
  return <div className="settings-row" data-setting="notifications.thisDevice"><p className="settings-row-desc">{THIS_DEVICE_NOTE}</p></div>;
}

export function NotificationsSection() {
  return (
    <>
      <PageIntro>What reaches you, and when.</PageIntro>
      <Card title="What you hear about">
        <ServerChoiceRow k="notifications.level" label="Alerts" keywords={['notifications', 'alerts', 'all', 'needs me', 'nothing', 'push', 'buzz']}
          description="Follows you to every device. Needs me: only cards and questions waiting on you. Nothing: no alerts; the bell still lists them."
          options={LEVEL_OPTIONS} />
        <AgentOverrides />
      </Card>
      <Card title="Quiet hours">
        <QuietHoursRow />
      </Card>
      <Card title="This device">
        <ThisDeviceNote />
      </Card>
    </>
  );
}
