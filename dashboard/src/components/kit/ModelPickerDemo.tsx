/**
 * ModelPickerDemo — the model picker on its own page (/kit/model-picker), for checking it in both themes and on a
 * phone without touching a real seat: a pick here only changes this page. `?agent=<id>` reads that agent's own
 * model list (GET only), as its chat would show it.
 */
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import ModelPicker, { type ModelPick } from './ModelPicker';
import { fetchAgentModel, switchableModels, type AgentModelInfo } from '../../lib/agentModel';
import type { PickerValue } from '../../lib/modelPicker';

export default function ModelPickerDemo() {
  const [params] = useSearchParams();
  const agent = params.get('agent');
  const [info, setInfo] = useState<AgentModelInfo | null>(null);
  const [value, setValue] = useState<PickerValue | null>({ providerId: 'claude', modelId: 'claude-opus-5-5[1m]' });
  const [last, setLast] = useState<ModelPick | null>(null);
  useEffect(() => {
    if (!agent) return;
    void fetchAgentModel(agent).then((m) => {
      setInfo(m);
      if (m?.runtime && m.model) setValue({ providerId: m.runtime, modelId: m.model });
    });
  }, [agent]);
  const agentModels = info?.runtime ? { providerId: info.runtime, models: switchableModels(info) } : null;
  const onValueChange = (p: ModelPick) => { setLast(p); setValue({ providerId: p.providerId, modelId: p.modelId }); };
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Model picker</h1>
        <p className="text-sm text-tone-500">A preview: picking here changes nothing but this page.{agent ? ` Models as ${agent}'s chat lists them.` : ''}</p>
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <ModelPicker value={value} onValueChange={onValueChange} agentModels={agentModels} />
        {last && <span className="text-xs text-tone-500" data-demo-picked>Picked {last.modelLabel} ({last.providerId}:{last.modelId})</span>}
      </div>
      <section>
        <h2 className="text-sm font-medium text-tone-300 mb-2">In place (a settings page)</h2>
        <div className="max-w-sm rounded-xl border border-tone-800 bg-tone-900 max-h-[420px] overflow-hidden flex flex-col">
          <ModelPicker variant="inline" value={value} onValueChange={onValueChange} agentModels={agentModels} />
        </div>
      </section>
    </div>
  );
}
