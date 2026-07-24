import { SeriesPoint } from '../api'

// Compact area+line trend for a stat panel (2px line, ~10% area wash per the
// dataviz marks). Single series, no axes — context, not a full chart.
export default function Sparkline({
  data, color = '#a855f7', height = 42,
}: { data: SeriesPoint[]; color?: string; height?: number }) {
  const n = data.length
  if (n < 2) return <div style={{ height }} />
  const max = Math.max(1, ...data.map(d => d.count))
  const W = 100, H = height
  const pts = data.map((d, i) => [(i / (n - 1)) * W, H - (d.count / max) * (H - 5) - 2])
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ')
  const area = `${line} L${W},${H} L0,${H} Z`
  const gid = `sl-${color.replace('#', '')}`
  const last = pts[pts.length - 1]

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke={color} strokeWidth={1.8}
        strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <circle cx={last[0]} cy={last[1]} r={2.6} fill={color} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
