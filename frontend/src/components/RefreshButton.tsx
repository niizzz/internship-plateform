import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api } from '../api'
import { useEffect, useState } from 'react'

export default function RefreshButton() {
  const qc = useQueryClient()
  const [polling, setPolling] = useState(false)
  const { data: status } = useQuery({
    queryKey: ['refreshStatus'],
    queryFn: api.refreshStatus,
    refetchInterval: polling ? 2000 : false,
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

  const running = polling || status?.status === 'running' || trigger.isPending
  const label = running ? 'Scraping…' : 'Refresh'

  return (
    <button
      onClick={() => trigger.mutate()}
      disabled={running}
      className="btn-neon px-3 py-1.5 rounded-md bg-gradient-to-r from-neon-cyan/15 to-neon-violet/15 border border-ink-600 text-neon-cyan text-xs font-semibold uppercase tracking-wider hover:border-neon-cyan/50 disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2"
      title={status?.implemented_scrapers?.length
        ? `Scrapers: ${status.implemented_scrapers.join(', ')}`
        : 'Refresh'}
    >
      {running ? <Spinner /> : <Pulse />}
      {label}
    </button>
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
