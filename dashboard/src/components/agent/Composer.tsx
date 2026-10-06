import { useState } from 'react';
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
}

async function uploadAttachment(file: File): Promise<SendAttachment> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/uploads', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  const data = await res.json();
  return { upload_id: data.filename };
}

export function Composer({ agentId = 'gm', seatName }: ComposerProps) {
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
    // A HUNG API never rejects on its own: the socket stays open and the promise never settles,
    // so the composer spins forever and the operator learns nothing. Measured against a
    // SIGSTOPped API. 15s is well past a slow-but-working send and well short of giving up on
    // the person waiting.
    const withTimeout = <T,>(pr: Promise<T>, ms = 15_000): Promise<T> =>
      Promise.race([pr, new Promise<T>((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))]);
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
    } catch {
      return {
        ok: false,
        note: "Can't reach the server — nothing was sent. Your message and photo are still here.",
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
        {/* Right gutter reserved so ChatInput's own Inject/Send + mode-toggle
            column (which is part of its normal flex layout) doesn't sit
            under the voice/call cluster overlaid below. */}
        <div>
          <ChatInput
            agentId={agentId}
            placeholder={seatName ? `Message ${seatName}` : `Ask ${settings.assistantName}`}
            draft={draft}
            onDraftChange={setDraft}
            onSend={handleSend}
          />
        </div>

        {/* ONE row of secondary controls, on the composer's own baseline and inside its column.
            They were previously an absolutely-positioned voice cluster OVER the input plus an
            orphan caption below it, which is why five controls read as three ragged rows. */}
        <div className="flex items-center gap-3 px-1 pb-1">
          <button
            type="button"
            onClick={() => setSheetOpen(true)}
            className="text-[10px] text-foreground/60 hover:text-foreground transition-colors"
            title="Choose the model this agent runs"
          >
            {modelLabel ?? 'Choose a model'}
          </button>
          <div className="ml-auto flex items-center gap-2">
            <VoiceControls
              route={location.pathname}
              focusedEntity={null}
              onPartial={setDraft}
              onFinal={setDraft}
              onCallEnded={(marker) => setDraft((d) => (d ? d + '\n' : '') + marker)}
              showCallButton={draft.trim() === ''}
            />
          </div>
        </div>
        <ModelSelectorSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />
      </div>
    </div>
  );
}
