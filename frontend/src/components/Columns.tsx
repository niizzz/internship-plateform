import { useEffect, useState } from 'react'
import { SeriesPoint } from '../api'

// Single-series column chart (magnitude over time). Follows the dataviz marks:
// rounded data-end, grows from one baseline, one hue, 2px surface gaps, a
// per-column hover tooltip, empty periods shown as faint stubs. No legend — the
// panel title names the one series.
export default function Columns({
  data, color = '#22d3ee', height = 116, unit = '',
}: {
  data: SeriesPoint[]
  color?: string
  height?: number
  unit?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const [mounted, setMounted] = useState(false)
  useEffect(() => { const t = setTimeout(() => setMounted(true), 30); return () => clearTimeout(t) }, [])

  const max = Math.max(1, ...data.map(d => d.count))
  const fmtDate = (iso: string) => {
    const d = new Date(iso + 'T00:00:00')
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  return (
    <div className="relative select-none" style={{ height }}>
      <div className="flex items-end gap-[2px] h-full">
        {data.map((d, i) => {
          const hp = (d.count / max) * 100
          const active = hover === i
          return (
            <div
              key={i}
              className="relative flex-1 h-full flex items-end cursor-default"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              <div
                className="w-full rounded-t-[3px]"
                style={{
                  height: mounted ? `${Math.max(hp, d.count ? 5 : 2)}%` : '2%',
                  maxWidth: 22, margin: '0 auto',
                  background: d.count ? color : '#1d2a52',
                  opacity: d.count ? (active ? 1 : 0.82) : 0.5,
                  boxShadow: active && d.count ? `0 0 12px -2px ${color}` : 'none',
                  transition: `height 700ms cubic-bezier(0.22,1,0.36,1) ${i * 12}ms, opacity 140ms, box-shadow 140ms`,
                }}
              />
            </div>
          )
        })}
      </div>

      {hover !== null && data[hover] && (
        <div
          className="absolute z-10 -translate-x-1/2 -translate-y-full pointer-events-none whitespace-nowrap
                     px-2 py-1 rounded-md bg-ink-950 border border-ink-600 shadow-glow-soft"
          style={{ left: `${((hover + 0.5) / data.length) * 100}%`, top: -4 }}
        >
          <div className="text-[10px] font-mono text-slate-400">{fmtDate(data[hover].date)}</div>
          <div className="text-xs font-semibold tabular-nums" style={{ color }}>
            {data[hover].count}{unit && <span className="text-slate-500 font-normal ml-1">{unit}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
