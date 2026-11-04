/** Settings › General › Text size: the iPhone / macOS control. A small A, a native range of five stops (tick dots on the
 *  track), a large A, and the stop's name under it (Small, Default, Large, Larger, Largest), never a number. The
 *  native input gives the arrow keys, Home / End and the screen-reader slider for free; aria-valuetext is the name.
 *  It moves in whole stops, so a drag changes the value at most once per stop crossed (no per-pixel work).
 *  Styles: settings.css (.settings-textsize). No CSS import here, so node tests can render it. */
import { useContext, type CSSProperties } from 'react';
import { TEXT_SCALES, textScaleAt, textScaleIndex, textScaleName, type TextScale } from '../../lib/displaySettings';
import { RowDescription } from './settingsState';

const LAST = TEXT_SCALES.length - 1;

export function TextSizeSlider({ labelledBy, value, onChange }: {
  labelledBy: string; value: TextScale; onChange: (v: TextScale) => void;
}) {
  const describedBy = useContext(RowDescription);
  const at = textScaleIndex(value);
  const name = textScaleName(value);
  return (
    <span className="settings-textsize" data-text-size>
      <span className="settings-textsize-a is-small" aria-hidden="true">A</span>
      <span className="settings-textsize-track">
        <span className="settings-textsize-line" aria-hidden="true" />
        {TEXT_SCALES.map((t, k) => (
          <span key={t} aria-hidden="true" data-text-size-tick={k} className="settings-textsize-tick"
                style={{ '--tick': k / LAST } as CSSProperties} />
        ))}
        <input type="range" className="settings-textsize-input" min={0} max={LAST} step={1} value={at}
               aria-labelledby={labelledBy} aria-describedby={describedBy} aria-valuetext={name}
               onChange={(e) => { const v = textScaleAt(Number(e.currentTarget.value)); if (v !== value) onChange(v); }} />
      </span>
      <span className="settings-textsize-a is-large" aria-hidden="true">A</span>
      <span className="settings-textsize-foot">
        <span className="settings-textsize-name" data-text-size-name={name} aria-hidden="true">{name}</span>
      </span>
    </span>
  );
}
