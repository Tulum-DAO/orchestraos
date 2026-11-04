/**
 * SettingsKit — the row rules every Settings category follows (spec §3): save on change (no Save button), a ✓ Saved
 * beside the row, the old value back with the server's reason on failure, a scope tag on every group header (This
 * device / You / Everyone), "Changed by X · time" under Everyone rows, a lock and "Only an admin can change this"
 * for anyone else, the delayed effect said on the row, and one confirmation for destructive actions.
 */
import { useContext, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Check, Lock } from 'lucide-react';
import { matchesQuery, SCOPE_LABEL, type SettingScopeTag } from '../../lib/settingsRoute';
import { formatChangedBy } from '../../lib/settingsApi';
import { ADMIN_LOCK_NOTE } from '../../lib/myRole';
import { SettingsQuery, type RowStatus } from './settingsState';
import './settings.css';

export function Group({ title, scope, children }: { title: string; scope: SettingScopeTag; children: ReactNode }) {
  return (
    <section className="settings-group" data-settings-group={scope}>
      <h3 className="arturo-panel-section settings-group-head">
        <span>{title}</span>
        <span className="settings-scope" data-scope={scope}>{SCOPE_LABEL[scope]}</span>
      </h3>
      <div className="arturo-panel-list">{children}</div>
    </section>
  );
}

export function SettingRow({ id, label, keywords = [], control, status = { kind: 'idle' }, note, detail, changedBy, changedAt, locked = false, stacked = false }: {
  id: string; label: string; keywords?: readonly string[]; control: ReactNode; status?: RowStatus;
  note?: string | null; detail?: string | null; changedBy?: string | null; changedAt?: string | null; locked?: boolean; stacked?: boolean;
}) {
  const q = useContext(SettingsQuery);
  const [now] = useState(() => Date.now());
  if (!matchesQuery(q, label, keywords)) return null;
  const changed = formatChangedBy(changedBy ?? null, changedAt ?? null, now);
  return (
    <div className="settings-row" data-setting={id}>
      <div className={`arturo-panel-field${stacked ? ' settings-stacked' : ''}${locked ? ' is-disabled' : ''}`}>
        <span id={`setting-${id}`}>{label}</span>
        <span className="settings-control">
          {control}
          {status.kind === 'saved' && <span className="settings-saved" role="status" data-setting-saved><Check size={14} aria-hidden /> Saved</span>}
        </span>
      </div>
      {status.kind === 'error' && <p className="arturo-panel-error" role="alert" data-setting-error>{status.text}</p>}
      {locked && <p className="arturo-panel-note settings-lock" data-setting-locked><Lock size={12} aria-hidden /> {ADMIN_LOCK_NOTE}</p>}
      {note && <p className="arturo-panel-note" data-setting-note>{note}</p>}
      {detail && <p className="arturo-panel-note" data-setting-detail>{detail}</p>}
      {changed && <p className="arturo-panel-note" data-setting-changed>{changed}</p>}
    </div>
  );
}

export interface ChoiceOption<T> { value: T; label: string; disabled?: boolean; title?: string }

/** A segmented choice (a radio group of buttons): one Tab stop (the picked option, else the first that can be
 *  picked); the arrow keys move to the next or previous option and pick it, Home / End the first and last. */
export function Choice<T extends string | number>({ labelledBy, value, options, onChange, disabled = false }: {
  labelledBy: string; value: T; options: readonly ChoiceOption<T>[]; onChange: (v: T) => void; disabled?: boolean;
}) {
  const enabled = options.filter((o) => !disabled && !o.disabled);
  const stop = enabled.find((o) => o.value === value) ?? enabled[0];
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, from: T) => {
    const i = enabled.findIndex((o) => o.value === from);
    const n = enabled.length;
    const to = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? enabled[(i + 1) % n]
      : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? enabled[(i - 1 + n) % n]
      : e.key === 'Home' ? enabled[0] : e.key === 'End' ? enabled[n - 1] : undefined;
    if (!to || i < 0) return;
    e.preventDefault();
    e.currentTarget.parentElement?.querySelector<HTMLElement>(`[data-choice="${CSS.escape(String(to.value))}"]`)?.focus();
    if (to.value !== value) onChange(to.value);
  };
  return (
    <span role="radiogroup" aria-labelledby={labelledBy} className="settings-choice">
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value} title={o.title}
                disabled={disabled || o.disabled} data-choice={String(o.value)} tabIndex={o === stop ? 0 : -1}
                className={`settings-choice-opt${o.value === value ? ' is-on' : ''}`}
                onKeyDown={(e) => onKey(e, o.value)}
                onClick={() => { if (o.value !== value) onChange(o.value); }}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

/** A destructive action asks once, naming what it does. */
export function ConfirmButton({ label, consequence, onConfirm, data }: { label: string; consequence: string; onConfirm: () => void; data?: string }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className="arturo-panel-link" onClick={() => setAsking(true)} data-confirm={data}>{label}</button>;
  return (
    <span className="settings-control" role="group" aria-label={label}>
      <span className="arturo-panel-note">{consequence}</span>
      <button type="button" className="arturo-panel-link" onClick={() => setAsking(false)}>Cancel</button>
      <button type="button" className="arturo-panel-primary" onClick={() => { setAsking(false); onConfirm(); }} data-confirm-yes={data}>{label}</button>
    </span>
  );
}
