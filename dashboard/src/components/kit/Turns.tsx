/**
 * Turns — a person's bubble and an agent's words exactly as the agent chat draws them, in its skin (lib/chatSkin.ts):
 * used by the thread itself and by an agent's call transcripts, so a call reads like the chat around it.
 */
import type { ReactNode } from 'react';
import { BUBBLES_PARAGRAPH, BUBBLES_USER, PARAGRAPH, assistantTurnClass } from '../../lib/chatSkin';

type TurnSkin = 'bubbles' | 'modern';

/** The person's bubble (its contents: the words with refs, or a live reveal). */
export function UserWords({ skin, children }: { skin: TurnSkin; children: ReactNode }) {
  return skin === 'bubbles'
    ? <div className={BUBBLES_USER} style={{ overflowWrap: 'anywhere' }} data-user-words="bubbles">{children}</div>
    : <div className="bubble-user" style={{ overflowWrap: 'anywhere' }} data-user-words="modern">{children}</div>;
}

/** A person's turn: the bubble at the right, as the thread lines it up. */
export function UserTurn({ skin, children, label }: { skin: TurnSkin; children: ReactNode; label?: ReactNode }) {
  return skin === 'bubbles'
    ? <div className="flex flex-col items-end">{label}<UserWords skin="bubbles">{children}</UserWords></div>
    : <div className="turn-user">{label}<UserWords skin="modern">{children}</UserWords></div>;
}

/** An agent's turn: `words` is the formatted reply (kit MessageText) or, live, a plain run of words (`plain`), drawn in
 *  the paragraph style the reply would have. */
export function AgentTurn({ skin, children, plain = false, label }: { skin: TurnSkin; children: ReactNode; plain?: boolean; label?: ReactNode }) {
  return (
    <div className={assistantTurnClass(skin)}>
      {label}
      {!plain ? children : skin === 'bubbles' ? <p className={BUBBLES_PARAGRAPH}>{children}</p> : <div className="txt"><p className={PARAGRAPH}>{children}</p></div>}
    </div>
  );
}
