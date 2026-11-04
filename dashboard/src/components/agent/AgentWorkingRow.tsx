/**
 * AgentWorkingRow — one quiet line above the agent page's composer: the agent's own avatar and a short phrase
 * (the look's numbers are in lib/workingRowLook.ts).
 *
 *   working   the face held still, and the step (row.text) in grey with a lighter band sweeping across it
 *   typing    "Typing…" (the reply is being written): the face itself melts into three dots (avatarCore MELT, the
 *             same melt as the call bar's thinking), whose brightness then runs left to right; back to tools, the
 *             dots reform into the face and the step returns
 *   otherwise no row at all: it shows ONLY while the agent is actively working (lib/agentWorkingRow rowShown)
 *
 * No name, spinner, dot, timer, pill, border or background (the dock's solid ground is behind it). Everything is sized
 * from the avatar size A and the text size; the text never moves when the avatar melts (its box stays A).
 *
 * Which truth it shows comes from lib/agentWorkingRow.ts, the same source as the sidebar dot; the face is written to
 * the avatar hub too, so it is right on a phone whose sidebar is closed. WorkingRowView is the line itself, shared with
 * Arturo's page (pages/ArturoHome.tsx), so every agent, Arturo included, works the same way.
 */
import { useEffect, useState, type CSSProperties } from 'react';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { avatarHub, ensureAgent, useAgentRecord } from '../avatar/agentState';
import { LAYOUT, type AvatarState } from '../avatar/avatarCore';
import { activityFor, type Activity } from '../avatar/avatarGesture';
import { avatarStateFor } from '../../lib/avatarStatus';
import { dotState, rowShown, workingRowFor, type WorkingRowInput } from '../../lib/agentWorkingRow';
import { isDegraded } from '../../lib/feedLiveness';
import { ROW_GAP_A, canvasSizeFor, workingRowSizes } from '../../lib/workingRowLook';
import { useAgentTyping } from '../../stores/agentTyping';
import { useDisplay, useTextAvatar } from '../../lib/deviceSettings';
import './agentWorking.css';

export const TYPING_PHRASE = 'Typing…';

/** The line: the agent's own avatar (still while working, melting into dots while typing) and the shimmering phrase. */
export function WorkingRowView({ agentId, name, phrase, typing, activity, avatarSize, textSize, avatarId, className }: {
  agentId: string;
  name: string;
  phrase: string;
  typing: boolean;
  /** What the agent is doing (its live step, avatarGesture activityFor): the working gestures fit it. */
  activity?: Activity;
  avatarSize?: number;
  textSize?: number;
  /** The avatar instance's id (default: the agent's own, so it shares the sidebar's model). */
  avatarId?: string;
  className?: string;
}) {
  const rec = useAgentRecord(agentId);
  const [sizes] = useState(workingRowSizes);
  // Beside text, so it follows text size (Settings › Display): the avatar by the avatar rule, the phrase by the factor.
  const scaledA = useTextAvatar(sizes.avatar);
  const { textScale } = useDisplay();
  const A = avatarSize ?? scaledA;
  const T = textSize ?? Math.round(sizes.text * textScale * 10) / 10;
  const S = canvasSizeFor(A, LAYOUT.u);
  const avatarState: AvatarState = typing ? 'thinking' : 'working';
  const shownPhrase = typing ? TYPING_PHRASE : phrase;
  const vars = { '--row-a': `${A}px`, '--row-text': `${T}px`, '--row-gap': `${ROW_GAP_A * A}px` } as CSSProperties;
  return (
    // Not a bar the floating Arturo button lifts over: it would drop again the moment the row goes (a layout shift).
    // On narrow screens the row keeps clear of the button's corner instead (agentWorking.css).
    <div className={`agent-working${className ? ` ${className}` : ''}`} role="status" aria-live="polite" data-row-kind={typing ? 'typing' : 'working'}
         style={vars}>
      <span className="agent-working-face" aria-hidden>
        <AgentAvatar agentId={avatarId ?? agentId} role="activity" size={S} state={avatarState} label={name} provider={null}
                     shape={rec.shape} color={rec.color} eyeColor={rec.eyeColor} sleepAfterMs={0} activity={activity ?? 'none'}
                     style={{ position: 'absolute', left: (A - S) / 2, top: (A - S) / 2 }} />
      </span>
      <span className="sr-only">{name}: </span>
      <span className="agent-working-phrase is-shimmer" data-phrase>
        {shownPhrase}
        <span className="agent-shimmer-win" aria-hidden><span className="agent-shimmer-copy">{shownPhrase}</span></span>
      </span>
    </div>
  );
}

export function AgentWorkingRow({ agentId, name, onResume, avatarSize, textSize, typing: typingProp, ...input }: WorkingRowInput & {
  agentId: string;
  name: string;
  /** Kept for callers; the row never shows for a stopped seat now, so Resume lives elsewhere. */
  onResume?: () => void;
  /** The row's avatar size A and text size, px (default: lib/workingRowLook workingRowSizes, today 24 and 13). */
  avatarSize?: number;
  textSize?: number;
  /** Writing its reply now (default: from its open chat stream). */
  typing?: boolean;
}) {
  void onResume;
  const rowInput: WorkingRowInput = input;
  const row = workingRowFor(rowInput);
  const streamTyping = useAgentTyping((s) => !!s.byAgent[agentId]);
  const typing = typingProp ?? streamTyping;
  const { state, menu } = dotState(rowInput);
  const face = avatarStateFor(state, menu, !!rowInput.feed && isDegraded(rowInput.feed));
  useEffect(() => {
    const prev = avatarHub.get(agentId) ?? ensureAgent(agentId);
    if (prev.status !== face) avatarHub.set(agentId, { status: face });
  }, [agentId, face]);

  // Writing its reply wins over a working step (and over a status that has not caught up yet); a state that needs
  // the person, or a down seat, shows no row.
  const busy = row.tone === 'working' || row.kind === 'idle' || row.kind === 'idleStep' || row.kind === 'unknown';
  const isTyping = typing && busy;
  const shown = isTyping || rowShown(row);
  if (!shown) return null;
  return <WorkingRowView agentId={agentId} name={name} phrase={row.text} typing={isTyping} activity={activityFor(rowInput.step, rowInput.tool)}
                         avatarSize={avatarSize} textSize={textSize} />;
}
