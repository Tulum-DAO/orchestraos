import { useState } from 'react';
import { withTimeout, sendFailureNote } from '../../lib/composerSend';
import { delegatedWorkLabel, type ComposerGate } from '../../lib/composerGate';
import { useLocation } from 'react-router-dom';
import { useAgentSettings } from '../../stores/agentSettings';
import { useModelSelection } from '../../stores/modelSelection';
import { ModelSelectorSheet } from './ModelSelectorSheet';
import { VoiceControls } from './VoiceControls';
import ChatInput from '../chat/ChatInput';
import {
  sendToAgent,
  isDelivered,
  isQueued,
  isHeld,
  isComposerHold,
  describeSendState,
  type SendAttachment,
} from '../../lib/agentSend';

interface ComposerProps {
  agentId?: string;
  /** When set, this is a seat's own page: the box says whom you are messaging, not "Ask Arturo". */
  seatName?: string;
  /** What the composer may do right now — see lib/composerGate.ts. Absent = send normally. */
  gate?: ComposerGate;
  /** Delegated agents in flight, shown as an affordance. Never gates the send. */
  subagents?: number;
}

async function uploadAttachment(file: File): Promise<SendAttachment> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/uploads', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  const data = await res.json();
  return { upload_id: data.filename };
}

export function Composer({ agentId = 'gm', seatName, gate, subagents }: ComposerProps) {
  const settings = useAgentSettings();
  const location = useLocation();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const modelLabel = useModelSelection((s) => s.modelLabel);
  // B2 video-upload capability gate: N/A — ChatInput's current attach
  // affordance has no video mime/extension support to gate yet, so
  // `capabilities?.video === true` has nothing to gate against for now.

  // B1 send-bridge (now unblocked — ChatInput.tsx gained the optional
  // draft/onDraftChange/onSend seam): upload any File attachments, then
  // deliver through the P3 gateway send bridge instead of the legacy
  // /inject path, mapped to ChatInput's {ok, note, queued, held} contract.
  const handleSend = async (
    { text, attachments }: { text: string; attachments: File[] },
    { force }: { force: boolean }
  ) => {
    // AN UNREACHABLE API MUST SAY SO, not vanish (P1 2026-10-06: the API was down 15:33-16:33Z
    // and sends failed with nothing on screen explaining why). Returning ok:false keeps the
    // draft AND the attachment where the operator left them; throwing would too, but the note
    // would be a stack-shaped 'Error: Failed to fetch' instead of a sentence.
    // Timeout semantics live in lib/composerSend.ts, with their reasoning and their tests.
    // The short version: a timeout is an UNKNOWN outcome, not a failure.
    try {
      const uploaded = attachments.length
        ? await withTimeout(Promise.all(attachments.map(uploadAttachment)))
        : undefined;
      const result = await withTimeout(sendToAgent(agentId, { text, attachments: uploaded }, { force }));
      return {
        ok: isDelivered(result),
        note: describeSendState(result) || undefined,
        queued: isQueued(result),
        held: isHeld(result) || isComposerHold(result),
      };
    } catch (err) {
      // Do NOT claim "nothing was sent" on a timeout. The race settles OUR promise; the
      // request is still in flight and the server may well deliver it. Saying it failed is
      // what makes the operator send again, which is how the agent gets the same
      // instruction twice.
      return {
        ok: false,
        note: sendFailureNote(err),
        queued: false,
        held: false,
      };
    }
  };

  return (
    // NOT `fixed`: the composer belongs to the chat column, not to the window. As a fixed
    // page-level bar it ran across the sidebar, cut the rail off ~145px from the bottom (so the
    // last agent rows could not be reached), and read as a global "talk to the system" box when
    // it is scoped to ONE agent.
    // `sticky bottom-0` keeps the composer pinned to the bottom of ITS OWN column while the
    // transcript scrolls behind it. It no longer has anything to do with the Arturo pill: the
    // pill is now pinned to one corner at the highest z-index and sits ON TOP of this bar by
    // design (operator ruling 2026-10-06), rather than being lifted clear of it.
    <div className="shrink-0 sticky bottom-0 z-10 bg-background border-t border-border safe-bottom">
      {/* Capped and centred on the SAME column as the transcript, so the two share one edge. */}
      <div className="relative mx-auto w-full max-w-[860px] px-3">
        {/* ONE row. The model picker and the voice controls ride the SEND ROW itself via
            ChatInput's leading/trailing slots. They were previously an orphan caption plus a
            second control row under Send, so five controls read as three ragged rows.
            `items-end` on that row puts every one of them on Send's baseline. */}
        {/* WHAT THE COMPOSER CAN DO RIGHT NOW, said plainly above the box.
            A mid-turn seat is NOT refused: Claude Code queues natively, so the honest line is
            "this will wait", not a disabled button. The blocked cases are the two where a
            keystroke does damage (it answers a menu) or nothing at all (the pane is down). */}
        {gate && gate.send === 'blocked' && (
          <p className="px-1 pb-1 text-[11px] text-amber-300" role="status">{gate.reason}</p>
        )}
        {gate && gate.send === 'enabled' && gate.queued && (
          <p className="px-1 pb-1 text-[11px] text-neutral-400" role="status">
            {gate.reason}
            {delegatedWorkLabel(subagents) && (
              <span className="text-neutral-500"> · {delegatedWorkLabel(subagents)}</span>
            )}
          </p>
        )}
        {gate && gate.send === 'enabled' && !gate.queued && delegatedWorkLabel(subagents) && (
          <p className="px-1 pb-1 text-[11px] text-neutral-500" role="status">
            {delegatedWorkLabel(subagents)}
          </p>
        )}
        <ChatInput
          agentId={agentId}
          disabled={gate?.send === 'blocked'}
          placeholder={seatName ? `Message ${seatName}` : `Ask ${settings.assistantName}`}
          draft={draft}
          onDraftChange={setDraft}
          onSend={handleSend}
          leading={
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="shrink-0 self-end mb-3 max-w-[120px] truncate text-[10px] text-foreground/60 hover:text-foreground transition-colors"
              title="Choose the model this agent runs"
            >
              {modelLabel ?? 'Choose a model'}
            </button>
          }
          trailing={
            <div className="shrink-0 self-end flex items-center gap-2">
              <VoiceControls
                route={location.pathname}
                focusedEntity={null}
                onPartial={setDraft}
                onFinal={setDraft}
                onCallEnded={(marker) => setDraft((d) => (d ? d + '\n' : '') + marker)}
                showCallButton={draft.trim() === ''}
              />
            </div>
          }
        />
        <ModelSelectorSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />
      </div>
    </div>
  );
}
