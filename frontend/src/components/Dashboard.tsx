import { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, DashboardData } from '../api'
import { comma } from '../lib/format'
import { useTilt } from '../lib/fx'
import CountUp from './CountUp'
import Columns from './Columns'
import Sparkline from './Sparkline'

const CATS = [
  { key: 'sales', label: 'Sales', color: '#22d3ee' },
  { key: 'trading', label: 'Trading', color: '#a855f7' },
  { key: 'structuring', label: 'Structuring', color: '#34d399' },
  { key: 'markets', label: 'Markets', color: '#fbbf24' },
]

export default function Dashboard() {
  const { data } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 20_000 })
  const d = data as DashboardData | undefined
  const f = d?.funnel
  const p = d?.pipeline

  const monthDelta = (f?.sent_this_month ?? 0) - (f?.sent_last_month ?? 0)

  return (
    <div className="space-y-4">
      {/* Application funnel — four hero stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          label="Apps sent" accent="#22d3ee" value={f?.apps_sent ?? 0}
          delta={f && f.sent_today > 0 ? { dir: 'up', text: `${f.sent_today} today`, good: true } : undefined}
          sub={f ? `${f.sent_this_month} this month` : ''}
        />
        <StatCard
          label="Responses" accent="#a855f7" value={f?.responses ?? 0}
          delta={f && f.apps_sent > 0 ? { dir: f.hit_rate >= 10 ? 'up' : 'flat', text: `${f.hit_rate}% hit rate`, good: f.hit_rate >= 10 } : undefined}
          sub={f && f.apps_sent === 0 ? 'awaiting first send' : 'replies of any kind'}
        />
        <StatCard
          label="Interviews" accent="#fbbf24" value={f?.interviews ?? 0}
          delta={f && f.interviews_this_week > 0 ? { dir: 'up', text: `${f.interviews_this_week} this week`, good: true } : undefined}
          sub={f && f.interviews === 0 ? 'none yet — keep grinding' : 'reached the desk'}
        />
        <StatCard
          label="Offers" accent="#34d399" value={f?.offers ?? 0}
          sub={f && f.offers === 0 ? 'still struggling tho' : 'the endgame'}
        />
      </div>

      {/* Applications-over-time + the struggle-o-meter */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Panel className="lg:col-span-3" title="Applications" tag="last 30 days"
          right={
            <div className="flex items-baseline gap-2 font-mono text-[11px]">
              <span className="text-slate-400">this month</span>
              <span className="text-slate-100 font-semibold tabular-nums text-sm">{f?.sent_this_month ?? 0}</span>
              <Trend delta={monthDelta} suffix={`vs ${f?.sent_last_month ?? 0} last`} />
            </div>
          }>
          {d && <Columns data={d.apps_series} color="#22d3ee" unit="app(s)" />}
          {f && f.apps_sent === 0 && (
            <div className="mt-2 text-[11px] text-slate-500 font-mono">
              Your application history fills this in — hit <span className="text-neon-cyan">Apply</span> on an offer to start the streak.
            </div>
          )}
        </Panel>

        <Panel className="lg:col-span-2" title="Struggle-o-meter" tag="live pipeline">
          <div className="flex items-end justify-between">
            <div className="relative">
              <span className="absolute -left-3 -top-2 h-3 w-3 rounded-full bg-neon-cyan/60 fx-radar" />
              <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">Live offers</div>
              <div className="text-3xl font-semibold text-slate-100 tabular-nums leading-none mt-1">
                <CountUp value={p?.active_offers ?? 0} />
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">New / 7d</div>
              <div className="text-xl font-semibold text-neon-cyan tabular-nums leading-none mt-1">
                +<CountUp value={p?.new_this_week ?? 0} />
              </div>
            </div>
            <div className="w-24 -mb-1">{d && <Sparkline data={d.offers_series} color="#22d3ee" />}</div>
          </div>

          <div className="mt-4 space-y-1.5">
            {CATS.map(c => {
              const n = p?.by_category?.[c.key] ?? 0
              const total = p?.active_offers || 1
              return (
                <div key={c.key} className="flex items-center gap-2">
                  <div className="w-[70px] text-[11px] text-slate-400 shrink-0">{c.label}</div>
                  <div className="flex-1 h-2 rounded-full bg-ink-800 overflow-hidden">
                    <div className="relative h-full rounded-full transition-all duration-700 overflow-hidden"
                      style={{ width: `${(n / total) * 100}%`, background: c.color, boxShadow: `0 0 8px -1px ${c.color}` }}>
                      {n > 0 && <div className="absolute inset-0 fx-barshimmer" />}
                    </div>
                  </div>
                  <div className="w-6 text-right text-[11px] font-mono tabular-nums text-slate-300">{n}</div>
                </div>
              )
            })}
          </div>

          <div className="mt-4 pt-3 border-t border-ink-700 grid grid-cols-2 gap-3 text-[11px] font-mono">
            <MiniStat label="ghost rate" value={f ? `${f.ghost_rate}%` : '—'}
              tone={f && f.ghost_rate > 50 ? 'bad' : 'muted'} />
            <MiniStat label="streak" value={f ? `${f.streak_days}d` : '—'} tone={f && f.streak_days > 0 ? 'good' : 'muted'} />
          </div>
        </Panel>
      </div>
    </div>
  )
}

function StatCard({ label, value, accent, delta, sub }: {
  label: string; value: number; accent: string
  delta?: { dir: 'up' | 'down' | 'flat'; text: string; good: boolean }
  sub?: string
}) {
  const tilt = useTilt(6)
  return (
    <div ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}
      className="fx-tilt fx-glare fx-sheen group relative bg-ink-900/80 border border-ink-700 rounded-xl p-4 overflow-hidden
                    hover:border-ink-600 transition-all">
      <div className="absolute inset-x-0 top-0 h-[2px] opacity-70 group-hover:opacity-100 transition-opacity"
        style={{ background: `linear-gradient(90deg, transparent, ${accent}, transparent)` }} />
      <div className="absolute -top-10 -right-8 w-28 h-28 rounded-full blur-2xl opacity-20 group-hover:opacity-40 transition-opacity duration-500"
        style={{ background: accent }} />
      <div className="relative">
        <div className="text-[10px] uppercase tracking-[0.18em] text-slate-500 font-mono">{label}</div>
        <div className="mt-1.5 text-[32px] leading-none font-semibold text-slate-100 tabular-nums">
          <CountUp value={value} format={n => comma(Math.round(n))} />
        </div>
        <div className="mt-2 h-4 flex items-center gap-1.5 text-[11px]">
          {delta ? (
            <span className="inline-flex items-center gap-1 font-mono"
              style={{ color: delta.good ? '#34d399' : delta.dir === 'flat' ? '#94a3b8' : '#fb7185' }}>
              {delta.dir === 'up' ? '▲' : delta.dir === 'down' ? '▼' : '◆'} {delta.text}
            </span>
          ) : (
            <span className="text-slate-500 font-mono">{sub}</span>
          )}
          {delta && sub && <span className="text-slate-600 font-mono">· {sub}</span>}
        </div>
      </div>
    </div>
  )
}

function Panel({ title, tag, right, className = '', children }: {
  title: string; tag?: string; right?: ReactNode; className?: string; children: ReactNode
}) {
  return (
    <div className={`fx-sheen bg-ink-900/70 border border-ink-700 rounded-xl p-4 hover:border-ink-600 transition-colors ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">{title}</h3>
          {tag && <span className="text-[10px] font-mono text-slate-500">{tag}</span>}
        </div>
        {right}
      </div>
      {children}
    </div>
  )
}

function Trend({ delta, suffix }: { delta: number; suffix?: string }) {
  const up = delta > 0, down = delta < 0
  const color = up ? '#34d399' : down ? '#fb7185' : '#94a3b8'
  return (
    <span className="inline-flex items-center gap-1" style={{ color }}>
      {up ? '▲' : down ? '▼' : '◆'}{Math.abs(delta)}
      {suffix && <span className="text-slate-600 ml-1">{suffix}</span>}
    </span>
  )
}

function MiniStat({ label, value, tone }: { label: string; value: string; tone: 'good' | 'bad' | 'muted' }) {
  const color = tone === 'good' ? 'text-neon-green' : tone === 'bad' ? 'text-neon-rose' : 'text-slate-300'
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-500">{label}</span>
      <span className={`font-semibold tabular-nums ${color}`}>{value}</span>
    </div>
  )
}
