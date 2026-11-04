/**
 * PanelRows — the agent panel's row kit (components/arturo/ArturoPanel.tsx), shared with Settings: Row (a label, its
 * value, a chevron; `soon` for something not built yet), Info (read-only), Switch, LinkRow (a chevron row that runs a
 * handler: "Server settings ›"). Styles: arturo.css (.arturo-panel-*), rotationSettings.css (.rotation-switch).
 */
import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import './rotationSettings.css';

/** One 52px row: the label, its current value in grey, a chevron. A row with nothing behind it yet is disabled and
 *  says so, rather than opening an empty page. */
export function Row<T extends string>({ id, label, value, onOpen, soon }: {
  id?: T; label: string; value?: string; onOpen?: (p: T) => void; soon?: boolean;
}) {
  if (soon || !id || !onOpen) {
    return (
      <button type="button" className="arturo-panel-row" disabled>
        <span>{label}</span>
        <span className="arturo-panel-rowvalue">Coming soon</span>
      </button>
    );
  }
  return (
    <button type="button" data-panel-row={id} className="arturo-panel-row" onClick={() => onOpen(id)}>
      <span>{label}</span>
      <span className="arturo-panel-rowend">
        {value ? <span className="arturo-panel-rowvalue">{value}</span> : null}
        <ChevronRight size={18} />
      </span>
    </button>
  );
}

/** A read-only row: a label and its value, no chevron. */
export function Info({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="arturo-panel-field">
      <span>{label}</span>
      <span className="arturo-panel-rowvalue">{children}</span>
    </div>
  );
}

export function Switch({ on, labelledBy, disabled, onToggle, data }: { on: boolean; labelledBy: string; disabled: boolean; onToggle: () => void; data: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-labelledby={labelledBy} disabled={disabled} onClick={onToggle}
            className={`rotation-switch${on ? ' is-on' : ''}`} data-rotation-switch={data}>
      <span className="rotation-switch-knob" />
    </button>
  );
}

/** A chevron row that runs a handler (it leaves the panel: "Server settings ›", a link to a Settings section). */
export function LinkRow({ label, value, onClick, data }: { label: string; value?: string; onClick: () => void; data?: string }) {
  return (
    <button type="button" className="arturo-panel-row" onClick={onClick} data-panel-link={data}>
      <span>{label}</span>
      <span className="arturo-panel-rowend">
        {value ? <span className="arturo-panel-rowvalue">{value}</span> : null}
        <ChevronRight size={18} />
      </span>
    </button>
  );
}
