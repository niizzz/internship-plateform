import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, QueueItem, MESSAGE_KIND_LABEL } from '../api'
import CompanyLogo from './CompanyLogo'
import { openTab } from '../lib/openTab'

const CONNECTION_NOTE_MAX = 300

// The send step for a batch. Drafting is automated; sending is not, so the job
// here is to make each one two clicks: open the composer, then confirm it went.
// One message at a time on purpose — a wall of drafts does not get read, and an
// unread draft is how a wrong message goes out.
export default function SendQueue() {
  const qc = useQueryClient()
  const [i, setI] = useState(0)
  const [body, setBody] = useState('')
  const [subject, setSubject] = useState('')
  const [copied, setCopied] = useState(false)
  // Shortcuts are live only while the panel is hovered or focused. Marking a
  // message sent has no undo, so a stray 's' typed while reading the list
  // above must not reach it.
  const [armed, setArmed] = useState(false)

  const { data: queue, isLoading } = useQuery({
    queryKey: ['send-queue'],
    queryFn: api.sendQueue,
  })

  const items = queue ?? []
  const idx = Math.min(i, Math.max(0, items.length - 1))
  const item: QueueItem | undefined = items[idx]
  const m = item?.message

  // Reset the editors whenever the queue moves to a different draft.
  useEffect(() => {
    setBody(m?.body ?? '')
    setSubject(m?.subject ?? '')
    setCopied(false)
  }, [m?.id])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['send-queue'] })
    qc.invalidateQueries({ queryKey: ['contacts'] })
    qc.invalidateQueries({ queryKey: ['outreach-summary'] })
  }

  const isEmail = m?.channel === 'email'
  const dirty = !!m && (body !== m.body || (isEmail && subject !== (m.subject ?? '')))
  const over = !!m && m.channel === 'linkedin' && m.kind === 'connection_note'
    && body.length > CONNECTION_NOTE_MAX

  const save = useMutation({
    mutationFn: () => api.editMessage(m!.id, body, isEmail ? subject : undefined),
  })
  const markSent = useMutation({
    mutationFn: () => api.markMessageSent(m!.id),
    onSuccess: () => { invalidate(); setCopied(false) },
  })

  const [copyFailed, setCopyFailed] = useState(false)

  // Returns whether it worked. A silent failure here is invisible until the
  // paste on the other side does nothing, so it has to surface.
  const copy = async (): Promise<boolean> => {
    try {
      await navigator.clipboard.writeText(body)
      setCopied(true); setCopyFailed(false)
      return true
    } catch {
      setCopied(false); setCopyFailed(true)
      return false
    }
  }

  // Save before opening: the compose URLs are built server-side from the
  // stored text, so an unsaved edit would open the previous version.
  const open = async (pick: (l: QueueItem['message']['send']) => string | null) => {
    const fresh = dirty ? await save.mutateAsync() : m!
    const url = pick(fresh.send)
    if (url) openTab(url)
  }

  const advance = async () => {
    if (!m) return
    if (dirty) await save.mutateAsync()
    await markSent.mutateAsync()
    // The sent item leaves the queue, so this index already points at the next.
    setI(Math.min(idx, Math.max(0, items.length - 2)))
  }

  // The default action for whichever channel this draft is on.
  const openComposer = async () => {
    if (!m) return
    if (isEmail) { await open(l => l.gmail ?? l.mailto) } else { await copy(); await open(l => l.linkedin) }
  }

  // One keypress per message. Sending stays a human act, but it should not cost
  // a human more than a keystroke. Typing in the editors is never intercepted,
  // so the guard checks the event target rather than tracking focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!armed) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      const k = e.key.toLowerCase()
      if (k === 's') { e.preventDefault(); void advance() }
      else if (k === 'o' || e.key === 'Enter') { e.preventDefault(); void openComposer() }
      else if (k === 'c') { e.preventDefault(); void copy() }
      else if (k === 'j' || e.key === 'ArrowRight') { setI(n => Math.min(items.length - 1, n + 1)) }
      else if (k === 'k' || e.key === 'ArrowLeft') { setI(n => Math.max(0, n - 1)) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (isLoading) {
    return <div className="h-24 rounded-xl bg-ink-900/60 border border-ink-700 animate-pulse" />
  }
  if (!items.length) {
    return (
      <div className="animate-risein bg-ink-900/40 border border-ink-700 border-dashed rounded-xl p-6 text-center">
        <p className="text-sm text-slate-300">Send queue is empty.</p>
        <p className="mt-1 text-[11px] text-slate-500">
          Select people above and draft a batch. Finished drafts queue up here.
        </p>
      </div>
    )
  }

  return (
    <div
      onMouseEnter={() => setArmed(true)}
      onMouseLeave={() => setArmed(false)}
      onFocusCapture={() => setArmed(true)}
      onBlurCapture={() => setArmed(false)}
      className={`animate-risein bg-ink-900/60 border rounded-xl backdrop-blur transition-colors ${
        armed ? 'border-neon-cyan/60' : 'border-neon-cyan/30'}`}
      style={{ animationDelay: '55ms' }}>
      <div className="p-3 flex items-center gap-3 border-b border-ink-700">
        <div className="min-w-0 flex-1">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">
            Send queue
          </div>
          <div className="text-sm text-slate-300">
            {items.length} draft{items.length === 1 ? '' : 's'} ready to send
          </div>
          <div className={`mt-0.5 text-[10px] font-mono transition-colors ${
            armed ? 'text-slate-400' : 'text-slate-600'}`}>
            <Key>o</Key> open composer · <Key>s</Key> sent, next · <Key>c</Key> copy ·
            {' '}<Key>j</Key>/<Key>k</Key> move
            <span className="ml-1 text-slate-600">
              {armed ? '· live' : '· hover to use'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 font-mono text-[11px]">
          <button onClick={() => setI(Math.max(0, idx - 1))} disabled={idx === 0}
            className="px-2 py-1 rounded border border-ink-600 text-slate-300 disabled:opacity-30
                       hover:border-neon-cyan/50 transition-all active:scale-95">←</button>
          <span className="text-slate-500 tabular-nums px-1">{idx + 1}/{items.length}</span>
          <button onClick={() => setI(Math.min(items.length - 1, idx + 1))}
            disabled={idx >= items.length - 1}
            className="px-2 py-1 rounded border border-ink-600 text-slate-300 disabled:opacity-30
                       hover:border-neon-cyan/50 transition-all active:scale-95">→</button>
        </div>
      </div>

      {item && m && (
        <div className="p-3 space-y-2">
          <div className="flex items-center gap-3">
            <CompanyLogo bank={item.bank} size={34} rounded={9} />
            <div className="min-w-0 flex-1">
              <div className="font-medium text-slate-100 truncate">{item.contact_name}</div>
              <div className="text-xs text-slate-400 truncate">
                {[item.role_title, item.bank].filter(Boolean).join(' · ')}
              </div>
            </div>
            <div className="text-[10px] uppercase tracking-wider font-mono text-slate-500 text-right">
              <div>{m.channel}</div>
              <div>{MESSAGE_KIND_LABEL[m.kind]}</div>
            </div>
          </div>

          {isEmail && (
            <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject"
              className="w-full bg-ink-900 border border-ink-700 rounded px-2 py-1.5 text-sm
                         text-slate-200 focus:outline-none focus:border-neon-cyan/50" />
          )}
          <textarea value={body} onChange={e => setBody(e.target.value)}
            rows={Math.min(12, body.split('\n').length + 2)}
            className={`w-full bg-ink-900 border rounded px-2 py-1.5 text-sm text-slate-200
                        leading-relaxed focus:outline-none resize-y ${
              over ? 'border-neon-rose/60' : 'border-ink-700 focus:border-neon-cyan/50'}`} />
          <div className="flex items-center gap-2 text-[10px] font-mono">
            <span className={over ? 'text-neon-rose' : 'text-slate-600'}>
              {body.length}{m.channel === 'linkedin' && m.kind === 'connection_note'
                ? `/${CONNECTION_NOTE_MAX}` : ' chars'}
            </span>
            {dirty && <span className="text-slate-500">unsaved</span>}
          </div>

          <p className="text-[11px] text-slate-500">
            {isEmail
              ? 'Gmail opens with the address, subject and body already filled in. Review, then send.'
              : m.kind === 'connection_note'
                ? 'On their profile: Connect → Add a note → paste (Ctrl+V) → Send.'
                : 'On their profile: Message → paste (Ctrl+V) → Send. The text is already on your clipboard.'}
          </p>
          {copyFailed && (
            <p className="text-[11px] text-neon-rose">
              Could not reach the clipboard, so nothing was copied. Select the text
              above and copy it by hand (Ctrl+A then Ctrl+C inside the box).
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
                <button onClick={() => open(l => l.gmail)} disabled={!m.send.gmail}
                  className="px-2.5 py-1 rounded border border-neon-cyan/50 text-neon-cyan text-xs
                             hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
                  Open in Gmail
                </button>
                <button onClick={() => open(l => l.mailto)} disabled={!m.send.mailto}
                  className="px-2.5 py-1 rounded border border-ink-600 text-slate-300 text-xs
                             hover:border-neon-cyan/50 disabled:opacity-40 transition-all active:scale-95">
                  Mail app
                </button>
              </>
            ) : (
              <button onClick={openComposer} disabled={!m.send.linkedin}
                className="px-2.5 py-1 rounded border border-neon-cyan/50 text-neon-cyan text-xs
                           hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
                Copy + open LinkedIn
              </button>
            )}
            <button onClick={advance} disabled={markSent.isPending}
              className="ml-auto px-3 py-1.5 rounded-lg border border-neon-green/50 text-neon-green
                         text-xs hover:bg-ink-800 disabled:opacity-40 transition-all active:scale-95">
              {markSent.isPending ? 'Saving…' : 'Sent → next'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="px-1 py-px rounded border border-ink-600 bg-ink-800 text-slate-400">
      {children}
    </kbd>
  )
}
