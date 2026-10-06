import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, EventRegStatus, NewEvent, RecruitingEvent } from '../api'
import CompanyLogo from '../components/CompanyLogo'
import { bankMeta } from '../lib/bankMeta'
import { openTab } from '../lib/openTab'

type Scope = 'relevant' | 'markets' | 'all'

const SCOPES: { id: Scope; label: string }[] = [
  { id: 'relevant', label: 'Markets + firm-wide' },
  { id: 'markets', label: 'Markets only' },
  { id: 'all', label: 'All divisions' },
]

const REL_STYLE: Record<string, { label: string; color: string }> = {
  markets: { label: 'Markets', color: '#34d399' },
  general: { label: 'Firm-wide', color: '#22d3ee' },
  other: { label: 'Other division', color: '#64748b' },
}

const TYPE_LABEL: Record<string, string> = {
  insight: 'Insight', info_session: 'Info session', networking: 'Networking',
  workshop: 'Workshop', competition: 'Competition', event: 'Event',
}

const STATUS: { id: EventRegStatus; label: string; color: string }[] = [
  { id: 'not_registered', label: 'Not registered', color: '#94a3b8' },
  { id: 'registered', label: 'Registered', color: '#34d399' },
  { id: 'attended', label: 'Attended', color: '#a855f7' },
  { id: 'skipped', label: 'Skipped', color: '#64748b' },
]

const DAY = 86_400_000

function daysUntil(iso: string | null): number | null {
  if (!iso) return null
  const d = new Date(iso)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const that = new Date(d); that.setHours(0, 0, 0, 0)
  return Math.round((that.getTime() - today.getTime()) / DAY)
}

function fmtTime(e: RecruitingEvent): string {
  if (!e.starts_at || e.all_day) return e.all_day ? 'time on sign-up page' : 'date TBC'
  const t = (iso: string) => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  const sameDay = e.ends_at && e.ends_at.slice(0, 10) === e.starts_at.slice(0, 10)
  const span = sameDay ? `${t(e.starts_at)}–${t(e.ends_at!)}` : t(e.starts_at)
  return `${span}${e.timezone ? ` ${shortTz(e.timezone)}` : ''}`
}

function shortTz(tz: string): string {
  if (tz === 'Europe/London') return 'UK'
  if (tz.startsWith('Europe/')) return tz.slice(7).replace('_', ' ')
  return tz
}

function isNew(e: RecruitingEvent): boolean {
  return Date.now() - new Date(e.first_seen_at + 'Z').getTime() < 2 * DAY
}

export default function EventsPage() {
  const qc = useQueryClient()
  const [scope, setScope] = useState<Scope>('relevant')
  const [bank, setBank] = useState('')
  const [openOnly, setOpenOnly] = useState(false)
  const [showPast, setShowPast] = useState(false)
  const [adding, setAdding] = useState(false)

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['events', showPast],
    queryFn: () => api.listEvents(showPast),
    refetchInterval: 30_000,
  })

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.updateEvent>[1] }) =>
      api.updateEvent(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['events'] }),
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteEvent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['events'] }),
  })

  const banks = useMemo(() => [...new Set(events.map(e => e.bank))].sort(), [events])

  const visible = useMemo(() => events.filter(e => {
    const mine = e.reg_status === 'registered' || e.reg_status === 'attended'
    if (!mine && !e.manual) {
      if (scope === 'markets' && e.relevance !== 'markets') return false
      if (scope === 'relevant' && e.relevance === 'other') return false
    }
    if (bank && e.bank !== bank) return false
    if (openOnly && !e.registration_open && !mine) return false
    return true
  }), [events, scope, bank, openOnly])

  const groups = useMemo(() => {
    const m = new Map<string, RecruitingEvent[]>()
    for (const e of visible) {
      const k = e.starts_at
        ? new Date(e.starts_at).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
        : 'Date TBC'
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(e)
    }
    return [...m.entries()]
  }, [visible])

  const stats = useMemo(() => {
    const open = visible.filter(e => e.registration_open).length
    const registered = events.filter(e => e.reg_status === 'registered').length
    const closingSoon = visible.filter(e => {
      const d = daysUntil(e.registration_deadline)
      return e.registration_open && d !== null && d >= 0 && d <= 7
    }).length
    return { shown: visible.length, open, registered, closingSoon }
  }, [visible, events])

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-slate-500 font-mono">Recruiting & networking</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Events</h1>
          <div className="text-sm text-slate-400">
            Insight evenings, desk sessions and challenges the banks publish, picked up on every refresh. Register on the bank's own page.
          </div>
        </div>
        <button onClick={() => setAdding(a => !a)}
          className="self-start sm:self-auto px-3 py-2 rounded-md border border-ink-600 text-sm text-slate-200 hover:border-neon-cyan/60 hover:text-neon-cyan transition-colors">
          {adding ? 'Close' : '+ Add event'}
        </button>
      </div>

      {adding && <AddEventForm onDone={() => { setAdding(false); qc.invalidateQueries({ queryKey: ['events'] }) }} />}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Shown" value={stats.shown} color="#e2e8f0" />
        <Stat label="Registration open" value={stats.open} color="#22d3ee" />
        <Stat label="Deadline ≤ 7 days" value={stats.closingSoon} color="#fbbf24" />
        <Stat label="You're registered" value={stats.registered} color="#34d399" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-ink-700 overflow-hidden">
          {SCOPES.map(s => (
            <button key={s.id} onClick={() => setScope(s.id)}
              className={`px-3 py-1.5 text-xs transition-colors ${scope === s.id
                ? 'bg-ink-800 text-neon-cyan' : 'text-slate-400 hover:text-slate-100'}`}>
              {s.label}
            </button>
          ))}
        </div>
        <select value={bank} onChange={e => setBank(e.target.value)}
          className="px-2 py-1.5 bg-ink-950 border border-ink-700 rounded-lg text-xs text-slate-300">
          <option value="">All banks</option>
          {banks.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        <Toggle on={openOnly} set={setOpenOnly} label="Open registration only" />
        <Toggle on={showPast} set={setShowPast} label="Show past" />
      </div>

      {isLoading && <div className="text-sm text-slate-500">Loading…</div>}
      {!isLoading && visible.length === 0 && (
        <div className="bg-ink-900/70 border border-ink-700 rounded-xl px-6 py-10 text-center text-sm text-slate-500">
          {events.length === 0
            ? 'No events yet. Hit Refresh in the header to pull them from the banks.'
            : 'No events match these filters.'}
        </div>
      )}

      {groups.map(([month, rows]) => (
        <section key={month} className="space-y-2">
          <h2 className="text-xs font-mono uppercase tracking-wider text-slate-500">{month}</h2>
          {rows.map(e => (
            <EventCard key={e.id} e={e}
              onStatus={s => update.mutate({ id: e.id, body: { reg_status: s } })}
              onRemove={() => remove.mutate(e.id)} />
          ))}
        </section>
      ))}

      <div className="text-[11px] text-slate-600 font-mono">
        Sources: JPMorgan, Goldman Sachs, Deutsche Bank, HSBC, Bank of America (Europe only). Spring weeks and insight
        programmes that banks post as job openings appear on the Offers page. Found one elsewhere? Use “Add event”.
      </div>
    </div>
  )
}

function EventCard({ e, onStatus, onRemove }: {
  e: RecruitingEvent; onStatus: (s: EventRegStatus) => void; onRemove: () => void
}) {
  const [open, setOpen] = useState(false)
  const m = bankMeta(e.bank)
  const rel = REL_STYLE[e.relevance] ?? REL_STYLE.general
  const start = e.starts_at ? new Date(e.starts_at) : null
  const dl = daysUntil(e.registration_deadline)
  const status = STATUS.find(s => s.id === e.reg_status) ?? STATUS[0]
  const past = (daysUntil(e.ends_at ?? e.starts_at) ?? 0) < 0
  const dim = (!e.registration_open && e.reg_status === 'not_registered') || past
  const where = e.is_virtual ? 'Virtual' : (e.city || e.location || e.country || 'Location TBC')

  return (
    <div className={`group relative bg-ink-900/80 border border-ink-700 rounded-xl hover:border-ink-600 transition-colors animate-rowin ${dim ? 'opacity-60' : ''}`}>
      <span className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full" style={{ background: rel.color }} />
      <div className="grid grid-cols-[auto,1fr] sm:grid-cols-[auto,auto,1fr,auto] gap-3 sm:gap-4 items-start p-3 sm:p-4 pl-4 sm:pl-5">
        <div className="w-12 text-center">
          {start ? (
            <>
              <div className="text-[10px] font-mono uppercase text-slate-500">
                {start.toLocaleDateString('en-GB', { weekday: 'short' })}
              </div>
              <div className="text-2xl font-semibold text-slate-100 leading-none tabular-nums">{start.getDate()}</div>
              <div className="text-[10px] font-mono uppercase text-slate-500">
                {start.toLocaleDateString('en-GB', { month: 'short' })}
              </div>
            </>
          ) : <div className="text-xs text-slate-500 pt-2">TBC</div>}
        </div>
        <div className="hidden sm:block"><CompanyLogo bank={e.bank} size={38} /></div>

        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-1">
            <span className="text-[10px] font-mono px-1 rounded" style={{ background: `${m.color}1f`, color: m.color }}>{e.bank}</span>
            <Badge color={rel.color}>{rel.label}</Badge>
            <Badge color="#94a3b8">{TYPE_LABEL[e.event_type] ?? 'Event'}</Badge>
            {e.manual && <Badge color="#a855f7">Added by you</Badge>}
            {isNew(e) && !e.manual && <Badge color="#fbbf24">New</Badge>}
          </div>
          <button onClick={() => setOpen(o => !o)} className="text-left text-sm sm:text-[15px] font-medium text-slate-100 hover:text-neon-cyan transition-colors">
            {e.title}
          </button>
          <div className="mt-1 text-xs text-slate-400 flex flex-wrap gap-x-3 gap-y-0.5">
            <span>🕒 {fmtTime(e)}</span>
            <span>{e.is_virtual ? '💻' : '📍'} {where}</span>
            {e.school && <span title="Usually only open to students of this school">🎓 {e.school}</span>}
            {e.registration_deadline && (
              <span className={dl !== null && dl >= 0 && dl <= 3 && e.registration_open ? 'text-neon-amber font-medium' : ''}>
                ⏳ register by {new Date(e.registration_deadline).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                {dl !== null && dl >= 0 && e.registration_open && ` (${dl === 0 ? 'today' : `${dl}d left`})`}
              </span>
            )}
          </div>
          {open && (
            <div className="mt-3 text-xs text-slate-300 whitespace-pre-line leading-relaxed max-h-72 overflow-auto pr-2">
              {e.description || 'No description published. Open the sign-up page for details.'}
              {e.division && <div className="mt-2 text-slate-500">Division: {e.division}</div>}
            </div>
          )}
        </div>

        <div className="col-span-2 sm:col-span-1 flex sm:flex-col items-center sm:items-end gap-2">
          {e.registration_open || e.reg_status !== 'not_registered' ? (
            <button onClick={() => openTab(e.register_url)}
              className="btn-neon px-3 py-1.5 rounded-md bg-neon-cyan text-ink-950 text-xs font-semibold hover:bg-cyan-300 whitespace-nowrap">
              Register ↗
            </button>
          ) : (
            <button onClick={() => openTab(e.register_url)} title="Sign-up has closed — opens the bank's events page"
              className="px-3 py-1.5 rounded-md border border-ink-600 text-xs text-slate-400 whitespace-nowrap">
              Registration closed
            </button>
          )}
          <select value={e.reg_status} onChange={ev => onStatus(ev.target.value as EventRegStatus)}
            className="px-2 py-1 bg-ink-950 border border-ink-700 rounded text-xs"
            style={{ color: status.color }}>
            {STATUS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <button onClick={onRemove} title={e.manual ? 'Delete' : "Hide (it won't come back)"}
            className="text-[11px] text-slate-600 hover:text-rose-300 sm:opacity-0 group-hover:opacity-100 transition-opacity">
            {e.manual ? 'Delete' : 'Hide'}
          </button>
        </div>
      </div>
    </div>
  )
}

function AddEventForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState<NewEvent>({ bank: '', title: '', register_url: '', is_virtual: false })
  const [date, setDate] = useState('')
  const [deadline, setDeadline] = useState('')
  const create = useMutation({
    mutationFn: () => api.createEvent({
      ...f,
      starts_at: date || null,
      registration_deadline: deadline ? `${deadline}T23:59:00` : null,
    }),
    onSuccess: onDone,
  })
  const set = (k: keyof NewEvent) => (ev: any) =>
    setF(p => ({ ...p, [k]: ev.target.type === 'checkbox' ? ev.target.checked : ev.target.value }))
  const ok = f.bank.trim() && f.title.trim() && /^https?:\/\//.test(f.register_url.trim())

  return (
    <div className="bg-ink-900/80 border border-ink-700 rounded-xl p-4 space-y-3 animate-risein">
      <div className="text-xs text-slate-400">
        Track an event the scrapers don't cover (Bright Network, a campus fair, a LinkedIn post). Refreshes never touch it.
      </div>
      <div className="grid sm:grid-cols-2 gap-2">
        <input className={inp} placeholder="Bank / firm" value={f.bank} onChange={set('bank')} />
        <input className={inp} placeholder="Event title" value={f.title} onChange={set('title')} />
        <input className={inp + ' sm:col-span-2'} placeholder="Registration link (https://…)" value={f.register_url} onChange={set('register_url')} />
        <label className="text-xs text-slate-500 flex flex-col gap-1">Date & time
          <input type="datetime-local" className={inp} value={date} onChange={e => setDate(e.target.value)} />
        </label>
        <label className="text-xs text-slate-500 flex flex-col gap-1">Registration deadline
          <input type="date" className={inp} value={deadline} onChange={e => setDeadline(e.target.value)} />
        </label>
        <input className={inp} placeholder="Location (city)" value={f.location ?? ''} onChange={set('location')} />
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={!!f.is_virtual} onChange={set('is_virtual')} /> Virtual
        </label>
      </div>
      {create.isError && <div className="text-xs text-rose-300">{String(create.error)}</div>}
      <button disabled={!ok || create.isPending} onClick={() => create.mutate()}
        className="btn-neon px-3 py-2 rounded-md bg-neon-cyan text-ink-950 text-sm font-semibold hover:bg-cyan-300 disabled:opacity-50">
        {create.isPending ? 'Saving…' : 'Add event'}
      </button>
    </div>
  )
}

const inp = 'px-3 py-2 bg-ink-950 border border-ink-700 rounded text-sm text-slate-200 focus:border-neon-cyan/60 outline-none'

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-ink-900/70 border border-ink-700 rounded-xl px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">{label}</div>
      <div className="text-2xl font-semibold tabular-nums" style={{ color }}>{value}</div>
    </div>
  )
}

function Badge({ color, children }: { color: string; children: any }) {
  return (
    <span className="text-[10px] px-1.5 py-[1px] rounded-full font-medium"
      style={{ background: `${color}1a`, color, boxShadow: `inset 0 0 0 1px ${color}40` }}>
      {children}
    </span>
  )
}

function Toggle({ on, set, label }: { on: boolean; set: (v: boolean) => void; label: string }) {
  return (
    <button onClick={() => set(!on)}
      className={`px-3 py-1.5 rounded-lg border text-xs transition-colors ${on
        ? 'border-neon-cyan/50 text-neon-cyan bg-ink-800' : 'border-ink-700 text-slate-400 hover:text-slate-100'}`}>
      {on ? '✓ ' : ''}{label}
    </button>
  )
}
