import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

export default function NotificationBell() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ['notifications'],
    queryFn: api.notifications,
    refetchInterval: 30_000,
  })
  const count = data?.length ?? 0

  const dismiss = async (id: number) => {
    await api.dismissNotification(id)
    qc.invalidateQueries({ queryKey: ['notifications'] })
    qc.invalidateQueries({ queryKey: ['offers'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="relative p-2 rounded-md hover:bg-ink-800 text-slate-300 hover:text-slate-100 transition-colors"
        title="Notifications"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.7}
            d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-5-5.9V4a1 1 0 10-2 0v1.1A6 6 0 006 11v3.2a2 2 0 01-.6 1.4L4 17h5m6 0a3 3 0 11-6 0" />
        </svg>
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 bg-neon-rose text-ink-950 text-[10px] font-bold rounded-full min-w-[16px] h-4 px-1 flex items-center justify-center font-mono">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-96 bg-ink-900 rounded-lg shadow-glow-soft border border-ink-700 z-30 max-h-[500px] overflow-y-auto">
          <div className="p-3 border-b border-ink-700 font-semibold text-sm flex justify-between items-center text-slate-100">
            <span>Notifications <span className="text-slate-500 font-mono">({count})</span></span>
            <button onClick={() => setOpen(false)} className="text-slate-500 hover:text-slate-200">✕</button>
          </div>
          {count === 0 && <div className="p-4 text-sm text-slate-500">All clear.</div>}
          {(data ?? []).map(n => (
            <div key={n.id} className="p-3 border-b border-ink-800 hover:bg-ink-800/60">
              <div className="text-[10px] text-neon-rose font-semibold uppercase tracking-wider">
                Offer removed
              </div>
              <div className="text-sm font-medium mt-1 text-slate-100">{n.offer_snapshot?.role_title || 'Unknown role'}</div>
              <div className="text-xs text-slate-400">{n.offer_snapshot?.bank} — {n.offer_snapshot?.location}</div>
              <div className="text-[11px] text-slate-500 mt-1 font-mono">
                {new Date(n.created_at).toLocaleString()}
              </div>
              <button
                onClick={() => dismiss(n.id)}
                className="mt-2 text-xs text-neon-cyan hover:text-cyan-300"
              >
                Dismiss & delete from DB
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
