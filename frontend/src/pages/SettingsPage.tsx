import { useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api, Automation, AutomationUpdate } from '../api'

export default function SettingsPage() {
  const qc = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const [cvFile, setCvFile] = useState<File | null>(null)
  const [clFile, setClFile] = useState<File | null>(null)
  const [extraFile, setExtraFile] = useState<File | null>(null)
  const [extraLabel, setExtraLabel] = useState('')
  const [cvWarning, setCvWarning] = useState<string | null>(null)

  const uploadCV = useMutation({
    mutationFn: () => api.uploadCV(cvFile!),
    onSuccess: (res: any) => {
      setCvFile(null)
      setCvWarning(res?.warning ?? null)
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
  })
  const uploadCL = useMutation({
    mutationFn: () => api.uploadCoverLetter(clFile!),
    onSuccess: () => { setClFile(null); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })
  const uploadExtra = useMutation({
    mutationFn: () => api.uploadExtraDocument(extraFile!, extraLabel),
    onSuccess: () => { setExtraFile(null); setExtraLabel(''); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })
  const deleteExtra = useMutation({
    mutationFn: (index: number) => api.deleteExtraDocument(index),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  return (
    <div className="max-w-3xl mx-auto px-6 py-6 space-y-4">
      <PageHeader title="Settings" subtitle="Automation, document engine, base CV & cover letter" />

      <AutomationCard />

      <Card title="Document engine — Claude Code CLI">
        <div className="text-sm mb-1">
          Status: <span className="text-neon-green font-mono">● uses your Claude Code subscription</span>
        </div>
        <Hint>
          Tailored CVs and cover letters are generated locally through the Claude Code CLI
          (<span className="font-mono">claude -p</span>) — no API key, no per-token billing.
          After each Refresh, documents are generated automatically for every new offer;
          the progress chip in the header shows the queue.
        </Hint>
      </Card>

      <Card title="Base CV">
        <div className="text-sm mb-3">
          Current: {settings?.base_cv_filename
            ? <span className="font-mono text-neon-cyan">{settings.base_cv_filename}</span>
            : <span className="italic text-slate-500">none uploaded</span>}
        </div>
        <FileRow
          file={cvFile} setFile={setCvFile}
          onUpload={() => uploadCV.mutate()}
          uploading={uploadCV.isPending}
        />
        {cvWarning && (
          <div className="mt-3 px-3 py-2 rounded-md bg-neon-amber/10 border border-neon-amber/40 text-neon-amber text-xs animate-risein">
            ⚠ {cvWarning}
          </div>
        )}
        <Hint>PDF or DOCX. PDFs are auto-converted to DOCX so the tailoring engine can edit text.</Hint>
      </Card>

      <Card title="Base cover letter (optional)">
        <div className="text-sm mb-3">
          Current: {settings?.base_cover_letter_filename
            ? <span className="font-mono text-neon-cyan">{settings.base_cover_letter_filename}</span>
            : <span className="italic text-slate-500">none uploaded</span>}
        </div>
        <FileRow
          file={clFile} setFile={setClFile}
          onUpload={() => uploadCL.mutate()}
          uploading={uploadCL.isPending}
        />
        <Hint>Upload your own cover-letter template. You'll be able to attach either this or the AI-generated one on each application.</Hint>
      </Card>

      <Card title="Additional documents">
        {settings?.extra_documents?.length
          ? (
            <ul className="mb-3 space-y-1.5">
              {settings.extra_documents.map((d, i) => (
                <li key={`${d.filename}-${i}`} className="flex items-center justify-between gap-2 text-sm">
                  <span>
                    <span className="text-slate-200">{d.label}</span>{' '}
                    <span className="font-mono text-neon-cyan text-xs">({d.filename})</span>
                  </span>
                  <button onClick={() => deleteExtra.mutate(i)}
                    disabled={deleteExtra.isPending}
                    className="px-2 py-1 rounded text-xs text-rose-300 hover:bg-rose-500/10 disabled:opacity-50">
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )
          : <div className="text-sm mb-3 italic text-slate-500">none uploaded</div>}
        <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center mb-2">
          <input type="text" placeholder="Label (e.g. CFA Level 1 result)"
            value={extraLabel} onChange={e => setExtraLabel(e.target.value)}
            className={inp + ' flex-1'} />
        </div>
        <FileRow
          file={extraFile} setFile={setExtraFile}
          onUpload={() => uploadExtra.mutate()}
          uploading={uploadExtra.isPending}
          accept=".pdf,.docx,.png,.jpg,.jpeg"
        />
        <Hint>
          Certificates, transcripts, test results… During assisted apply these are uploaded
          into "other / additional / supporting document" file inputs on application forms
          (the CV and cover letter keep their own dedicated slots).
        </Hint>
      </Card>
    </div>
  )
}

const inp = "px-3 py-2 bg-ink-950 border border-ink-700 rounded text-sm focus:border-neon-cyan/60"
const btnPrimary = "btn-neon px-3 py-2 rounded-md bg-neon-cyan text-ink-950 text-sm font-semibold hover:bg-cyan-300 disabled:opacity-50"

function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-2">
      <div className="text-[11px] uppercase tracking-[0.2em] text-slate-500 font-mono">Config</div>
      <h1 className="mt-1 text-2xl font-semibold text-slate-100">{title}</h1>
      <div className="text-sm text-slate-400">{subtitle}</div>
    </div>
  )
}

function Card({ title, children }: { title: string; children: any }) {
  return (
    <section className="bg-ink-900/80 border border-ink-700 rounded-xl p-5">
      <h2 className="font-semibold text-slate-100 mb-3 text-sm uppercase tracking-wider">{title}</h2>
      {children}
    </section>
  )
}

function Hint({ children }: { children: any }) {
  return <div className="mt-2 text-xs text-slate-500">{children}</div>
}

function FileRow({ file, setFile, onUpload, uploading, accept = '.pdf,.docx' }: {
  file: File | null; setFile: (f: File | null) => void; onUpload: () => void; uploading: boolean
  accept?: string
}) {
  return (
    <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
      <input type="file" accept={accept}
        onChange={e => setFile(e.target.files?.[0] ?? null)}
        className="text-sm text-slate-300 file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:bg-ink-700 file:text-slate-200 file:cursor-pointer hover:file:bg-ink-600" />
      <button onClick={onUpload}
        disabled={!file || uploading}
        className={btnPrimary}>
        {uploading ? 'Uploading…' : 'Upload'}
      </button>
    </div>
  )
}

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function AutomationCard() {
  const qc = useQueryClient()
  const { data: a } = useQuery({ queryKey: ['automation'], queryFn: api.getAutomation, refetchInterval: 60_000 })
  const [draft, setDraft] = useState<AutomationUpdate>({})
  const [pwd, setPwd] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const save = useMutation({
    mutationFn: (body: AutomationUpdate) => api.updateAutomation(body),
    onSuccess: (res: Automation) => {
      qc.setQueryData(['automation'], res); setDraft({}); setPwd('')
      setMsg({ ok: true, text: 'Saved.' })
    },
    onError: (e: any) => setMsg({ ok: false, text: String(e.message ?? e) }),
  })
  const test = useMutation({
    mutationFn: api.testEmail,
    onSuccess: () => { setMsg({ ok: true, text: `Test email sent to ${a?.notify_email}. Check that inbox (and spam).` }); qc.invalidateQueries({ queryKey: ['automation'] }) },
    onError: (e: any) => {
      const raw = String(e.message ?? e)
      const m = raw.match(/"detail":"(.*)"/)
      setMsg({ ok: false, text: m ? m[1] : raw })
    },
  })

  if (!a) return <Card title="Automation"><div className="text-sm text-slate-500">Loading…</div></Card>
  const v = <K extends keyof AutomationUpdate>(k: K) => (k in draft ? draft[k] : (a as any)[k]) as AutomationUpdate[K]
  const set = (k: keyof AutomationUpdate, val: any) => setDraft(d => ({ ...d, [k]: val }))
  const dirty = Object.keys(draft).length > 0 || pwd !== ''

  return (
    <Card title="Automation — daily refresh & email alerts">
      <label className="flex items-center gap-3 text-sm text-slate-200">
        <input type="checkbox" checked={!!v('auto_refresh')}
          onChange={e => save.mutate({ auto_refresh: e.target.checked })} />
        Refresh offers + events automatically every
        <input type="number" min={1} max={168} value={v('auto_refresh_hours') ?? 24}
          onChange={e => set('auto_refresh_hours', Number(e.target.value))}
          className={inp + ' w-16 py-1'} /> hours
      </label>
      <div className="mt-1 ml-6 text-xs text-slate-500 font-mono">
        last: {fmtWhen(a.last_refresh_at)} · next: {a.auto_refresh ? fmtWhen(a.next_refresh_at) : 'off'}
      </div>
      <Hint>
        Runs while the platform is open (the backend window). If the PC was off, it catches up within ~10 minutes of launching.
      </Hint>

      <div className="mt-5 pt-4 border-t border-ink-700 space-y-3">
        <label className="flex items-center gap-3 text-sm text-slate-200">
          <input type="checkbox" checked={!!v('email_notifications')}
            onChange={e => set('email_notifications', e.target.checked)} />
          Email me when a refresh finds new offers or events
        </label>
        <div className="grid sm:grid-cols-2 gap-2">
          <Field label="Send alerts to">
            <input className={inp} type="email" value={v('notify_email') ?? ''}
              onChange={e => set('notify_email', e.target.value)} placeholder="you@school.edu" />
          </Field>
          <Field label="Send from (Gmail address)">
            <input className={inp} type="email" value={v('smtp_user') ?? ''}
              onChange={e => set('smtp_user', e.target.value)} placeholder="you@gmail.com" />
          </Field>
          <Field label={`Gmail app password ${a.has_smtp_password ? '(saved, type to replace)' : ''}`}>
            <input className={inp} type="password" value={pwd} autoComplete="new-password"
              onChange={e => setPwd(e.target.value)} placeholder={a.has_smtp_password ? '••••••••••••••••' : '16-character app password'} />
          </Field>
          <Field label="SMTP server : port">
            <div className="flex gap-2">
              <input className={inp + ' flex-1'} value={v('smtp_host') ?? ''} onChange={e => set('smtp_host', e.target.value)} />
              <input className={inp + ' w-20'} type="number" value={v('smtp_port') ?? 465} onChange={e => set('smtp_port', Number(e.target.value))} />
            </div>
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className={btnPrimary} disabled={!dirty || save.isPending}
            onClick={() => save.mutate({ ...draft, ...(pwd ? { smtp_password: pwd } : {}) })}>
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          <button disabled={!a.email_status.configured || test.isPending || dirty} onClick={() => test.mutate()}
            title={dirty ? 'Save first' : ''}
            className="px-3 py-2 rounded-md border border-ink-600 text-sm text-slate-200 hover:border-neon-cyan/60 disabled:opacity-40">
            {test.isPending ? 'Sending…' : 'Send test email'}
          </button>
          {a.email_status.at && (
            <span className={`text-xs font-mono ${a.email_status.ok ? 'text-neon-green' : 'text-rose-300'}`}>
              last email {fmtWhen(a.email_status.at)}: {a.email_status.detail}
            </span>
          )}
        </div>
        {msg && <div className={`text-xs ${msg.ok ? 'text-neon-green' : 'text-rose-300'}`}>{msg.text}</div>}
        <Hint>
          One digest per refresh, only for items that are new since you switched this on, and only events with open
          registration in Markets or firm-wide. Gmail needs an <b>App Password</b> (Google Account → Security →
          2-Step Verification → App passwords), not your normal password. It is stored only in the local database.
        </Hint>
      </div>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: any }) {
  return <label className="flex flex-col gap-1 text-xs text-slate-500">{label}{children}</label>
}
