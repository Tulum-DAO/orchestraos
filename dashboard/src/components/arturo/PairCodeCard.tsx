/**
 * PairCodeCard — a pairing code Arturo made for one of the operator's devices (pair_device).
 *
 * The brain never sees the code: the server puts it in this card only, and the card lives in the
 * page's state, never in thread history. It carries what `orchestra pair` prints with it: what the
 * device will be able to do, how to revoke it, and not to show it on a shared screen. It hides itself
 * when the code expires (congruence DEC-1791485978471942, A10).
 */
import { useEffect, useState } from 'react';
import type { PairCard } from '../../lib/arturo';

export function remainingSeconds(expiresAtMs: number, nowMs: number): number {
  return Math.max(0, Math.ceil((expiresAtMs - nowMs) / 1000));
}

export default function PairCodeCard({ card }: { card: PairCard }) {
  const [expiresAt] = useState(() => Date.now() + card.expires_in_s * 1000);
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);
  const left = remainingSeconds(expiresAt, now);
  useEffect(() => {
    if (left <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [left]);

  if (left <= 0) {
    return <div className="pair-card expired">The {card.device} code has expired. Ask me for a new one.</div>;
  }
  const mm = Math.floor(left / 60), ss = String(left % 60).padStart(2, '0');
  return (
    <div className="pair-card" role="group" aria-label={`Pairing code for your ${card.device}`}>
      <div className="pair-head">Pairing code for your {card.device} <span className="pair-ttl">{mm}:{ss}</span></div>
      <code className="pair-code">{card.code}</code>
      <button className="pair-copy" onClick={() => {
        void navigator.clipboard?.writeText(card.code).then(() => setCopied(true), () => setCopied(false));
      }}>{copied ? 'Copied' : 'Copy code'}</button>
      <div className="pair-line">{card.where} Paste it there and press Pair.</div>
      <div className="pair-line dim">This device will be able to {card.powers}. Remove it any time with <code>{card.revoke}</code>.</div>
      <div className="pair-line warn">Don&apos;t share your screen while this code is showing: it works once, for anyone, until it expires.</div>
    </div>
  );
}
