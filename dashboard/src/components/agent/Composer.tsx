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
    <div className="fixed bottom-0 left-0 right-0 bg-background border-t border-border safe-bottom">
      {/* Composer wrapper with ChatInput extension */}
      <div className="relative">
        {/* Right gutter reserved so ChatInput's own Inject/Send + mode-toggle
            column (which is part of its normal flex layout) doesn't sit
            under the voice/call cluster overlaid below. */}
        <div className="pr-28">
          <ChatInput
            agentId={agentId}
            placeholder={seatName ? `Message ${seatName}` : `Ask ${settings.assistantName}`}
            draft={draft}
            onDraftChange={setDraft}
            onSend={handleSend}
          />
        </div>

        {/* Extended features overlaid on ChatInput — only the cluster itself
            is clickable, the rest of this layer must not eat clicks meant
            for ChatInput underneath. */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute right-2 bottom-2 flex items-center gap-2 pointer-events-auto">
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

        {/* AgentChip -> ModelSelectorSheet (B2) */}
        <button
          type="button"
          onClick={() => setSheetOpen(true)}
          className="text-[10px] text-foreground/60 hover:text-foreground px-4 py-1 transition-colors"
        >
          {modelLabel ?? 'Choose a model'}
        </button>
        <ModelSelectorSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />
      </div>
    </div>
  );
}
