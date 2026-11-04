/** Settings › Voice & calls: the install's voice provider, default voice, whether new agents take calls and the daily
 *  limit (everyone, admin); the microphone, call transcript and voice colour on this device (moved from the agent
 *  panel's "On this device"). */
import { useEffect, useState } from 'react';
import { fetchVoices, type VoiceRow } from '../../../lib/arturoVoice';
import { useAgentSettings } from '../../../stores/agentSettings';
import { Choice, Group, SettingRow } from '../SettingsKit';
import { useSavedFlash } from '../settingsState';
import { ServerNumberRow, ServerSelectRow, ServerSwitchRow } from '../ServerRows';

function DefaultVoiceRow() {
  const [voices, setVoices] = useState<VoiceRow[] | null>(null);
  useEffect(() => {
    let alive = true;
    void fetchVoices().then((v) => { if (alive) setVoices(v ?? []); });
    return () => { alive = false; };
  }, []);
  return <ServerSelectRow k="voice.defaultVoice" label="Default voice" keywords={['voice', 'speak', 'gpt-live', 'sound']}
    options={(voices ?? []).map((v) => ({ value: v.id, label: v.title }))} />;
}

function MicRow() {
  const s = useAgentSettings();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="voice.micMode" label="Microphone" keywords={['microphone', 'mic', 'talk', 'hold', 'tap']} status={status}
    control={<Choice labelledBy="setting-voice.micMode" value={s.micMode}
      options={[{ value: 'hold' as const, label: 'Hold to talk' }, { value: 'toggle' as const, label: 'Tap to talk' }]}
      onChange={(v) => { s.setMicMode(v); flash({ kind: 'saved' }); }} />} />;
}

function TranscriptRow() {
  const s = useAgentSettings();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="voice.transcriptMode" label="Call transcript" keywords={['transcript', 'captions', 'spoken']} status={status}
    control={<Choice labelledBy="setting-voice.transcriptMode" value={s.transcriptMode}
      options={[{ value: 'all-at-once' as const, label: 'All at once' }, { value: 'as-spoken' as const, label: 'As spoken' }]}
      onChange={(v) => { s.setTranscriptMode(v); flash({ kind: 'saved' }); }} />} />;
}

function VoiceColourRow() {
  const s = useAgentSettings();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="voice.accentVoice" label="Voice colour" keywords={['colour', 'color', 'voice', 'accent']} status={status}
    control={<input type="color" value={s.accentVoice} aria-labelledby="setting-voice.accentVoice" className="arturo-panel-swatch"
                    onChange={(e) => { s.setAccentVoice(e.target.value); flash({ kind: 'saved' }); }} />} />;
}

export function VoiceSection() {
  return (
    <>
      <Group title="Voice for everyone" scope="everyone">
        <ServerSelectRow k="voice.provider" label="Voice provider" keywords={['provider', 'vendor', 'gpt-live', 'openai', 'hume', 'elevenlabs']}
          options={(row) => (row.choices ?? []).map((c) => ({ value: c.value, label: c.available ? c.label : `${c.label} (Not available on this install)`, disabled: !c.available }))} />
        <DefaultVoiceRow />
        <ServerSwitchRow k="voice.newAgentsTakeCalls" label="New agents take calls" keywords={['calls', 'new agents', 'phone']} />
        <ServerNumberRow k="voice.dailyLimitMin" label="Daily voice limit" suffix="min" min={1} max={1440} keywords={['limit', 'minutes', 'cap', 'usage']} />
      </Group>
      <Group title="Calls on this device" scope="device">
        <MicRow />
        <TranscriptRow />
        <VoiceColourRow />
      </Group>
    </>
  );
}
