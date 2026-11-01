/**
 * modelTitle.ts — an agent's model named the way the model picker names it ("Opus 5.5 · 1M"), for the places that
 * show the model without opening the picker: the composer's overflow item and the drawer's Setup rows. One reading
 * with the picker (lib/modelPicker.ts): a short id that stands for a fuller model is that model, and the context
 * size comes from the gateway's subtitle.
 */
import { canonicalId, findCurrent, isHidden, modelTitle, nameAndTag, pickerModelsFor } from './modelPicker.ts';
import type { AgentModelInfo } from './agentModel.ts';

/** Words for a model id when there is no list to look it up in: "claude-opus-5-5[1m]" → "Opus 5.5". */
function wordsFor(id: string): string {
  const m = id.toLowerCase().replace(/\[[^\]]*\]$/, '');
  const fam = m.match(/(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?/);
  if (fam) return `${fam[1][0].toUpperCase()}${fam[1].slice(1)} ${fam[2]}${fam[3] && fam[3].length <= 2 ? `.${fam[3]}` : ''}`;
  return id;
}

/**
 * Any surface's name for a model, by the picker's rules: a short id is the model it stands for ("opus" is Opus 5.5
 * at 1M), the context size is a tag ("Opus 5.5 · 1M"), and words already known (a saved label, the gateway's
 * title) are reused. The model the picker never offers (Opus 5.5 at 200k) is never called plain "Opus 5.5": an
 * agent still running it says so ("Opus 5.5 · 200K").
 */
export function modelIdTitle(providerId: string, id: string, words?: string | null, subtitle?: string): string {
  const canonical = canonicalId(providerId, id);
  // words saved in this very form ("Opus 5.5 · 1M") read like the gateway's ("Opus 5.5 (1M)")
  const said = (words ?? '').trim().replace(/\s+·\s+(\d+(?:\.\d+)?\s*[KkMm])$/, ' ($1)');
  const { name, tag } = nameAndTag(canonical, said || wordsFor(canonical), subtitle);
  return modelTitle({ name, tag: tag ?? (isHidden(providerId, canonical) ? '200K' : undefined) });
}

/** A saved brain choice's label (Arturo), named as the picker names it now, whatever words were saved with it. */
export function choiceTitle(c: { provider: string; model: string; label?: string | null }): string {
  return c.model ? modelIdTitle(c.provider, c.model, c.label && c.label !== c.provider ? c.label : null) : (c.label || c.provider);
}

export function agentModelTitle(info: AgentModelInfo | null): string | null {
  if (!info?.model) return null;
  const runtime = info.runtime ?? '';
  // Every model the agent lists, the ones it cannot switch to included: the one it runs may be one of them.
  const models = pickerModelsFor(runtime, runtime, info.models.map((m) => ({
    id: m.id, label: m.title || m.label || m.id, ...(m.subtitle ? { subtitle: m.subtitle } : {}),
    capabilities: { text: true, image: false, audio: false, video: false, context_window: null },
  })));
  const current = findCurrent(models, { providerId: runtime, modelId: info.model });
  if (current) return modelTitle(current);
  const m = info.models.find((x) => x.id === info.model);
  return modelIdTitle(runtime, info.model, m?.title || m?.label, m?.subtitle);
}
