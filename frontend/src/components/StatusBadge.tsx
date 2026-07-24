import { ApplicationStatus, STATUS_COLOR, STATUS_LABEL } from '../api'

// Statuses that are "in play" get a heartbeat glow — the pipeline should feel
// alive wherever something is actually happening.
const PULSE: Partial<Record<ApplicationStatus, string>> = {
  applied: 'rgba(34,211,238,0.35)',
  online_assessment: 'rgba(251,191,36,0.35)',
  interview: 'rgba(168,85,247,0.4)',
  offer: 'rgba(52,211,153,0.45)',
}

export default function StatusBadge({ status }: { status: ApplicationStatus }) {
  const pulse = PULSE[status]
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium uppercase tracking-wide ${STATUS_COLOR[status]} ${pulse ? 'fx-heartbeat' : ''}`}
      style={pulse ? ({ ['--pulse' as any]: pulse }) : undefined}
    >
      {pulse && <span className="h-1 w-1 rounded-full bg-current" />}
      {STATUS_LABEL[status]}
    </span>
  )
}
