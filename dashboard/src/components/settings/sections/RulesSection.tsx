/** Settings › Timeout rules (attention part 2b), in the OpenBot layout Permissions uses: an intro, then one card per
 *  rule (the rule as a sentence, an on/off switch, Edit and Delete) and an Add button. The edit form picks the
 *  trigger (a question unanswered / no progress), the timeframe, an optional scope (agent, kind, risk) and the
 *  actions the server offers (remind, mark as stuck, let it expire, hand it to another agent, choose for me), each
 *  with its own settings. Hand off, expire and choose for me carry a fixed note: never on permission cards or
 *  high-risk questions (the server's safety gate refuses them anyway). Part 1's seeded defaults are shown as they are;
 *  this page changes none of them until a person does. Helpers: lib/rulesPage.ts; requests: lib/supervision.ts. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useAgents } from '../../../hooks/useAgents';
import { agentChoices } from '../../../lib/notificationsPage';
import {
  actionChoices, actionLabel, EMPTY_FORM, repeatChoices, formToRule, human, isFormError, newAction, ruleBody, ruleSentence, ruleToForm,
  type RuleAction, type RuleForm,
} from '../../../lib/rulesPage';
import { deleteRule, fetchRules, saveRule, setRuleEnabled, type RulesState, type StoredRule } from '../../../lib/supervision';
import { Switch } from '../../arturo/PanelRows';
import { Card, ConfirmButton, PageIntro, SettingRow } from '../SettingsKit';
import { descIdOf } from '../settingsState';

const INTRO = 'What happens when something waits too long. Nothing acts for you unless you add it.';
const loading = <span className="panel-rowvalue">…</span>;

function useRules() {
  const [state, setState] = useState<RulesState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const apply = useCallback((r: Awaited<ReturnType<typeof fetchRules>>) => {
    if (!alive.current) return;
    if (r.ok) { setState(r.value); setError(null); } else setError(r.error);
  }, []);
  const reload = useCallback(() => fetchRules().then(apply), [apply]);
  useEffect(() => { void fetchRules().then(apply); }, [apply]);
  return { state, error, reload };
}

/** One action's own settings (repeat, mode, to, who decides). */
function ActionParams({ a, idx, agents, onChange }: { a: RuleAction; idx: number; agents: { id: string; name: string }[]; onChange: (a: RuleAction) => void }) {
  const id = `rules.form.action.${idx}`;
  if (a.type === 'remind') {
    return (
      <select className="panel-value" aria-label="Repeat" data-rule-repeat={idx} value={String((a.repeat_every_s ?? 0) / 60)}
              onChange={(e) => { const m = Number(e.target.value); onChange(m ? { ...a, repeat_every_s: m * 60 } : { type: a.type }); }}>
        {repeatChoices(a.repeat_every_s).map((m) => <option key={m} value={m}>{m ? `then every ${human(m * 60)}` : 'once'}</option>)}
      </select>
    );
  }
  if (a.type === 'expire') {
    return (
      <select className="panel-value" aria-label="How it expires" data-rule-mode={idx} value={a.mode ?? 'default'}
              onChange={(e) => onChange({ ...a, mode: e.target.value as 'default' | 'no_answer' })}>
        <option value="default">take the agent's default answer</option>
        <option value="no_answer">close it with no answer</option>
      </select>
    );
  }
  if (a.type === 'hand_off') {
    return (
      <select className="panel-value" aria-label="Hand it to" id={id} data-rule-to={idx} value={a.to ?? 'supervisor'}
              onChange={(e) => onChange({ ...a, to: e.target.value })}>
        <option value="supervisor">the agent's supervisor</option>
        {a.to && a.to !== 'supervisor' && !agents.some((g) => g.id === a.to) && <option value={a.to}>{a.to}</option>}
        {agents.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
      </select>
    );
  }
  if (a.type === 'choose_for_me') {
    return (
      <select className="panel-value" aria-label="Who decides" data-rule-by={idx} value={a.by ?? 'self'}
              onChange={(e) => onChange({ ...a, by: e.target.value as 'self' | 'supervisor' })}>
        <option value="self">the agent itself (when it can)</option>
        <option value="supervisor">its supervisor</option>
      </select>
    );
  }
  return null;
}

function RuleEditor({ rule, state, onDone }: { rule: StoredRule | null; state: RulesState; onDone: (changed: boolean) => void }) {
  const [form, setForm] = useState<RuleForm>(() => (rule ? ruleToForm(rule) : { ...EMPTY_FORM, actions: [...EMPTY_FORM.actions] }));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data } = useAgents();
  const agents = agentChoices(data);
  const choices = actionChoices(state.available_actions);
  const min = state.min_after_s[form.trigger];
  const set = (p: Partial<RuleForm>) => { setError(null); setForm((f) => ({ ...f, ...p })); };
  const used = new Set(form.actions.map((a) => String(a.type)));
  const addable = choices.filter((c) => !used.has(c.type) && (form.trigger === 'unanswered' || c.type === 'remind' || c.type === 'mark_stuck'));
  const notes = [...new Set(form.actions.map((a) => choices.find((c) => c.type === a.type)?.note).filter((n): n is string => !!n))];

  const save = async () => {
    const r = formToRule(form, { min_after_s: min });
    if (isFormError(r)) { setError(r.error); return; }
    setBusy(true);
    const res = await saveRule(rule?.id ?? null, ruleBody({ ...r, name: rule?.name ?? null }));
    setBusy(false);
    if (res.ok) onDone(true); else setError(res.error);
  };

  return (
    <Card title={rule ? 'Edit rule' : 'New rule'}>
      <SettingRow id="rules.form.trigger" label="When" keywords={['trigger']}
        control={
          <select className="panel-value" aria-labelledby="setting-rules.form.trigger" data-rule-trigger value={form.trigger}
                  onChange={(e) => {
                    const t = e.target.value as RuleForm['trigger'];
                    set({ trigger: t, kind: t === 'no_progress' ? '' : form.kind, risk: t === 'no_progress' ? '' : form.risk,
                          actions: t === 'no_progress' ? form.actions.filter((a) => a.type === 'remind' || a.type === 'mark_stuck') : form.actions });
                  }}>
            <option value="unanswered">a question waits for an answer</option>
            <option value="no_progress">an agent makes no progress</option>
          </select>} />
      <SettingRow id="rules.form.minutes" label="For" keywords={['timeframe', 'minutes']}
        description={`${min >= 3600 ? `${min / 3600} h` : `${Math.round(min / 60)} min`} or more`}
        control={<span className="settings-control">
          <input type="number" className="panel-value" min={min / 60} step="any" value={form.minutes}
                 aria-labelledby="setting-rules.form.minutes" aria-describedby={descIdOf('rules.form.minutes')} data-rule-minutes
                 onChange={(e) => set({ minutes: Number(e.target.value) })} />
          <span className="panel-rowvalue">min</span>
        </span>} />
      <SettingRow id="rules.form.agent" label="Agent" keywords={['scope', 'agent']} description="Leave on any agent for a rule for all of them."
        control={
          <select className="panel-value" aria-labelledby="setting-rules.form.agent" aria-describedby={descIdOf('rules.form.agent')} data-rule-agent
                  value={form.agent} onChange={(e) => set({ agent: e.target.value })}>
            <option value="">any agent</option>
            {form.agent && !agents.some((a) => a.id === form.agent) && <option value={form.agent}>{form.agent}</option>}
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>} />
      {form.trigger === 'unanswered' && <>
        <SettingRow id="rules.form.kind" label="Kind" keywords={['scope', 'kind']}
          control={
            <select className="panel-value" aria-labelledby="setting-rules.form.kind" data-rule-kind value={form.kind}
                    onChange={(e) => set({ kind: e.target.value as RuleForm['kind'] })}>
              <option value="">any question</option>
              <option value="decision">decisions</option>
              <option value="questionnaire">questionnaires</option>
              <option value="permission">permission requests</option>
              <option value="human_task">tasks for you</option>
            </select>} />
        <SettingRow id="rules.form.risk" label="Risk" keywords={['scope', 'risk']}
          control={
            <select className="panel-value" aria-labelledby="setting-rules.form.risk" data-rule-risk value={form.risk}
                    onChange={(e) => set({ risk: e.target.value as RuleForm['risk'] })}>
              <option value="">any risk</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>} />
      </>}
      {form.actions.map((a, i) => (
        <SettingRow key={`${a.type}-${i}`} id={`rules.form.action.${i}`} label={`${i ? 'Then' : 'Do'}: ${actionLabel(String(a.type))}`} keywords={['action']}
          detail={choices.find((c) => c.type === a.type)?.disabled ? choices.find((c) => c.type === a.type)?.why : null}
          control={<span className="settings-control">
            <ActionParams a={a} idx={i} agents={agents} onChange={(n) => set({ actions: form.actions.map((x, j) => (j === i ? n : x)) })} />
            <button type="button" className="panel-link" data-rule-remove={i} aria-label={`Remove ${actionLabel(String(a.type))}`}
                    onClick={() => set({ actions: form.actions.filter((_, j) => j !== i) })}>Remove</button>
          </span>} />
      ))}
      {notes.map((n) => <p key={n} className="panel-note" data-rule-note>{n}</p>)}
      <SettingRow id="rules.form.add" label="Add an action" keywords={['action', 'add']}
        control={
          <select className="panel-value" aria-labelledby="setting-rules.form.add" value="" data-rule-add disabled={!addable.length}
                  onChange={(e) => { if (e.target.value) set({ actions: [...form.actions, newAction(e.target.value)] }); }}>
            <option value="">{addable.length ? 'Pick one…' : 'Nothing else to add'}</option>
            {addable.map((c) => <option key={c.type} value={c.type} disabled={c.disabled} title={c.why ?? undefined}>{c.label}{c.disabled && c.why ? ` (${c.why})` : ''}</option>)}
          </select>} />
      {error && <p className="panel-error" role="alert" data-rule-error>{error}</p>}
      <div className="settings-row">
        <span className="settings-control">
          <button type="button" className="panel-link" onClick={() => onDone(false)} disabled={busy}>Cancel</button>
          <button type="button" className="panel-primary" onClick={() => void save()} disabled={busy} data-rule-save>{rule ? 'Save' : 'Add rule'}</button>
        </span>
      </div>
    </Card>
  );
}

export function RulesSection() {
  const { state, error, reload } = useRules();
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const [rowError, setRowError] = useState<{ id: string; text: string } | null>(null);

  if (!state) return <><PageIntro>{INTRO}</PageIntro>{error ? <p className="panel-error" role="alert">{error}</p> : loading}</>;
  if (!state.armed) return <><PageIntro>{INTRO}</PageIntro><p className="panel-note">Timeout rules are not switched on for this server yet.</p></>;

  const done = (changed: boolean) => { setEditing(null); if (changed) void reload(); };
  const toggle = async (r: StoredRule) => {
    const res = await setRuleEnabled(r.id, !r.enabled);
    setRowError(res.ok ? null : { id: r.id, text: res.error });
    void reload();
  };
  const remove = async (r: StoredRule) => {
    const res = await deleteRule(r.id);
    setRowError(res.ok ? null : { id: r.id, text: res.error });
    void reload();
  };

  return <>
    <PageIntro>{INTRO}</PageIntro>
    <Card title="Your rules" description="The most specific rule wins: one for an agent beats one for everyone.">
      {state.rules.length === 0 && <p className="panel-note">No rules. Nothing happens on its own when something waits.</p>}
      {state.rules.map((r) => {
        const id = `rules.${r.id}`;
        return <SettingRow key={r.id} id={id} label={ruleSentence(r)} keywords={['rule', ...r.actions.map((a) => String(a.type))]}
          description={r.enabled ? null : 'Off'}
          detail={rowError?.id === r.id ? rowError.text : null}
          control={<span className="settings-control" data-rule={r.id}>
            <Switch on={!!r.enabled} labelledBy={`setting-${id}`} disabled={false} onToggle={() => void toggle(r)} data={`rule-${r.id}`} />
            <button type="button" className="panel-link" onClick={() => setEditing(r.id)} data-rule-edit={r.id}>Edit</button>
            <ConfirmButton label="Delete" consequence="Delete this rule?" onConfirm={() => void remove(r)} data={`rule-delete-${r.id}`} />
          </span>} />;
      })}
      {editing === null && (
        <div className="settings-row">
          <button type="button" className="panel-link" onClick={() => setEditing('new')} data-rule-new>Add a rule</button>
        </div>
      )}
    </Card>
    {editing !== null && (
      <RuleEditor key={editing} rule={editing === 'new' ? null : state.rules.find((r) => r.id === editing) ?? null} state={state} onDone={done} />
    )}
  </>;
}
