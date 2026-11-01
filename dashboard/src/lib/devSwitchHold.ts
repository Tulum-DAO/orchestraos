/**
 * devSwitchHold.ts — the first message after Dev waits for the warm chat CLI.
 *
 * Back to chat posts POST /api/agents/:id/dev {on:false}; the API answers only once the new CLI has booted (at most
 * about 3 s, with `booted` and `settled`). A message sent before that answer would reach a CLI that is still
 * starting and lose the warm start, so a send made while the switch is pending waits for its answer, at most
 * DEV_OFF_HOLD_MS, then goes anyway. Nothing new is shown: the page's existing "switching" state covers the wait.
 */
const pending = new Map<string, Promise<unknown>>();

/** The longest a send waits for the switch's answer. */
export const DEV_OFF_HOLD_MS = 3000;

/** Record a pending switch back to chat for this seat; it clears itself when the answer arrives. */
export function holdSendsUntil<T>(agentId: string, answer: Promise<T>): Promise<T> {
  pending.set(agentId, answer);
  const clear = () => { if (pending.get(agentId) === answer) pending.delete(agentId); };
  answer.then(clear, clear);
  return answer;
}

/** Resolves when no switch is pending for this seat, its answer has arrived, or `maxMs` has passed. Never rejects. */
export async function waitForChatCli(agentId: string, maxMs = DEV_OFF_HOLD_MS): Promise<void> {
  const p = pending.get(agentId);
  if (!p) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  await Promise.race([
    p.then(() => undefined, () => undefined),
    new Promise<void>((r) => { timer = setTimeout(r, maxMs); }),
  ]);
  if (timer) clearTimeout(timer);
}
