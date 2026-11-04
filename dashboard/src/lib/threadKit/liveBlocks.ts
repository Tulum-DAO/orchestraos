/**
 * liveBlocks.ts — a stable id for each block of a live reply (lib/transcriptStream.ts liveTextOf), so its reveal
 * (lib/revealStore.ts) follows it across stream frames. The server sends each frame's blocks as
 * { uuid, text }: the uuid is null until the block is finished, and landed blocks leave from the front. A block is
 * the same block as one in the last frame when it has that block's uuid, or when its words extend that block's
 * (and their uuids do not differ). Pure, apart from the small memory per agent kept by stableLiveBlocks.
 */
export interface LiveBlock { id?: string; uuid?: string | null; text: string }
export interface IdBlock { id: string; uuid: string | null; text: string }

export function assignLiveIds(prev: IdBlock[], next: LiveBlock[], newId: () => string): IdBlock[] {
  const out: IdBlock[] = [];
  let from = 0;
  for (const b of next) {
    const uuid = b.uuid ?? null;
    let k = -1;
    for (let j = from; j < prev.length; j++) {
      const p = prev[j];
      const same = !!uuid && p.uuid === uuid;
      const grows = (!p.uuid || p.uuid === uuid) && b.text.startsWith(p.text);
      if (same || grows) { k = j; break; }
    }
    if (k >= 0) { out.push({ id: prev[k].id, uuid, text: b.text }); from = k + 1; }
    else out.push({ id: newId(), uuid, text: b.text });
  }
  return out;
}

const memory = new Map<string, IdBlock[]>();
let seq = 0;

/** The blocks of `scope`'s live reply (an agent's), each with its stable id. [] forgets the last frame. */
export function stableLiveBlocks(scope: string, blocks: LiveBlock[]): IdBlock[] {
  const next = assignLiveIds(memory.get(scope) ?? [], blocks, () => `${scope}#live${++seq}`);
  if (next.length) memory.set(scope, next); else memory.delete(scope);
  return next;
}
