/*
 * LookPicker — the one picker for an agent's avatar look, used in the drawer's Avatar page and in the
 * "Pick a look" popover, so there is one picker, not two.
 *
 * The large live avatar sits on top with a pencil badge; under it a card: Bot · Generate · Upload (only
 * Bot today), Reset, then the 16 shapes drawn in the current colour and the swatches, ink first. A pick
 * shows on every avatar of the agent at once (the hub) and is put back, with the reason, if the server
 * refuses it. Eye colour stays on auto: no picker for it yet.
 *
 * Styled with Arturo's own variables (arturo.css), since it lives inside Arturo's page.
 */
import { useState } from 'react';
import { Pencil } from 'lucide-react';
import { AgentAvatar } from './AgentAvatar';
import { SHAPE_KEYS, SWATCHES } from './avatarCore';
import { avatarHub, useAgentRecord, defaultLook } from './agentState';
import { pickLook, resetToDefault, shapeName } from '../../lib/avatarLooks';


export function LookPicker({ agentId, heroSize = 160, canSave, onHeroClick }: {
  agentId: string;
  heroSize?: number;
  /** Only an admin can save a look; for anyone else the picker is shown but read-only. */
  canSave: boolean;
  onHeroClick?: () => void;
}) {
  const rec = useAgentRecord(agentId);
  const [error, setError] = useState<string | null>(null);
  const current = { shape: rec.shape || defaultLook(agentId).shape, color: rec.color || 'ink' };

  const pick = (patch: { shape?: string; color?: string }) => {
    if (!canSave) return;
    if ((patch.shape && patch.shape === current.shape && rec.chosen) || (patch.color && patch.color === current.color && rec.chosen)) return;
    setError(null);
    // a pick saves the whole look, so the first pick makes the default look the saved one
    void pickLook(avatarHub, agentId, { shape: current.shape, color: current.color, eyeColor: 'auto', ...patch }).then(setError);
  };
  const reset = () => {
    if (!canSave) return;
    setError(null);
    void resetToDefault(avatarHub, agentId, defaultLook(agentId)).then(setError);
  };

  return (
    <div className="look-picker" data-look-picker={agentId}>
      <div className="look-hero la-on-panel">
        <AgentAvatar agentId={agentId} role="drawer" size={heroSize} selected showStatus={false} label={rec.name || agentId} />
        {canSave && (
          <button type="button" className="look-pencil" aria-label="Change look" onClick={onHeroClick}>
            <Pencil size={14} />
          </button>
        )}
      </div>
      <div className="look-card">
        <div className="look-head">
          <div className="look-seg" role="radiogroup" aria-label="Avatar source">
            <button type="button" role="radio" aria-checked="true" className="is-on">Bot</button>
            <button type="button" role="radio" aria-checked="false" disabled title="Coming soon" aria-describedby="look-soon">Generate</button>
            <button type="button" role="radio" aria-checked="false" disabled title="Coming soon" aria-describedby="look-soon">Upload</button>
          </div>
          <button type="button" className="look-reset" onClick={reset} disabled={!canSave || !rec.chosen}>Reset</button>
        </div>
        <p id="look-soon" className="look-soon">Generate and Upload: coming soon</p>
        <div className="look-shapes" role="radiogroup" aria-label="Shape">
          {SHAPE_KEYS.map((k) => (
            <button key={k} type="button" role="radio" aria-checked={k === current.shape} aria-label={shapeName(k)} title={shapeName(k)}
                    data-shape={k} className={`look-shape${k === current.shape ? ' is-on' : ''}`} disabled={!canSave} onClick={() => pick({ shape: k })}>
              <AgentAvatar agentId={`${agentId}:${k}`} state="idle" role="pill" size={40} shape={k} color={current.color} eyeColor="auto" reducedMotion />
            </button>
          ))}
        </div>
        <div className="look-swatches" role="radiogroup" aria-label="Colour">
          {SWATCHES.map((s) => (
            <button key={s.hex} type="button" role="radio" aria-checked={s.hex === current.color} aria-label={s.name} title={s.name}
                    data-color={s.hex} className={`look-swatch${s.hex === current.color ? ' is-on' : ''}${s.hex === 'ink' ? ' is-ink' : ''}`}
                    style={s.hex === 'ink' ? undefined : { background: s.hex }} disabled={!canSave} onClick={() => pick({ color: s.hex })} />
          ))}
        </div>
        {error && <p className="look-error" role="alert">{error}</p>}
        {!canSave && <p className="look-soon">Only an admin can change an agent's look.</p>}
      </div>
    </div>
  );
}

/** The picker in a small floating dialog (the "Pick a look" card opens it). */
export function LookPickerPopover({ agentId, open, onClose, canSave }: { agentId: string; open: boolean; onClose: () => void; canSave: boolean }) {
  if (!open) return null;
  return (
    <div className="look-pop-scrim" onClick={onClose} onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}>
      <div className="look-pop" role="dialog" aria-modal="true" aria-label="Pick a look" onClick={(e) => e.stopPropagation()}>
        <div className="look-pop-bar">
          <span className="look-pop-title">Pick a look</span>
          <button type="button" className="arturo-panel-done" onClick={onClose} autoFocus>Done</button>
        </div>
        <LookPicker agentId={agentId} heroSize={128} canSave={canSave} />
      </div>
    </div>
  );
}
