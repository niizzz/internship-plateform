import { ApplicationStatus, STATUS_COLOR, STATUS_LABEL } from '../api'

export default function StatusBadge({ status }: { status: ApplicationStatus }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium uppercase tracking-wide ${STATUS_COLOR[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  )
}
