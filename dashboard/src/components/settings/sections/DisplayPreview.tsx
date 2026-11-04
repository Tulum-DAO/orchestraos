/** Settings › Display: a small live picture of what the rows change (a person's bubble, an agent's reply with its
 *  face, a tool line, a time, a sidebar row). It reads the same variables as the app, so it changes as rows change. */
import { AgentAvatar } from '../../avatar/AgentAvatar';
import { ARTURO_ID } from '../../avatar/agentState';
import { BUBBLES_AGENT, BUBBLES_USER } from '../../../lib/chatSkin';
import { MESSAGE_TIME_TONE } from '../../../lib/messageTime';
import { useTextAvatar } from '../../../lib/deviceSettings';

export function DisplayPreview() {
  const face = useTextAvatar(20);
  const rowFace = useTextAvatar(32);
  return (
    <div className="settings-preview" aria-label="Preview" data-display-preview>
      <div className="ui-feed-gap">
        <div className="flex flex-col items-end">
          <div className={BUBBLES_USER}>Is the deploy done?</div>
          <span className={`block mt-0.5 ${MESSAGE_TIME_TONE} text-right`} data-msg-time>10:02</span>
        </div>
        <div className="flex items-start gap-2">
          <AgentAvatar agentId={ARTURO_ID} role="activity" size={face} label="Agent" />
          <div className="min-w-0">
            <div className={BUBBLES_AGENT}>Yes, it finished at 10:02 and every check is green.</div>
            <div className="text-px12 text-muted-foreground mt-1">Ran Bash · npm test <span className={MESSAGE_TIME_TONE} data-msg-time>10:01</span></div>
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2 px-2 py-[calc(6px*var(--ui-gap,1))] min-h-8 rounded bg-neutral-100 dark:bg-neutral-900 text-sm">
        <AgentAvatar agentId={ARTURO_ID} role="sidebar" size={rowFace} label="Agent" />
        <span className="truncate">A sidebar row</span>
      </div>
    </div>
  );
}
