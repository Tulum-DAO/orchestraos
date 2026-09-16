interface Props {
  agent: string;
  tasksDone: number;
  successRate: number;
  errors: number;
  sparkline: number[];
}

export function EffectivenessCard({ agent, tasksDone, successRate, errors, sparkline }: Props) {
  const initial = agent[0].toUpperCase();
  const maxSpark = Math.max(...sparkline, 1);
  const sparkW = 80, sparkH = 24;
  const sparkPoints = sparkline.map((v, i) =>
    `${(i / Math.max(sparkline.length - 1, 1)) * sparkW},${sparkH - (v / maxSpark) * sparkH}`
  ).join(' ');

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border border-neutral-800 bg-neutral-900">
      <div className="w-8 h-8 rounded-full bg-neutral-700 flex items-center justify-center text-sm font-bold shrink-0">
        {initial}
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-sm truncate">{agent}</p>
        <p className="text-xs text-neutral-500">
          {tasksDone} done &middot; {successRate}% rate{errors > 0 && ` \u00b7 ${errors} errors`}
        </p>
      </div>
      <svg width={sparkW} height={sparkH} className="shrink-0">
        <polyline points={sparkPoints} fill="none" stroke="#737373" strokeWidth={1.5} />
      </svg>
    </div>
  );
}
