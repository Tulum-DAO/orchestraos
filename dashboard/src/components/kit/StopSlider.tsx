/** StopSlider — a slider of N named stops, drawn like Settings › General › Text size (TextSizeSlider: a native range
 *  in whole stops, a tick dot per stop on the track, a round white thumb), in the picker's own tokens (panel.css
 *  .panel-stops). The native input gives role=slider, ←/→ and Home/End and the screen-reader value for free;
 *  aria-valuetext is the stop's name, never a number. Hovering the track names the stop under the pointer (title).
 *
 *  The thumb follows a drag or the arrow keys at once (a local draft); `onIndex` is called ONCE, on release (pointer
 *  up, key up, blur), and only for a stop other than `index` (lib/stopSlider.ts). `unset`: no stop is chosen (the
 *  thumb rests hollow on `index`); a release or Enter / Space there picks it. No CSS import here, so node tests can
 *  render it. */
import { useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react';
import { sliderCommit, sliderDraft, sliderMove, sliderSync, type CommitHow, type SliderDraft } from '../../lib/stopSlider';

export interface StopSliderProps {
  /** The stops' names, in order ("Low", "Medium", …). */
  stops: string[];
  index: number;
  /** What is said for the value while the thumb rests on `index` (the stop's name, or "CLI default" when unset). */
  valueText: string;
  onIndex: (i: number) => void;
  labelledBy: string;
  unset?: boolean;
  /** Data-attribute stem: data-<name>-tick on each tick, data-<name>-name on the name. */
  attr?: string;
  /** Draw the value's name before the track (it follows the thumb while it moves). */
  showName?: boolean;
  /** False: no name while the hollow thumb rests unset (something beside it already says so). */
  nameWhenUnset?: boolean;
  nameClassName?: string;
}

export function StopSlider({ stops, index, valueText, onIndex, labelledBy, unset = false, attr = 'stop', showName = false,
  nameWhenUnset = true, nameClassName }: StopSliderProps) {
  const last = Math.max(1, stops.length - 1);
  // The draft, with the props it was made for and a generation that moves on every change of those props (the
  // switch landed, it was refused and put back, "CLI default" was pressed): the thumb then shows the value given.
  const [view, setView] = useState(() => ({ draft: sliderDraft(index), index, unset, gen: 0 }));
  let at = view;
  if (view.index !== index || view.unset !== unset) {
    at = { draft: view.unset !== unset ? sliderDraft(index) : sliderSync(view.draft, view.index, index), index, unset, gen: view.gen + 1 };
    setView(at);
  }
  const draft = at.draft;
  // The draft as the last handler left it, for a release that runs before the re-render (a change and a pointer up
  // in one tick). Written and read in handlers only; one from an older generation is stale and ignored.
  const latest = useRef<{ draft: SliderDraft; gen: number } | null>(null);
  const current = (): SliderDraft => (latest.current && latest.current.gen === at.gen ? latest.current.draft : draft);
  const setDraft = (d: SliderDraft) => { latest.current = { draft: d, gen: at.gen }; setView({ ...at, draft: d }); };
  const [hoverName, setHoverName] = useState<string | null>(null);

  const commit = (how: CommitHow) => {
    const r = sliderCommit(current(), index, unset, how);
    setDraft(r.draft);
    if (r.call !== null) onIndex(r.call);
  };
  const atRest = draft.shown === index;
  const hollow = unset && atRest;
  const name = atRest ? valueText : stops[draft.shown] ?? valueText;
  // The stop under the pointer, said as the tooltip.
  const hover = (e: PointerEvent<HTMLInputElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width) return;
    const n = stops[Math.round(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * last)] ?? null;
    if (n !== hoverName) setHoverName(n);
  };
  const key = (e: KeyboardEvent<HTMLInputElement>) => {
    if (unset && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); commit('activate'); }
  };
  return (
    <>
      {showName && (nameWhenUnset || !hollow) && <span className={nameClassName} aria-hidden="true" {...{ [`data-${attr}-name`]: name }}>{name}</span>}
      <span className={`panel-stops${hollow ? ' is-unset' : ''}`}>
        <span className="panel-stops-line" aria-hidden="true" />
        {stops.map((s, k) => (
          <span key={s} aria-hidden="true" className="panel-stops-tick" {...{ [`data-${attr}-tick`]: k }}
                style={{ '--tick': k / last } as CSSProperties} />
        ))}
        <input type="range" className="panel-stops-input" min={0} max={stops.length - 1} step={1} value={draft.shown}
               aria-labelledby={labelledBy} aria-valuetext={name} title={hoverName ?? valueText}
               onPointerMove={hover} onPointerLeave={() => setHoverName(null)}
               onChange={(e) => setDraft(sliderMove(current(), Number(e.currentTarget.value)))}
               onPointerUp={() => commit('pointer')}
               onPointerCancel={() => commit('pointer')} onKeyUp={() => commit('key')} onBlur={() => commit('blur')} onKeyDown={key} />
      </span>
    </>
  );
}
