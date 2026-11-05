/**
 * CallTranscript — the live call's words, turn by turn, labelled You / the agent's name (stores/callStore.ts turns,
 * from the two speakers' own captions). The newest words fade in with the shared reveal (components/kit/RevealText):
 * in place, append only, nothing reflows. Follows the end while you are at it.
 *
 * `skin`: an agent's own call is drawn in its chat's skin (components/kit/Turns: Bubbles' blue bubble and plain words,
 * no names; Modern's bubble and flat reply, labelled). Without one (a call with Arturo) it is the labelled list.
 */
import { useEffect, useRef } from 'react';
import RevealText from '../kit/RevealText';
import { AgentTurn, UserTurn } from '../kit/Turns';
import type { CallTurn } from '../../lib/callTranscript';

export default function CallTranscript({ turns, name, className = '', skin }: { turns: CallTurn[]; name: string; className?: string; skin?: 'bubbles' | 'modern' }) {
  const box = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && atEnd.current) el.scrollTop = el.scrollHeight;
  });
  return (
    <div ref={box} className={`overflow-y-auto overscroll-contain ${className}`} data-call-transcript
         onScroll={(e) => { const el = e.currentTarget; atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}>
      {turns.length === 0 ? (
        <p className="py-6 text-center text-px13 text-muted-foreground">The call's words show here as you talk.</p>
      ) : (
        skin ? (
          <ol className={`flex flex-col gap-2.5 py-2${skin === 'modern' ? ' kit-modern' : ''}`} data-turn-skin={skin}>
            {turns.map((t, i) => {
              const label = skin === 'modern' ? <span className="text-px11 font-medium text-muted-foreground">{t.role === 'user' ? 'You' : name}</span> : undefined;
              const words = <RevealText text={t.text} done={t.final} />;
              return (
                <li key={i} data-call-turn={t.role}>
                  {t.role === 'user' ? <UserTurn skin={skin} label={label}>{words}</UserTurn> : <AgentTurn skin={skin} plain label={label}>{words}</AgentTurn>}
                </li>
              );
            })}
          </ol>
        ) : (
        <ol className="flex flex-col gap-3 py-2">
          {turns.map((t, i) => (
            <li key={i} className="flex flex-col gap-0.5" data-call-turn={t.role}>
              <span className={`text-px11 font-medium ${t.role === 'user' ? 'text-muted-foreground' : 'text-foreground'}`}>
                {t.role === 'user' ? 'You' : name}
              </span>
              <RevealText text={t.text} done={t.final} className="text-px14 leading-relaxed text-foreground" />
            </li>
          ))}
        </ol>
        )
      )}
    </div>
  );
}
