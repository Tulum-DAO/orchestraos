/**
 * THE WORDS, per state, shared by every surface (gm msg_a855ebca, 2026-10-07). The gateway's wire
 * keeps reason:"busy" because the hold logic keys on it, so clients choose the words from
 * `state`. Shaw's Esc-on-a-menu report read "the agent is busy" for a seat that was not busy at
 * all: it held a draft, or waited on a prompt. iOS and the Quest carry these strings verbatim.
 * The stalled pair was agreed with ios-watch-dev and shipped on iOS 248.
 */
export const STATE_COPY = {
  stranded: "There's unsent text in this agent's input box — send or clear it first.",
  waiting: 'Waiting on a prompt or permission in this agent',
  stalledBefore: "Will be queued — the agent hasn't moved in a while, so your message may wait until it does.",
  stalledAfter: "Queued — the agent hasn't moved in a while, so your message may wait until it does.",
  // gm msg_fbc3b9d0: a retired seat is decommissioned on purpose, not merely "not running".
  retired: 'This agent is retired — a message would go nowhere',
  // gm msg_d8732aa5: the words follow the SERVER's send state (agent-send.ts), never "Not delivered".
  // held/busy_working: mid-turn, the CLI queues it natively.
  queuedOk: 'Queued — it will read this when its turn ends',
  // queued: the durable fallback for a pane that is down/unreachable/refusing; no turn is promised.
  queuedDurable: 'Saved to its inbox — it will be delivered when the agent can take it',
} as const;

// Dependency-free on purpose: agentSend.ts imports this, and its tests run under plain
// `node --experimental-strip-types`, which cannot resolve an extensionless import.
