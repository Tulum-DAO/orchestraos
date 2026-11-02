/*
 * <AgentAvatar>: a living avatar on a canvas, ported from the prototype's AgentAvatar.js.
 *
 * Shared mode (the dashboard): no `state` prop. The avatar reads the agent's live record from the shared
 * hub (agentState.ts), so every instance of one agent updates together and the animated ones run on one
 * clock.
 *
 *   avatarHub.set('arturo', { status: 'working', blurb: 'Reading inbox…', provider: 'openai' })
 *   <AgentAvatar agentId="arturo" role="sidebar"  size={32} selected />
 *   <AgentAvatar agentId="arturo" role="pill"     size={28} />
 *   <AgentAvatar agentId="arturo" role="activity" size={24} />
 *   <AgentAvatar agentId="arturo" role="drawer"   size={128} selected />
 *
 *   role:      'sidebar' (default) | 'pill' | 'activity' | 'drawer'  (rules: ROLES in avatarCore.ts)
 *   selected:  this agent is the selected one (idle wander + eye-follow)
 *   provider:  'claude' | 'openai' | 'gemini' | 'grok', or a runtime id such as 'codex' (defaults to the record)
 *   followRadius: px, default 160
 *   followAlways: follow a mouse from any distance, even while it rests (the home face)
 *
 * Standalone mode (the look picker's thumbnails): pass `state` and the avatar keeps its own private model,
 * with the `shape`, `color` and `eyeColor` given.
 *
 * Identity mode (`showStatus={false}`: the header pill, the drawer face, the picker's hero): the agent's own
 * look and badge from the hub, but a private model held at idle that never sleeps, so the face is the
 * agent's picture, not its status. Only the sidebar avatar says what the agent is doing.
 */
import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { AvatarModel, REACTIONS, STATES, type AvatarState, type ReactionName, type Role } from './avatarCore';
import { attach, type AttachOpts } from './avatarCanvas';
import { providerMark } from './brandMarks';
import { avatarHub, useAgentRecord, defaultLook, ensureAgent } from './agentState';

function usePrefersReducedMotion(): boolean {
  const q = typeof matchMedia !== 'undefined' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
  const [v, setV] = useState(q ? q.matches : false);
  useEffect(() => {
    if (!q) return;
    const on = () => setV(q.matches);
    q.addEventListener('change', on);
    return () => q.removeEventListener('change', on);
  }, [q]);
  return v;
}

export interface AgentAvatarProps {
  agentId: string;
  role?: Role;
  size?: number;
  selected?: boolean;
  provider?: string | null;
  followRadius?: number;
  followAlways?: boolean;
  /** Standalone only (with `state`): the look to draw. Shared avatars take their look from the hub. */
  shape?: string;
  color?: string;
  eyeColor?: string;
  /** Standalone mode: draw this state with a private model instead of the agent's live one. */
  state?: AvatarState;
  sleepAfterMs?: number;
  /** False: draw the agent's look and badge but not its status (identity mode, above). */
  showStatus?: boolean;
  /** A reaction played once, shortly after the avatar appears (a hello). */
  greet?: ReactionName;
  /** No constant motion (breathing, bob): blinks, glances and one-off reactions only. */
  calm?: boolean;
  /** Each change makes the eyes glance at `glanceAt` for a moment (e.g. on every keystroke in an input). */
  glanceKey?: unknown;
  glanceAt?: { x: number; y: number };
  reducedMotion?: boolean;
  label?: string;
  onClick?: (e: MouseEvent<HTMLCanvasElement>, model: AvatarModel) => void;
  className?: string;
  style?: CSSProperties;
}

export function AgentAvatar(props: AgentAvatarProps) {
  const {
    agentId, role = 'sidebar', size = 48, selected, provider, followRadius, followAlways,
    shape: shapeProp, color: colorProp, eyeColor: eyeProp, state: stateProp, sleepAfterMs: sleepProp = 30000,
    showStatus = true, greet, calm, glanceKey, glanceAt, reducedMotion, label, onClick, className, style,
  } = props;
  const rec = useAgentRecord(agentId);
  const identity = !showStatus && stateProp === undefined;
  const state: AvatarState | undefined = identity ? 'idle' : stateProp;
  const shared = state === undefined;
  const shape = identity ? shapeProp || rec.shape : shapeProp;
  const color = identity ? colorProp || rec.color : colorProp;
  const eyeColor = identity ? eyeProp || rec.eyeColor : eyeProp;
  const sleepAfterMs = identity ? 0 : sleepProp;
  const systemReduced = usePrefersReducedMotion();
  const rm = reducedMotion ?? systemReduced;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // The model is made in an effect, not during render: the shared one may create the agent's hub record,
  // and that notifies every other avatar of the agent, which must not happen mid-render.
  const [model, setModel] = useState<AvatarModel | null>(null);
  useEffect(() => {
    if (shared) {
      ensureAgent(agentId);                       // the default look (ink, hash shape) before the model reads it
      setModel(avatarHub.model(agentId, { sleepAfterMs, reducedMotion: rm }));
      return;
    }
    const look = defaultLook(agentId);
    setModel(new AvatarModel({ agentId, shape: shape || look.shape, color: color || look.color, eyeColor: eyeColor || look.eyeColor, state, sleepAfterMs, followPointer: false, reducedMotion: rm }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one model per agent and mode; the effects below keep it current
  }, [agentId, shared]);
  const prov = providerMark(provider !== undefined ? provider : rec.provider);
  const detachRef = useRef<ReturnType<typeof attach> | null>(null);

  // One canvas registration per model and size; role, selection and provider update it in place (the
  // second effect runs after the first, and again whenever the canvas is registered anew).
  useEffect(() => {
    if (!model) return;
    const d = attach(canvasRef.current as HTMLCanvasElement, model, size, { role });
    detachRef.current = d;
    return () => { d(); detachRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- role is applied by the effect below too
  }, [model, size]);
  useEffect(() => {
    const opts: AttachOpts = { role, selected: !!selected, provider: prov, followRadius, followAlways };
    detachRef.current?.update(opts);
  }, [model, size, role, selected, prov, followRadius, followAlways]);
  useEffect(() => { if (model && !shared && state) model.setState(state); }, [shared, state, model]);
  useEffect(() => { if (model && calm !== undefined) model.configure({ calm }); }, [model, calm]);
  const glanceRef = useRef(glanceKey);
  useEffect(() => {
    if (!model || glanceRef.current === glanceKey) return;
    glanceRef.current = glanceKey;
    if (glanceAt) model.glance(glanceAt.x, glanceAt.y, 1.6);
  }, [model, glanceKey, glanceAt]);
  // after a beat, so the hello is seen rather than lost in the page settling (react() skips it under reduced
  // motion). The eyes do not chase the cursor until it has played out, so the wink is seen whole.
  useEffect(() => {
    if (!model || !greet) return;
    model.followAfter = Infinity;
    const t = setTimeout(() => {
      model.react(greet);
      model.followAfter = model.reaction ? model.t + REACTIONS[greet].dur : 0;
    }, 600);
    return () => { clearTimeout(t); model.followAfter = 0; };
  }, [model, greet]);
  useEffect(() => {
    if (!model) return;
    if (shared) { model.configure({ reducedMotion: rm, sleepAfterMs }); return; }
    model.configure({ shape: shape || model.shape, color: color || model.color, eyeColor: eyeColor || model.eyeColor, sleepAfterMs, reducedMotion: rm });
  }, [shared, model, shape, color, eyeColor, sleepAfterMs, rm]);

  const st = shared ? rec.status : (state as AvatarState);
  const stateLabel = (STATES[st] || STATES.idle).label;
  return (
    <canvas
      ref={canvasRef}
      className={className}
      role="img"
      data-role={role}
      data-agent={agentId}
      aria-label={identity ? (label || rec.name || agentId) : `${label || rec.name || agentId}: ${stateLabel}`}
      onClick={onClick && model ? (e) => onClick(e, model) : undefined}
      style={{ width: size, height: size, display: 'block', touchAction: 'manipulation', ...style }}
    />
  );
}
