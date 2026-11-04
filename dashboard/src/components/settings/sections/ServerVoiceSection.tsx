/** Settings › (Server) Voice & calls: the install's voice provider (each option with its one-line word, the voice
 *  service's own copy), default voice, whether new agents take calls and the daily voice limit. Everyone, admin: a
 *  member sees them read-only with the lock; the server enforces it (403). Moved here, unchanged, from the old Voice &
 *  calls page; the agent panel's provider rows open this category (openSettings('server-voice')). A person's own mic
 *  and its colour are Settings › Voice (VoiceSection.tsx). */
import { useEffect, useState } from 'react';
import { fetchVoices, type VoiceRow } from '../../../lib/arturoVoice';
import { providerOptions, SERVER_VOICE_INTRO } from '../../../lib/voicePage';
import { Group, PageIntro, SettingRow } from '../SettingsKit';
import { ChoiceList } from '../ChoiceList';
import { ServerNumberRow, ServerSelectRow, ServerSwitchRow } from '../ServerRows';
import { useServerSetting } from '../useServerSetting';

const PROVIDER = 'voice.provider';

/** The provider, as a list: each option's name, its one line under it, a check on the one in use. One the install
 *  can't run stays listed, greyed, saying "Not available on this install". */
function ProviderRow() {
  const { row, status, save, locked, loadError } = useServerSetting(PROVIDER);
  const options = providerOptions(row?.choices);
  return <SettingRow id={PROVIDER} label="Voice provider" keywords={['provider', 'vendor', 'gpt-live', 'openai', 'hume', 'elevenlabs']} stacked
    status={status} locked={locked} note={row?.note} detail={row?.detail ?? (loadError ? `Couldn't load settings: ${loadError}` : null)}
    changedBy={row?.scope === 'everyone' ? row.changedBy : null} changedAt={row?.changedAt}
    control={!row ? <span className="arturo-panel-rowvalue">…</span>
      // No list from the voice service (unreachable, or an older one): say so, not an empty box. The detail says why.
      : !options.length ? <span className="arturo-panel-rowvalue" data-provider-none>Couldn't load providers</span>
      : <ChoiceList labelledBy={`setting-${PROVIDER}`} value={typeof row.value === 'string' ? row.value : null}
                    options={options} disabled={!row.editable} onChange={(v) => void save(v)} />} />;
}

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

export function ServerVoiceSection() {
  return (
    <>
      <PageIntro>{SERVER_VOICE_INTRO}</PageIntro>
      <Group title="Voice for everyone" scope="everyone">
        <ProviderRow />
        <DefaultVoiceRow />
        <ServerSwitchRow k="voice.newAgentsTakeCalls" label="New agents take calls" keywords={['calls', 'new agents', 'phone']} />
        <ServerNumberRow k="voice.dailyLimitMin" label="Daily voice limit" suffix="min" min={1} max={1440} keywords={['limit', 'minutes', 'cap', 'usage']} />
      </Group>
    </>
  );
}
