/** Settings › Voice (yours, on this device), in the OpenBot layout General uses: an intro, then cards of rows (a title,
 *  one grey line that says the scope in words, the control on the right).
 *    Dictation  Microphone (hold to talk / tap to talk) and Voice colour: the mic button in an agent's chat box
 *               (components/agent/VoiceControls.tsx reads micMode; --accent-voice colours it while it listens).
 *  Both live in this browser (localStorage orchestra.agentSettings, stores/agentSettings.ts), as before. No Call
 *  transcript row: nothing reads transcriptMode, so it is not shown (its stored value is left as it is). The
 *  install's voice rows (provider, default voice, new agents take calls, daily limit) are under Server › Voice & calls
 *  (sections/ServerVoiceSection.tsx). Words: lib/voicePage.ts. */
import { useAgentSettings } from '../../../stores/agentSettings';
import { COLOUR_DESC, MIC_DESC, SERVER_VOICE_POINTER, VOICE_INTRO } from '../../../lib/voicePage';
import { serverGroupLabel } from '../../../lib/settingsRoute';
import { useOpenSettings } from '../useOpenSettings';
import { Card, Choice, PageIntro, SettingRow } from '../SettingsKit';
import { descIdOf, useSavedFlash } from '../settingsState';

function MicRow() {
  const s = useAgentSettings();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="voice.micMode" label="Microphone" description={MIC_DESC}
    keywords={['microphone', 'mic', 'talk', 'hold', 'tap', 'dictation', 'dictate', 'speech', 'push to talk']} status={status}
    control={<Choice labelledBy="setting-voice.micMode" value={s.micMode}
      options={[{ value: 'hold' as const, label: 'Hold to talk' }, { value: 'toggle' as const, label: 'Tap to talk' }]}
      onChange={(v) => { s.setMicMode(v); flash({ kind: 'saved' }); }} />} />;
}

function VoiceColourRow() {
  const s = useAgentSettings();
  const [status, flash] = useSavedFlash();
  return <SettingRow id="voice.accentVoice" label="Voice colour" description={COLOUR_DESC}
    keywords={['colour', 'color', 'voice', 'accent', 'mic', 'microphone', 'dictation']} status={status}
    control={<input type="color" value={s.accentVoice} aria-labelledby="setting-voice.accentVoice" aria-describedby={descIdOf('voice.accentVoice')}
                    className="panel-swatch" onChange={(e) => { s.setAccentVoice(e.target.value); flash({ kind: 'saved' }); }} />} />;
}

export function VoiceSection() {
  const openSettings = useOpenSettings();
  return (
    <>
      <PageIntro>{VOICE_INTRO}</PageIntro>
      <p className="settings-page-intro" data-voice-server-link>
        {SERVER_VOICE_POINTER}{' '}
        <button type="button" className="panel-link settings-inline-link" onClick={() => openSettings('server-voice')}>
          {serverGroupLabel()} › Voice &amp; calls
        </button>.
      </p>
      <Card title="Dictation">
        <MicRow />
        <VoiceColourRow />
      </Card>
    </>
  );
}
