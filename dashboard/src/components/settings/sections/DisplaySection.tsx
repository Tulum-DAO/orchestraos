/** Settings › Display (this device), in the OpenBot layout General uses: an intro, the live preview, a Layout card
 *  (spacing, message width, sidebar width) and a Motion card, each row a title, one grey line that says it is on this
 *  device, and its control; then a quiet "Reset display settings" at the bottom. Every change applies at once, with
 *  no reload; the values, keys and effects are as before (lib/displaySettings.ts). Text size, agent steps and
 *  timestamps are on General.
 *
 *  Per row: Spacing, Message width and Motion keep their named choices (three discrete values each). Sidebar width is
 *  continuous (a drag, 200–360), so it keeps the drag and says what it is in words: Automatic (follows text size) or
 *  Custom, with "Use automatic" to go back; never a px number. Message width and Sidebar width show only on desktop: a
 *  phone's messages are always 85% of the screen and its sidebar is a drawer sized by text size, so a row there would
 *  change nothing (as the send shortcut row on a touch screen). */
import { useContext } from 'react';
import { useDeviceSetting, resetDisplay } from '../../../lib/deviceSettings';
import { matchesQuery } from '../../../lib/settingsRoute';
import { Card, ConfirmButton, PageIntro, SettingRow } from '../SettingsKit';
import { SettingsQuery, useSavedFlash } from '../settingsState';
import { SavedSlot } from '../SavedSlot';
import { DeviceChoiceRow } from '../DeviceRows';
import { DisplayPreview } from './DisplayPreview';

function SidebarWidthRow() {
  const [width, set] = useDeviceSetting('sidebarWidth');
  const [status, flash] = useSavedFlash();
  return <SettingRow id="display.sidebarWidth" label="Sidebar width" keywords={['sidebar', 'width', 'drag', 'narrow', 'wide', 'automatic', 'custom']} status={status}
    description="Drag the sidebar's edge to make it wider or narrower on this device. Automatic follows the text size; double-click the edge to go back."
    control={<>
      <span className="panel-rowvalue" data-sidebar-width={width ?? 'auto'}>{width === null ? 'Automatic' : 'Custom'}</span>
      {width !== null && <button type="button" className="panel-link" onClick={() => { set(null); flash({ kind: 'saved' }); }}>Use automatic</button>}
    </>} />;
}

/** What Reset puts back is DISPLAY_RESET_KEYS (lib/displaySettings.ts): only this page's rows. */
const RESET_WHAT = 'Spacing, message width, sidebar width and motion go back to the defaults on this device.';

/** A quiet text button under the cards, asking once and naming what it resets. */
function ResetDisplay() {
  const q = useContext(SettingsQuery);
  const [status, flash] = useSavedFlash();
  if (!matchesQuery(q, 'Reset display settings', ['reset', 'default', 'display'])) return null;
  return (
    <div className="settings-page-action" data-setting="display.reset">
      <ConfirmButton label="Reset display settings" confirmLabel="Reset" data="display-reset" consequence={RESET_WHAT}
                     onConfirm={() => { resetDisplay(); flash({ kind: 'saved' }); }} />
      <SavedSlot on={status.kind === 'saved'} />
    </div>
  );
}

export function DisplaySection({ desktop }: { desktop: boolean }) {
  return (
    <>
      <PageIntro>Spacing, widths and motion on this device.</PageIntro>
      <DisplayPreview />
      <Card title="Layout">
        <DeviceChoiceRow k="spacing" label="Spacing" keywords={['spacing', 'density', 'compact', 'roomy', 'comfortable']}
          description="The room between messages and between sidebar rows, on this device."
          options={[{ value: 'compact', label: 'Compact' }, { value: 'comfortable', label: 'Comfortable' }, { value: 'roomy', label: 'Roomy' }]} />
        {desktop && <DeviceChoiceRow k="messageWidth" label="Message width" keywords={['message', 'width', 'bubble', 'column', 'narrow', 'wide']}
          description="How wide messages and the chat column may grow on a wide screen, on this device."
          options={[{ value: 'narrow', label: 'Narrow' }, { value: 'default', label: 'Default' }, { value: 'wide', label: 'Wide' }]} />}
        {desktop && <SidebarWidthRow />}
      </Card>
      <Card title="Motion">
        <DeviceChoiceRow k="motion" label="Animations" keywords={['motion', 'animation', 'animations', 'reduce', 'reduced', 'fade', 'system']}
          description="The word fade, follow-scroll, avatar gestures and pop-ins, on this device. Reduced shows everything at once."
          options={[{ value: 'system', label: 'Follow system' }, { value: 'reduced', label: 'Reduced' }, { value: 'full', label: 'Full' }]} />
      </Card>
      <ResetDisplay />
    </>
  );
}
