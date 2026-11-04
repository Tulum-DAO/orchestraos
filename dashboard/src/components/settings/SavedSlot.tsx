/** A row's ✓ Saved, in a slot beside its title that is always there ("the 'saved' text should not move
 *  any buttons"). The tick and word always take their room and are only shown or hidden (visibility), so showing them
 *  never moves a control, a label or the row's height. A screen reader hears "Saved" from its own status line.
 *  Styles: settings.css (.settings-saved-slot). No CSS import here, so node tests can render it. */
import { Check } from 'lucide-react';

export function SavedSlot({ on }: { on: boolean }) {
  return (
    <span className="settings-saved-slot" data-saved-slot>
      <span className={`settings-saved${on ? '' : ' is-idle'}`} aria-hidden="true" {...(on ? { 'data-setting-saved': '' } : {})}>
        <Check size={14} aria-hidden /> Saved
      </span>
      <span className="sr-only" role="status">{on ? 'Saved' : ''}</span>
    </span>
  );
}
