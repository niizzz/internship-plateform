import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, Offer, STATUS_LABEL, ApplicationStatus, CATEGORY_COLOR } from '../api'
import { bankMeta } from '../lib/bankMeta'
import { postedInfo } from '../lib/format'
import StatusBadge from '../components/StatusBadge'
import Dashboard from '../components/Dashboard'
import RecentOffers from '../components/RecentOffers'
import CompanyLogo from '../components/CompanyLogo'
import CompanyHover from '../components/CompanyHoverCard'
import { useTilt } from '../lib/fx'

const STATUSES: ApplicationStatus[] = ['not_applied', 'applied', 'online_assessment', 'interview', 'offer', 'rejected']

export default function OffersPage() {
  const [bank, setBank] = useState('')
  const [category, setCategory] = useState('')
  const [country, setCountry] = useState('')
  const [status, setStatus] = useState<string>('')
  const [search, setSearch] = useState('')
  const [startYear, setStartYear] = useState('')

  const { data: offers = [], isLoading } = useQuery({
    queryKey: ['offers', { bank, category, country, status, search, startYear }],
    queryFn: () => api.listOffers({ bank, category, country, status, search, start_year: startYear }),
  })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: api.stats })

  const banks = useMemo(() => Object.keys(stats?.by_bank ?? {}).sort(), [stats])
  const startYears = useMemo(
    () => Object.keys(stats?.by_start_year ?? {}).filter(y => y !== 'unknown').sort(),
    [stats]
  )
  const countries = useMemo(
    () => Array.from(new Set(offers.map(o => o.country).filter(Boolean))).sort(),
    [offers]
  )

  return (
    <div className="max-w-7xl mx-auto px-6 py-6 space-y-6">
      <div className="animate-risein">
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500 font-mono flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-neon-cyan/70 fx-radar" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-neon-cyan" />
          </span>
          Live desk
        </div>
        <h1 className="mt-1 text-2xl font-semibold fx-textshine">S&amp;T Internship Pipeline</h1>
      </div>

      <div className="animate-risein" style={{ animationDelay: '60ms' }}><Dashboard /></div>
      <div className="animate-risein" style={{ animationDelay: '120ms' }}><RecentOffers /></div>

      <div className="pt-1">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-200">All offers</h2>
          <span className="text-[10px] font-mono text-slate-500">{offers.length} shown</span>
        </div>
        <FilterBar
          search={search} setSearch={setSearch}
          bank={bank} setBank={setBank} banks={banks}
          category={category} setCategory={setCategory}
          country={country} setCountry={setCountry} countries={countries}
          status={status} setStatus={setStatus}
          startYear={startYear} setStartYear={setStartYear} startYears={startYears}
        />

        {isLoading ? (
          <SkeletonGrid />
        ) : offers.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {offers.map((o, i) => (
              <div key={o.id} className="animate-rowin" style={{ animationDelay: `${Math.min(i, 14) * 45}ms` }}>
                <OfferCard o={o} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function FilterBar(props: any) {
  const inp = "px-3 py-2 bg-ink-900 border border-ink-700 rounded-md text-sm placeholder-slate-500 focus:border-neon-cyan/60 focus:shadow-neon-cyan transition-shadow"
  return (
    <div className="bg-ink-900/60 border border-ink-700 rounded-xl p-3 mb-5 flex flex-wrap gap-2 items-center backdrop-blur">
      <input
        placeholder="Search title, bank, location, start date…"
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
      <select value={props.startYear} onChange={e => props.setStartYear(e.target.value)} className={inp}>
        <option value="">Any start year</option>
        {props.startYears.map((y: string) => <option key={y} value={y}>Starts {y}</option>)}
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
  const m = bankMeta(o.bank)
  const posted = postedInfo(o.posted_at, o.first_seen_at)
  const tilt = useTilt(5)
  const facts = [
    { label: 'Start date', value: o.start_date_raw || 'see posting', highlight: true },
    { label: posted.label, value: posted.value },
    { label: 'Location', value: o.city || o.country || '—' },
    { label: 'Category', value: o.category },
  ]
  return (
    <CompanyHover bank={o.bank} facts={facts} className="block">
      <div ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}
        className="fx-tilt fx-glare relative rounded-xl">
      <Link to={`/offers/${o.id}`}
        className="fx-sheen group relative block bg-ink-900/80 border border-ink-700 rounded-xl p-4 transition-all overflow-hidden"
        style={{ boxShadow: 'none' }}
        onMouseEnter={e => { e.currentTarget.style.boxShadow = `0 0 0 1px ${m.color}66, 0 16px 40px -14px ${m.color}` }}
        onMouseLeave={e => { e.currentTarget.style.boxShadow = 'none' }}>
        <div className="absolute top-0 right-0 h-px w-24 opacity-0 group-hover:opacity-100 transition-opacity"
          style={{ background: `linear-gradient(90deg, transparent, ${m.color})` }} />
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <CompanyLogo bank={o.bank} size={40} />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono uppercase tracking-wider text-slate-500 truncate">{o.bank}</span>
                <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${catCls}`}>{o.category}</span>
              </div>
              <h3 className="mt-1 text-sm font-semibold text-slate-100 group-hover:text-neon-cyan transition-colors line-clamp-2">
                {o.role_title}
              </h3>
            </div>
          </div>
          <StatusBadge status={o.application_status} />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs text-slate-400">
          <Meta label="Location" value={o.city || o.country || '—'} />
          <Meta label="Program" value={o.program_type.replace(/_/g, ' ')} />
          <Meta label="Start" value={o.start_date_raw || '—'} />
          <Meta label={posted.label} value={posted.value} />
        </div>

        <div className="mt-3 pt-3 border-t border-ink-700 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <Pill on={o.has_tailored_cv} label="CV" />
            <Pill on={o.has_tailored_cover_letter} label="CL" />
          </div>
          <span className="text-[10px] font-mono transition-transform group-hover:translate-x-0.5"
            style={{ color: m.color }}>{m.symbol} →</span>
        </div>
      </Link>
      </div>
    </CompanyHover>
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
    <div className="bg-ink-900/60 border border-dashed border-ink-700 rounded-xl p-10 text-center relative overflow-hidden">
      <div className="mx-auto mb-4 relative h-10 w-10">
        <span className="absolute inset-0 rounded-full bg-neon-cyan/50 fx-radar" />
        <span className="absolute inset-0 rounded-full bg-neon-cyan/30 fx-radar" style={{ animationDelay: '0.7s' }} />
        <span className="absolute inset-[14px] rounded-full bg-neon-cyan" />
      </div>
      <div className="text-lg font-semibold text-slate-100 mb-1">Scanning the street…</div>
      <div className="text-sm text-slate-400">No offers in the DB yet — hit <span className="text-neon-cyan font-medium">Refresh</span> in the header to fire the scrapers.</div>
    </div>
  )
}
