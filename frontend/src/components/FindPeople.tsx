import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  api, AskType, ImportResult, MessageKind, Suggestion,
  ASK_LABEL, CATEGORY_COLOR, TIER_LABEL,
} from '../api'
import CompanyLogo from './CompanyLogo'

// Ranked people to approach, derived from the user's own LinkedIn data export.
// Nothing here reads LinkedIn: the CSV is the file LinkedIn hands the user on
// request, which is why this is an import and not a scrape.
export default function FindPeople() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [category, setCategory] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [showHow, setShowHow] = useState(false)
  const [showSearches, setShowSearches] = useState(false)

  const { data: suggestions, isLoading } = useQuery({
    queryKey: ['suggestions', category],
    queryFn: () => api.suggestions({ category: category || undefined }),
  })
  const { data: links } = useQuery({
    queryKey: ['search-links'],
    queryFn: api.searchLinks,
    enabled: showSearches,
  })

  const imp = useMutation({
    mutationFn: (f: File) => api.importLinkedIn(f),
    onSuccess: r => {
      setResult(r); setErr(null)
      qc.invalidateQueries({ queryKey: ['suggestions'] })
    },
    onError: (e: Error) => { setErr(e.message); setResult(null) },
  })

  const hasAny = (suggestions?.length ?? 0) > 0

  return (
    <div className="animate-risein bg-ink-900/60 border border-ink-700 rounded-xl backdrop-blur"
      style={{ animationDelay: '50ms' }}>
      <div className="p-3 flex items-center gap-3 flex-wrap border-b border-ink-700">
        <div className="min-w-0 flex-1">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">
            Find people
          </div>
          <div className="text-sm text-slate-300">
            {hasAny
              ? `${suggestions!.length} people in your connections worth writing to`
              : 'Import your LinkedIn connections to see who is worth writing to'}
          </div>
        </div>
        <input ref={fileRef} type="file" accept=".csv" className="hidden"
          onChange={e => { const f = e.target.files?.[0]; if (f) imp.mutate(f); e.target.value = '' }} />
        <button onClick={() => setShowHow(v => !v)}
          className="text-[11px] text-slate-500 hover:text-slate-300">
          How do I get the file?
        </button>
        <button onClick={() => setShowSearches(v => !v)}
          className="px-2.5 py-1.5 rounded-lg border border-ink-600 text-slate-300 text-xs
                     hover:border-neon-violet/50 transition-all active:scale-95">
          LinkedIn searches
        </button>
        <button onClick={() => fileRef.current?.click()} disabled={imp.isPending}
          className="px-3 py-1.5 rounded-lg border border-neon-cyan/50 text-neon-cyan text-sm
                     hover:bg-ink-800 disabled:opacity-50 transition-all active:scale-95">
          {imp.isPending ? 'Reading…' : hasAny ? 'Re-import' : 'Import Connections.csv'}
        </button>
      </div>

      {showHow && (
        <div className="px-3 py-2 border-b border-ink-700 text-[11px] text-slate-400 space-y-1">
          <p>
            On LinkedIn: <span className="text-slate-300">Settings &amp; Privacy → Data privacy
            → Get a copy of your data → Connections → Request archive</span>. It arrives by
            email in about ten minutes. Unzip it and drop <code className="text-neon-cyan">Connections.csv</code> here.
          </p>
          <p>
            That export is your own data, given to you by LinkedIn. Reading it breaks nothing,
            unlike a bot walking your connection list, which is what gets accounts restricted.
          </p>
        </div>
      )}

      {showSearches && links && (
        <div className="px-3 py-2 border-b border-ink-700">
          <p className="text-[11px] text-slate-500 mb-2">
            Searches on LinkedIn's own site, filtered to your existing connections.
            Useful for people the export classified as something else.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {links.map(l => (
              <a key={l.url} href={l.url} target="_blank" rel="noreferrer"
                className="px-2 py-1 rounded border border-ink-700 text-[11px] text-slate-300
                           hover:border-neon-violet/50 hover:text-neon-violet transition-colors">
                {l.label} ↗
              </a>
            ))}
          </div>
        </div>
      )}

      {err && <div className="px-3 py-2 text-[11px] text-neon-rose border-b border-ink-700">{err}</div>}

      {result && (
        <div className="px-3 py-2 border-b border-ink-700 text-[11px] text-slate-400">
          Read <span className="text-slate-200 tabular-nums">{result.total_rows}</span> connections:
          {' '}<span className="text-neon-green tabular-nums">{result.matched}</span> on a markets desk
          {result.new > 0 && <> (<span className="text-neon-cyan tabular-nums">{result.new}</span> new)</>}.
          {' '}Skipped <span className="tabular-nums">{result.no_employer_match}</span> outside finance
          and <span className="tabular-nums">{result.not_markets_role}</span> not front-office.
          <br />
          <span className="text-neon-green tabular-nums">{result.with_shared_email}</span> shared
          an email address in the export;
          {' '}<span className="tabular-nums">{result.with_guessed_email}</span> got a derived
          one from their employer's format, which is a guess and may not exist.
        </div>
      )}

      {hasAny && (
        <>
          <BulkBar
            ids={suggestions!.map(x => x.id)}
            selected={selected}
            onSelectAll={() => setSelected(new Set(suggestions!.map(x => x.id)))}
            onClear={() => setSelected(new Set())}
            onDone={() => setSelected(new Set())}
          />
          <div className="px-3 py-2 flex flex-wrap items-center gap-1.5 border-b border-ink-700">
            <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono mr-1">Desk</span>
            {['', 'sales', 'structuring', 'trading', 'markets'].map(c => (
              <button key={c || 'all'} onClick={() => setCategory(c)} aria-pressed={category === c}
                className={`px-2 py-1 rounded border text-[11px] transition-all active:scale-95 ${
                  category === c ? 'border-neon-cyan bg-ink-800 text-neon-cyan'
                                 : 'border-ink-700 text-slate-400 hover:border-neon-cyan/40'}`}>
                {c || 'All'}
              </button>
            ))}
          </div>
          <div className="divide-y divide-ink-800">
            {suggestions!.map(s => (
              <SuggestionRow key={s.id} s={s}
                checked={selected.has(s.id)}
                onToggle={() => setSelected(prev => {
                  const next = new Set(prev)
                  next.has(s.id) ? next.delete(s.id) : next.add(s.id)
                  return next
                })} />
            ))}
          </div>
        </>
      )}

      {!hasAny && !isLoading && !result && (
        <div className="px-3 py-6 text-center text-[11px] text-slate-500">
          Nothing imported yet. The export names each connection's employer and job
          title, which is enough to rank who actually sits on a markets desk.
        </div>
      )}
    </div>
  )
}

function SuggestionRow({ s, checked, onToggle }: {
  s: Suggestion; checked: boolean; onToggle: () => void
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['suggestions'] })
    qc.invalidateQueries({ queryKey: ['contacts'] })
    qc.invalidateQueries({ queryKey: ['outreach-summary'] })
  }
  const add = useMutation({ mutationFn: () => api.addSuggestion(s.id), onSuccess: invalidate })
  const dismiss = useMutation({ mutationFn: () => api.dismissSuggestion(s.id), onSuccess: invalidate })

  return (
    <div className={`p-3 flex items-start gap-3 transition-colors ${
      checked ? 'bg-cyan-500/5' : 'hover:bg-ink-800/30'}`}>
      <input type="checkbox" checked={checked} onChange={onToggle}
        aria-label={`Select ${s.full_name}`}
        className="mt-2.5 accent-cyan-400 cursor-pointer" />
      <CompanyLogo bank={s.bank} size={34} rounded={9} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-slate-100 truncate">{s.full_name}</span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${
            CATEGORY_COLOR[s.category] ?? 'bg-ink-800 text-slate-300 ring-1 ring-inset ring-ink-600'}`}>
            {s.category}
          </span>
          <span className="text-[10px] text-slate-500 font-mono uppercase tracking-wide">
            {TIER_LABEL[s.employer_tier]}
          </span>
        </div>
        <div className="text-xs text-slate-400 truncate mt-0.5">{s.position}</div>
        <div className="text-[10px] font-mono mt-0.5">
          {s.email ? (
            <span className="text-neon-green">✉ {s.email}</span>
          ) : s.email_guess ? (
            <span className="text-slate-500" title="Derived from the employer's mail format. Unverified — it may bounce.">
              ✉ {s.email_guess} <span className="text-neon-amber">guess</span>
            </span>
          ) : (
            <span className="text-slate-600">no email · LinkedIn only</span>
          )}
        </div>
        <button onClick={() => setOpen(v => !v)}
          className="mt-1 text-[10px] text-slate-600 hover:text-slate-400 font-mono">
          {open ? 'hide' : 'why this person'}
        </button>
        {open && (
          <ul className="mt-1 space-y-0.5">
            {s.reasons.map((r, i) => (
              <li key={i} className="text-[11px] text-slate-500">· {r}</li>
            ))}
            <li className="text-[11px] text-slate-600">
              · listed at &ldquo;{s.company_raw}&rdquo;
              {s.connected_on && <>, connected {s.connected_on}</>}
              {s.email && <>, email in the export</>}
            </li>
          </ul>
        )}
      </div>
      <div className="flex flex-col items-end gap-1.5 shrink-0">
        <span className="font-mono text-sm tabular-nums text-neon-green">{s.score}</span>
        <button onClick={() => add.mutate()} disabled={add.isPending}
          className="px-2 py-1 rounded border border-neon-green/50 text-neon-green text-[11px]
                     hover:bg-ink-800 disabled:opacity-50 transition-all active:scale-95">
          {add.isPending ? 'Adding…' : 'Add to outreach'}
        </button>
        <button onClick={() => dismiss.mutate()}
          className="text-[10px] text-slate-600 hover:text-neon-rose transition-colors">
          Not relevant
        </button>
      </div>
    </div>
  )
}

// Select many, draft many. The slow part (writing N individually researched
// messages) is what gets automated; the send stays one human click per message,
// in the Send queue.
function BulkBar({ ids, selected, onSelectAll, onClear, onDone }: {
  ids: number[]
  selected: Set<number>
  onSelectAll: () => void
  onClear: () => void
  onDone: () => void
}) {
  const qc = useQueryClient()
  const [channel, setChannel] = useState<'auto' | 'linkedin' | 'email'>('auto')
  const [ask, setAsk] = useState<AskType>('chat')
  // Every suggestion comes from the connections export, so these people are
  // already 1st-degree: there is no Connect button to hang a note on.
  const [kind, setKind] = useState<MessageKind>('dm')
  const [humanize, setHumanize] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  // Only poll while a batch is actually running.
  const { data: status } = useQuery({
    queryKey: ['bulk-status'],
    queryFn: api.bulkDraftStatus,
    refetchInterval: q => (q.state.data?.running ? 1500 : false),
  })
  const running = !!status?.running

  const start = useMutation({
    mutationFn: () => api.bulkDraft({
      suggestion_ids: [...selected], kind, channel, ask_type: ask, humanize,
    }),
    onSuccess: () => {
      setErr(null); onDone()
      qc.invalidateQueries({ queryKey: ['bulk-status'] })
      qc.invalidateQueries({ queryKey: ['suggestions'] })
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
    onError: (e: Error) => setErr(e.message),
  })

  // Once a batch finishes, refresh everything it touched.
  if (status && !status.running && status.total > 0 && status.done === status.total) {
    qc.invalidateQueries({ queryKey: ['send-queue'] })
  }

  const n = selected.size
  // Measured: ~6s per draft, ~12s with the humaniser pass.
  const mins = Math.max(1, Math.round((n * (humanize ? 12 : 6)) / 60))

  if (running) {
    const pct = status.total ? Math.round((status.done / status.total) * 100) : 0
    return (
      <div className="px-3 py-2 border-b border-ink-700 bg-cyan-500/5">
        <div className="flex items-center gap-3 text-xs">
          <span className="text-neon-cyan tabular-nums">
            Drafting {status.done}/{status.total}
          </span>
          <span className="text-slate-500 truncate flex-1">{status.current ?? '…'}</span>
          {status.failed > 0 && (
            <span className="text-neon-rose tabular-nums">{status.failed} failed</span>
          )}
        </div>
        <div className="mt-1.5 h-1 rounded bg-ink-800 overflow-hidden">
          <div className="h-full bg-neon-cyan transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          Each one is written from that person's own firm, desk and title. You can leave this page.
        </p>
      </div>
    )
  }

  return (
    <div className="px-3 py-2 border-b border-ink-700 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={n === ids.length ? onClear : onSelectAll}
          className="px-2 py-1 rounded border border-ink-600 text-[11px] text-slate-300
                     hover:border-neon-cyan/50 transition-all active:scale-95">
          {n === ids.length ? 'Clear selection' : `Select all ${ids.length}`}
        </button>
        <span className="text-[11px] text-slate-500 tabular-nums">{n} selected</span>

        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {(['auto', 'linkedin', 'email'] as const).map(c => (
            <button key={c} onClick={() => setChannel(c)} aria-pressed={channel === c}
              title={c === 'auto' ? 'Email where the export gave an address, LinkedIn otherwise' : undefined}
              className={`px-2 py-1 rounded border text-[11px] transition-all active:scale-95 ${
                channel === c ? 'border-neon-cyan bg-ink-800 text-neon-cyan'
                              : 'border-ink-700 text-slate-400 hover:border-neon-cyan/40'}`}>
              {c}
            </button>
          ))}
          {(['chat', 'referral'] as AskType[]).map(a => (
            <button key={a} onClick={() => setAsk(a)} aria-pressed={ask === a}
              className={`px-2 py-1 rounded border text-[11px] transition-all active:scale-95 ${
                ask === a ? 'border-neon-violet bg-ink-800 text-neon-violet'
                          : 'border-ink-700 text-slate-400 hover:border-neon-violet/40'}`}>
              {ASK_LABEL[a].replace('Ask for a ', '')}
            </button>
          ))}
          <label className="flex items-center gap-1 text-[11px] text-slate-400 cursor-pointer">
            <input type="checkbox" checked={humanize} onChange={e => setHumanize(e.target.checked)}
              className="accent-cyan-400" />
            humanise
          </label>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-500">
          Direct message — these are existing connections, so there is no
          connection request to attach a note to.
        </span>
        <button onClick={() => start.mutate()} disabled={n === 0 || start.isPending}
          className="ml-auto px-3 py-1.5 rounded-lg border border-neon-green/50 text-neon-green text-xs
                     hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
          {start.isPending ? 'Starting…' : `Draft ${n || ''} personalised message${n === 1 ? '' : 's'}`}
        </button>
      </div>

      {n > 0 && (
        <p className="text-[11px] text-slate-500">
          {n} separate drafts, roughly {mins} minute{mins === 1 ? '' : 's'}. They land in the
          Send queue for you to review and send. Nothing goes out on its own.
          {n > 20 && (
            <span className="text-neon-amber">
              {' '}LinkedIn caps connection requests near 100–200 a week, and a burst of
              near-identical messages in one day is what gets flagged. Spread these out.
            </span>
          )}
        </p>
      )}
      {err && <p className="text-[11px] text-neon-rose">{err}</p>}
    </div>
  )
}
