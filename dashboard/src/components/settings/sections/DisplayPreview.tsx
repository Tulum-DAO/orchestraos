/** Settings' live preview (General under Text size, and Display): a tiny fixed conversation drawn by the real chat, the
 *  agent chat's own ThreadView and its node primitives (TranscriptCards GroupedNodeView), in the skin Settings › General
 *  › Chat style picks (Bubbles or Modern), with Show agent steps and Timestamps as they are on this device, then a
 *  sidebar row. It reads the same variables and settings as the app, so it changes as soon as a row does: the chat
 *  style (the server row, shown at once as it saves), agent steps (a run starts open on Detailed; the thread is drawn
 *  again when it changes, as a run reads it once when it mounts) and timestamps (data-timestamps on <html>).
 *  Sandboxed: its rows are fixed (lib/settingsPreview.ts), nothing is fetched, streamed or sent, there are no reactions
 *  and it is inert, so no click, key or focus reaches it. */
import { useMemo } from 'react';
import { AgentAvatar } from '../../avatar/AgentAvatar';
import { ARTURO_ID } from '../../avatar/agentState';
import ThreadView from '../../kit/ThreadView';
import { GroupedNodeView } from '../../chat/TranscriptCards';
import { useDeviceSetting, useTextAvatar } from '../../../lib/deviceSettings';
import { previewRows, previewSkin } from '../../../lib/settingsPreview';
import { useServerSettings } from '../../../stores/serverSettings';

export function DisplayPreview() {
  const skin = previewSkin(useServerSettings((s) => s.rows['general.defaultChatStyle']?.value));
  const [steps] = useDeviceSetting('toolDetail');
  const rows = useMemo(() => previewRows(skin), [skin]);
  const rowFace = useTextAvatar(32);
  const modern = skin === 'modern';
  return (
    <div className="settings-preview" aria-label="Preview" data-display-preview data-preview-skin={skin} data-preview-steps={steps} inert>
      {/* The agent chat column's own classes: the row gap, and Modern's colour variables. */}
      <div key={`${skin}:${steps}`} className={`ui-feed-gap${modern ? ' kit-modern' : ''}`} data-preview-thread>
        <ThreadView rows={rows} skin={skin} times={modern}
          renderNode={(node, interrupted) => <GroupedNodeView node={node} interrupted={interrupted} look="lines" />} />
      </div>
      <div className="mt-3 flex items-center gap-2 px-2 py-[calc(6px*var(--ui-gap,1))] min-h-8 rounded bg-neutral-100 dark:bg-neutral-900 text-sm">
        <AgentAvatar agentId={ARTURO_ID} role="sidebar" size={rowFace} label="Agent" />
        <span className="truncate">A sidebar row</span>
      </div>
    </div>
  );
}
