interface Props {
  label: string;
  percent: number;
  color: string;
  sublabel?: string;
}

export function UsageBar({ label, percent, color, sublabel }: Props) {
  return (
    <div className="mb-3">
      <div className="flex justify-between text-sm mb-1">
        <span className="font-medium">{label}</span>
        <span className="text-neutral-500">{Math.round(percent)}%</span>
      </div>
      <div className="w-full h-3 rounded-full bg-neutral-800">
        <div className={`h-3 rounded-full transition-all ${color}`} style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
      {sublabel && <p className="text-xs text-neutral-600 mt-0.5">{sublabel}</p>}
    </div>
  );
}
