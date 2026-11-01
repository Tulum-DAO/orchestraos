/**
 * callPhase.ts — what a live call is doing, from the events the voice session gives (lib/voiceSession.ts): the call
 * store's state and the avatar's (components/avatar/avatarCore.ts listening / thinking / talking).
 *
 *   connecting  the socket is opening (the microphone is being granted)
 *   listening   the call is up and it is your turn: you may be talking
 *   thinking    you finished a turn (your words were committed) and the agent has not started speaking: the avatar's
 *               silent dots. The first ~3 s are silent by design; past that the SERVER may speak a short
 *               acknowledgement (that audio is playback, so it reads as talking)
 *   talking     the agent's voice is playing
 *   ended       the call is over
 * Pure, so every transition is tested without a microphone.
 */
export type CallPhase = 'connecting' | 'listening' | 'thinking' | 'talking' | 'ended';

export type CallEvent =
  | { type: 'session'; state: 'idle' | 'connecting' | 'live' | 'error' }
  | { type: 'userSpeaking' }
  | { type: 'userDone' }
  | { type: 'playback'; playing: boolean };

export function nextPhase(phase: CallPhase, e: CallEvent): CallPhase {
  if (phase === 'ended') return 'ended';
  switch (e.type) {
    case 'session':
      if (e.state === 'connecting') return 'connecting';
      if (e.state === 'live') return phase === 'connecting' ? 'listening' : phase;
      return 'ended';                                   // idle after a call, or an error
    case 'playback':
      if (phase === 'connecting') return phase;
      return e.playing ? 'talking' : 'listening';
    case 'userSpeaking':
      // Your words while the agent talks are a barge-in: still its voice playing until it stops (the server cuts it).
      return phase === 'connecting' || phase === 'talking' ? phase : 'listening';
    case 'userDone':
      return phase === 'listening' ? 'thinking' : phase;
  }
}

/** The avatar state for a call phase. */
export function avatarStateForCall(phase: CallPhase): 'listening' | 'thinking' | 'talking' | 'idle' {
  return phase === 'thinking' ? 'thinking' : phase === 'talking' ? 'talking' : phase === 'ended' ? 'idle' : 'listening';
}
