/** Settings › Display (this device): text size, spacing, message width, sidebar width, tool call detail, timestamps,
 *  motion, and a reset, under a live preview. Every change applies at once, with no reload. */
import { useDeviceSetting, useDisplay, resetDisplay } from '../../../lib/deviceSettings';
import { defaultSidebarWidth, TEXT_SCALES, type Display } from '../../../lib/displaySettings';
import { Choice, ConfirmButton, Group, SettingRow, type ChoiceOption } from '../SettingsKit';
import { useSavedFlash } from '../settingsState';
import { DisplayPreview } from './DisplayPreview';

type ChoiceKey = Exclude<keyof Display, 'sidebarWidth'>;

function DeviceChoiceRow<K extends ChoiceKey>({ k, label, keywords, options, note, stacked }: {
  k: K; label: string; keywords: string[]; options: readonly ChoiceOption<Display[K]>[]; note?: string; stacked?: boolean;
}) {
  const [value, set] = useDeviceSetting(k);
  const [status, flash] = useSavedFlash();
  return <SettingRow id={`display.${k}`} label={label} keywords={keywords} status={status} note={note} stacked={stacked}
    control={<Choice<Display[K]> labelledBy={`setting-display.${k}`} value={value} options={options}
                     onChange={(v) => { set(v); flash({ kind: 'saved' }); }} />} />;
}

function SidebarWidthRow() {
  const [width, set] = useDeviceSetting('sidebarWidth');
  const { textScale } = useDisplay();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="display.sidebarWidth" label="Sidebar width" keywords={['sidebar', 'width', 'drag', 'narrow', 'wide']} status={status}
    note="Drag the sidebar's edge to change it; double-click the edge to reset."
    control={<>
      <span className="arturo-panel-rowvalue" data-sidebar-width>{width ?? defaultSidebarWidth(textScale)} px</span>
      {width !== null && <button type="button" className="arturo-panel-link" onClick={() => { set(null); flash({ kind: 'saved' }); }}>Reset</button>}
    </>} />;
}

function ResetRow() {
  const [status, flash] = useSavedFlash();
  return <SettingRow id="display.reset" label="Reset display settings" keywords={['reset', 'default']} status={status}
    control={<ConfirmButton label="Reset" data="display-reset"
                            consequence="Text size, spacing, widths, timestamps and motion go back to the defaults on this device."
                            onConfirm={() => { resetDisplay(); flash({ kind: 'saved' }); }} />} />;
}

export function DisplaySection({ desktop }: { desktop: boolean }) {
  return (
    <>
      <DisplayPreview />
      <Group title="Display" scope="device">
        <DeviceChoiceRow k="textScale" label="Text size" keywords={['text', 'font', 'size', 'bigger', 'smaller', 'zoom']} stacked
          options={TEXT_SCALES.map((t) => ({ value: t, label: `${Math.round(t * 100)}%` }))} />
        <DeviceChoiceRow k="spacing" label="Spacing" keywords={['spacing', 'density', 'compact', 'roomy']}
          options={[{ value: 'compact', label: 'Compact' }, { value: 'comfortable', label: 'Comfortable' }, { value: 'roomy', label: 'Roomy' }]} />
        {desktop && <DeviceChoiceRow k="messageWidth" label="Message width" keywords={['message', 'width', 'bubble', 'column']}
          options={[{ value: 'narrow', label: 'Narrow' }, { value: 'default', label: 'Default' }, { value: 'wide', label: 'Wide' }]} />}
        {desktop && <SidebarWidthRow />}
        <DeviceChoiceRow k="toolDetail" label="Tool call detail" keywords={['tool', 'calls', 'detail', 'commands']}
          options={[{ value: 'summary', label: 'Summary' }, { value: 'normal', label: 'Normal' }, { value: 'verbose', label: 'Detailed' }]} />
        <DeviceChoiceRow k="timestamps" label="Timestamps" keywords={['time', 'timestamps', 'clock', 'hover']}
          options={[{ value: 'always', label: 'Always' }, { value: 'hover', label: 'On hover' }]} />
        <DeviceChoiceRow k="motion" label="Motion" keywords={['motion', 'animation', 'reduce', 'fade']} stacked
          note="One switch for the word fade, follow-scroll, avatar gestures and pop-in animations. Reduced shows everything at once."
          options={[{ value: 'system', label: 'Follow system' }, { value: 'reduced', label: 'Reduced' }, { value: 'full', label: 'Full' }]} />
        <ResetRow />
      </Group>
    </>
  );
}
