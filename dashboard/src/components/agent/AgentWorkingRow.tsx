/**
 * AgentWorkingRow — one quiet line above the agent page's composer: the agent's own avatar and a short phrase
 * (the look's numbers are in lib/workingRowLook.ts).
 *
 *   working   the face with its bob, and the step (row.text) in grey with a lighter band sweeping across it
 *   typing    "Typing…" (the reply is being written): the face itself melts into three dots (avatarCore MELT, the
 *             same melt as the call bar's thinking), whose brightness then runs left to right; back to tools, the
 *             dots reform into the face and the step returns
 *   others    needs input, queued, stopped / crashed / offline (with Resume), retired, the feed down or updating:
 *             the avatar and the state's own words and colour, no shimmer
 *   idle      no row at all
 *
 * No name, spinner, dot, timer, pill, border or background (the dock's solid ground is behind it). Everything is sized
 * from the avatar size A and the text size; the text never moves when the avatar melts (its box stays A).
 *
 * Which truth it shows comes from lib/agentWorkingRow.ts, the same source as the sidebar dot; the face is written to
 * the avatar hub too, so it is right on a phone whose sidebar is closed.
 */
import { useEffect, useState, type CSSProperties } from 'react';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { avatarHub, ensureAgent, useAgentRecord } from '../avatar/agentState';
import { LAYOUT, STATES, type AvatarState } from '../avatar/avatarCore';
import { avatarStateFor } from '../../lib/avatarStatus';
import { dotState, rowShown, workingRowFor, type RowTone, type WorkingRowInput } from '../../lib/agentWorkingRow';
import { isDegraded } from '../../lib/feedLiveness';
import { ROW_GAP_A, bobScaleFor, canvasSizeFor, workingRowSizes } from '../../lib/workingRowLook';
import { useAgentTyping } from '../../stores/agentTyping';
import './agentWorking.css';

/** The words' colour for the states that are not work (as the old line had them). */
const TONE: Record<RowTone, string> = {
  working: '',
  needs: 'text-tone-amber-300',
  queued: 'text-[#BF5AF2]',
  down: 'text-tone-400',
  idle: 'text-tone-green-400',
  muted: 'text-tone-500',
};

export const TYPING_PHRASE = 'Typing…';

export function AgentWorkingRow({ agentId, name, onResume, avatarSize, textSize, typing: typingProp, ...input }: WorkingRowInput & {
  agentId: string;
  name: string;
  onResume?: () => void;
  /** The row's avatar size A and text size, px (default: lib/workingRowLook workingRowSizes, today 24 and 13). */
  avatarSize?: number;
  textSize?: number;
  /** Writing its reply now (default: from its open chat stream). */
  typing?: boolean;
}) {
  const row = workingRowFor(input);
  const streamTyping = useAgentTyping((s) => !!s.byAgent[agentId]);
  const typing = typingProp ?? streamTyping;
  const rec = useAgentRecord(agentId);
  const { state, menu } = dotState(input);
  const face = avatarStateFor(state, menu, !!input.feed && isDegraded(input.feed));
  useEffect(() => {
    const prev = avatarHub.get(agentId) ?? ensureAgent(agentId);
    if (prev.status !== face) avatarHub.set(agentId, { status: face });
  }, [agentId, face]);

  // Writing its reply wins over a working step (and over a status that has not caught up yet); a state that needs
  // the person, or a down seat, keeps its own line.
  const busy = row.tone === 'working' || row.kind === 'idle' || row.kind === 'idleStep' || row.kind === 'unknown';
  const isTyping = typing && busy;
  const shown = isTyping || rowShown(row);
  const [sizes] = useState(workingRowSizes);
  const A = avatarSize ?? sizes.avatar;
  const T = textSize ?? sizes.text;
  const S = canvasSizeFor(A, LAYOUT.u);
  const avatarState: AvatarState = isTyping ? 'thinking' : row.tone === 'working' ? 'working' : face;
  if (!shown) return null;
  const working = isTyping || row.tone === 'working';
  const phrase = isTyping ? TYPING_PHRASE : row.text;
  const vars = { '--row-a': `${A}px`, '--row-text': `${T}px`, '--row-gap': `${ROW_GAP_A * A}px` } as CSSProperties;
  return (
    // Not a bar the floating Arturo button lifts over: it would drop again the moment the row goes (a layout shift).
    // On narrow screens the row keeps clear of the button's corner instead (agentWorking.css).
    <div className="agent-working" role="status" aria-live="polite" data-row-kind={isTyping ? 'typing' : row.kind} data-row-state={row.state ?? ''}
         style={vars}>
      <span className="agent-working-face" aria-hidden>
        <AgentAvatar agentId={agentId} role="activity" size={S} state={avatarState} label={name} provider={null}
                     shape={rec.shape} color={rec.color} eyeColor={rec.eyeColor} sleepAfterMs={0}
                     bob={working ? bobScaleFor(STATES.working.bob) : 1}
                     style={{ position: 'absolute', left: (A - S) / 2, top: (A - S) / 2 }} />
      </span>
      <span className="sr-only">{name}: </span>
      <span className={`agent-working-phrase${working ? ' is-shimmer' : ` ${TONE[row.tone]}`}`} data-phrase>
        {phrase}
        {working && <span className="agent-shimmer-win" aria-hidden><span className="agent-shimmer-copy">{phrase}</span></span>}
      </span>
      {/* NOT offered for a RETIRED seat: retired is a generation somebody deliberately ended (offersResume). */}
      {row.resume && onResume && (
        <button type="button" className="agent-working-resume" onClick={onResume}>Resume</button>
      )}
    </div>
  );
}
