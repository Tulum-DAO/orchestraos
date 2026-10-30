/**
 * arturoCopy.ts — the words a reply's copy button puts on the clipboard: what the reply says, as shown.
 * Tool cards and voice-call cards are not words, so they are left out; a turn that ran tools gives its text
 * parts in order, a blank line between them.
 */
import { isCallEndedLine, parseVoiceSegments } from './threadKit/calls';
import type { TurnPart } from './turnParts';

export function replyCopyText(text: string, parts?: TurnPart[]): string {
  const pieces = parts && parts.some((p) => p.kind !== 'text')
    ? parts.flatMap((p) => (p.kind === 'text' ? [p.text] : []))
    : parseVoiceSegments(text || '').flatMap((s) => (s.kind === 'prose' && !isCallEndedLine(s.text) ? [s.text] : []));
  return pieces.map((p) => p.trim()).filter(Boolean).join('\n\n');
}
