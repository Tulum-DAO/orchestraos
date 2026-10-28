/**
 * AgentIdentityPill — an agent's face and name, the one element behind every place the app names an agent this
 * way: Arturo's header pill, the pill a sidebar face shows on hover, and the "Message from" line in a chat. Its
 * look lives in one rule set (.agent-pill in index.css), so changing it there changes every one of them; a place
 * that should differ says so explicitly (a variant here, or its own class).
 *
 *   filled (default): the pill is always drawn (Arturo's header, the sidebar hover pill).
 *   bare:             the face and name sit in a line of text; the pill appears behind them only on hover or
 *                     keyboard focus, without moving them.
 *   text 'inline':    the surrounding line's 13px regular text instead of the header's 15px semibold.
 *   statusRoom false: where the face never shows a status dot, the room kept for it on the face's right is
 *                     taken back, so the name sits next to the face.
 *
 * A button when it does something (onClick), a plain span otherwise.
 */
import type { ButtonHTMLAttributes, CSSProperties } from 'react';
import { clsx } from 'clsx';
import { AgentAvatar, type AgentAvatarProps } from './AgentAvatar';

export interface AgentIdentityPillProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  agentId: string;
  name: string;
  /** The face's size in px. */
  size?: number;
  variant?: 'filled' | 'bare';
  text?: 'header' | 'inline';
  /** Keep the room on the face's right for a status dot (default). False where no dot is ever shown. */
  statusRoom?: boolean;
  /** Passed to the face (role, look, state…); its agentId, size and label come from the pill. */
  avatar?: Partial<AgentAvatarProps>;
  className?: string;
  style?: CSSProperties;
}

export default function AgentIdentityPill({ agentId, name, size = 32, variant = 'filled', text = 'header', statusRoom = true,
  avatar, className, style: styleIn, onClick, type, ...rest }: AgentIdentityPillProps) {
  const cls = clsx('agent-pill', variant === 'bare' && 'is-bare', text === 'inline' && 'is-inline',
    !statusRoom && 'no-status-room', className);
  const style = { ...styleIn, ['--agent-face' as string]: `${size}px` } as CSSProperties;
  const inner = (
    <>
      <AgentAvatar role="pill" {...avatar} agentId={avatar?.agentId ?? agentId} size={size} label={avatar?.label ?? name} />
      <span className="agent-pill-name">{name}</span>
    </>
  );
  if (!onClick) return <span className={cls} style={style} data-agent-pill={agentId}>{inner}</span>;
  return (
    <button type={type ?? 'button'} className={cls} style={style} onClick={onClick} data-agent-pill={agentId} {...rest}>
      {inner}
    </button>
  );
}
