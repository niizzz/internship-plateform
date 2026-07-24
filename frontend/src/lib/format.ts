// Small formatting helpers for the live desk: compact numbers, relative time,
// and "how long on site" age labels. Timestamps from the API are naive UTC ISO
// strings (no zone), so we parse them as UTC.

export function compact(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1) + 'K'
  return (n / 1_000_000).toFixed(1) + 'M'
}

export function comma(n: number): string {
  return n.toLocaleString('en-US')
}

function parseUTC(iso: string): number {
  // API sends naive UTC; append Z if it has no timezone so Date treats it as UTC.
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'
  return new Date(s).getTime()
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return '—'
  const secs = Math.max(0, (Date.now() - parseUTC(iso)) / 1000)
  if (secs < 45) return 'now'
  const m = secs / 60
  if (m < 60) return `${Math.round(m)}m`
  const h = m / 60
  if (h < 24) return `${Math.round(h)}h`
  const d = h / 24
  if (d < 30) return `${Math.round(d)}d`
  const mo = d / 30
  if (mo < 12) return `${Math.round(mo)}mo`
  return `${Math.round(d / 365)}y`
}

export function daysAgo(iso?: string | null): number | null {
  if (!iso) return null
  return Math.max(0, Math.floor((Date.now() - parseUTC(iso)) / 86_400_000))
}

// "Posted" = how long the role has been live on the BANK's site, from the ATS's
// own publication date. When a bank doesn't expose one we fall back to when this
// platform first saw it — labelled differently so the two are never conflated.
export function postedInfo(
  postedAt?: string | null, firstSeen?: string | null,
): { label: string; value: string; real: boolean } {
  if (postedAt) return { label: 'Posted', value: `${ago(postedAt)} ago`, real: true }
  if (firstSeen) return { label: 'First seen', value: `${ago(firstSeen)} ago`, real: false }
  return { label: 'Posted', value: '—', real: false }
}

// Age of an offer on the site, from a day count, as a short label.
export function ageLabel(days: number): string {
  if (days <= 0) return 'today'
  if (days === 1) return '1 day'
  if (days < 7) return `${days} days`
  const w = Math.round(days / 7)
  if (days < 30) return `${w} wk${w > 1 ? 's' : ''}`
  const mo = Math.round(days / 30)
  return `${mo} mo${mo > 1 ? 's' : ''}`
}

// Urgency tint for how long an offer has been live (fresh -> stale).
export function ageTone(days: number): 'fresh' | 'recent' | 'aging' | 'stale' {
  if (days <= 2) return 'fresh'
  if (days <= 7) return 'recent'
  if (days <= 21) return 'aging'
  return 'stale'
}
