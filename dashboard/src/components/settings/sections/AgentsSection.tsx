/** Settings › Agents: defaults for new agents (everyone, admin). Each agent's own settings stay in its panel. */
import { useState } from 'react';
import ModelPicker from '../../kit/ModelPicker';
import { COMPACT_MAX, COMPACT_MIN } from '../../../lib/seatSettings';
import { Group, SettingRow } from '../SettingsKit';
import { ServerNumberRow, ServerSwitchRow } from '../ServerRows';
import { useServerSetting } from '../useServerSetting';
import { useServerSettings } from '../../../stores/serverSettings';
import { mergeDefaultModel } from '../../../lib/settingsApi';

function DefaultModelRow() {
  const { row, status, save, locked, loadError } = useServerSetting('agents.defaultModel');
  const models = (row?.value && typeof row.value === 'object' ? row.value : {}) as Record<string, string>;
  const [runtime, setRuntime] = useState('claude');
  return <SettingRow id="agents.defaultModel" label="Default model for new agents" keywords={['model', 'runtime', 'claude', 'codex', 'gemini', 'grok']}
    stacked status={status} locked={locked} note={row?.note} detail={row?.detail ?? (loadError ? `Couldn't load settings: ${loadError}` : null)}
    changedBy={row?.changedBy} changedAt={row?.changedAt}
    control={row?.editable
      ? <ModelPicker variant="inline" value={{ providerId: runtime, modelId: models[runtime] ?? '' }}
          onValueChange={(p) => { if (!(p.providerId in models)) return; setRuntime(p.providerId); void save({ runtime: p.providerId, model: p.modelId }, mergeDefaultModel(models, p.providerId, p.modelId)); }} />
      : <span className="arturo-panel-rowvalue">{Object.entries(models).map(([r, m]) => `${r}: ${m}`).join(' · ') || '…'}</span>} />;
}

function SafetyLimitsRow() {
  const row = useServerSettings((s) => s.rows['agents.safetyLimits']);
  const loadError = useServerSettings((s) => s.error);
  const v = row?.value as { forced_compaction_pct?: number; context_ceiling_pct?: number } | null | undefined;
  return <SettingRow id="agents.safetyLimits" label="Safety limits" keywords={['safety', 'limit', 'guard', 'ceiling', 'context']}
    note="Fixed: these keep agents from running out of room." detail={row?.detail ?? (!row && loadError ? `Couldn't load settings: ${loadError}` : null)}
    control={<span className="arturo-panel-rowvalue">{v && typeof v === 'object' ? `Forced compaction at ${v.forced_compaction_pct}% · context ceiling ${v.context_ceiling_pct}%` : '…'}</span>} />;
}

export function AgentsSection() {
  return (
    <Group title="Defaults for new agents" scope="everyone">
      <DefaultModelRow />
      <ServerSwitchRow k="agents.autoCompact" label="Auto-compact" keywords={['compact', 'context', 'rotation']} />
      <ServerNumberRow k="agents.compactAt" label="Compact at" suffix="%" min={COMPACT_MIN} max={COMPACT_MAX} stepper keywords={['compact', 'percent', 'context']} />
      <ServerSwitchRow k="agents.showCompaction" label="Show compaction messages" keywords={['compact', 'messages', 'notice']} />
      <SafetyLimitsRow />
    </Group>
  );
}
