/**
 * arturoCallGroups.ts — Arturo's feed with each voice call folded into one card. It moved into the thread kit
 * (lib/threadKit/calls.ts) with the rest of the call reading; this file keeps the old import path working.
 */
export { callEndOf, groupCalls, fmtClock, transcriptText, type CallTurn, type FeedItem } from './threadKit/calls.ts';
