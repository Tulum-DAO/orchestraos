import { useRef, useState } from 'react';
import { Cpu, PhoneCall } from 'lucide-react';
import { withTimeout, sendFailureNote } from '../../lib/composerSend';
import { delegatedWorkLabel, type ComposerGate } from '../../lib/composerGate';
import { useLocation } from 'react-router-dom';
import { useAgentSettings } from '../../stores/agentSettings';
import { useModelSelection } from '../../stores/modelSelection';
import { ModelSelectorSheet } from './ModelSelectorSheet';
import { VoiceControls, type VoiceControlsHandle } from './VoiceControls';
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
  /** Stop may be offered (composerGate.canStopTurn: mid-turn, no menu open). */
  canStop?: boolean;
  /** Interrupt the turn: the Esc key, same path as the Dev-mode ActionBar. */
  onStop?: () => Promise<unknown>;
}

async function uploadAttachment(file: File): Promise<SendAttachment> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/uploads', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  const data = await res.json();
  return { upload_id: data.filename };
}

export function Composer({ agentId = 'gm', seatName, gate, subagents, canStop, onStop }: ComposerProps) {
  const settings = useAgentSettings();
  const location = useLocation();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const modelLabel = useModelSelection((s) => s.modelLabel);
  const voiceRef = useRef<VoiceControlsHandle>(null);
  const [inCall, setInCall] = useState(false);
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
        held: isHeld(result),
        // A composer-hold is a REFUSAL (409, nothing delivered: text already sits in the agent's
        // box), not a hold. Folding it into `held` made the panel call it a success (review of #198).
        refused: isComposerHold(result),
        // carried so the panel can SHOW the draft it would overwrite
        composer_text: result.composer_text,
        stranded: result.stranded,
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
    // FLOATING (Shaw 2026-10-09): no full-width bar any more. The page (AgentPage) positions this
    // over the bottom of the chat column and pads the transcript by its measured height, so the
    // conversation scrolls underneath and the last message is never covered. Still scoped to the
    // chat column, never `fixed` to the window: as a window-level bar it once ran across the
    // sidebar and read as a global "talk to the system" box when it is scoped to ONE agent.
    <div className="shrink-0 safe-bottom">
      {/* Capped and centred on the SAME column as the transcript, so the two share one edge. */}
      <div className="relative mx-auto w-full max-w-[800px]">
        {/* WHAT THE COMPOSER CAN DO RIGHT NOW, said plainly directly above the pill.
            A mid-turn seat is NOT refused: Claude Code queues natively, so the honest line is
            "this will wait", not a disabled button. The blocked cases are the two where a
            keystroke does damage (it answers a menu) or nothing at all (the pane is down). */}
        {gate && gate.send === 'blocked' && (
          <p className="mx-6 mb-1 text-[11px] text-amber-500 dark:text-amber-300" role="status">{gate.reason}</p>
        )}
        {gate && gate.send === 'enabled' && gate.queued && (
          <p className="mx-6 mb-1 text-[11px] text-muted-foreground" role="status">
            {gate.reason}
            {delegatedWorkLabel(subagents) && (
              <span className="opacity-80"> · {delegatedWorkLabel(subagents)}</span>
            )}
          </p>
        )}
        {gate && gate.send === 'enabled' && !gate.queued && delegatedWorkLabel(subagents) && (
          <p className="mx-6 mb-1 text-[11px] text-muted-foreground" role="status">
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
          canStop={canStop}
          onStop={onStop}
          // Below 480px the model chip and the call button live here, so the text gets the row.
          menuItems={(close) => (
            <>
              <button type="button" role="menuitem"
                className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-left text-foreground hover:bg-muted rounded-lg"
                onClick={() => { close(); setSheetOpen(true); }}>
                <Cpu size={15} className="shrink-0 text-muted-foreground" />
                <span className="truncate">Model <span className="text-muted-foreground">· {modelLabel ?? 'choose'}</span></span>
              </button>
              {/* Start only, and only with no call live: a live call's End button is inline. */}
              {draft.trim() === '' && !inCall && (
                <button type="button" role="menuitem"
                  className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-left text-foreground hover:bg-muted rounded-lg"
                  onClick={() => { close(); void voiceRef.current?.startCall(); }}>
                  <PhoneCall size={15} className="shrink-0 text-muted-foreground" />
                  <span>Call {settings.assistantName}</span>
                </button>
              )}
            </>
          )}
          leading={
            // Compact model chip: the label from sm up, an icon below it. Below 480px it folds
            // into the pill's overflow menu (menuItems below). Opens the existing ModelSelectorSheet.
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="touch-circle sm:w-auto sm:h-9 sm:min-w-9 shrink-0 sm:px-2.5 max-w-[130px] flex items-center justify-center gap-1.5 rounded-full text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors max-[480px]:hidden"
              title={modelLabel ? `Model: ${modelLabel} — choose the model this agent runs` : 'Choose the model this agent runs'}
              aria-label={modelLabel ? `Model: ${modelLabel}` : 'Choose a model'}
            >
              <Cpu size={16} className="shrink-0 sm:hidden" />
              <span className="hidden sm:inline truncate">{modelLabel ?? 'Choose a model'}</span>
            </button>
          }
          trailing={
            <VoiceControls
              ref={voiceRef}
              idleCallClassName="max-[480px]:hidden"
              onInCallChange={setInCall}
              route={location.pathname}
              focusedEntity={null}
              onPartial={setDraft}
              onFinal={setDraft}
              onCallEnded={(marker) => setDraft((d) => (d ? d + '\n' : '') + marker)}
              showCallButton={draft.trim() === ''}
            />
          }
        />
        <ModelSelectorSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />
      </div>
    </div>
  );
}
