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
  start_year: number | null   // best-effort year the programme starts in
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

export type EventRegStatus = 'not_registered' | 'registered' | 'attended' | 'skipped'

export interface RecruitingEvent {
  id: number
  bank: string
  title: string
  event_type: string          // insight | info_session | networking | workshop | competition | event
  relevance: 'markets' | 'general' | 'other'
  division: string | null
  is_virtual: boolean
  location: string | null
  city: string | null
  country: string | null
  school: string | null
  starts_at: string | null    // wall-clock time at the event, labelled by `timezone`
  ends_at: string | null
  all_day: boolean
  timezone: string | null
  registration_deadline: string | null
  registration_open: boolean
  register_url: string
  source_url: string | null
  description: string | null
  manual: boolean
  first_seen_at: string
  is_active: boolean
  reg_status: EventRegStatus
  notes: string | null
}

export interface NewEvent {
  bank: string
  title: string
  register_url: string
  starts_at?: string | null
  registration_deadline?: string | null
  location?: string | null
  is_virtual?: boolean
  notes?: string | null
}

export interface Automation {
  auto_refresh: boolean
  auto_refresh_hours: number
  last_refresh_at: string | null
  next_refresh_at: string | null
  email_notifications: boolean
  notify_email: string | null
  smtp_host: string | null
  smtp_port: number | null
  smtp_user: string | null
  has_smtp_password: boolean
  email_status: { configured: boolean; at: string | null; ok: boolean | null; detail: string | null }
}

export type AutomationUpdate = Partial<Omit<Automation,
  'last_refresh_at' | 'next_refresh_at' | 'has_smtp_password' | 'email_status'>> & { smtp_password?: string }

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

export interface PipelineEvent {
  from_status: ApplicationStatus | null
  to_status: ApplicationStatus
  at: string
}

export interface PipelineRow {
  offer_id: number
  bank: string
  role_title: string
  location: string
  country: string
  category: string
  program_type: string
  apply_url: string
  start_date_raw: string | null
  start_year: number | null
  is_active: boolean
  status: ApplicationStatus
  notes: string | null
  applied_at: string | null
  updated_at: string
  days_since_applied: number | null
  days_in_stage: number | null
  has_tailored_cv: boolean
  has_tailored_cover_letter: boolean
  events: PipelineEvent[]
}

export interface PipelineOut {
  rows: PipelineRow[]
  by_status: Record<string, number>
  total: number
  stalest_days: number | null
}

export interface Stats {
  total_active_offers: number
  by_status: Record<string, number>
  by_bank: Record<string, number>
  by_start_year: Record<string, number>   // "2027": 15, "unknown": 14
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

// ---------------------------------------------------------------- outreach
export type ContactType = 'alumni' | 'intern' | 'junior' | 'recruiter' | 'senior'
export type OutreachStatus =
  | 'to_contact' | 'sent' | 'replied' | 'call_booked' | 'referred' | 'closed'
export type MessageKind = 'connection_note' | 'dm' | 'followup' | 'thank_you'
export type Channel = 'linkedin' | 'email'
export type AskType = 'chat' | 'referral'

// Compose URLs built by the backend. Opening one hands the draft to the user's
// own mail client or their LinkedIn tab; nothing is sent on their behalf.
export interface SendLinks {
  mailto: string | null
  gmail: string | null
  linkedin: string | null
}

export interface Contact {
  id: number
  full_name: string
  bank: string
  role_title: string | null
  desk: string | null
  location: string | null
  contact_type: ContactType
  linkedin_url: string | null
  email: string | null
  email_guess: string | null
  email_guess_confidence: string | null
  // Already a 1st-degree connection: no Connect button exists for them, so a
  // connection note has nowhere to attach.
  is_connection: boolean
  school: string | null
  grad_year: string | null
  shared_context: string | null
  offer_id: number | null
  offer_title: string | null
  status: OutreachStatus
  notes: string | null
  last_sent_at: string | null
  next_followup_at: string | null
  replied_at: string | null
  followup_count: number
  followups_left: number
  due_in_days: number | null   // negative = overdue
  days_since_sent: number | null
  message_count: number
  created_at: string
}

export interface OutreachMessage {
  id: number
  contact_id: number
  kind: MessageKind
  channel: Channel
  ask_type: AskType
  subject: string | null
  send: SendLinks
  body: string
  char_count: number
  over_limit: boolean
  created_at: string
  sent_at: string | null
  edited: boolean
}

export interface OutreachSummary {
  total: number
  by_status: Record<string, number>
  by_type: Record<string, number>
  due_now: number
  sent_total: number
  replied_total: number
  reply_rate: number
  drafts_unsent: number
}

export interface ContactInput {
  full_name: string
  bank: string
  is_connection?: boolean
  role_title?: string | null
  desk?: string | null
  location?: string | null
  contact_type?: ContactType
  linkedin_url?: string | null
  email?: string | null
  school?: string | null
  grad_year?: string | null
  shared_context?: string | null
  offer_id?: number | null
  notes?: string | null
}

export interface Suggestion {
  id: number
  full_name: string
  linkedin_url: string | null
  email: string | null
  email_guess: string | null
  email_guess_confidence: string | null
  company_raw: string
  bank: string
  employer_tier: 'target_bank' | 'other_ib' | 'market_maker'
  position: string
  category: string
  seniority: string
  score: number
  reasons: string[]
  connected_on: string | null
  dismissed: boolean
  contact_id: number | null
}

export interface ImportResult {
  total_rows: number
  matched: number
  with_shared_email: number
  with_guessed_email: number
  new: number
  already_known: number
  no_employer_match: number
  not_markets_role: number
  top: Suggestion[]
}

export interface SearchLink { label: string; scope: string; url: string }

export interface BatchStatus {
  running: boolean
  total: number
  done: number
  failed: number
  current: string | null
  last_error: string | null
  contact_ids: number[]
}

export interface QueueItem {
  message: OutreachMessage
  contact_id: number
  contact_name: string
  bank: string
  role_title: string | null
  contact_type: ContactType
  status: OutreachStatus
}

export interface BulkDraftRequest {
  suggestion_ids?: number[]
  contact_ids?: number[]
  kind?: MessageKind
  channel?: 'auto' | 'linkedin' | 'email'
  ask_type?: AskType
  target_role?: string
  humanize?: boolean
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
  pipeline: (params: { status?: string; include_not_applied?: boolean } = {}) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.include_not_applied) qs.set('include_not_applied', 'true')
    return jget<PipelineOut>(`/api/pipeline?${qs}`)
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
  listEvents: (includePast = false) =>
    jget<RecruitingEvent[]>(`/api/events${includePast ? '?include_past=true' : ''}`),
  updateEvent: (id: number, body: { reg_status?: EventRegStatus; notes?: string; hidden?: boolean }) =>
    jsend<RecruitingEvent>('PATCH', `/api/events/${id}`, body),
  createEvent: (body: NewEvent) => jsend<RecruitingEvent>('POST', '/api/events', body),
  deleteEvent: (id: number) => jsend<{ ok: boolean }>('DELETE', `/api/events/${id}`),
  getAutomation: () => jget<Automation>('/api/settings/automation'),
  updateAutomation: (body: AutomationUpdate) => jsend<Automation>('PUT', '/api/settings/automation', body),
  testEmail: () => jsend<{ ok: boolean }>('POST', '/api/settings/automation/test-email'),
  getProfile: () => jget<Profile>('/api/profile'),
  updateProfile: (body: Profile) => jsend<Profile>('PUT', '/api/profile', body),
  importLinkedIn: async (file: File): Promise<ImportResult> => {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/outreach/import/linkedin', { method: 'POST', body: fd })
    if (!r.ok) throw new Error(await r.text())
    return r.json()
  },
  suggestions: (params: { category?: string; bank?: string; min_score?: number } = {}) => {
    const qs = new URLSearchParams()
    if (params.category) qs.set('category', params.category)
    if (params.bank) qs.set('bank', params.bank)
    if (params.min_score) qs.set('min_score', String(params.min_score))
    return jget<Suggestion[]>(`/api/outreach/suggestions?${qs}`)
  },
  addSuggestion: (id: number) => jsend<Contact>('POST', `/api/outreach/suggestions/${id}/add`),
  dismissSuggestion: (id: number) =>
    jsend<Suggestion>('POST', `/api/outreach/suggestions/${id}/dismiss`),
  searchLinks: () => jget<SearchLink[]>('/api/outreach/search-links'),
  bulkDraft: (body: BulkDraftRequest) =>
    jsend<BatchStatus>('POST', '/api/outreach/bulk-draft', body),
  bulkDraftStatus: () => jget<BatchStatus>('/api/outreach/bulk-draft/status'),
  sendQueue: () => jget<QueueItem[]>('/api/outreach/queue'),
  outreachSummary: () => jget<OutreachSummary>('/api/outreach/summary'),
  listContacts: (params: { status?: string; bank?: string; contact_type?: string; due?: boolean } = {}) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.bank) qs.set('bank', params.bank)
    if (params.contact_type) qs.set('contact_type', params.contact_type)
    if (params.due) qs.set('due', 'true')
    return jget<Contact[]>(`/api/outreach/contacts?${qs}`)
  },
  createContact: (body: ContactInput) =>
    jsend<Contact>('POST', '/api/outreach/contacts', body),
  updateContact: (id: number, body: Partial<ContactInput> & { status?: OutreachStatus }) =>
    jsend<Contact>('PATCH', `/api/outreach/contacts/${id}`, body),
  deleteContact: (id: number) =>
    fetch(`/api/outreach/contacts/${id}`, { method: 'DELETE' }).then(r => {
      if (!r.ok) throw new Error('delete failed')
    }),
  listMessages: (contactId: number) =>
    jget<OutreachMessage[]>(`/api/outreach/contacts/${contactId}/messages`),
  draftMessage: (contactId: number, body: {
    kind: MessageKind; channel?: Channel; ask_type?: AskType; target_role?: string
    extra_instructions?: string; humanize?: boolean
  }) =>
    jsend<OutreachMessage>('POST', `/api/outreach/contacts/${contactId}/draft`, body),
  editMessage: (id: number, bodyText: string, subject?: string | null) =>
    jsend<OutreachMessage>('PATCH', `/api/outreach/messages/${id}`,
      { body: bodyText, ...(subject !== undefined ? { subject } : {}) }),
  markMessageSent: (id: number) =>
    jsend<Contact>('POST', `/api/outreach/messages/${id}/sent`),
  deleteMessage: (id: number) =>
    fetch(`/api/outreach/messages/${id}`, { method: 'DELETE' }).then(r => {
      if (!r.ok) throw new Error('delete failed')
    }),
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

export const OUTREACH_STATUS_LABEL: Record<OutreachStatus, string> = {
  to_contact: 'To contact',
  sent: 'Sent',
  replied: 'Replied',
  call_booked: 'Call booked',
  referred: 'Referred',
  closed: 'Closed',
}

export const OUTREACH_STATUS_COLOR: Record<OutreachStatus, string> = {
  to_contact: 'bg-ink-800 text-slate-300 ring-1 ring-inset ring-ink-600',
  sent: 'bg-cyan-500/10 text-neon-cyan ring-1 ring-inset ring-cyan-500/40',
  replied: 'bg-violet-500/10 text-neon-violet ring-1 ring-inset ring-violet-500/40',
  call_booked: 'bg-amber-500/10 text-neon-amber ring-1 ring-inset ring-amber-500/40',
  referred: 'bg-emerald-500/10 text-neon-green ring-1 ring-inset ring-emerald-500/40',
  closed: 'bg-rose-500/10 text-neon-rose ring-1 ring-inset ring-rose-500/40',
}

// Ordered by observed reply rate: alumni and current interns answer, seniors
// mostly do not. The UI lists them in this order for that reason.
export const CONTACT_TYPE_LABEL: Record<ContactType, string> = {
  alumni: 'Alumni',
  intern: 'Intern',
  junior: 'Desk junior',
  recruiter: 'Recruiter',
  senior: 'Senior',
}

export const CONTACT_TYPE_COLOR: Record<ContactType, string> = {
  alumni: 'bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-500/30',
  intern: 'bg-cyan-500/10 text-cyan-300 ring-1 ring-inset ring-cyan-500/30',
  junior: 'bg-violet-500/10 text-violet-300 ring-1 ring-inset ring-violet-500/30',
  recruiter: 'bg-amber-500/10 text-amber-300 ring-1 ring-inset ring-amber-500/30',
  senior: 'bg-rose-500/10 text-rose-300 ring-1 ring-inset ring-rose-500/30',
}

export const MESSAGE_KIND_LABEL: Record<MessageKind, string> = {
  connection_note: 'Connection note',
  dm: 'Direct message',
  followup: 'Follow-up',
  thank_you: 'Thank-you',
}

export const CHANNEL_LABEL: Record<Channel, string> = {
  linkedin: 'LinkedIn',
  email: 'Email',
}

export const ASK_LABEL: Record<AskType, string> = {
  chat: 'Ask for a chat',
  referral: 'Ask for a referral',
}

// Shown under the picker so the trade-off is visible at the moment of choosing.
export const ASK_HINT: Record<AskType, string> = {
  chat: 'About their desk, their path, their previous role. No CV, no ask to pass anything on.',
  referral: 'Names the role and asks them to put your CV forward. Converts best once they already know you.',
}

export const TIER_LABEL: Record<Suggestion['employer_tier'], string> = {
  target_bank: 'Tracked bank',
  other_ib: 'Investment bank',
  market_maker: 'Market maker',
}
