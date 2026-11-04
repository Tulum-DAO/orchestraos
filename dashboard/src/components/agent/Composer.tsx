import { useCallback, useEffect, useRef, useState } from 'react';
import { Cpu } from 'lucide-react';
import { delegatedWorkLabel, type ComposerGate } from '../../lib/composerGate';
import { useLocation } from 'react-router-dom';
import { useAssistantName } from '../../lib/assistantName';
import {
  fetchAgentModel, switchAgentModel, modelLabel as labelFor, pickRefusal, switchNotice, switchableModels,
  type AgentModelInfo, type SwitchNotice,
} from '../../lib/agentModel';
import ModelPicker from '../kit/ModelPicker';
import { agentModelTitle } from '../../lib/modelTitle';
import { useComposerDraft } from '../../stores/composerDrafts';
import { VoiceControls, type VoiceControlsHandle } from './VoiceControls';
import { callSupport } from '../../lib/callSupport';
import { useVoiceReady } from '../../hooks/useVoiceReady';
import { useCallStore } from '../../stores/callStore';
import { ARTURO_ID, useAgentRecord } from '../avatar/agentState';
import { agentCallBlock, callPartyId, useAgentCallsOn } from '../../lib/agentCallsOn';
import ChatInput from '../chat/ChatInput';
import { sendFromComposer } from '../../lib/agentSendFlow';
import { handOffCall, refreshTranscript } from '../../lib/agentCalls';

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

export function Composer({ agentId = 'gm', seatName, gate, subagents, canStop, onStop }: ComposerProps) {
  const aname = useAssistantName();
  const location = useLocation();
  const [sheetOpen, setSheetOpen] = useState(false);
  // The agent's draft (stores/composerDrafts.ts): kept when the page switches to Dev or to another agent.
  const { draft: { text: draft }, setText: setDraft } = useComposerDraft(agentId);
  // The model this agent runs, from the gateway; picking another switches it (lib/agentModel.ts).
  // Both kept with the agent they belong to, so opening another agent never shows this one's model.
  const [modelOf, setModelOf] = useState<{ id: string; info: AgentModelInfo | null } | null>(null);
  const [noticeOf, setNoticeOf] = useState<{ id: string; notice: SwitchNotice } | null>(null);
  const agentModel = modelOf?.id === agentId ? modelOf.info : null;
  const notice = noticeOf?.id === agentId ? noticeOf.notice : null;
  const setNotice = useCallback((n: SwitchNotice | null) => setNoticeOf(n ? { id: agentId, notice: n } : null), [agentId]);
  const refreshModel = useCallback(() => {
    void fetchAgentModel(agentId).then((info) => setModelOf({ id: agentId, info }));
  }, [agentId]);
  useEffect(() => { refreshModel(); }, [refreshModel]);
  useEffect(() => {
    if (notice?.tone !== 'ok') return;
    const t = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(t);
  }, [notice, setNotice]);
  const modelLabel = labelFor(agentModel, agentModel?.model);
  // The model as the picker names it ("Opus 5.5 · 1M"), for the chip before the picker has read its list, and for the
  // overflow item.
  const modelTitle = agentModelTitle(agentModel);
  const onPick = (pick: { providerId: string; modelId: string; modelLabel: string }) => {
    const refused = pickRefusal(agentModel, pick);
    if (refused) { setNotice({ tone: 'error', text: refused }); return; }
    setNotice({ tone: 'wait', text: `Switching to ${pick.modelLabel}…` });
    void switchAgentModel(agentId, pick.modelId).then(({ status, body }) => {
      setNotice(switchNotice(status, body, pick.modelLabel));
      // A confirmed live switch shows at once. The seat's record catches up a few seconds later, and a read
      // made before then still names the old model, so the re-read waits.
      if (status === 200 && body.ok && body.switching === 'switched' && agentModel) {
        setModelOf({ id: agentId, info: { ...agentModel, model: pick.modelId } });
        setTimeout(refreshModel, 8000);
      } else {
        refreshModel();
      }
    });
  };
  const voiceRef = useRef<VoiceControlsHandle>(null);
  const [inCall, setInCall] = useState(false);
  // Why a call can't be had here, when it can't: this browser, the server's readiness check (no voice key; asked on
  // open and on focus, lib/voiceReady.ts), or a call that could not start.
  const [callFailed, setCallFailed] = useState<string | null>(null);
  const ready = useVoiceReady();
  // One call at a time: while a call with another agent is live, this composer offers no Call (mic only).
  const otherCall = useCallStore((s) => (s.call && s.call.agentId !== agentId ? callPartyId(s.call, ARTURO_ID) : null));
  const otherName = useAgentRecord(otherCall ?? '').name || otherCall;
  // The call is always with this agent: "Call <agent>" once its calls reach it (agent_calls on
  // its settings); until then Call is off and says why, never a call handed to Arturo.
  const agentCalls = useAgentCallsOn(agentId);
  const callee = seatName || agentId;
  const callBlocked = (otherCall ? `You're on a call with ${otherName}` : null) ?? agentCallBlock(agentCalls, callee)
    ?? callSupport().reason ?? (ready && !ready.ok ? ready.reason ?? null : null) ?? callFailed;
  // B2 video-upload capability gate: N/A — ChatInput's current attach
  // affordance has no video mime/extension support to gate yet, so
  // `capabilities?.video === true` has nothing to gate against for now.

  // Upload, POST /send (the held-aware route), and the transcript check on an unknown outcome:
  // lib/agentSendFlow.ts, shared with the floating agent pane so the two behave the same.
  const handleSend = (
    payload: { text: string; attachments: File[] },
    opts: { force: boolean; priorRowIds?: ReadonlySet<string> },
  ) => sendFromComposer(agentId, payload, opts);

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
        {notice && (
          <p className={`mx-6 mb-1 text-px11 ${notice.tone === 'error' ? 'text-red-600 dark:text-red-400'
            : 'text-muted-foreground'}`} role="status">{notice.text}</p>
        )}
        {gate && gate.send === 'blocked' && (
          <p className="mx-6 mb-1 text-px11 text-amber-500 dark:text-amber-300" role="status">{gate.reason}</p>
        )}
        {/* "Will be queued…" says what happens to a typed draft, so it shows only while there is one: with an empty
            box the working row sits right on the message box. */}
        {gate && gate.send === 'enabled' && gate.queued && draft.trim() !== '' && (
          <p className="mx-6 mb-1 text-px11 text-muted-foreground" role="status">
            {gate.reason}
            {delegatedWorkLabel(subagents) && (
              <span className="opacity-80"> · {delegatedWorkLabel(subagents)}</span>
            )}
          </p>
        )}
        {gate && gate.send === 'enabled' && !gate.queued && delegatedWorkLabel(subagents) && (
          <p className="mx-6 mb-1 text-px11 text-muted-foreground" role="status">
            {delegatedWorkLabel(subagents)}
          </p>
        )}
        <ChatInput
          agentId={agentId}
          disabled={gate?.send === 'blocked'}
          placeholder={seatName ? `Message ${seatName}` : `Ask ${aname}`}
          draft={draft}
          onDraftChange={setDraft}
          onSend={handleSend}
          canStop={canStop}
          onStop={onStop}
          // The round button's call (lib/roundAction.ts): what the phone button started, from the empty box.
          // The button names who answers: always this agent (lib/agentCallsOn.ts agentCallBlock).
          call={{ inCall, available: !callBlocked, reason: callBlocked ?? undefined, agentName: callee,
                  onToggle: () => { void voiceRef.current?.toggleCall(); } }}
          // Below 480px the model chip and the call button live here, so the text gets the row.
          menuItems={(close) => (
            <>
              <button type="button" role="menuitem"
                className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-left text-foreground hover:bg-muted rounded-lg"
                onClick={() => { close(); setSheetOpen(true); }}>
                <Cpu size={15} className="shrink-0 text-muted-foreground" />
                <span className="truncate">Model <span className="text-muted-foreground">· {modelTitle ?? 'choose'}</span></span>
              </button>
            </>
          )}
          leading={
            // The kit's model picker: its chip here; below 480px the chip folds into the pill's overflow menu (menuItems
            // above), whose item opens the same picker. A pick switches this agent (onPick): live on a headless seat.
            <ModelPicker open={sheetOpen} onOpenChange={setSheetOpen} triggerClassName="mb-0.5 max-[480px]:hidden"
              label={modelTitle ?? modelLabel ?? undefined}
              value={agentModel?.runtime && agentModel.model ? { providerId: agentModel.runtime, modelId: agentModel.model } : null}
              onValueChange={onPick}
              agentModels={agentModel?.runtime ? { providerId: agentModel.runtime, models: switchableModels(agentModel) } : null} />
          }
          trailing={
            <VoiceControls
              ref={voiceRef}
              agentId={agentId}
              agentName={callee}
              agentCalls={agentCalls}
              onInCallChange={setInCall}
              onCallUnavailable={setCallFailed}
              route={location.pathname}
              focusedEntity={null}
              onPartial={setDraft}
              onFinal={setDraft}
              // At hang-up the call is handed to the agent (the hand-off route); the draft is never touched.
              onCallEnded={(callId) => { void handOffCall(agentId, callId).then((r) => { if (r === 'ok') refreshTranscript(agentId); }); }}
            />
          }
        />
      </div>
    </div>
  );
}
