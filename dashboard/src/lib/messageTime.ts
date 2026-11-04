/**
 * messageTime.ts — when a message happened, as the chat says it ("No timestamps anywhere. You can't tell
 * whether 'Hello? Are you still there?' was sent 5 seconds or 5 hours ago, and that is the one question this page
 * has to answer."): clock time (local, 24h) for anything older than a minute, "just now" under it, the full local
 * date and time on hover. A missing or bad time: nothing, never a guessed time. One formatter for the time under a
 * bubble (components/chat/TranscriptCards MessageTime) and on Bubbles' thin tool lines (components/kit/ToolCard).
 */
/** The muted look of a message's time. */
export const MESSAGE_TIME_TONE = 'text-[10px] text-tone-600';

export function messageTimeLabel(ts: string | undefined, now: number = Date.now()): { label: string; title: string } | null {
  if (!ts) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  const ageS = (now - d.getTime()) / 1000;
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return { label: ageS < 60 ? 'just now' : hhmm, title: d.toLocaleString() };
}
