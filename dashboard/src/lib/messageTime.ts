/**
 * messageTime.ts — when a message happened, as the chat says it ("No timestamps anywhere. You can't tell
 * whether 'Hello? Are you still there?' was sent 5 seconds or 5 hours ago, and that is the one question this page
 * has to answer."): clock time (local, 24h) for anything older than a minute, "just now" under it, the full local
 * date and time on hover. A missing or bad time: nothing, never a guessed time. One formatter for the time under a
 * bubble (components/chat/TranscriptCards MessageTime) and on Bubbles' thin tool lines (components/kit/ToolCard).
 */
/** The muted look of a message's time. */
export const MESSAGE_TIME_TONE = 'text-px10 text-tone-600';

interface ClockParts { y: number; mo: number; d: number; h: number; mi: number }

/** A moment's calendar date and clock time in `timeZone` (the install's, Settings › General); "" or an unknown zone:
 *  this device's clock. */
function partsIn(t: Date, timeZone: string): ClockParts {
  if (timeZone) {
    try {
      const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit',
        minute: '2-digit', hourCycle: 'h23', timeZone }).formatToParts(t).map((x) => [x.type, x.value]));
      return { y: +p.year, mo: +p.month - 1, d: +p.day, h: +p.hour % 24, mi: +p.minute };
    } catch { /* an unknown zone: the device's clock */ }
  }
  return { y: t.getFullYear(), mo: t.getMonth(), d: t.getDate(), h: t.getHours(), mi: t.getMinutes() };
}
const hhmmOf = (p: ClockParts) => `${String(p.h).padStart(2, '0')}:${String(p.mi).padStart(2, '0')}`;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function messageTimeLabel(ts: string | undefined, now: number = Date.now(), timeZone = ''): { label: string; title: string } | null {
  if (!ts) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  const ageS = (now - d.getTime()) / 1000;
  const hhmm = hhmmOf(partsIn(d, timeZone));
  let title = d.toLocaleString();
  if (timeZone) {
    try { title = d.toLocaleString(undefined, { timeZone, timeZoneName: 'short' }); } catch { /* an unknown zone: the viewer's clock */ }
  }
  return { label: ageS < 60 ? 'just now' : hhmm, title };
}

/** A clock label with its day, in the same zone as the chat's times: "14:05" today, "Yesterday 14:05", "Oct 28, 14:05"
 *  this year, "Oct 28 2025" before. Result cards and the decisions history. */
export function clockWhen(at: number | null, now: number = Date.now(), timeZone = ''): string {
  if (!at) return '';
  const t = new Date(at);
  if (Number.isNaN(t.getTime())) return '';
  const p = partsIn(t, timeZone), n = partsIn(new Date(now), timeZone);
  const day = (x: ClockParts) => Date.UTC(x.y, x.mo, x.d);
  if (day(p) === day(n)) return hhmmOf(p);
  if (day(n) - day(p) === 86_400_000) return `Yesterday ${hhmmOf(p)}`;
  if (p.y === n.y) return `${MONTHS[p.mo]} ${p.d}, ${hhmmOf(p)}`;
  return `${MONTHS[p.mo]} ${p.d} ${p.y}`;
}
