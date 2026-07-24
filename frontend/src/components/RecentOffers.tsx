import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, DashboardData, RecentOffer, ApplicationStatus } from '../api'
import { bankMeta } from '../lib/bankMeta'
import { ageLabel, ageTone, daysAgo, postedInfo } from '../lib/format'
import CompanyLogo from './CompanyLogo'
import CompanyHover from './CompanyHoverCard'
import StatusBadge from './StatusBadge'

const TONE: Record<string, string> = { fresh: '#34d399', recent: '#22d3ee', aging: '#fbbf24', stale: '#64748b' }

export default function RecentOffers() {
  const { data } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 20_000 })
  const d = data as DashboardData | undefined
  const rows = d?.recent_offers ?? []

  return (
    <div className="bg-ink-900/70 border border-ink-700 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-ink-700">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-green opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-neon-green" />
          </span>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">Fresh offers</h3>
          <span className="text-[10px] font-mono text-slate-500">newest on the desk</span>
        </div>
        <div className="text-[11px] font-mono text-slate-400">
          <span className="text-neon-cyan font-semibold tabular-nums">+{d?.pipeline.new_this_week ?? 0}</span> this week
        </div>
      </div>

      <div className="divide-y divide-ink-800/70">
        {rows.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-slate-500">No offers yet — hit Refresh to fire the scrapers.</div>
        )}
        {rows.map((o, i) => <Row key={o.id} o={o} i={i} />)}
      </div>
    </div>
  )
}

function Row({ o, i }: { o: RecentOffer; i: number }) {
  const m = bankMeta(o.bank)
  // How long it's been live on the BANK's site (their publication date), falling
  // back to when we first saw it if the ATS doesn't expose one.
  const liveDays = daysAgo(o.posted_at) ?? o.age_days
  const tone = ageTone(liveDays)
  const posted = postedInfo(o.posted_at, o.first_seen_at)
  const facts = [
    { label: 'Start date', value: o.start_date_raw || 'see posting', highlight: true },
    { label: posted.label, value: posted.value },
    { label: 'Location', value: o.city || o.country || '—' },
    { label: 'Category', value: o.category },
  ]
  return (
    <CompanyHover bank={o.bank} facts={facts} className="block">
      <Link to={`/offers/${o.id}`}
        className="group relative grid grid-cols-[auto,1fr,auto] items-center gap-3 px-4 py-2.5
                   hover:bg-ink-800/50 transition-colors animate-rowin"
        style={{ animationDelay: `${i * 40}ms` }}>
        <span className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
          style={{ background: m.color }} />
        <CompanyLogo bank={o.bank} size={34} rounded={9} />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono px-1 rounded" style={{ background: `${m.color}1f`, color: m.color }}>{m.symbol}</span>
            <span className="text-sm text-slate-100 truncate group-hover:text-neon-cyan transition-colors">{o.role_title}</span>
          </div>
          <div className="text-[11px] text-slate-500 truncate capitalize">{(o.city || o.country || '—')} · {o.category}</div>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden sm:block"><StatusBadge status={o.application_status as ApplicationStatus} /></div>
          <div className="text-right w-16">
            <div className="text-xs font-mono font-semibold tabular-nums" style={{ color: TONE[tone] }}>{ageLabel(liveDays)}</div>
            <div className="text-[9px] text-slate-600 font-mono uppercase tracking-wider">
              {o.posted_at ? 'on site' : 'first seen'}
            </div>
          </div>
        </div>
      </Link>
    </CompanyHover>
  )
}
