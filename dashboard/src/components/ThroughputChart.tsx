import { useState } from 'react';

interface DataPoint { date: string; count: number }
interface Props { data: DataPoint[] }

export function ThroughputChart({ data }: Props) {
  const [hover, setHover] = useState<{ x: number; y: number; date: string; count: number } | null>(null);

  if (!data.length) return <p className="text-neutral-600 text-sm">No task data yet</p>;

  const W = 600, H = 200, PAD = 40;
  const maxCount = Math.max(...data.map(d => d.count), 1);
  const points = data.map((d, i) => ({
    x: PAD + (i / Math.max(data.length - 1, 1)) * (W - PAD * 2),
    y: H - PAD - (d.count / maxCount) * (H - PAD * 2),
    ...d
  }));
  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-48">
        {/* Grid lines */}
        {[0, 25, 50, 75, 100].map(pct => {
          const y = H - PAD - (pct / 100) * (H - PAD * 2);
          return <line key={pct} x1={PAD} y1={y} x2={W - PAD} y2={y} stroke="#262626" strokeWidth={1} />;
        })}
        {/* Y-axis labels */}
        {[0, Math.round(maxCount / 2), maxCount].map((v, i) => (
          <text key={i} x={PAD - 8} y={H - PAD - (v / maxCount) * (H - PAD * 2) + 4}
            textAnchor="end" fill="#525252" fontSize={10}>{v}</text>
        ))}
        {/* X-axis labels (first, middle, last) */}
        {[0, Math.floor(data.length / 2), data.length - 1].filter(i => data[i]).map(i => (
          <text key={i} x={points[i].x} y={H - 8} textAnchor="middle" fill="#525252" fontSize={10}>
            {data[i].date.slice(5)}
          </text>
        ))}
        {/* Line */}
        <path d={pathD} fill="none" stroke="#a3a3a3" strokeWidth={2} />
        {/* Points */}
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={3} fill="#a3a3a3"
            onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)}
            className="cursor-pointer hover:fill-white" />
        ))}
      </svg>
      {hover && (
        <div className="absolute bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs pointer-events-none"
          style={{ left: hover.x, top: hover.y - 30 }}>
          {hover.date}: {hover.count} tasks
        </div>
      )}
    </div>
  );
}
