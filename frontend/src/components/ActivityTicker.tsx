import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, DashboardData, ActivityItem } from '../api'
import { bankMeta } from '../lib/bankMeta'
import { ago } from '../lib/format'

const LABEL: Record<string, { text: string; color: string }> = {
  new: { text: 'NEW', color: '#34d399' },
  applied: { text: 'APPLIED', color: '#22d3ee' },
  online_assessment: { text: 'OA', color: '#fbbf24' },
  interview: { text: 'INTERVIEW', color: '#a855f7' },
  offer: { text: 'OFFER', color: '#34d399' },
  rejected: { text: 'REJECTED', color: '#fb7185' },
}

function deskTag(role: string): string {
  const w = (role || '').split(/[^a-zA-Z]+/).find(x => x.length >= 3) || role
  return w.slice(0, 6).toUpperCase()
}

export default function ActivityTicker() {
  const { data } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 20_000 })
  const items = (data as DashboardData | undefined)?.activity ?? []
  if (items.length === 0) return null
  // Duplicate the run so the marquee loops seamlessly.
  const run = [...items, ...items]

  return (
    <div className="relative border-b border-ink-700 bg-ink-950/60 overflow-hidden group">
      <div className="ticker-fade-l" /><div className="ticker-fade-r" />
      <div className="ticker-track flex items-center whitespace-nowrap py-2 group-hover:[animation-play-state:paused]">
        {run.map((it, i) => <Item key={i} it={it} />)}
      </div>
    </div>
  )
}

function Item({ it }: { it: ActivityItem }) {
  const m = bankMeta(it.bank)
  const lab = LABEL[it.label] ?? { text: it.label.toUpperCase(), color: '#94a3b8' }
  return (
    <>
      <Link to={`/offers/${it.id}`} className="inline-flex items-center gap-2 px-4 hover:brightness-125 transition">
        <span className="font-mono text-[11px] text-slate-400">
          <span style={{ color: m.color }}>{m.symbol}</span>
          <span className="text-slate-600">.{deskTag(it.role_title)}</span>
        </span>
        <span className="font-mono text-[10px] font-bold tracking-wide" style={{ color: lab.color }}>{lab.text}</span>
        <span className="font-mono text-[10px] text-slate-600">{ago(it.at)}</span>
      </Link>
      <span className="text-ink-600 select-none">·</span>
    </>
  )
}
