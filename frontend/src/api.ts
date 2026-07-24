export type ApplicationStatus =
  | 'not_applied' | 'applied' | 'online_assessment' | 'interview' | 'offer' | 'rejected'

export interface Offer {
  id: number
  bank: string
  role_title: string
  category: string
  location: string
  country: string
  city: string | null
  start_date_raw: string | null
  duration: string | null
  program_type: string
  description: string | null
  apply_url: string
  source_url: string | null
  posted_at: string | null   // when the BANK published it (null if not exposed)
  first_seen_at: string
  last_seen_at: string
  is_active: boolean
  application_status: ApplicationStatus
  application_notes: string | null
  has_tailored_cv: boolean
  has_tailored_cover_letter: boolean
}

export interface NotificationItem {
  id: number
  kind: string
  created_at: string
  offer_snapshot: Record<string, any>
}

export interface RefreshStatus {
  status: 'idle' | 'started' | 'running' | 'done' | 'error'
  started_at: string | null
  finished_at: string | null
  result: any
  implemented_scrapers: string[]
}

export interface ExtraDoc {
  label: string
  filename: string
}

export interface SettingsInfo {
  has_api_key: boolean
  anthropic_model: string
  base_cv_filename: string | null
  base_cover_letter_filename: string | null
  extra_documents: ExtraDoc[]
}

export interface TailorQueueStatus {
  running: boolean
  total: number
  done: number
  failed: number
  current: string | null
  last_error: string | null
  skipped?: string
}

export interface Profile {
  full_name?: string | null
  email?: string | null
  phone?: string | null
  gender?: string | null
  nationality?: string | null
  date_of_birth?: string | null
  address?: string | null
  city?: string | null
  country?: string | null
  postcode?: string | null
  linkedin_url?: string | null
  github_url?: string | null
  portfolio_url?: string | null
  university?: string | null
  degree?: string | null
  graduation_year?: string | null
  gpa?: string | null
  work_authorization?: string | null
  languages?: string | null
}

export type DocSource = 'tailored' | 'original'

export interface ApplyAssistResult {
  apply_url: string
  cv_path: string | null
  cl_path: string | null
  cv_source: DocSource
  cl_source: DocSource
  profile: Profile
  application_status: ApplicationStatus
  tailoring?: boolean  // docs are being generated in the background and will attach when ready
}

export interface Stats {
  total_active_offers: number
  by_status: Record<string, number>
  by_bank: Record<string, number>
  unread_notifications: number
}

export interface DashboardFunnel {
  apps_sent: number; responses: number; interviews: number; offers: number
  sent_today: number; sent_this_week: number; sent_this_month: number; sent_last_month: number
  interviews_this_week: number; hit_rate: number; ghost_rate: number; streak_days: number
}
export interface DashboardPipeline {
  active_offers: number; new_today: number; new_this_week: number
  by_status: Record<string, number>; by_category: Record<string, number>
}
export interface SeriesPoint { date: string; count: number }
export interface RecentOffer {
  id: number; bank: string; role_title: string; category: string
  city: string | null; country: string; start_date_raw: string | null
  first_seen_at: string | null; posted_at: string | null
  age_days: number; application_status: ApplicationStatus
}
export interface ActivityItem {
  kind: 'app' | 'offer_new'; id: number; bank: string; role_title: string; label: string; at: string
}
export interface DashboardData {
  generated_at: string
  funnel: DashboardFunnel
  pipeline: DashboardPipeline
  apps_series: SeriesPoint[]
  offers_series: SeriesPoint[]
  recent_offers: RecentOffer[]
  activity: ActivityItem[]
}

export interface TailorResult {
  cv_path: string
  cover_letter_path: string
  cover_letter_text: string
  cv_changed_paragraphs: number
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}: ${await r.text()}`)
  return r.json() as Promise<T>
}

async function jsend<T>(method: string, path: string, body?: any): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  listOffers: (params: Record<string, string | undefined> = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v)
    return jget<Offer[]>(`/api/offers?${qs}`)
  },
  getOffer: (id: number) => jget<Offer>(`/api/offers/${id}`),
  updateApplication: (id: number, body: { status?: ApplicationStatus; notes?: string }) =>
    jsend<Offer>('PATCH', `/api/offers/${id}/application`, body),
  refresh: () => jsend<RefreshStatus>('POST', '/api/refresh'),
  refreshStatus: () => jget<RefreshStatus>('/api/refresh/status'),
  tailor: (id: number) => jsend<TailorResult>('POST', `/api/offers/${id}/tailor`),
  tailorStatus: () => jget<TailorQueueStatus>('/api/tailor/status'),
  tailorRunAll: () => jsend<TailorQueueStatus>('POST', '/api/tailor/run'),
  downloadDocUrl: (id: number, kind: 'cv' | 'cover_letter', source: DocSource = 'tailored') =>
    `/api/offers/${id}/document/${kind}/${source}`,
  applyAssist: (id: number, body: { cv_source: DocSource; cl_source: DocSource }) =>
    jsend<ApplyAssistResult>('POST', `/api/offers/${id}/apply-assist`, body),
  notifications: () => jget<NotificationItem[]>('/api/notifications'),
  dismissNotification: (id: number) =>
    fetch(`/api/notifications/${id}`, { method: 'DELETE' }).then(r => {
      if (!r.ok) throw new Error('dismiss failed')
    }),
  getSettings: () => jget<SettingsInfo>('/api/settings'),
  updateSettings: (body: { anthropic_api_key?: string; anthropic_model?: string }) =>
    jsend<SettingsInfo>('PUT', '/api/settings', body),
  uploadCV: async (file: File): Promise<SettingsInfo> => {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/settings/base-cv', { method: 'POST', body: fd })
    if (!r.ok) throw new Error(await r.text())
    return r.json()
  },
  uploadCoverLetter: async (file: File): Promise<SettingsInfo> => {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/settings/base-cover-letter', { method: 'POST', body: fd })
    if (!r.ok) throw new Error(await r.text())
    return r.json()
  },
  uploadExtraDocument: async (file: File, label: string): Promise<SettingsInfo> => {
    const fd = new FormData()
    fd.append('file', file)
    if (label.trim()) fd.append('label', label.trim())
    const r = await fetch('/api/settings/extra-document', { method: 'POST', body: fd })
    if (!r.ok) throw new Error(await r.text())
    return r.json()
  },
  deleteExtraDocument: (index: number) =>
    jsend<SettingsInfo>('DELETE', `/api/settings/extra-document/${index}`),
  getProfile: () => jget<Profile>('/api/profile'),
  updateProfile: (body: Profile) => jsend<Profile>('PUT', '/api/profile', body),
  stats: () => jget<Stats>('/api/stats'),
  dashboard: () => jget<DashboardData>('/api/dashboard'),
}

export const STATUS_LABEL: Record<ApplicationStatus, string> = {
  not_applied: 'Not applied',
  applied: 'Applied',
  online_assessment: 'OA',
  interview: 'Interview',
  offer: 'Offer',
  rejected: 'Rejected',
}

export const STATUS_COLOR: Record<ApplicationStatus, string> = {
  not_applied: 'bg-ink-800 text-slate-300 ring-1 ring-inset ring-ink-600',
  applied: 'bg-cyan-500/10 text-neon-cyan ring-1 ring-inset ring-cyan-500/40',
  online_assessment: 'bg-amber-500/10 text-neon-amber ring-1 ring-inset ring-amber-500/40',
  interview: 'bg-violet-500/10 text-neon-violet ring-1 ring-inset ring-violet-500/40',
  offer: 'bg-emerald-500/10 text-neon-green ring-1 ring-inset ring-emerald-500/40',
  rejected: 'bg-rose-500/10 text-neon-rose ring-1 ring-inset ring-rose-500/40',
}

export const CATEGORY_COLOR: Record<string, string> = {
  sales: 'bg-cyan-500/10 text-cyan-300 ring-1 ring-inset ring-cyan-500/30',
  trading: 'bg-violet-500/10 text-violet-300 ring-1 ring-inset ring-violet-500/30',
  structuring: 'bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-500/30',
  markets: 'bg-amber-500/10 text-amber-300 ring-1 ring-inset ring-amber-500/30',
}
