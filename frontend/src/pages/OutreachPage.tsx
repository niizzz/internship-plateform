import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  api, AskType, Channel, Contact, ContactInput, ContactType, MessageKind,
  OutreachMessage, OutreachStatus,
  ASK_HINT, ASK_LABEL, CHANNEL_LABEL,
  CONTACT_TYPE_COLOR, CONTACT_TYPE_LABEL, MESSAGE_KIND_LABEL,
  OUTREACH_STATUS_COLOR, OUTREACH_STATUS_LABEL,
} from '../api'
import CompanyLogo from '../components/CompanyLogo'
import FindPeople from '../components/FindPeople'
import SendQueue from '../components/SendQueue'
import { ago } from '../lib/format'
import { openTab } from '../lib/openTab'

// Reply rate drops steeply down this list, so it is also the order the type
// filter and the add-contact form present.
const TYPES: ContactType[] = ['alumni', 'intern', 'junior', 'recruiter', 'senior']
const STATUSES: OutreachStatus[] = [
  'to_contact', 'sent', 'replied', 'call_booked', 'referred', 'closed',
]
const KINDS: MessageKind[] = ['connection_note', 'dm', 'followup', 'thank_you']
const CHANNELS: Channel[] = ['linkedin', 'email']
const ASKS: AskType[] = ['chat', 'referral']

// A connection note is a LinkedIn artefact; over email the first touch is just
// an email, so the note option disappears rather than producing a dead end.
const kindsFor = (ch: Channel): MessageKind[] =>
  ch === 'email' ? KINDS.filter(k => k !== 'connection_note') : KINDS

const CONNECTION_NOTE_MAX = 300

export default function OutreachPage() {
  const [status, setStatus] = useState<OutreachStatus | ''>('')
  const [type, setType] = useState<ContactType | ''>('')
  const [dueOnly, setDueOnly] = useState(false)
  const [adding, setAdding] = useState(false)
  const [open, setOpen] = useState<number | null>(null)

  const { data: summary } = useQuery({
    queryKey: ['outreach-summary'],
    queryFn: api.outreachSummary,
    refetchInterval: 30_000,
  })
  const { data: contacts, isLoading } = useQuery({
    queryKey: ['contacts', status, type, dueOnly],
    queryFn: () => api.listContacts({
      status: status || undefined,
      contact_type: type || undefined,
      due: dueOnly || undefined,
    }),
  })

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">
      <div className="animate-risein flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500 font-mono flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-neon-green/70 fx-radar" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-neon-green" />
            </span>
            Networking
          </div>
          <h1 className="mt-1 text-2xl font-semibold fx-textshine">Outreach</h1>
          <p className="mt-1 text-sm text-slate-400 max-w-2xl">
            Drafts every message and tracks every thread. You send them yourself:
            LinkedIn bans automated sending, and a message worth reading is worth
            the ten seconds to paste.
          </p>
        </div>
        <button
          onClick={() => setAdding(v => !v)}
          className="shrink-0 px-3 py-2 rounded-lg border border-neon-cyan/50 text-neon-cyan text-sm
                     hover:bg-ink-800 transition-all active:scale-95">
          {adding ? 'Cancel' : '+ Add contact'}
        </button>
      </div>

      {summary && <SummaryBar s={summary} onDue={() => { setDueOnly(true); setStatus('') }} />}

      <FindPeople />

      <SendQueue />

      {adding && <AddContactForm onDone={() => setAdding(false)} />}

      <FilterBar
        status={status} type={type} dueOnly={dueOnly}
        counts={summary?.by_status ?? {}}
        onStatus={s => setStatus(s === status ? '' : s)}
        onType={t => setType(t === type ? '' : t)}
        onDue={() => setDueOnly(v => !v)}
      />

      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map(i => (
            <div key={i} className="h-20 rounded-xl bg-ink-900/60 border border-ink-700 animate-pulse" />
          ))}
        </div>
      ) : !contacts?.length ? (
        <Empty filtered={!!status || !!type || dueOnly}
          onClear={() => { setStatus(''); setType(''); setDueOnly(false) }}
          onAdd={() => setAdding(true)} />
      ) : (
        <div className="space-y-3">
          {contacts.map((c, i) => (
            <div key={c.id} className="animate-rowin"
              style={{ animationDelay: `${Math.min(i, 12) * 45}ms` }}>
              <ContactRow
                c={c}
                expanded={open === c.id}
                onToggle={() => setOpen(open === c.id ? null : c.id)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SummaryBar({ s, onDue }: { s: NonNullable<ReturnType<typeof useQuery<any>>['data']>; onDue: () => void }) {
  const tiles = [
    { label: 'Contacts', value: s.total, tone: 'text-slate-100' },
    { label: 'Due now', value: s.due_now, tone: s.due_now > 0 ? 'text-neon-amber' : 'text-slate-100', click: s.due_now > 0 ? onDue : undefined },
    { label: 'Sent', value: s.sent_total, tone: 'text-slate-100' },
    { label: 'Replied', value: s.replied_total, tone: 'text-neon-violet' },
    {
      label: 'Reply rate',
      value: s.sent_total ? `${Math.round(s.reply_rate * 100)}%` : '—',
      tone: 'text-neon-green',
    },
    { label: 'Unsent drafts', value: s.drafts_unsent, tone: 'text-slate-100' },
  ]
  return (
    <div className="animate-risein grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3"
      style={{ animationDelay: '40ms' }}>
      {tiles.map(t => (
        <div key={t.label}
          onClick={t.click}
          className={`bg-ink-900/60 border border-ink-700 rounded-xl p-3 backdrop-blur ${
            t.click ? 'cursor-pointer hover:border-neon-amber/50 transition-colors' : ''}`}>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">{t.label}</div>
          <div className={`text-xl font-semibold tabular-nums leading-tight ${t.tone}`}>{t.value}</div>
        </div>
      ))}
    </div>
  )
}

function FilterBar(props: {
  status: OutreachStatus | ''; type: ContactType | ''; dueOnly: boolean
  counts: Record<string, number>
  onStatus: (s: OutreachStatus) => void
  onType: (t: ContactType) => void
  onDue: () => void
}) {
  return (
    <div className="animate-risein bg-ink-900/60 border border-ink-700 rounded-xl p-3 backdrop-blur space-y-2"
      style={{ animationDelay: '60ms' }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono w-14">Stage</span>
        {STATUSES.map(s => {
          const n = props.counts[s] ?? 0
          const on = props.status === s
          return (
            <button key={s} onClick={() => props.onStatus(s)} aria-pressed={on}
              className={`px-2.5 py-1 rounded-lg border text-xs transition-all active:scale-95 ${
                on ? 'border-neon-cyan bg-ink-800 text-neon-cyan'
                   : 'border-ink-700 text-slate-400 hover:border-neon-cyan/40'
              } ${n === 0 ? 'opacity-45' : ''}`}>
              {OUTREACH_STATUS_LABEL[s]} <span className="tabular-nums text-slate-500">{n}</span>
            </button>
          )
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono w-14">Who</span>
        {TYPES.map(t => {
          const on = props.type === t
          return (
            <button key={t} onClick={() => props.onType(t)} aria-pressed={on}
              className={`px-2.5 py-1 rounded-lg border text-xs transition-all active:scale-95 ${
                on ? 'border-neon-violet bg-ink-800 text-neon-violet'
                   : 'border-ink-700 text-slate-400 hover:border-neon-violet/40'}`}>
              {CONTACT_TYPE_LABEL[t]}
            </button>
          )
        })}
        <button onClick={props.onDue} aria-pressed={props.dueOnly}
          className={`ml-auto px-2.5 py-1 rounded-lg border text-xs transition-all active:scale-95 ${
            props.dueOnly ? 'border-neon-amber bg-ink-800 text-neon-amber'
                          : 'border-ink-700 text-slate-400 hover:border-neon-amber/40'}`}>
          Follow-up due
        </button>
      </div>
    </div>
  )
}

function ContactRow({ c, expanded, onToggle }: {
  c: Contact; expanded: boolean; onToggle: () => void
}) {
  const overdue = c.due_in_days !== null && c.due_in_days <= 0
  return (
    <div className={`bg-ink-900/60 border rounded-xl backdrop-blur transition-colors ${
      overdue ? 'border-neon-amber/50' : 'border-ink-700 hover:border-ink-600'}`}>
      <button onClick={onToggle}
        className="w-full text-left p-3 flex items-center gap-3">
        <CompanyLogo bank={c.bank} size={38} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-slate-100 truncate">{c.full_name}</span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${CONTACT_TYPE_COLOR[c.contact_type]}`}>
              {CONTACT_TYPE_LABEL[c.contact_type]}
            </span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${OUTREACH_STATUS_COLOR[c.status]}`}>
              {OUTREACH_STATUS_LABEL[c.status]}
            </span>
          </div>
          <div className="text-xs text-slate-400 truncate mt-0.5">
            {[c.role_title, c.desk, c.location].filter(Boolean).join(' · ') || c.bank}
          </div>
        </div>
        <div className="hidden sm:flex flex-col items-end text-[11px] font-mono shrink-0">
          {overdue ? (
            <span className="text-neon-amber">
              follow-up {c.due_in_days === 0 ? 'today' : `${Math.abs(c.due_in_days!)}d overdue`}
            </span>
          ) : c.due_in_days !== null ? (
            <span className="text-slate-500">follow-up in {c.due_in_days}d</span>
          ) : c.last_sent_at ? (
            <span className="text-slate-500">sent {ago(c.last_sent_at)} ago</span>
          ) : (
            <span className="text-slate-600">not contacted</span>
          )}
          <span className="text-slate-600">{c.message_count} msg</span>
        </div>
      </button>
      {expanded && <ContactDetail c={c} />}
    </div>
  )
}

function ContactDetail({ c }: { c: Contact }) {
  const qc = useQueryClient()
  const [channel, setChannel] = useState<Channel>(c.email ? 'email' : 'linkedin')
  const [ask, setAsk] = useState<AskType>('chat')
  const [targetRole, setTargetRole] = useState('')
  const [kind, setKind] = useState<MessageKind>(
    c.status !== 'to_contact' ? 'followup'
      : c.is_connection ? 'dm'          // already connected: Message, not Connect
      : 'connection_note')
  const [extra, setExtra] = useState('')
  const [humanize, setHumanize] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const { data: messages } = useQuery({
    queryKey: ['messages', c.id],
    queryFn: () => api.listMessages(c.id),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['messages', c.id] })
    qc.invalidateQueries({ queryKey: ['contacts'] })
    qc.invalidateQueries({ queryKey: ['outreach-summary'] })
  }

  const draft = useMutation({
    mutationFn: () => api.draftMessage(c.id, {
      kind, channel, ask_type: ask,
      target_role: targetRole.trim() || undefined,
      extra_instructions: extra.trim() || undefined, humanize,
    }),
    onSuccess: () => { setErr(null); setExtra(''); invalidate() },
    onError: (e: Error) => setErr(e.message),
  })

  // Promoting a guess is deliberate: until this runs, `email` stays empty and
  // the email channel stays unavailable for this person.
  const useGuess = useMutation({
    mutationFn: () => api.updateContact(c.id, { email: c.email_guess }),
    onSuccess: invalidate,
  })

  const setStatus = useMutation({
    mutationFn: (s: OutreachStatus) => api.updateContact(c.id, { status: s }),
    onSuccess: invalidate,
  })

  const del = useMutation({
    mutationFn: () => api.deleteContact(c.id),
    onSuccess: invalidate,
  })

  return (
    <div className="border-t border-ink-700 p-3 space-y-4">
      {!c.email && c.email_guess && (
        <div className="flex flex-wrap items-center gap-2 text-[11px] bg-ink-950/50 border border-ink-700 rounded-lg p-2">
          <span className="text-slate-400">
            Likely address <span className="font-mono text-slate-200">{c.email_guess}</span>
            <span className="text-neon-amber"> · unverified</span>
          </span>
          <span className="text-slate-600">
            built from {c.bank}'s usual format, not confirmed. Email is off until you accept it.
          </span>
          <button
            onClick={() => useGuess.mutate()}
            disabled={useGuess.isPending}
            className="ml-auto px-2 py-1 rounded border border-neon-cyan/50 text-neon-cyan
                       hover:bg-ink-800 disabled:opacity-50 transition-all active:scale-95">
            {useGuess.isPending ? 'Saving…' : 'Use this address'}
          </button>
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-3 text-xs">
        <Facts c={c} />
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">Move to</div>
          <div className="flex flex-wrap gap-1.5">
            {STATUSES.map(s => (
              <button key={s} disabled={s === c.status || setStatus.isPending}
                onClick={() => setStatus.mutate(s)}
                className={`px-2 py-1 rounded border text-[11px] transition-all active:scale-95 ${
                  s === c.status
                    ? 'border-ink-600 text-slate-600 cursor-default'
                    : 'border-ink-700 text-slate-300 hover:border-neon-cyan/50'}`}>
                {OUTREACH_STATUS_LABEL[s]}
              </button>
            ))}
          </div>
          {c.linkedin_url && (
            <a href={c.linkedin_url} target="_blank" rel="noreferrer"
              className="inline-block text-[11px] text-neon-cyan hover:underline">
              Open LinkedIn profile ↗
            </a>
          )}
        </div>
      </div>

      {/* ---- drafting ---- */}
      <div className="bg-ink-950/50 border border-ink-700 rounded-lg p-3 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono w-12">Via</span>
          {CHANNELS.map(ch => {
            const noEmail = ch === 'email' && !c.email
            return (
              <button key={ch} disabled={noEmail}
                title={noEmail ? 'No email address on this contact yet' : undefined}
                onClick={() => {
                  setChannel(ch)
                  // A connection note does not exist over email.
                  if (ch === 'email' && kind === 'connection_note') setKind('dm')
                }}
                aria-pressed={channel === ch}
                className={`px-2.5 py-1 rounded-lg border text-xs transition-all active:scale-95 ${
                  channel === ch ? 'border-neon-cyan bg-ink-800 text-neon-cyan'
                                 : 'border-ink-700 text-slate-400 hover:border-neon-cyan/40'
                } ${noEmail ? 'opacity-40 cursor-not-allowed' : ''}`}>
                {CHANNEL_LABEL[ch]}
              </button>
            )
          })}
          <label className="ml-auto flex items-center gap-1.5 text-[11px] text-slate-400 cursor-pointer">
            <input type="checkbox" checked={humanize} onChange={e => setHumanize(e.target.checked)}
              className="accent-cyan-400" />
            humanise (slower, worth it)
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono w-12">Ask</span>
          {ASKS.map(a => (
            <button key={a} onClick={() => setAsk(a)} aria-pressed={ask === a}
              className={`px-2.5 py-1 rounded-lg border text-xs transition-all active:scale-95 ${
                ask === a ? 'border-neon-violet bg-ink-800 text-neon-violet'
                          : 'border-ink-700 text-slate-400 hover:border-neon-violet/40'}`}>
              {ASK_LABEL[a]}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-slate-500 pl-14 -mt-1">{ASK_HINT[ask]}</p>

        {ask === 'referral' && !c.offer_id && (
          <input
            value={targetRole} onChange={e => setTargetRole(e.target.value)}
            placeholder="Which role? e.g. Structured Products Sales summer internship 2027, Paris"
            className="w-full bg-ink-900 border border-ink-700 rounded px-2 py-1.5 text-xs text-slate-200
                       placeholder:text-slate-600 focus:outline-none focus:border-neon-violet/50" />
        )}
        {ask === 'referral' && c.status === 'to_contact' && (
          <p className="text-[11px] text-neon-amber/90">
            You have not spoken to {c.full_name.split(' ')[0]} yet. A cold referral
            ask converts far worse than a chat first, and you only get one. The
            draft will hedge it accordingly, but consider asking for the chat.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono w-12">Type</span>
          {kindsFor(channel)
            .filter(k => !(k === 'connection_note' && c.is_connection))
            .map(k => (
            <button key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
              className={`px-2.5 py-1 rounded-lg border text-xs transition-all active:scale-95 ${
                kind === k ? 'border-neon-cyan bg-ink-800 text-neon-cyan'
                           : 'border-ink-700 text-slate-400 hover:border-neon-cyan/40'}`}>
              {channel === 'email' && k === 'dm' ? 'First email' : MESSAGE_KIND_LABEL[k]}
            </button>
          ))}
        </div>
        <input
          value={extra} onChange={e => setExtra(e.target.value)}
          placeholder="Optional steer, e.g. 'mention I'm in Paris in October' or 'keep it to two lines'"
          className="w-full bg-ink-900 border border-ink-700 rounded px-2 py-1.5 text-xs text-slate-200
                     placeholder:text-slate-600 focus:outline-none focus:border-neon-cyan/50" />
        <div className="flex items-center gap-2">
          <button onClick={() => draft.mutate()} disabled={draft.isPending}
            className="px-3 py-1.5 rounded-lg border border-neon-cyan/50 text-neon-cyan text-xs
                       hover:bg-ink-800 disabled:opacity-50 transition-all active:scale-95">
            {draft.isPending ? 'Drafting…' : `Draft ${MESSAGE_KIND_LABEL[kind].toLowerCase()}`}
          </button>
          {draft.isPending && (
            <span className="text-[11px] text-slate-500 font-mono">
              Claude CLI · {humanize ? 'two passes' : 'one pass'}
            </span>
          )}
          <button onClick={() => { if (confirm(`Delete ${c.full_name} and all their drafts?`)) del.mutate() }}
            className="ml-auto text-[11px] text-slate-600 hover:text-neon-rose transition-colors">
            Delete contact
          </button>
        </div>
        {err && <div className="text-[11px] text-neon-rose">{err}</div>}
        {!c.shared_context && (
          <div className="text-[11px] text-slate-500">
            No shared context recorded, so drafts will be written as an honest cold
            approach. Adding a real tie (same course, met at an event, their own
            post) is the single biggest lift to reply rate.
          </div>
        )}
      </div>

      {!!messages?.length && (
        <div className="space-y-2">
          {messages.map(m => <MessageCard key={m.id} m={m} onChanged={invalidate} />)}
        </div>
      )}
    </div>
  )
}

function Facts({ c }: { c: Contact }) {
  const rows: [string, string | null][] = [
    ['Firm', c.bank],
    ['Role', c.role_title],
    ['Desk', c.desk],
    ['Email', c.email],
    ['School', c.school ? `${c.school}${c.grad_year ? ` '${c.grad_year}` : ''}` : null],
    ['Target offer', c.offer_title],
    ['Shared context', c.shared_context],
    ['Notes', c.notes],
  ]
  return (
    <dl className="space-y-1">
      {rows.filter(([, v]) => v).map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="text-[10px] uppercase tracking-wider text-slate-500 font-mono w-24 shrink-0 pt-0.5">{k}</dt>
          <dd className="text-slate-300">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function MessageCard({ m, onChanged }: { m: OutreachMessage; onChanged: () => void }) {
  const [body, setBody] = useState(m.body)
  const [subject, setSubject] = useState(m.subject ?? '')
  const [copied, setCopied] = useState(false)
  const isEmail = m.channel === 'email'
  const dirty = body !== m.body || (isEmail && subject !== (m.subject ?? ''))
  const chars = body.length
  const over = m.kind === 'connection_note' && chars > CONNECTION_NOTE_MAX

  const save = useMutation({
    mutationFn: () => api.editMessage(m.id, body, isEmail ? subject : undefined),
    onSuccess: onChanged,
  })

  // Save first, then open: the compose URLs are built server-side from the
  // stored text, so sending an unsaved edit would open the previous draft. It
  // also means the DB holds exactly what was sent.
  const openSend = async (pick: (l: OutreachMessage['send']) => string | null) => {
    const fresh = dirty ? await save.mutateAsync() : m
    const url = pick(fresh.send)
    if (url) openTab(url)
  }
  const sent = useMutation({
    mutationFn: () => api.markMessageSent(m.id),
    onSuccess: onChanged,
  })
  const del = useMutation({
    mutationFn: () => api.deleteMessage(m.id),
    onSuccess: onChanged,
  })

  const [copyFailed, setCopyFailed] = useState(false)

  const copy = async (): Promise<boolean> => {
    try {
      await navigator.clipboard.writeText(body)
      setCopied(true); setCopyFailed(false)
      setTimeout(() => setCopied(false), 1600)
      return true
    } catch {
      // Swallowing this made a denied clipboard look like a success, and the
      // paste on LinkedIn then did nothing with no explanation.
      setCopied(false); setCopyFailed(true)
      return false
    }
  }

  return (
    <div className={`border rounded-lg p-3 space-y-2 ${
      m.sent_at ? 'border-ink-700 bg-ink-900/40' : 'border-neon-cyan/30 bg-ink-950/60'}`}>
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider font-mono">
        <span className="text-slate-400">
          {isEmail && m.kind === 'dm' ? 'First email' : MESSAGE_KIND_LABEL[m.kind]}
        </span>
        <span className="text-slate-600">{m.channel === 'email' ? 'email' : 'linkedin'}</span>
        {m.ask_type === 'referral' && <span className="text-neon-violet">referral</span>}
        {m.edited && <span className="text-slate-600">edited</span>}
        <span className={over ? 'text-neon-rose' : 'text-slate-600'}>
          {chars}{m.kind === 'connection_note' ? `/${CONNECTION_NOTE_MAX}` : ' chars'}
        </span>
        <span className="ml-auto text-slate-600">
          {m.sent_at ? `sent ${ago(m.sent_at)} ago` : `drafted ${ago(m.created_at)} ago`}
        </span>
      </div>
      {isEmail && (
        <input
          value={subject} onChange={e => setSubject(e.target.value)}
          placeholder="Subject"
          className="w-full bg-ink-900 border border-ink-700 rounded px-2 py-1.5 text-sm text-slate-200
                     placeholder:text-slate-600 focus:outline-none focus:border-neon-cyan/50" />
      )}
      <textarea
        value={body} onChange={e => setBody(e.target.value)} rows={Math.min(10, body.split('\n').length + 2)}
        className={`w-full bg-ink-900 border rounded px-2 py-1.5 text-sm text-slate-200 leading-relaxed
                    focus:outline-none resize-y ${
          over ? 'border-neon-rose/60' : 'border-ink-700 focus:border-neon-cyan/50'}`} />
      {over && (
        <div className="text-[11px] text-neon-rose">
          LinkedIn cuts a connection note at {CONNECTION_NOTE_MAX} characters. Trim
          it, or redraft.
        </div>
      )}
      <p className="text-[11px] text-slate-500">
        {isEmail
          ? 'Gmail opens pre-filled. Review, then send.'
          : m.kind === 'connection_note'
            ? 'On their profile: Connect → Add a note → paste (Ctrl+V).'
            : 'On their profile: Message → paste (Ctrl+V). The text is on your clipboard.'}
      </p>
      {copyFailed && (
        <p className="text-[11px] text-neon-rose">
          Could not reach the clipboard. Select the text above and copy it by hand.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={copy}
          className="px-2.5 py-1 rounded border border-ink-600 text-slate-300 text-xs
                     hover:border-neon-cyan/50 transition-all active:scale-95">
          {copied ? 'Copied' : 'Copy'}
        </button>
        {isEmail ? (
          <>
            <button onClick={() => openSend(l => l.gmail)} disabled={!m.send.gmail || save.isPending}
              title={m.send.gmail ? 'Opens Gmail with this drafted' : 'No email address on this contact'}
              className="px-2.5 py-1 rounded border border-neon-cyan/50 text-neon-cyan text-xs
                         hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
              Open in Gmail
            </button>
            <button onClick={() => openSend(l => l.mailto)} disabled={!m.send.mailto || save.isPending}
              className="px-2.5 py-1 rounded border border-ink-600 text-slate-300 text-xs
                         hover:border-neon-cyan/50 disabled:opacity-40 transition-all active:scale-95">
              Mail app
            </button>
          </>
        ) : (
          <button
            onClick={async () => { await copy(); await openSend(l => l.linkedin) }}
            disabled={!m.send.linkedin || save.isPending}
            title={m.send.linkedin ? 'Copies the text and opens their profile' : 'No LinkedIn URL on this contact'}
            className="px-2.5 py-1 rounded border border-neon-cyan/50 text-neon-cyan text-xs
                       hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
            Copy + open LinkedIn
          </button>
        )}
        {dirty && (
          <button onClick={() => save.mutate()} disabled={save.isPending}
            className="px-2.5 py-1 rounded border border-ink-600 text-slate-400 text-xs
                       hover:border-neon-cyan/50 disabled:opacity-50 transition-all active:scale-95">
            Save edit
          </button>
        )}
        {!m.sent_at && (
          <button onClick={() => sent.mutate()} disabled={sent.isPending || dirty}
            title={dirty ? 'Save your edit first' : 'Record that you sent this'}
            className="px-2.5 py-1 rounded border border-neon-green/50 text-neon-green text-xs
                       hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
            I sent this
          </button>
        )}
        <button onClick={() => del.mutate()}
          className="ml-auto text-[11px] text-slate-600 hover:text-neon-rose transition-colors">
          Delete
        </button>
      </div>
    </div>
  )
}

function AddContactForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState<ContactInput>({
    full_name: '', bank: '', contact_type: 'junior',
  })
  const [err, setErr] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => api.createContact(f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['contacts'] })
      qc.invalidateQueries({ queryKey: ['outreach-summary'] })
      onDone()
    },
    onError: (e: Error) => setErr(e.message),
  })

  const set = (k: keyof ContactInput) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: e.target.value })

  const valid = f.full_name.trim() && f.bank.trim()

  return (
    <div className="animate-risein bg-ink-900/60 border border-ink-700 rounded-xl p-4 backdrop-blur space-y-3">
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <Field label="Name *" value={f.full_name} onChange={set('full_name')} placeholder="Marie Dupont" />
        <Field label="Firm *" value={f.bank ?? ''} onChange={set('bank')} placeholder="Société Générale" />
        <div>
          <label className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">Who they are</label>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {TYPES.map(t => (
              <button key={t} onClick={() => setF({ ...f, contact_type: t })}
                aria-pressed={f.contact_type === t}
                className={`px-2 py-1 rounded border text-[11px] transition-all active:scale-95 ${
                  f.contact_type === t
                    ? 'border-neon-violet bg-ink-800 text-neon-violet'
                    : 'border-ink-700 text-slate-400 hover:border-neon-violet/40'}`}>
                {CONTACT_TYPE_LABEL[t]}
              </button>
            ))}
          </div>
        </div>
        <Field label="Their role" value={f.role_title ?? ''} onChange={set('role_title')}
          placeholder="Analyst, Equity Derivatives Sales" />
        <Field label="Desk / product" value={f.desk ?? ''} onChange={set('desk')}
          placeholder="Structured Equity Derivatives" />
        <Field label="Location" value={f.location ?? ''} onChange={set('location')} placeholder="Paris" />
        <Field label="School (if alumni)" value={f.school ?? ''} onChange={set('school')} placeholder="Dauphine" />
        <Field label="Grad year" value={f.grad_year ?? ''} onChange={set('grad_year')} placeholder="21" />
        <Field label="LinkedIn URL" value={f.linkedin_url ?? ''} onChange={set('linkedin_url')}
          placeholder="https://www.linkedin.com/in/…" />
      </div>
      <div>
        <label className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">
          Shared context — the real tie, in your words
        </label>
        <textarea
          value={f.shared_context ?? ''} onChange={set('shared_context')} rows={2}
          placeholder="Same MSc two years above me · spoke at our markets society in March · ex-Lazard like my summer desk"
          className="mt-1 w-full bg-ink-900 border border-ink-700 rounded px-2 py-1.5 text-sm text-slate-200
                     placeholder:text-slate-600 focus:outline-none focus:border-neon-cyan/50" />
        <p className="mt-1 text-[11px] text-slate-500">
          Only what is true goes in here. The drafter is forbidden from inventing a
          connection, so anything you leave out simply will not appear.
        </p>
      </div>
      {err && <div className="text-[11px] text-neon-rose">{err}</div>}
      <div className="flex items-center gap-2">
        <button onClick={() => create.mutate()} disabled={!valid || create.isPending}
          className="px-3 py-1.5 rounded-lg border border-neon-green/50 text-neon-green text-sm
                     hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
          {create.isPending ? 'Adding…' : 'Add contact'}
        </button>
        <button onClick={onDone} className="text-xs text-slate-500 hover:text-slate-300">Cancel</button>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, placeholder }: {
  label: string; value: string
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  placeholder?: string
}) {
  return (
    <div>
      <label className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">{label}</label>
      <input value={value} onChange={onChange} placeholder={placeholder}
        className="mt-1 w-full bg-ink-900 border border-ink-700 rounded px-2 py-1.5 text-sm text-slate-200
                   placeholder:text-slate-600 focus:outline-none focus:border-neon-cyan/50" />
    </div>
  )
}

function Empty({ filtered, onClear, onAdd }: {
  filtered: boolean; onClear: () => void; onAdd: () => void
}) {
  return (
    <div className="bg-ink-900/40 border border-ink-700 border-dashed rounded-xl p-10 text-center">
      <p className="text-slate-300">
        {filtered ? 'No contacts match that filter.' : 'No contacts yet.'}
      </p>
      <p className="mt-1 text-sm text-slate-500 max-w-md mx-auto">
        {filtered
          ? 'Try clearing it.'
          : 'Add someone worth writing to. Alumni and current interns reply far more than anyone senior, so start there.'}
      </p>
      <button onClick={filtered ? onClear : onAdd}
        className="mt-4 px-3 py-1.5 rounded-lg border border-neon-cyan/50 text-neon-cyan text-sm
                   hover:bg-ink-800 transition-all active:scale-95">
        {filtered ? 'Clear filters' : 'Add your first contact'}
      </button>
    </div>
  )
}
