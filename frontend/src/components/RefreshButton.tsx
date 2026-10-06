import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api } from '../api'
import { useEffect, useState } from 'react'
import ScraperHealth, { HealthItem } from './ScraperHealth'

export default function RefreshButton() {
  const qc = useQueryClient()
  const [polling, setPolling] = useState(false)
  const { data: status } = useQuery({
    queryKey: ['refreshStatus'],
    queryFn: api.refreshStatus,
    // Also poll while the backend reports a run we didn't start (the 24h
    // auto-refresh), otherwise the label freezes at the first count it saw.
    refetchInterval: q => (polling || q.state.data?.status === 'running') ? 2000 : false,
  })

  const trigger = useMutation({
    mutationFn: api.refresh,
    onSuccess: () => setPolling(true),
  })

  useEffect(() => {
    if (status?.status === 'done' || status?.status === 'error') {
      setPolling(false)
      qc.invalidateQueries({ queryKey: ['offers'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['notifications'] })
    }
  }, [status?.status, qc])

  // Banks are persisted as each scraper finishes, so stream new offers into the
  // list live: whenever the "done" count ticks up, refetch offers + stats.
  const done: number | undefined = status?.result?.done
  useEffect(() => {
    if (status?.status === 'running' && typeof done === 'number') {
      qc.invalidateQueries({ queryKey: ['offers'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['notifications'] })
    }
  }, [done, status?.status, qc])

  const running = polling || status?.status === 'running' || trigger.isPending
  const total: number | undefined = status?.result?.total
  const label = running
    ? (typeof done === 'number' && typeof total === 'number' ? `Scraping ${done}/${total}…` : 'Scraping…')
    : 'Refresh'

  // Surface scraper health: failed banks + banks held by the zero-yield guard.
  // A silent scraper failure looking identical to "no news" was an audit finding.
  // The backend sends a reason per bank; fall back to bare names if a refresh
  // predating that is still the last one in memory.
  const detail = (names: string[], rows: HealthItem[] | undefined, fallback: string): HealthItem[] =>
    rows?.length ? rows : names.map(bank => ({ bank, reason: fallback }))
  const failed = detail(status?.result?.failed ?? [], status?.result?.failed_detail,
    'scraper errored - no reason recorded')
  const suspect = detail(status?.result?.suspect_zero ?? [], status?.result?.suspect_detail,
    'returned 0 offers - deactivation held for one refresh')
  const showHealth = !running && status?.status === 'done' && (failed.length > 0 || suspect.length > 0)

  return (
    <div className="flex items-center gap-1.5">
      {showHealth && <ScraperHealth failed={failed} suspect={suspect} />}
      <button
        onClick={() => trigger.mutate()}
        disabled={running}
        className="btn-neon relative overflow-hidden px-3 py-1.5 rounded-md bg-gradient-to-r from-neon-cyan/15 to-neon-violet/15 border border-ink-600 text-neon-cyan text-xs font-semibold uppercase tracking-wider hover:border-neon-cyan/50 disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2"
        title={status?.implemented_scrapers?.length
          ? `Scrapers: ${status.implemented_scrapers.join(', ')}`
          : 'Refresh'}
      >
        {running && typeof done === 'number' && typeof total === 'number' && total > 0 && (
          <span className="absolute inset-x-0 bottom-0 h-[2px] bg-neon-cyan/70 transition-all duration-500"
            style={{ width: `${(done / total) * 100}%` }} />
        )}
        {running ? <Spinner /> : <Pulse />}
        {label}
      </button>
    </div>
  )
}

function Spinner() {
  return (
    <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

function Pulse() {
  return (
    <span className="relative flex h-2 w-2">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-cyan opacity-60"></span>
      <span className="relative inline-flex rounded-full h-2 w-2 bg-neon-cyan"></span>
    </span>
  )
}
