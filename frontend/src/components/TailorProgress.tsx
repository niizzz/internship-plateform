import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api } from '../api'

/** Header chip showing the background CV/CL generation queue.
 *  Hidden when idle with nothing pending; click to start a manual run. */
export default function TailorProgress() {
  const qc = useQueryClient()
  const { data: st } = useQuery({
    queryKey: ['tailorStatus'],
    queryFn: api.tailorStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2500 : 15000),
  })
  const run = useMutation({
    mutationFn: api.tailorRunAll,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tailorStatus'] }),
  })

  // When a run finishes, refresh offer lists so the CV/CL badges update.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && st && !st.running) {
      qc.invalidateQueries({ queryKey: ['offers'] })
      qc.invalidateQueries({ queryKey: ['offer'] })
    }
    wasRunning.current = !!st?.running
  }, [st?.running, qc])

  if (!st) return null

  if (st.running) {
    const pct = st.total ? Math.round((st.done / st.total) * 100) : 0
    return (
      <div
        className="px-3 py-1.5 rounded-md border border-violet-500/40 bg-violet-500/10 text-neon-violet text-xs font-semibold uppercase tracking-wider flex items-center gap-2"
        title={st.current ? `Now tailoring: ${st.current}` : 'Generating tailored documents'}
      >
        <Spinner />
        Docs {st.done}/{st.total} · {pct}%
      </div>
    )
  }

  return (
    <button
      onClick={() => run.mutate()}
      disabled={run.isPending}
      className="btn-neon px-3 py-1.5 rounded-md bg-ink-800/60 border border-ink-600 text-slate-400 text-xs font-semibold uppercase tracking-wider hover:border-neon-violet/50 hover:text-neon-violet disabled:opacity-60"
      title={
        st.failed
          ? `Last run: ${st.failed} failed${st.last_error ? ` — ${st.last_error}` : ''}. Click to retry missing docs.`
          : 'Generate tailored CV + cover letter for offers that are missing them'
      }
    >
      {st.failed ? `Docs · ${st.failed} failed ↻` : 'Docs ✓'}
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
