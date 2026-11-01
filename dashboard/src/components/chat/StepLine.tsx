/**
 * StepLine — one short, quiet line saying what the agent is doing right now (lib/liveStep.ts): "Running a shell
 * command: npm test", "Reading plan.md", "Thinking…", "Waiting for your answer", "Idle". Used where the agent's own
 * status line is not (the docked panel, the cards); the agent page says it in its status line above the composer.
 */
import { Loader2 } from 'lucide-react';
import { stepText, type AgentStep } from '../../lib/liveStep';

export function StepLine({ step, className = '' }: { step: AgentStep | null | undefined; className?: string }) {
  if (!step) return null;
  const busy = step.kind === 'tool' || step.kind === 'thinking';
  const tone = busy ? 'text-tone-orange-300' : step.kind === 'waiting' ? 'text-tone-amber-300' : 'text-tone-500';
  return (
    <div className={`flex items-center gap-1.5 min-w-0 text-[11px] ${tone} ${className}`.trim()} role="status" data-step={step.kind}>
      {busy ? <Loader2 size={11} className="animate-spin shrink-0" aria-hidden />
        : <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${step.kind === 'waiting' ? 'bg-amber-400' : 'bg-tone-600'}`} aria-hidden />}
      <span className="truncate">{stepText(step)}</span>
    </div>
  );
}
