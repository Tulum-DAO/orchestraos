import { useEffect, useRef, useState } from 'react';
import { HandsFreeCall, isInCall } from '../lib/handsFree';
import { VoiceSession, type CallToolEvent, type VoiceSessionStartOptions, type VoiceSessionState } from '../lib/voiceSession';
import { useCallStore } from '../stores/callStore';
import { askSwitchCall, useArturoCall } from '../lib/switchCall';
import { ARTURO_ID, avatarHub } from '../components/avatar/agentState';
import { callPartyId } from '../lib/agentCallsOn';
import { assistantName } from '../lib/assistantName';

/** Live voice mode for a composer: one HandsFreeCall (lib/handsFree.ts) plus the live
 *  captions as React state. The home composer and the Arturo pane both use it. */
export function useHandsFreeCall(ev: {
  onFinal?: (text: string, role: 'user' | 'arturo') => void;
  onUnavailable?: (reason: string) => void;
  onEnded?: (text: string) => void;
  onTool?: (e: CallToolEvent) => void;
}) {
  const [callState, setCallState] = useState<VoiceSessionState>('idle');
  const [liveUser, setLiveUser] = useState('');
  const [liveArturo, setLiveArturo] = useState('');
  const [muted, setMuted] = useState(false);
  const evRef = useRef(ev);
  evRef.current = ev;
  const ctl = useRef<HandsFreeCall | null>(null);
  if (!ctl.current) {
    ctl.current = new HandsFreeCall((cb) => new VoiceSession(cb), {
      onState: (s) => { setCallState(s); if (s === 'idle' || s === 'error') { setLiveUser(''); setLiveArturo(''); } },
      onPartial: (text, role) => { if (role === 'user') setLiveUser(text); else setLiveArturo(text); },
      onFinal: (text, role) => {
        if (role === 'user') setLiveUser(''); else setLiveArturo('');
        evRef.current.onFinal?.(text, role);
      },
      onUnavailable: (reason) => evRef.current.onUnavailable?.(reason),
      onEnded: (text) => { setLiveUser(''); setLiveArturo(''); evRef.current.onEnded?.(text); },
      onTool: (e) => evRef.current.onTool?.(e),
      onMuted: (m) => setMuted(m),
    });
  }
  useEffect(() => () => ctl.current?.dispose(), []);
  // While this call is up the app can see it (lib/switchCall.ts): an agent's Call asks before ending it.
  const live = isInCall(callState);
  useEffect(() => {
    if (!live) return;
    const mine = { name: assistantName(), end: () => ctl.current?.end() };
    useArturoCall.setState({ live: mine });
    return () => { if (useArturoCall.getState().live === mine) useArturoCall.setState({ live: null }); };
  }, [live]);
  return {
    callState, liveUser, liveArturo, inCall: isInCall(callState), muted,
    toggleMute: () => ctl.current!.toggleMute(),
    // One call at a time: with an agent's call live, starting this one asks first, and only a yes ends that call.
    toggle: async (opts: VoiceSessionStartOptions) => {
      const other = useCallStore.getState().call;
      if (!ctl.current!.inCall && other) {
        // Named for who answers it: the agent, or Arturo when the call went through him.
        const party = callPartyId(other, ARTURO_ID);
        const yes = await askSwitchCall(avatarHub.get(party)?.name || party, assistantName());
        if (!yes) return;
        useCallStore.getState().hangUp();
      }
      return ctl.current!.toggle(opts);
    },
    setContext: (opts: VoiceSessionStartOptions) => ctl.current!.setContext(opts),
  };
}
