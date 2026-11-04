/** "This device" rows (browser storage, lib/deviceSettings.ts), shared by Settings › General and › Display. */
import { useSyncExternalStore, type ReactNode } from 'react';
import { useDeviceSetting } from '../../lib/deviceSettings';
import type { Display, SendShortcut } from '../../lib/displaySettings';
import { isMacPlatform } from '../../lib/settingsRoute';
import { FINE_POINTER_QUERY, hasFinePointer, sendShortcutNote } from '../../lib/composerGate';
import { Choice, SettingRow, type ChoiceOption } from './SettingsKit';
import { useSavedFlash } from './settingsState';

type ChoiceKey = Exclude<keyof Display, 'sidebarWidth' | 'sendShortcut'>;

export function DeviceChoiceRow<K extends ChoiceKey>({ k, label, description, keywords, options, note, stacked, below }: {
  k: K; label: string; description?: string; keywords: string[]; options: readonly ChoiceOption<Display[K]>[]; note?: string; stacked?: boolean; below?: ReactNode;
}) {
  const [value, set] = useDeviceSetting(k);
  const [status, flash] = useSavedFlash();
  return <SettingRow id={`display.${k}`} label={label} description={description} keywords={keywords} status={status} note={note} stacked={stacked} below={below}
    control={<Choice<Display[K]> labelledBy={`setting-display.${k}`} value={value} options={options}
                     onChange={(v) => { set(v); flash({ kind: 'saved' }); }} />} />;
}

const subscribeFine = (cb: () => void) => { const mq = window.matchMedia(FINE_POINTER_QUERY); mq.addEventListener('change', cb); return () => mq.removeEventListener('change', cb); };

/** Send shortcut. Shown only where the device has a fine pointer somewhere (hasFinePointer, the same test the
 *  composers use, lib/composerGate.ts): on a touch-only device an on-screen keyboard has no ⌘ or Ctrl and the
 *  composers keep their own Enter, so a row that changes nothing would only mislead. Unset, neither option is
 *  checked: the composers differ until a choice is made, and picking either one stores it. */
export function SendShortcutRow() {
  const fine = useSyncExternalStore(subscribeFine, hasFinePointer, () => true);
  const [value, set] = useDeviceSetting('sendShortcut');
  const [status, flash] = useSavedFlash();
  if (!fine) return null;
  const mod = typeof navigator !== 'undefined' && isMacPlatform(navigator.platform) ? '⌘' : 'Ctrl';
  const options: ChoiceOption<SendShortcut>[] = [
    { value: 'enter', label: 'Enter to send', title: 'Shift+Enter starts a new line' },
    { value: 'mod-enter', label: `${mod}+Enter to send`, title: 'Enter starts a new line' },
  ];
  return <SettingRow id="general.sendShortcut" label="Send shortcut" description="Choose the keys that send a message on this device."
    keywords={['send', 'enter', 'return', 'shortcut', 'keyboard', 'cmd', 'ctrl', 'command', 'new line', 'newline']} status={status}
    note={sendShortcutNote(value)}
    control={<Choice<SendShortcut> labelledBy="setting-general.sendShortcut" value={value} options={options}
                     onChange={(v) => { set(v); flash({ kind: 'saved' }); }} />} />;
}
