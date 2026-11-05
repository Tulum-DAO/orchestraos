/** The agent drawer's Supervision row and page (ArturoPanel; hidden for Arturo).
 *    Role                 a native select: the project's default first, then every role (PUT /api/supervision/agents/:id).
 *    Override             this agent's own level, high-risk and wait, each "Use role" until set.
 *    Safety gate          the gate badge ("Protected", "Can't be gated yet" for codex / grok).
 *    What <name> may do   every grant that reaches it, each with a switch: its own grant is revoked; an inherited role,
 *                         project or whole-server grant is overridden by an agent-level deny, and switched back on by
 *                         deleting that deny (lib/supervisionPanel.ts toggleGrant).
 *    Edit roles           Settings › Permissions.
 *  A member sees only the agents they can see: the API enforces it. */
import { useId, useState, type ReactNode } from 'react';
import { createGrant, gateBadge, putAgent, revokeGrant, waitLabel, type LevelId } from '../../lib/supervision';
import { grantLabel, grantSource, scopeNames } from '../../lib/permissionsPage';
import { drawerGrants, hasOverride, panelValue, roleOptions, roleValue, toggleGrant } from '../../lib/supervisionPanel';
import { useSupervision } from '../settings/useSupervision';
import { Info, LinkRow, Row, Switch } from './PanelRows';

/** The Setup section's row: "Hands-off (project default) · Let them run". */
export function SupervisionRow({ agentId, onOpen }: { agentId: string; onOpen: (p: 'supervision') => void }) {
  const { sup } = useSupervision({ grants: false });
  const a = sup?.agents.find((x) => x.agent === agentId);
  return <Row id="supervision" label="Supervision" value={panelValue(a, sup?.levels)} onOpen={onOpen} />;
}

export function SupervisionPage({ agentId, name, onEditRoles }: { agentId: string; name: string; onEditRoles: () => void }) {
  const { sup, grants, error, write } = useSupervision({ agent: agentId });
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ids = useId();
  if (!sup) return error ? <p className="panel-error" role="alert">Couldn't load supervision: {error}</p> : <p className="panel-empty">Loading…</p>;
  const a = sup.agents.find((x) => x.agent === agentId);
  if (!a) return <p className="panel-empty">{name} isn't on the supervision list yet.</p>;
  const run = (w: Parameters<typeof write>[0]) => { setBusy(true); void write(w).then((e) => { setBusy(false); setSaveError(e); }); };
  const put = (body: Parameters<typeof putAgent>[1]) => run(putAgent(agentId, body));
  const o = a.override;
  const hr = o.high_risk ?? null;
  const rows = drawerGrants(grants ?? [], agentId);
  const names = scopeNames(sup);
  const flip = (g: (typeof rows)[number]['grant'], on: boolean) => {
    const t = toggleGrant(g, agentId, on, grants ?? []);
    if (t.op === 'create') run(createGrant(t.body));
    else if (t.op === 'revoke') run(revokeGrant(t.id));
  };
  const field = (label: string, control: ReactNode, id: string) => (
    <div className="panel-field"><span id={id}>{label}</span>{control}</div>
  );
  return (
    <>
      {!sup.armed && <p className="panel-note">Supervision is not switched on for this server yet: {name} runs on its own.</p>}
      <div className="panel-list" data-supervision-page={agentId}>
        {field('Role', (
          <select className="panel-value" aria-labelledby={`${ids}-role`} value={roleValue(a)} disabled={busy} data-supervision-role
                  onChange={(e) => put({ role_id: e.target.value || null })}>
            {roleOptions(sup, agentId).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        ), `${ids}-role`)}
        <Info label="Now">{panelValue(a, sup.levels)}</Info>
        <Info label="Safety gate">{gateBadge(a.gate)}</Info>
      </div>
      <details className="panel-list" open={hasOverride(a) || undefined} data-supervision-override>
        <summary className="panel-row">Override for this agent</summary>
        {field('Level', (
          <select className="panel-value" aria-labelledby={`${ids}-level`} value={o.level ?? ''} disabled={busy}
                  onChange={(e) => put({ level: (e.target.value || null) as LevelId | null })}>
            <option value="">Use role</option>
            {sup.levels.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
        ), `${ids}-level`)}
        {field('High-risk', (
          <select className="panel-value" aria-labelledby={`${ids}-risk`} value={o.high_risk ? 'own' : ''} disabled={busy}
                  onChange={(e) => put({ high_risk: e.target.value ? [...a.effective.high_risk] : null })}>
            <option value="">Use role</option>
            <option value="own">Choose for this agent</option>
          </select>
        ), `${ids}-risk`)}
        {hr && sup.categories.map((c) => (
          <label key={c.id} className="panel-field" title={c.detail}>
            <span>{c.label}</span>
            <input type="checkbox" checked={hr.includes(c.id)} disabled={busy}
                   onChange={(e) => put({ high_risk: e.target.checked ? [...hr, c.id] : hr.filter((x) => x !== c.id) })} />
          </label>
        ))}
        {field('When something is held', (
          <select className="panel-value" aria-labelledby={`${ids}-wait`} value={o.hold_wait_s == null ? '' : String(o.hold_wait_s)} disabled={busy}
                  onChange={(e) => put({ hold_wait_s: e.target.value === '' ? null : Number(e.target.value) })}>
            <option value="">Use role</option>
            {sup.wait_choices.map((s) => <option key={s} value={s}>{waitLabel(s)}</option>)}
          </select>
        ), `${ids}-wait`)}
      </details>
      {saveError && <p className="panel-error" role="alert">{saveError}</p>}
      <h3 className="panel-section">What {name} may do without asking</h3>
      <div className="panel-list" data-supervision-grants>
        {rows.length ? rows.map(({ grant: g, on }) => {
          const id = `${ids}-g-${g.id}`;
          const source = grantSource(g);
          return (
            <div key={g.id} className="panel-field">
              <span id={id}>{grantLabel(g, sup.categories, names)}{source ? <span className="panel-rowvalue"> · {source}</span> : null}</span>
              <Switch on={on} labelledBy={id} disabled={busy || g.state !== 'active'} data={`grant-${g.id}`} onToggle={() => flip(g, !on)} />
            </div>
          );
        }) : <p className="panel-empty">Nothing yet: anything its level holds waits for you.</p>}
      </div>
      <div className="panel-list">
        <LinkRow label="Edit roles" data="edit-roles" onClick={onEditRoles} />
      </div>
    </>
  );
}
