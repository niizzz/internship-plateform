import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  api, PipelineRow, ApplicationStatus, STATUS_LABEL, STATUS_COLOR, CATEGORY_COLOR,
} from '../api'
import CompanyLogo from '../components/CompanyLogo'
import StatusBadge from '../components/StatusBadge'
import { ago, ageLabel } from '../lib/format'
import { burstConfetti } from '../lib/fx'

// The stages in funnel order. "Not applied" is deliberately absent: this page
// is the record of what you actually sent, and the Offers page already covers
// everything you haven't.
const STAGES: ApplicationStatus[] = [
  'applied', 'online_assessment', 'interview', 'offer', 'rejected',
]

const CELEBRATE: Partial<Record<ApplicationStatus, string[]>> = {
  interview: ['#a855f7', '#22d3ee', '#f0abfc'],
  offer: ['#34d399', '#22d3ee', '#fbbf24'],
}

// A stage stops ageing once it is terminal — a rejection sitting 40 days is
// not "stale", it is closed.
const TERMINAL: ApplicationStatus[] = ['rejected', 'offer']

function staleTone(days: number | null, status: ApplicationStatus): string {
  if (days === null || TERMINAL.includes(status)) return 'text-slate-500'
  if (days >= 21) return 'text-neon-rose'
  if (days >= 10) return 'text-neon-amber'
  return 'text-slate-400'
}

export default function PipelinePage() {
  const [stage, setStage] = useState<ApplicationStatus | ''>('')
  const [open, setOpen] = useState<number | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['pipeline'],
    queryFn: () => api.pipeline(),
  })

  const rows = useMemo(
    () => (data?.rows ?? []).filter(r => !stage || r.status === stage),
    [data, stage]
  )

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">
      <div className="animate-risein">
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500 font-mono flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-neon-violet/70 fx-radar" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-neon-violet" />
          </span>
          Application tracker
        </div>
        <h1 className="mt-1 text-2xl font-semibold fx-textshine">Pipeline</h1>
        <p className="mt-1 text-sm text-slate-400">
          Every application you have sent, and where it stands.
        </p>
      </div>

      <StageBar
        byStatus={data?.by_status ?? {}}
        total={data?.total ?? 0}
        stalest={data?.stalest_days ?? null}
        active={stage}
        onPick={s => setStage(s === stage ? '' : s)}
      />

      {isLoading ? (
        <SkeletonRows />
      ) : rows.length === 0 ? (
        <Empty filtered={!!stage} onClear={() => setStage('')} />
      ) : (
        <div className="space-y-3">
          {rows.map((r, i) => (
            <div key={r.offer_id} className="animate-rowin"
              style={{ animationDelay: `${Math.min(i, 12) * 45}ms` }}>
              <Row row={r} expanded={open === r.offer_id}
                onToggle={() => setOpen(open === r.offer_id ? null : r.offer_id)} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function StageBar(props: {
  byStatus: Record<string, number>
  total: number
  stalest: number | null
  active: ApplicationStatus | ''
  onPick: (s: ApplicationStatus) => void
}) {
  return (
    <div className="animate-risein bg-ink-900/60 border border-ink-700 rounded-xl p-3 backdrop-blur"
      style={{ animationDelay: '60ms' }}>
      <div className="flex flex-wrap items-center gap-2">
        {STAGES.map(s => {
          const n = props.byStatus[s] ?? 0
          const on = props.active === s
          return (
            <button key={s} onClick={() => props.onPick(s)}
              aria-pressed={on}
              className={`px-3 py-2 rounded-lg border text-left transition-all active:scale-95 ${
                on
                  ? 'border-neon-cyan bg-ink-800 shadow-[inset_0_0_0_1px_rgba(34,211,238,0.35)]'
                  : 'border-ink-700 hover:border-neon-cyan/40'
              } ${n === 0 ? 'opacity-45' : ''}`}>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">
                {STATUS_LABEL[s]}
              </div>
              <div className="text-lg font-semibold tabular-nums text-slate-100 leading-tight">{n}</div>
            </button>
          )
        })}
        <div className="ml-auto flex items-center gap-4 px-2 font-mono text-[11px]">
          <span className="text-slate-500">
            total <span className="text-slate-100 font-semibold tabular-nums">{props.total}</span>
          </span>
          {props.stalest !== null && (
            <span className="text-slate-500">
              oldest live{' '}
              <span className={`font-semibold tabular-nums ${
                props.stalest >= 21 ? 'text-neon-rose'
                  : props.stalest >= 10 ? 'text-neon-amber' : 'text-slate-100'}`}>
                {props.stalest}d
              </span>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ row, expanded, onToggle }: {
  row: PipelineRow; expanded: boolean; onToggle: () => void
}) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState<string | null>(null)
  const catCls = CATEGORY_COLOR[row.category] ?? CATEGORY_COLOR.markets

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['pipeline'] })
    qc.invalidateQueries({ queryKey: ['offers'] })
    qc.invalidateQueries({ queryKey: ['offer', row.offer_id] })
    qc.invalidateQueries({ queryKey: ['stats'] })
    qc.invalidateQueries({ queryKey: ['dashboard'] })
  }
  const setStatus = useMutation({
    mutationFn: (s: ApplicationStatus) => api.updateApplication(row.offer_id, { status: s }),
    onSuccess: invalidate,
  })
  const saveNotes = useMutation({
    mutationFn: () => api.updateApplication(row.offer_id, { notes: notes ?? '' }),
    onSuccess: invalidate,
  })

  return (
    <div className="bg-ink-900/80 border border-ink-700 rounded-xl p-4 hover:border-ink-600 transition-colors">
      <div className="flex items-start gap-3">
        <CompanyLogo bank={row.bank} size={38} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">{row.bank}</span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${catCls}`}>
              {row.category}
            </span>
            {!row.is_active && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium uppercase bg-ink-800 text-slate-400 ring-1 ring-inset ring-ink-600"
                title="This posting is no longer live on the bank's site — your application record is kept.">
                posting closed
              </span>
            )}
          </div>
          <Link to={`/offers/${row.offer_id}`}
            className="block mt-0.5 font-semibold text-slate-100 hover:text-neon-cyan transition-colors leading-snug">
            {row.role_title}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-mono text-slate-500">
            <span>{row.location}</span>
            {row.start_date_raw && <span>starts {row.start_date_raw}</span>}
            <span>applied {row.applied_at ? ago(row.applied_at) + ' ago' : '—'}</span>
            <span className={staleTone(row.days_in_stage, row.status)}>
              {row.days_in_stage !== null ? `${ageLabel(row.days_in_stage)} in ${STATUS_LABEL[row.status].toLowerCase()}` : ''}
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <StatusBadge status={row.status} />
          <button onClick={onToggle}
            className="text-[11px] font-mono text-slate-500 hover:text-neon-cyan transition-colors">
            {expanded ? 'hide' : 'details'}
          </button>
        </div>
      </div>

      {/* One tap per stage — no dragging, so it works the same on a phone. */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {STAGES.map(s => (
          <button key={s}
            disabled={setStatus.isPending}
            onClick={(e) => {
              if (row.status === s) return
              setStatus.mutate(s)
              const colors = CELEBRATE[s]
              if (colors) {
                const b = e.currentTarget.getBoundingClientRect()
                burstConfetti(b.left + b.width / 2, b.top + b.height / 2, colors)
              }
            }}
            className={`px-2.5 py-1 rounded text-[11px] font-medium border transition-all active:scale-95 disabled:opacity-60 ${
              row.status === s
                ? `${STATUS_COLOR[s]} cursor-default`
                : 'border-ink-600 text-slate-400 hover:border-neon-cyan/40 hover:text-neon-cyan'
            }`}>
            {STATUS_LABEL[s]}
          </button>
        ))}
      </div>

      {expanded && (
        <div className="mt-4 pt-4 border-t border-ink-700 grid gap-4 md:grid-cols-2">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-mono">Timeline</div>
            <ol className="space-y-1.5">
              {row.events.map((e, i) => (
                <li key={i} className="flex items-center gap-2 text-xs">
                  <span className="h-1.5 w-1.5 rounded-full bg-neon-cyan shrink-0" />
                  <span className="text-slate-300">
                    {e.from_status ? `${STATUS_LABEL[e.from_status]} → ` : ''}
                    <span className="font-medium">{STATUS_LABEL[e.to_status]}</span>
                  </span>
                  <span className="ml-auto font-mono text-[11px] text-slate-500">{ago(e.at)} ago</span>
                </li>
              ))}
              {row.events.length === 0 && (
                <li className="text-xs text-slate-500">No transitions recorded yet.</li>
              )}
            </ol>
            <div className="mt-3 flex flex-wrap gap-3 text-[11px] font-mono text-slate-500">
              <span className={row.has_tailored_cv ? 'text-neon-green' : ''}>
                CV {row.has_tailored_cv ? '✓ tailored' : '— base'}
              </span>
              <span className={row.has_tailored_cover_letter ? 'text-neon-green' : ''}>
                CL {row.has_tailored_cover_letter ? '✓ tailored' : '— none'}
              </span>
              <a href={row.apply_url} target="_blank" rel="noreferrer"
                className="text-slate-400 hover:text-neon-cyan transition-colors">
                posting ↗
              </a>
            </div>
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-mono">
              Notes
            </label>
            <textarea
              value={notes ?? row.notes ?? ''}
              onChange={e => setNotes(e.target.value)}
              rows={4}
              placeholder="Recruiter name, OA deadline, interview format…"
              className="w-full px-3 py-2 bg-ink-950 border border-ink-700 rounded text-sm placeholder-slate-600 focus:border-neon-cyan/60" />
            <button
              onClick={() => saveNotes.mutate()}
              disabled={notes === null || saveNotes.isPending}
              className="mt-2 px-3 py-1 text-xs rounded border border-ink-600 text-slate-300 hover:border-neon-cyan/40 hover:text-neon-cyan disabled:opacity-40 disabled:hover:border-ink-600 disabled:hover:text-slate-300">
              {saveNotes.isPending ? 'Saving…' : saveNotes.isSuccess && notes !== null ? 'Saved ✓' : 'Save notes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Empty({ filtered, onClear }: { filtered: boolean; onClear: () => void }) {
  return (
    <div className="text-center py-16 border border-dashed border-ink-700 rounded-xl">
      <div className="text-slate-400">
        {filtered ? 'Nothing at this stage yet.' : 'No applications sent yet.'}
      </div>
      <div className="mt-1 text-sm text-slate-500">
        {filtered
          ? 'Pick another stage, or clear the filter.'
          : 'Mark an offer as Applied and it will appear here.'}
      </div>
      {filtered ? (
        <button onClick={onClear}
          className="mt-4 px-3 py-1.5 text-xs rounded border border-ink-600 text-slate-300 hover:border-neon-cyan/40 hover:text-neon-cyan">
          Show all
        </button>
      ) : (
        <Link to="/"
          className="inline-block mt-4 px-3 py-1.5 text-xs rounded border border-ink-600 text-slate-300 hover:border-neon-cyan/40 hover:text-neon-cyan">
          Browse offers
        </Link>
      )}
    </div>
  )
}

function SkeletonRows() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="h-28 rounded-xl bg-ink-900/60 border border-ink-700 animate-pulse" />
      ))}
    </div>
  )
}
