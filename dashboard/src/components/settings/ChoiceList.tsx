/** A list of choices, one per line, each with its own one-line description under its name (OpenBot's provider list):
 *  a radio group with one Tab stop (the picked option, else the first that can be picked); the arrow keys move focus to
 *  the next or previous option, Home / End to the first and last, and only Space / Enter or a click picks one: every
 *  pick saves an install-wide row at once (Settings › Server › Voice & calls), so walking the list must not. A disabled option stays listed, greyed, its
 *  description saying why. Used where the options need a word each (Settings › Server › Voice & calls › Voice provider).
 *  Styles: settings.css (.settings-choicelist). No CSS import here, so node tests can render it. */
import { useContext, type KeyboardEvent } from 'react';
import { Check } from 'lucide-react';
import { choiceListTarget, RowDescription } from './settingsState';

export interface ChoiceListOption { value: string; label: string; description?: string | null; disabled?: boolean }

export function ChoiceList({ labelledBy, value, options, onChange, disabled = false }: {
  labelledBy: string; value: string | null; options: readonly ChoiceListOption[]; onChange: (v: string) => void; disabled?: boolean;
}) {
  const describedBy = useContext(RowDescription);
  const enabled = options.filter((o) => !disabled && !o.disabled).map((o) => o.value);
  const stop = enabled.includes(value ?? '') ? value : enabled[0];
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, from: string) => {
    const to = choiceListTarget(enabled, from, e.key);
    if (to === undefined) return;
    e.preventDefault();
    // Focus only; the button's own Space / Enter (a click) picks.
    e.currentTarget.parentElement?.querySelector<HTMLElement>(`[data-choice="${CSS.escape(to)}"]`)?.focus();
  };
  return (
    <span role="radiogroup" aria-labelledby={labelledBy} aria-describedby={describedBy} className="settings-choicelist">
      {options.map((o) => {
        const on = o.value === value;
        const descId = o.description ? `${labelledBy}-opt-${o.value}` : undefined;
        return (
          <button key={o.value} type="button" role="radio" aria-checked={on} aria-describedby={descId}
                  disabled={disabled || o.disabled} data-choice={o.value} tabIndex={o.value === stop ? 0 : -1}
                  className={`settings-choicelist-opt${on ? ' is-on' : ''}`}
                  onKeyDown={(e) => onKey(e, o.value)}
                  onClick={() => { if (!on) onChange(o.value); }}>
            <span className="settings-choicelist-text">
              <span className="settings-choicelist-label">{o.label}</span>
              {o.description && <span id={descId} className="settings-row-desc">{o.description}</span>}
            </span>
            <span className="settings-choicelist-check" aria-hidden>{on && <Check size={18} />}</span>
          </button>
        );
      })}
    </span>
  );
}
