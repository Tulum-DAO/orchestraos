// The server's message ref for agent chat (spec "Message refs"): `<session>~<uuid>#<part>`.
//   - `<session>~<uuid>` is chat-transcript.ts cursorOf() for an item with a uuid;
//   - part = the item's index among the items that share its uuid in that session's item list (all blocks of one
//     Claude line share o.uuid), so the same block gets the same ref on every read, in any window or page.
// The SERVER stamps it as item.ref (chatWindow and the transcript-tail stream); the client never builds one.
// Items with no uuid (the client's positional n${i}) and synthetic rows get none: they have no stable identity.
const SYNTHETIC_UUID = /^(rot:|queued:|interrupted:|batch:)/;

export function refable(it: any): boolean {
  if (!it || it.kind === 'rotation' || it.kind === 'compaction') return false;
  const u = it.uuid;
  if (typeof u === 'number') return Number.isFinite(u);
  return typeof u === 'string' && u !== '' && !SYNTHETIC_UUID.test(u);
}

/** How many items before `at` (from `from`) share items[at]'s uuid. */
export function partAt(items: readonly any[], at: number, from = 0): number {
  const u = String(items[at]?.uuid);
  let n = 0;
  for (let k = from; k < at; k++) if (String(items[k]?.uuid) === u) n++;
  return n;
}

export function messageRef(session: string, uuid: string, part: number): string {
  return `${session}~${uuid}#${part}`;
}

/** Shallow copies of the refable items with `ref` set, numbering parts over THIS list; the others as they are.
 *  No session: the list itself, unstamped. */
export function stampRefs(items: any[], session: string | null): any[] {
  if (!session) return items;
  const seen = new Map<string, number>();
  return items.map((it) => {
    if (!refable(it)) return it;
    const u = String(it.uuid);
    const part = seen.get(u) ?? 0;
    seen.set(u, part + 1);
    return { ...it, ref: messageRef(session, u, part) };
  });
}
