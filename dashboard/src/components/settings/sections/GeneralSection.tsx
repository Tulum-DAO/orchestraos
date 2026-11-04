/** Settings › General: Appearance and emoji skin tone (this device), the default chat style (you), the install's time
 *  zone (everyone, admin). */
import { useState } from 'react';
import { useTheme } from '../../../theme/ThemeProvider';
import { APPEARANCE, type ThemeChoice } from '../../../lib/theme';
import { readTone, writeTone, SKIN_TONES, type SkinTone } from '../../../lib/emojiPicker';
import { timeZoneOptions } from '../../../lib/settingsApi';
import { Choice, Group, SettingRow } from '../SettingsKit';
import { useSavedFlash } from '../settingsState';
import { ServerChoiceRow, ServerSelectRow } from '../ServerRows';

const TONE_NAMES = ['Default', 'Light', 'Medium-light', 'Medium', 'Medium-dark', 'Dark'];

function AppearanceRow() {
  const { theme, setTheme } = useTheme();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="general.appearance" label="Appearance" keywords={['theme', 'dark', 'light', 'system', 'mode']} status={status}
    control={<Choice<ThemeChoice> labelledBy="setting-general.appearance" value={theme} options={APPEARANCE.map((o) => ({ value: o.id, label: o.label }))}
                     onChange={(v) => { setTheme(v); flash({ kind: 'saved' }); }} />} />;
}

function SkinToneRow() {
  const [tone, setTone] = useState<SkinTone>(() => readTone());
  const [status, flash] = useSavedFlash();
  return <SettingRow id="general.emojiSkinTone" label="Emoji skin tone" keywords={['emoji', 'skin', 'tone', 'reactions']} status={status}
    control={<Choice<SkinTone> labelledBy="setting-general.emojiSkinTone" value={tone}
                     options={SKIN_TONES.map((t, i) => ({ value: t, label: `👋${t}`, title: TONE_NAMES[i] }))}
                     onChange={(v) => { writeTone(v); setTone(v); flash({ kind: 'saved' }); }} />} />;
}

export function GeneralSection() {
  return (
    <>
      <Group title="Look" scope="device">
        <AppearanceRow />
        <SkinToneRow />
      </Group>
      <Group title="Your chats" scope="user">
        <ServerChoiceRow k="general.defaultChatStyle" label="Default chat style for new agents" keywords={['chat', 'style', 'bubbles', 'modern']}
          options={[{ value: 'bubbles', label: 'Bubbles' }, { value: 'modern', label: 'Modern' }]} />
      </Group>
      <Group title="Install" scope="everyone">
        <ServerSelectRow k="general.timeZone" label="Time zone (times shown in the app)" keywords={['time', 'zone', 'clock', 'timezone']}
          options={(row) => timeZoneOptions(typeof row.value === 'string' ? row.value : '')} />
      </Group>
    </>
  );
}
