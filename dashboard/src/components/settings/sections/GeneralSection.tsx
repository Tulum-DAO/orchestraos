/** Settings › General (OpenBot's layout: a label above a rounded card, each row a title, one grey line and its control).
 *  Appearance: theme, the chat style new agents start with (you, every device; under Theme), text
 *  size (a slider of named stops) with its live preview, emoji skin tone (this device). Conversations: the send
 *  shortcut, agent steps open or folded, timestamps (this device). No scope tags: one card mixes scopes, so each row's description says it in words. The install's time
 *  zone lives in its own category at the bottom of the list (sections/ServerSection.tsx). */
import { useState } from 'react';
import { useTheme } from '../../../theme/ThemeProvider';
import { APPEARANCE, type ThemeChoice } from '../../../lib/theme';
import { readTone, writeTone, SKIN_TONES, type SkinTone } from '../../../lib/emojiPicker';
import { useDeviceSetting } from '../../../lib/deviceSettings';
import { Card, Choice, PageIntro, SettingRow } from '../SettingsKit';
import { useSavedFlash } from '../settingsState';
import { ServerChoiceRow } from '../ServerRows';
import { DeviceChoiceRow, SendShortcutRow } from '../DeviceRows';
import { DisplayPreview } from './DisplayPreview';
import { TextSizeSlider } from '../TextSizeSlider';

const TONE_NAMES = ['Default', 'Light', 'Medium-light', 'Medium', 'Medium-dark', 'Dark'];

function ThemeRow() {
  const { theme, setTheme } = useTheme();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="general.appearance" label="Theme" description="Follow the system, or always use light or dark, on this device."
    keywords={['appearance', 'theme', 'dark', 'light', 'system', 'mode']} status={status}
    control={<Choice<ThemeChoice> labelledBy="setting-general.appearance" value={theme} options={APPEARANCE.map((o) => ({ value: o.id, label: o.label }))}
                     onChange={(v) => { setTheme(v); flash({ kind: 'saved' }); }} />} />;
}

/** Text size: a slider of five named stops (Small … Largest), never percentages. Each stop applies
 *  at once and is stored (lib/deviceSettings.ts), as the buttons did; the ✓ Saved sits in the row's slot beside its title. */
function TextSizeRow() {
  const [value, set] = useDeviceSetting('textScale');
  const [status, flash] = useSavedFlash();
  return <SettingRow id="display.textScale" label="Text size" description="Makes all text in the app bigger or smaller on this device." stacked
    keywords={['text', 'font', 'size', 'bigger', 'smaller', 'zoom', 'small', 'large', 'larger', 'largest']} below={<DisplayPreview />} status={status}
    control={<TextSizeSlider labelledBy="setting-display.textScale" value={value}
                             onChange={(v) => { set(v); flash({ kind: 'saved' }); }} />} />;
}

function SkinToneRow() {
  const [tone, setTone] = useState<SkinTone>(() => readTone());
  const [status, flash] = useSavedFlash();
  return <SettingRow id="general.emojiSkinTone" label="Emoji skin tone" description="The tone emoji you pick start with, on this device."
    keywords={['emoji', 'skin', 'tone', 'reactions']} status={status}
    control={<Choice<SkinTone> labelledBy="setting-general.emojiSkinTone" value={tone}
                     options={SKIN_TONES.map((t, i) => ({ value: t, label: `👋${t}`, title: TONE_NAMES[i] }))}
                     onChange={(v) => { writeTone(v); setTone(v); flash({ kind: 'saved' }); }} />} />;
}

export function GeneralSection() {
  return (
    <>
      <PageIntro>How OrchestraOS looks and behaves for you.</PageIntro>
      <Card title="Appearance">
        <ThemeRow />
        <ServerChoiceRow k="general.defaultChatStyle" label="Chat style" keywords={['chat', 'style', 'bubbles', 'modern', 'default', 'new agents']}
          description="The look new agents' chats start with. Follows you to every device; each agent can still change its own."
          options={[{ value: 'bubbles', label: 'Bubbles' }, { value: 'modern', label: 'Modern' }]} />
        <TextSizeRow />
        <SkinToneRow />
      </Card>
      <Card title="Conversations">
        <SendShortcutRow />
        <DeviceChoiceRow k="toolDetail" label="Show agent steps" keywords={['agent', 'steps', 'tool', 'calls', 'detail', 'thinking', 'commands', 'collapse', 'expand']}
          description="Whether a turn's tool calls, and the thinking between them, start open (Detailed) or folded to one line (Summary, Normal). On this device."
          options={[{ value: 'summary', label: 'Summary' }, { value: 'normal', label: 'Normal' }, { value: 'verbose', label: 'Detailed' }]} />
        <DeviceChoiceRow k="timestamps" label="Timestamps" keywords={['time', 'timestamps', 'clock', 'hover', 'off']}
          description="Times under messages and on agent steps, on this device. On a touch screen, On hover shows them always."
          options={[{ value: 'always', label: 'Always' }, { value: 'hover', label: 'On hover' }, { value: 'off', label: 'Off' }]} />
      </Card>
    </>
  );
}
