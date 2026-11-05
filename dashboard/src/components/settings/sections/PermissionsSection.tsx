/** Settings › Permissions, in the OpenBot layout NotificationsSection uses: an intro, then cards of rows.
 *    Your default     how closely your agents are watched (three levels), what is high-risk under Check in, and how
 *                     long a held call waits for you.
 *    Roles            one row per role (its one-line summary, what uses it) with Edit, Duplicate and Delete; New role.
 *    Projects         each project's default role (a native select).
 *    Agents           each agent's role (a native select, its project's default first), an Override disclosure and its
 *                     gate badge.
 *    Whole server … Sessions   the standing grants by level ("dev may deploy without asking"): Narrow and Revoke; a
 *                     pending chat grant shows Confirm / Decline, which answer its confirm card.
 *    Add a permission who → what → optional narrowing.
 *  Writes: lib/supervision.ts (api routes/supervision.ts). The person's default, roles, projects and the wider grants
 *  are an admin's; a member assigns roles to the agents they see and manages those agents' own grants.
 *  Helpers: lib/permissionsPage.ts, lib/supervisionPanel.ts. */
import { useState } from 'react';
import {
  answerGrantCard, createGrant, createRole, deleteRole, duplicateRole, gateBadge, narrowGrant, putAgent, putDefault, putProjectRole,
  revokeGrant, updateRole, waitLabel, type Category, type Grant, type GrantScope, type LevelId, type Narrow, type Policy, type Role, type Supervision,
} from '../../../lib/supervision';
import {
  grantLabel, grantSource, grantsByLevel, levelOptions, narrowOptions, newGrantBody, projectRows, roleInUse, roleSummary, scopeNames,
  usedByLine, validateNewGrant, validateRole, type GrantDraft,
} from '../../../lib/permissionsPage';
import { hasOverride, panelValue, roleOptions, roleValue } from '../../../lib/supervisionPanel';
import { useInstallAdmin } from '../../../lib/myRole';
import { Card, Choice, ConfirmButton, PageIntro, SettingRow } from '../SettingsKit';
import { descIdOf, useSavedFlash, type RowStatus } from '../settingsState';
import { useSupervision } from '../useSupervision';

const INTRO = "By default your agents run on their own. Use roles to choose how closely an agent is supervised and what it may do without asking. An agent's own setting wins over its role, its role over its project's, and the project's over yours.";
const loading = <span className="panel-rowvalue">…</span>;

type Write = ReturnType<typeof useSupervision>['write'];

/** A row's ✓ Saved or error, for the row that last saved. */
function useRowStatus(): [(id: string) => RowStatus, (id: string, err: string | null) => void] {
  const [status, flash] = useSavedFlash();
  const [which, setWhich] = useState<string | null>(null);
  return [(id) => (id === which ? status : { kind: 'idle' }), (id, err) => { setWhich(id); flash(err ? { kind: 'error', text: err } : { kind: 'saved' }); }];
}

/** The high-risk checkboxes (Check in): each category with its detail line. */
function HighRisk({ id, value, cats, disabled, onChange }: { id: string; value: string[]; cats: Category[]; disabled: boolean; onChange: (v: string[]) => void }) {
  return (
    <span className="settings-control" role="group" aria-labelledby={`setting-${id}`} data-high-risk={id}>
      {cats.map((c) => (
        <label key={c.id} className="settings-control" title={c.detail}>
          <input type="checkbox" checked={value.includes(c.id)} disabled={disabled}
                 onChange={(e) => onChange(e.target.checked ? [...value, c.id] : value.filter((x) => x !== c.id))} />
          <span>{c.label}</span>
        </label>
      ))}
    </span>
  );
}

function WaitSelect({ id, value, choices, disabled, onChange, useRole }: {
  id: string; value: number | null; choices: number[]; disabled: boolean; onChange: (v: number | null) => void; useRole?: string;
}) {
  return (
    <select className="panel-value" aria-labelledby={`setting-${id}`} aria-describedby={descIdOf(id)} disabled={disabled}
            value={value === null ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}>
      {useRole && <option value="">{useRole}</option>}
      {choices.map((s) => <option key={s} value={s}>{waitLabel(s)}</option>)}
    </select>
  );
}

/** Level, high-risk and wait: the person's default and the role editor share it. */
function PolicyRows({ id, sup, value, disabled, locked = false, onChange, status }: {
  id: string; sup: Supervision; value: Policy; disabled: boolean; locked?: boolean; onChange: (p: Partial<Policy>) => void; status: (id: string) => RowStatus;
}) {
  const detail = sup.levels.find((l) => l.id === value.level)?.detail;
  return <>
    <SettingRow id={`${id}.level`} label="How closely" keywords={['supervision', 'level', 'watch closely', 'check in', 'let them run']}
      description={detail ?? null} status={status(`${id}.level`)} locked={locked}
      control={<Choice<LevelId> labelledBy={`setting-${id}.level`} value={value.level} options={levelOptions(sup.levels)} disabled={disabled} onChange={(v) => onChange({ level: v })} />} />
    {value.level === 'check_in' && (
      <SettingRow id={`${id}.high_risk`} label="High-risk" keywords={['high-risk', 'categories', 'deploy', 'delete', 'install']} stacked
        description="Under Check in, only these wait for you." status={status(`${id}.high_risk`)}
        control={<HighRisk id={`${id}.high_risk`} value={value.high_risk} cats={sup.categories} disabled={disabled} onChange={(v) => onChange({ high_risk: v })} />} />
    )}
    <SettingRow id={`${id}.wait`} label="When something is held" keywords={['wait', 'hold', 'held', 'minutes']}
      description="How long a held call waits for your answer before the agent carries on without it." status={status(`${id}.wait`)}
      control={<WaitSelect id={`${id}.wait`} value={value.hold_wait_s} choices={sup.wait_choices} disabled={disabled} onChange={(v) => onChange({ hold_wait_s: v ?? 0 })} />} />
  </>;
}

/** The role editor (Edit / New role): name, description, level, high-risk, wait; an existing role's grants and the
 *  add-a-permission form fixed to it. */
function RoleEditor({ sup, role, grants, write, onDone }: { sup: Supervision; role: Role | null; grants: Grant[]; write: Write; onDone: (id: string | null) => void }) {
  const [draft, setDraft] = useState({
    name: role?.name ?? '', description: role?.description ?? '',
    level: role?.level ?? 'check_in' as LevelId, high_risk: role?.high_risk ?? [], hold_wait_s: role?.hold_wait_s ?? 0,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const names = sup.roles.filter((r) => r.id !== role?.id).map((r) => r.name);
  const id = `supervision.role.${role?.id ?? 'new'}`;
  const save = async () => {
    const bad = validateRole(draft, names);
    if (bad) { setError(bad); return; }
    setBusy(true);
    const body = { ...draft, name: draft.name.trim(), description: draft.description.trim() };
    const r = role ? await updateRole(role.id, body) : await createRole(body);
    setBusy(false);
    if (!r.ok) { setError(r.error); return; }
    void write(Promise.resolve(r));
    onDone(r.value.id);
  };
  const roleGrants = grants.filter((g) => g.scope === 'role' && g.scope_ref === role?.id);
  return (
    <div className="settings-row" data-role-editor={role?.id ?? 'new'}>
      <SettingRow id={`${id}.name`} label="Name" control={<input className="panel-input" aria-labelledby={`setting-${id}.name`} value={draft.name} maxLength={60}
        onChange={(e) => { setError(null); setDraft({ ...draft, name: e.target.value }); }} />} />
      <SettingRow id={`${id}.description`} label="Description" control={<input className="panel-input" aria-labelledby={`setting-${id}.description`} value={draft.description} maxLength={200}
        onChange={(e) => setDraft({ ...draft, description: e.target.value })} />} />
      <PolicyRows id={id} sup={sup} value={draft} disabled={busy} onChange={(p) => setDraft({ ...draft, ...p })} status={() => ({ kind: 'idle' })} />
      {error && <p className="panel-error" role="alert">{error}</p>}
      <div className="settings-control">
        <button type="button" className="panel-link" onClick={() => onDone(null)}>Cancel</button>
        <button type="button" className="panel-primary" disabled={busy} onClick={() => void save()} data-role-save>{role ? 'Save role' : 'Create role'}</button>
      </div>
      {role && <>
        <p className="settings-row-desc">What {role.name} agents may do without asking</p>
        {roleGrants.length ? roleGrants.map((g) => <GrantRow key={g.id} g={g} sup={sup} canEdit write={write} />)
          : <p className="panel-empty">Nothing yet: anything under its level waits for you.</p>}
        <AddGrant sup={sup} write={write} admin fixed={{ scope: 'role', ref: role.id }} />
      </>}
    </div>
  );
}

function RolesCard({ sup, grants, admin, write }: { sup: Supervision; grants: Grant[]; admin: boolean; write: Write }) {
  const [editing, setEditing] = useState<string | null>(null);   // a role id, or 'new'
  const [status, mark] = useRowStatus();
  const names = sup.roles.map((r) => r.name);
  const copyName = (n: string) => { let c = `${n} (copy)`; for (let i = 2; names.includes(c); i++) c = `${n} (copy ${i})`; return c; };
  return (
    <Card title="Roles" description="A role is a level and a set of permissions. Give one to a project or an agent.">
      {sup.roles.map((r) => {
        const id = `supervision.roles.${r.id}`;
        const inUse = roleInUse(r);
        return <SettingRow key={r.id} id={id} label={r.name} keywords={['role', r.name]} status={status(id)} locked={!admin}
          description={roleSummary(r, sup.categories, sup.levels)} note={[r.description, usedByLine(r)].filter(Boolean).join(' · ')}
          below={editing === r.id && <RoleEditor sup={sup} role={r} grants={grants} write={write} onDone={(saved) => { setEditing(null); if (saved) mark(id, null); }} />}
          control={admin && <>
            <button type="button" className="panel-link" onClick={() => setEditing(editing === r.id ? null : r.id)} data-role-edit={r.id}>Edit</button>
            <button type="button" className="panel-link" data-role-duplicate={r.id}
                    onClick={() => void write(duplicateRole(r.id, copyName(r.name))).then((e) => mark(id, e))}>Duplicate</button>
            {inUse
              ? <button type="button" className="panel-link" disabled title="Assigned to a project or agent">Delete (in use)</button>
              : <ConfirmButton label="Delete" consequence={`Delete ${r.name}?`} data={`role-delete-${r.id}`}
                               onConfirm={() => void write(deleteRole(r.id)).then((e) => { if (e) mark(id, e); })} />}
          </>} />;
      })}
      {admin && (editing === 'new'
        ? <RoleEditor sup={sup} role={null} grants={grants} write={write} onDone={(saved) => setEditing(saved)} />
        : <div className="settings-row"><button type="button" className="panel-link" onClick={() => setEditing('new')} data-role-new>New role</button></div>)}
    </Card>
  );
}

function ProjectsCard({ sup, admin, write }: { sup: Supervision; admin: boolean; write: Write }) {
  const [status, mark] = useRowStatus();
  const rows = projectRows(sup);
  if (!rows.length) return null;
  return (
    <Card title="Projects" description="Each project's default role. Its agents follow it unless they have their own.">
      {rows.map((p) => {
        const id = `supervision.projects.${p.label}`;
        return <SettingRow key={p.key} id={id} label={p.label} keywords={['project', p.label, ...p.agents]} status={status(id)} locked={!admin}
          description={p.agents.length ? p.agents.join(', ') : 'No agents'}
          control={<select className="panel-value" aria-labelledby={`setting-${id}`} aria-describedby={descIdOf(id)} disabled={!admin}
                           value={p.roleId ?? ''} onChange={(e) => void write(putProjectRole(p.key, e.target.value)).then((err) => mark(id, err))}>
            {!p.roleId && <option value="">…</option>}
            {sup.roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>} />;
      })}
    </Card>
  );
}

/** An agent's own level, high-risk and wait, each "Use role" until set. */
function OverrideForm({ sup, agent, write, onSaved }: { sup: Supervision; agent: Supervision['agents'][number]; write: Write; onSaved: (e: string | null) => void }) {
  const o = agent.override;
  const id = `supervision.override.${agent.agent}`;
  const put = (body: Parameters<typeof putAgent>[1]) => void write(putAgent(agent.agent, body)).then(onSaved);
  return (
    <details className="settings-row" data-override={agent.agent} open={hasOverride(agent) || undefined}>
      <summary className="panel-link">Override</summary>
      <SettingRow id={`${id}.level`} label="Level" control={
        <select className="panel-value" aria-labelledby={`setting-${id}.level`} value={o.level ?? ''} onChange={(e) => put({ level: (e.target.value || null) as LevelId | null })}>
          <option value="">Use role</option>
          {sup.levels.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
        </select>} />
      <SettingRow id={`${id}.high_risk`} label="High-risk" stacked control={
        <select className="panel-value" aria-labelledby={`setting-${id}.high_risk`} value={o.high_risk ? 'own' : ''}
                onChange={(e) => put({ high_risk: e.target.value ? [...agent.effective.high_risk] : null })}>
          <option value="">Use role</option>
          <option value="own">Choose for this agent</option>
        </select>}
        below={o.high_risk && <HighRisk id={`${id}.high_risk`} value={o.high_risk} cats={sup.categories} disabled={false} onChange={(v) => put({ high_risk: v })} />} />
      <SettingRow id={`${id}.wait`} label="When something is held" control={
        <WaitSelect id={`${id}.wait`} value={o.hold_wait_s ?? null} choices={sup.wait_choices} disabled={false} useRole="Use role" onChange={(v) => put({ hold_wait_s: v })} />} />
    </details>
  );
}

function AgentsCard({ sup, write }: { sup: Supervision; write: Write }) {
  const [status, mark] = useRowStatus();
  if (!sup.agents.length) return null;
  return (
    <Card title="Agents" description="Give an agent its own role, or override part of it.">
      {sup.agents.map((a) => {
        const id = `supervision.agents.${a.agent}`;
        return <SettingRow key={a.agent} id={id} label={a.agent} keywords={['agent', a.agent, a.runtime]} status={status(id)}
          description={`${panelValue(a, sup.levels)}${a.gate.status === 'not_supported' ? '' : ` · ${gateBadge(a.gate)}`}`}
          below={<OverrideForm sup={sup} agent={a} write={write} onSaved={(e) => mark(id, e)} />}
          control={<select className="panel-value" aria-labelledby={`setting-${id}`} aria-describedby={descIdOf(id)} value={roleValue(a)}
                           onChange={(e) => void write(putAgent(a.agent, { role_id: e.target.value || null })).then((err) => mark(id, err))}>
            {roleOptions(sup, a.agent).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>} />;
      })}
    </Card>
  );
}

/** Narrow a grant: only what is still open (an action or recipient not set yet), and a folder inside the current one. */
function NarrowForm({ g, sup, write, onDone }: { g: Grant; sup: Supervision; write: Write; onDone: () => void }) {
  const [n, setN] = useState<Narrow>({});
  const [error, setError] = useState<string | null>(null);
  const opts = narrowOptions(g.category, sup.categories);
  const save = async () => {
    const body: Narrow = {};
    if (n.action) body.action = n.action;
    if (n.recipient?.trim()) body.recipient = n.recipient.trim();
    if (n.folder?.trim()) body.folder = n.folder.trim();
    const bad = validateNewGrant({ scope: 'fleet', category: g.category, folder: body.folder ?? undefined });
    if (bad) { setError(bad); return; }
    if (!Object.keys(body).length) { onDone(); return; }
    const e = await write(narrowGrant(g.id, body));
    if (e) setError(e); else onDone();
  };
  return (
    <div className="settings-control" data-narrow={g.id}>
      {opts.map((o) => o.kind === 'action' ? (!g.narrow.action && o.actions.length > 0 && (
        <select key="action" className="panel-input" aria-label="Only this" value={n.action ?? ''} onChange={(e) => setN({ ...n, action: e.target.value || undefined })}>
          <option value="">Any</option>
          {o.actions.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      )) : o.kind === 'folder' ? (
        <input key="folder" className="panel-input" aria-label="Only in this folder" placeholder={g.narrow.folder ? `Inside ${g.narrow.folder}` : 'Only in folder (full path)'}
               value={n.folder ?? ''} onChange={(e) => setN({ ...n, folder: e.target.value })} />
      ) : (!g.narrow.recipient && (
        <input key="recipient" className="panel-input" aria-label="Only to" placeholder="Only to (address or account)" value={n.recipient ?? ''}
               onChange={(e) => setN({ ...n, recipient: e.target.value })} />
      )))}
      <button type="button" className="panel-link" onClick={onDone}>Cancel</button>
      <button type="button" className="panel-primary" onClick={() => void save()}>Narrow</button>
      {error && <p className="panel-error" role="alert">{error}</p>}
    </div>
  );
}

function GrantRow({ g, sup, canEdit, write }: { g: Grant; sup: Supervision; canEdit: boolean; write: Write }) {
  const [narrowing, setNarrowing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `supervision.grant.${g.id}`;
  const label = grantLabel(g, sup.categories, scopeNames(sup));
  const pending = g.state === 'pending';
  const answer = (yes: boolean) => {
    if (!g.source_card) { void write(revokeGrant(g.id)).then(setError); return; }
    void write(answerGrantCard(g.source_card, yes)).then(setError);
  };
  return <SettingRow id={id} label={label} keywords={['permission', 'grant', g.category, g.narrow.action ?? '']} locked={!canEdit}
    description={grantSource(g) || null} note={pending ? 'Waiting for your confirmation' : g.overridden_by ? `Overridden by ${g.overridden_by}` : null}
    status={error ? { kind: 'error', text: error } : { kind: 'idle' }}
    below={narrowing && <NarrowForm g={g} sup={sup} write={write} onDone={() => setNarrowing(false)} />}
    control={canEdit && (pending ? <>
      <button type="button" className="panel-link" onClick={() => answer(false)} data-grant-decline={g.id}>Decline</button>
      <button type="button" className="panel-primary" onClick={() => answer(true)} data-grant-confirm={g.id}>Confirm</button>
    </> : <>
      {g.effect !== 'deny' && <button type="button" className="panel-link" onClick={() => setNarrowing(!narrowing)} data-grant-narrow={g.id}>Narrow</button>}
      <ConfirmButton label="Revoke" consequence="Agents will ask again" data={`grant-revoke-${g.id}`} onConfirm={() => void write(revokeGrant(g.id)).then(setError)} />
    </>)} />;
}

const WHO: { scope: GrantScope; label: string; admin: boolean }[] = [
  { scope: 'fleet', label: 'Whole server', admin: true }, { scope: 'project', label: 'A project', admin: true },
  { scope: 'role', label: 'A role', admin: true }, { scope: 'agent', label: 'An agent', admin: false },
];

/** Add a permission: who → what → optional narrowing. `fixed`: the role editor's form, for that role only. */
function AddGrant({ sup, write, admin, fixed }: { sup: Supervision; write: Write; admin: boolean; fixed?: { scope: GrantScope; ref: string } }) {
  const [d, setD] = useState<GrantDraft>(fixed ? { ...fixed, category: '' } : { scope: admin ? 'fleet' : 'agent', ref: '', category: '' });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const id = `supervision.add.${fixed?.ref ?? 'any'}`;
  const refs: { value: string; label: string }[] = d.scope === 'project' ? sup.projects.map((p) => ({ value: p.key, label: p.label }))
    : d.scope === 'role' ? sup.roles.map((r) => ({ value: r.id, label: r.name }))
    : d.scope === 'agent' ? sup.agents.map((a) => ({ value: a.agent, label: a.agent })) : [];
  const opts = d.category ? narrowOptions(d.category, sup.categories) : [];
  const add = async () => {
    const bad = validateNewGrant(d);
    if (bad) { setError(bad); return; }
    const e = await write(createGrant(newGrantBody(d)));
    if (e) { setError(e); return; }
    setSaved(true);
    setD({ scope: fixed?.scope ?? d.scope, ref: fixed?.ref ?? d.ref, category: '' });
  };
  const set = (p: Partial<GrantDraft>) => { setError(null); setSaved(false); setD({ ...d, ...p }); };
  return <SettingRow id={id} label={fixed ? 'Add a permission to this role' : 'Add a permission'} keywords={['add', 'permission', 'grant', 'allow']} stacked
    status={error ? { kind: 'error', text: error } : saved ? { kind: 'saved' } : { kind: 'idle' }}
    description="What it may do without asking. Narrow it to one action, folder or recipient if you like."
    control={null}
    below={<div className="settings-control" data-add-grant={fixed?.ref ?? 'any'}>
      {!fixed && <>
        <select className="panel-input" aria-label="Who" value={d.scope} onChange={(e) => set({ scope: e.target.value as GrantScope, ref: '' })}>
          {WHO.filter((w) => admin || !w.admin).map((w) => <option key={w.scope} value={w.scope}>{w.label}</option>)}
        </select>
        {d.scope !== 'fleet' && (
          <select className="panel-input" aria-label="Which" value={d.ref ?? ''} onChange={(e) => set({ ref: e.target.value })}>
            <option value="">Pick…</option>
            {refs.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        )}
      </>}
      <select className="panel-input" aria-label="May do" value={d.category}
              onChange={(e) => set({ category: e.target.value, action: undefined, folder: undefined, recipient: undefined })}>
        <option value="">What it may do…</option>
        {sup.categories.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select>
      {opts.map((o) => o.kind === 'action' ? (
        <select key="action" className="panel-input" aria-label="Only this" value={d.action ?? ''} onChange={(e) => set({ action: e.target.value || undefined })}>
          <option value="">Any</option>
          {o.actions.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      ) : o.kind === 'folder' ? (
        <input key="folder" className="panel-input" aria-label="Only in this folder" placeholder="Only in folder (full path)" value={d.folder ?? ''} onChange={(e) => set({ folder: e.target.value })} />
      ) : (
        <input key="recipient" className="panel-input" aria-label="Only to" placeholder="Only to (address or account)" value={d.recipient ?? ''} onChange={(e) => set({ recipient: e.target.value })} />
      ))}
      <button type="button" className="panel-primary" onClick={() => void add()} data-add-grant-save>Add</button>
    </div>} />;
}

export function PermissionsSection() {
  const admin = useInstallAdmin();
  const { sup, grants, error, write } = useSupervision();
  const [status, mark] = useRowStatus();
  if (!sup) {
    return <><PageIntro>{INTRO}</PageIntro>{error ? <p className="panel-error" role="alert">Couldn't load permissions: {error}</p> : <p className="panel-empty">{loading}</p>}</>;
  }
  const all = grants ?? [];
  // A member manages the grants of the agents they see (and those agents' sessions); the wider ones are read-only.
  const canEditGrant = (g: Grant) => admin || g.scope === 'agent' || g.scope === 'session';
  const saveDefault = (p: Partial<Policy>) => {
    const key = p.level ? 'level' : p.high_risk ? 'high_risk' : 'wait';
    void write(putDefault(p)).then((e) => mark(`supervision.default.${key}`, e));
  };
  return (
    <>
      <PageIntro>{INTRO}</PageIntro>
      {!sup.armed && <p className="panel-note" data-supervision-unarmed>Supervision is not switched on for this server yet: everything runs on its own and nothing here can be changed.</p>}
      {error && <p className="panel-error" role="alert">{error}</p>}
      <Card title="Your default" description="Your agents follow this unless a role, their project or their own setting says otherwise.">
        <PolicyRows id="supervision.default" sup={sup} value={sup.default} disabled={!admin || !sup.armed} locked={!admin} onChange={saveDefault} status={status} />
      </Card>
      <RolesCard sup={sup} grants={all} admin={admin} write={write} />
      <ProjectsCard sup={sup} admin={admin} write={write} />
      <AgentsCard sup={sup} write={write} />
      {grantsByLevel(all.filter((g) => g.state === 'active' || g.state === 'pending')).map((lvl) => (
        <Card key={lvl.scope} title={lvl.title} description={lvl.scope === 'fleet' ? 'What every agent may do without asking.' : undefined}>
          {lvl.grants.map((g) => <GrantRow key={g.id} g={g} sup={sup} canEdit={canEditGrant(g)} write={write} />)}
        </Card>
      ))}
      <Card title="Add a permission">
        <AddGrant sup={sup} write={write} admin={admin} />
      </Card>
    </>
  );
}
