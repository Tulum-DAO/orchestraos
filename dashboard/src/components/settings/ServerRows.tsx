/** Server-backed rows (You / Everyone) built on SettingRow and useServerSetting. */
import { useState, type ReactNode } from 'react';
import { Minus, Plus } from 'lucide-react';
import { Choice, SettingRow, type ChoiceOption } from './SettingsKit';
import { Switch } from '../arturo/PanelRows';
import { useServerSetting } from './useServerSetting';
import { descIdOf } from './settingsState';
import { withCurrent, type SettingRow as Row, type SelectOption } from '../../lib/settingsApi';

interface Common { k: string; label: string; keywords?: readonly string[]; description?: string;
  /** Shown under the row (Agents: "N of M use their own"). */
  below?: ReactNode }
const loading = <span className="panel-rowvalue">…</span>;
/** The control's aria-describedby: the row's description, when it has one (SettingRow gives it this id). */
const descOf = (k: string, description?: string) => (description ? descIdOf(k) : undefined);
const numText = (v: unknown) => (typeof v === 'number' ? String(v) : '');

/** A row that failed to load comes back with value null and `detail` saying why (api routes/settings.ts safeRead);
 *  when the whole GET failed there is no row, and the load error is said instead. */
function common(row: Row | undefined, loadError?: string | null) {
  return { note: row?.note, detail: row?.detail ?? (loadError ? `Couldn't load settings: ${loadError}` : null),
    changedBy: row?.scope === 'everyone' ? row.changedBy : null, changedAt: row?.changedAt };
}

export function ServerChoiceRow({ k, label, keywords, description, options }: Common & { options: readonly ChoiceOption<string>[] }) {
  const { row, status, save, locked, loadError } = useServerSetting(k);
  return <SettingRow id={k} label={label} description={description} keywords={keywords} status={status} locked={locked} {...common(row, loadError)}
    control={row ? <Choice labelledBy={`setting-${k}`} value={typeof row.value === 'string' ? row.value : ''} options={options} disabled={!row.editable} onChange={(v) => void save(v)} /> : loading} />;
}

export function ServerSelectRow({ k, label, keywords, description, options }: Common & { options: SelectOption[] | ((row: Row) => SelectOption[]) }) {
  const { row, status, save, locked, loadError } = useServerSetting(k);
  const list = row ? withCurrent(typeof options === 'function' ? options(row) : options, row.value) : [];
  return <SettingRow id={k} label={label} description={description} keywords={keywords} status={status} locked={locked} {...common(row, loadError)}
    control={row ? (
      <select className="panel-value" aria-labelledby={`setting-${k}`} aria-describedby={descOf(k, description)} value={typeof row.value === 'string' ? row.value : ''}
              disabled={!row.editable} onChange={(e) => void save(e.target.value)}>
        {list.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
      </select>
    ) : loading} />;
}

export function ServerSwitchRow({ k, label, keywords, description, below }: Common) {
  const { row, status, save, locked, loadError } = useServerSetting(k);
  return <SettingRow id={k} label={label} description={description} keywords={keywords} status={status} locked={locked} below={below} {...common(row, loadError)}
    control={row ? <Switch on={row.value === true} labelledBy={`setting-${k}`} describedBy={descOf(k, description)} disabled={!row.editable} onToggle={() => void save(!(row.value === true))} data={k} /> : loading} />;
}

export function ServerNumberRow({ k, label, keywords, description, below, min, max, suffix, stepper = false }: Common & { min: number; max: number; suffix: string; stepper?: boolean }) {
  const { row, status, flash, save, locked, loadError } = useServerSetting(k);
  // null while not being edited: the field shows the row's value, so a change from another dashboard shows; while
  // focused it keeps what is being typed (and the field is never remounted, so focus stays).
  const [draft, setDraft] = useState<string | null>(null);
  const commit = (text: string) => {
    setDraft(null);
    if (!row) return;
    const n = Number(text.trim());
    if (!Number.isInteger(n) || n < min || n > max) { flash({ kind: 'error', text: `A whole number from ${min} to ${max}` }); return; }
    if (n !== row.value) void save(n);
  };
  // No number (a failed read): nothing to step from, so the steppers are off.
  const n = typeof row?.value === 'number' ? row.value : null;
  return <SettingRow id={k} label={label} description={description} keywords={keywords} status={status} locked={locked} below={below} {...common(row, loadError)}
    control={row ? (
      <span className="settings-control">
        {/* With the stepper: one box, a filled − on the left, the number and its suffix in the middle, a filled + on the
            right (the same control as an agent's own "Compact at", RotationSettings.tsx). Without it the wrappers are
            display:contents, so the plain field lays out as before. */}
        <span className={stepper ? 'rotation-stepper' : 'settings-field'}>
          {stepper && <button type="button" className="rotation-step" aria-label="Lower" aria-describedby={descOf(k, description)} disabled={!row.editable || n === null || n <= min} onClick={() => { if (n !== null) void save(n - 1); }}><Minus size={16} strokeWidth={2.5} /></button>}
          <span className={stepper ? 'rotation-number' : 'settings-field'}>
            <input value={draft ?? numText(row.value)} inputMode="numeric" className={stepper ? undefined : 'panel-input settings-number'}
                   aria-labelledby={`setting-${k}`} aria-describedby={descOf(k, description)} disabled={!row.editable} data-setting-input={k}
                   onFocus={(e) => setDraft((d) => d ?? e.target.value)} onChange={(e) => setDraft(e.target.value)}
                   onBlur={(e) => commit(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />
            <span aria-hidden>{suffix}</span>
          </span>
          {stepper && <button type="button" className="rotation-step" aria-label="Raise" aria-describedby={descOf(k, description)} disabled={!row.editable || n === null || n >= max} onClick={() => { if (n !== null) void save(n + 1); }}><Plus size={16} strokeWidth={2.5} /></button>}
        </span>
      </span>
    ) : loading} />;
}
