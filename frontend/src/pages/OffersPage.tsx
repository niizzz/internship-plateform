import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, Offer, STATUS_LABEL, ApplicationStatus, CATEGORY_COLOR } from '../api'
import StatusBadge from '../components/StatusBadge'

const STATUSES: ApplicationStatus[] = ['not_applied', 'applied', 'online_assessment', 'interview', 'offer', 'rejected']

export default function OffersPage() {
  const [bank, setBank] = useState('')
  const [category, setCategory] = useState('')
  const [country, setCountry] = useState('')
  const [status, setStatus] = useState<string>('')
  const [search, setSearch] = useState('')

  const { data: offers = [], isLoading } = useQuery({
    queryKey: ['offers', { bank, category, country, status, search }],
    queryFn: () => api.listOffers({ bank, category, country, status, search }),
  })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: api.stats })

  const banks = useMemo(() => Object.keys(stats?.by_bank ?? {}).sort(), [stats])
  const countries = useMemo(
    () => Array.from(new Set(offers.map(o => o.country).filter(Boolean))).sort(),
    [offers]
  )

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <HeroStats stats={stats} offerCount={offers.length} />

      <FilterBar
        search={search} setSearch={setSearch}
        bank={bank} setBank={setBank} banks={banks}
        category={category} setCategory={setCategory}
        country={country} setCountry={setCountry} countries={countries}
        status={status} setStatus={setStatus}
      />

      {isLoading ? (
        <SkeletonGrid />
      ) : offers.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {offers.map(o => <OfferCard key={o.id} o={o} />)}
        </div>
      )}
    </div>
  )
}

function HeroStats({ stats, offerCount }: { stats: any; offerCount: number }) {
  return (
    <div className="mb-5 flex items-end justify-between flex-wrap gap-3">
      <div>
        <div className="text-[11px] uppercase tracking-[0.2em] text-slate-500 font-mono">Live Desk</div>
        <h1 className="mt-1 text-2xl font-semibold text-slate-100">
          S&amp;T Internship Pipeline
        </h1>
        <div className="mt-1 text-sm text-slate-400">
          <span className="text-neon-cyan font-mono font-semibold tabular-nums">{stats?.total_active_offers ?? offerCount}</span>
          <span className="ml-1">active offers</span>
          {Object.entries(stats?.by_status ?? {}).filter(([_, c]) => (c as number) > 0).map(([s, c]) => (
            <span key={s} className="ml-3 inline-flex items-center gap-1">
              <StatusBadge status={s as ApplicationStatus} />
              <span className="font-mono tabular-nums text-slate-300">{c as number}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function FilterBar(props: any) {
  const inp = "px-3 py-2 bg-ink-900 border border-ink-700 rounded-md text-sm placeholder-slate-500 focus:border-neon-cyan/60 focus:shadow-neon-cyan transition-shadow"
  return (
    <div className="bg-ink-900/60 border border-ink-700 rounded-xl p-3 mb-5 flex flex-wrap gap-2 items-center backdrop-blur">
      <input
        placeholder="Search title or location…"
        value={props.search} onChange={e => props.setSearch(e.target.value)}
        className={`${inp} flex-1 min-w-[220px]`}
      />
      <select value={props.bank} onChange={e => props.setBank(e.target.value)} className={inp}>
        <option value="">All banks</option>
        {props.banks.map((b: string) => <option key={b}>{b}</option>)}
      </select>
      <select value={props.category} onChange={e => props.setCategory(e.target.value)} className={inp}>
        <option value="">All categories</option>
        <option value="sales">Sales</option>
        <option value="trading">Trading</option>
        <option value="structuring">Structuring</option>
        <option value="markets">Markets</option>
      </select>
      <select value={props.country} onChange={e => props.setCountry(e.target.value)} className={inp}>
        <option value="">All countries</option>
        {props.countries.map((c: string) => <option key={c}>{c}</option>)}
      </select>
      <select value={props.status} onChange={e => props.setStatus(e.target.value)} className={inp}>
        <option value="">Any status</option>
        {STATUSES.map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
      </select>
    </div>
  )
}

function OfferCard({ o }: { o: Offer }) {
  const catCls = CATEGORY_COLOR[o.category] ?? CATEGORY_COLOR.markets
  return (
    <Link to={`/offers/${o.id}`}
      className="group relative block bg-ink-900/80 border border-ink-700 rounded-xl p-4 hover:border-neon-cyan/40 hover:shadow-neon-cyan transition-all overflow-hidden">
      <div className="absolute top-0 right-0 h-px w-24 bg-gradient-to-r from-transparent to-neon-cyan/40 opacity-0 group-hover:opacity-100 transition-opacity" />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono uppercase tracking-wider text-slate-500">{o.bank}</span>
            <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${catCls}`}>{o.category}</span>
          </div>
          <h3 className="mt-1 text-sm font-semibold text-slate-100 group-hover:text-neon-cyan transition-colors line-clamp-2">
            {o.role_title}
          </h3>
        </div>
        <StatusBadge status={o.application_status} />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs text-slate-400">
        <Meta label="Location" value={o.city || o.country || '—'} />
        <Meta label="Program" value={o.program_type.replace(/_/g, ' ')} />
        <Meta label="Start" value={o.start_date_raw || '—'} />
        <Meta label="Duration" value={o.duration || '—'} />
      </div>

      <div className="mt-3 pt-3 border-t border-ink-700 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <Pill on={o.has_tailored_cv} label="CV" />
          <Pill on={o.has_tailored_cover_letter} label="CL" />
        </div>
        <span className="text-[10px] text-slate-500 font-mono">view →</span>
      </div>
    </Link>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-600 font-mono">{label}</div>
      <div className="text-slate-300 capitalize truncate">{value}</div>
    </div>
  )
}

function Pill({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={`px-1.5 py-0.5 rounded font-mono text-[10px] ${
      on
        ? 'bg-neon-cyan/15 text-neon-cyan ring-1 ring-inset ring-neon-cyan/30'
        : 'bg-ink-800 text-slate-500 ring-1 ring-inset ring-ink-700'
    }`}>{label}{on ? ' ✓' : ' —'}</span>
  )
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="bg-ink-900/60 border border-ink-700 rounded-xl p-4 h-40 animate-pulse" />
      ))}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="bg-ink-900/60 border border-dashed border-ink-700 rounded-xl p-10 text-center">
      <div className="text-lg font-semibold text-slate-100 mb-1">No offers in the DB yet</div>
      <div className="text-sm text-slate-400">Hit <span className="text-neon-cyan font-medium">Refresh</span> in the header to fire the scrapers.</div>
    </div>
  )
}
