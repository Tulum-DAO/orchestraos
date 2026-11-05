/**
 * SettingsKit — the row rules every Settings category follows (spec §3): save on change (no Save button), a ✓ Saved
 * beside the row, the old value back with the server's reason on failure, a scope tag on every group header (This
 * device / You / Everyone), "Changed by X · time" under Everyone rows, a lock and "Only an admin can change this"
 * for anyone else, the delayed effect said on the row, and one confirmation for destructive actions.
 * A page in the OpenBot layout (General) uses Card instead of Group: a label above a rounded card, rows inside with a
 * grey description under the title (which says the scope in words) and hairlines between them.
 * The ✓ Saved sits in a slot beside the row's title that is always there (SavedSlot): showing or hiding it never moves a
 * control, a label or the row's height.
 */
import { useContext, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Lock } from 'lucide-react';
import { matchesQuery, SCOPE_LABEL, type SettingScopeTag } from '../../lib/settingsRoute';
import { formatChangedBy } from '../../lib/settingsApi';
import { ADMIN_LOCK_NOTE } from '../../lib/myRole';
import { descIdOf, RowDescription, SettingsQuery, type RowStatus } from './settingsState';
import { SavedSlot } from './SavedSlot';
import '../kit/panel.css';
import './settings.css';

export function Group({ title, scope, children }: { title: string; scope: SettingScopeTag; children: ReactNode }) {
  return (
    <section className="settings-group" data-settings-group={scope}>
      <h3 className="panel-section settings-group-head">
        <span>{title}</span>
        <span className="settings-scope" data-scope={scope}>{SCOPE_LABEL[scope]}</span>
      </h3>
      <div className="panel-list">{children}</div>
    </section>
  );
}

/** A page's one-line summary under its title (the title is the bar's). */
export function PageIntro({ children }: { children: ReactNode }) {
  return <p className="settings-page-intro" data-settings-intro>{children}</p>;
}

/** A section label above a rounded card of rows. No scope tag: each row's description says who it affects. An optional
 *  one-line description sits between the label and the card (Agents: "Agents follow these defaults…"). */
export function Card({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="settings-group settings-cardgroup" data-settings-card={title}>
      <h3 className="settings-card-label">{title}</h3>
      {description && <p className="settings-card-desc" data-settings-card-desc>{description}</p>}
      <div className="settings-card">{children}</div>
    </section>
  );
}

export function SettingRow({ id, label, description, keywords = [], control, status = { kind: 'idle' }, note, detail, changedBy, changedAt, locked = false, stacked = false, below }: {
  id: string; label: string; description?: string | null; keywords?: readonly string[]; control: ReactNode; status?: RowStatus;
  note?: string | null; detail?: string | null; changedBy?: string | null; changedAt?: string | null; locked?: boolean; stacked?: boolean;
  /** Shown under the row's title and control (Text size's live preview). */
  below?: ReactNode;
}) {
  const q = useContext(SettingsQuery);
  const [now] = useState(() => Date.now());
  if (!matchesQuery(q, label, keywords)) return null;
  const changed = formatChangedBy(changedBy ?? null, changedAt ?? null, now);
  const descId = description ? descIdOf(id) : undefined;
  return (
    <div className="settings-row" data-setting={id}>
      <div className={`panel-field${stacked ? ' settings-stacked' : ''}${locked ? ' is-disabled' : ''}`}>
        {description
          ? <span className="settings-row-text"><span className="settings-row-titleline"><span id={`setting-${id}`} className="settings-row-title">{label}</span><SavedSlot on={status.kind === 'saved'} /></span><span id={descId} className="settings-row-desc" data-setting-desc>{description}</span></span>
          : <span className="settings-row-titleline"><span id={`setting-${id}`}>{label}</span><SavedSlot on={status.kind === 'saved'} /></span>}
        <span className="settings-control">
          <RowDescription.Provider value={descId}>{control}</RowDescription.Provider>
        </span>
      </div>
      {below}
      {status.kind === 'error' && <p className="panel-error" role="alert" data-setting-error>{status.text}</p>}
      {locked && <p className="panel-note settings-lock" data-setting-locked><Lock size={12} aria-hidden /> {ADMIN_LOCK_NOTE}</p>}
      {note && <p className="panel-note" data-setting-note>{note}</p>}
      {detail && <p className="panel-note" data-setting-detail>{detail}</p>}
      {changed && <p className="panel-note" data-setting-changed>{changed}</p>}
    </div>
  );
}

export interface ChoiceOption<T> { value: T; label: string; disabled?: boolean; title?: string }

/** A segmented choice (a radio group of buttons): one Tab stop (the picked option, else the first that can be
 *  picked); the arrow keys move to the next or previous option and pick it, Home / End the first and last. A null
 *  value is "nothing chosen yet": no option is checked, and picking any one (the first too) calls onChange. */
export function Choice<T extends string | number>({ labelledBy, value, options, onChange, disabled = false }: {
  labelledBy: string; value: T | null; options: readonly ChoiceOption<T>[]; onChange: (v: T) => void; disabled?: boolean;
}) {
  const describedBy = useContext(RowDescription);
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
    <span role="radiogroup" aria-labelledby={labelledBy} aria-describedby={describedBy} className="settings-choice">
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
export function ConfirmButton({ label, confirmLabel = label, consequence, onConfirm, data, className = 'panel-link' }: {
  label: string; consequence: string; onConfirm: () => void; data?: string;
  /** The confirming button's words, when shorter than the action's ("Reset" for "Reset display settings"). */
  confirmLabel?: string; className?: string;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className={className} onClick={() => setAsking(true)} data-confirm={data}>{label}</button>;
  return (
    <span className="settings-control" role="group" aria-label={label}>
      <span className="panel-note">{consequence}</span>
      <button type="button" className="panel-link" onClick={() => setAsking(false)}>Cancel</button>
      <button type="button" className="panel-primary" onClick={() => { setAsking(false); onConfirm(); }} data-confirm-yes={data}>{confirmLabel}</button>
    </span>
  );
}
