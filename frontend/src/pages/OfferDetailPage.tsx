import { useParams, Link } from 'react-router-dom'
import { useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api, ApplicationStatus, STATUS_LABEL, TailorResult, DocSource, CATEGORY_COLOR } from '../api'
import { postedInfo } from '../lib/format'
import StatusBadge from '../components/StatusBadge'
import OfferDescription from '../components/OfferDescription'

const STATUSES: ApplicationStatus[] = ['not_applied', 'applied', 'online_assessment', 'interview', 'offer', 'rejected']

export default function OfferDetailPage() {
  const { id } = useParams<{ id: string }>()
  const offerId = Number(id)
  const qc = useQueryClient()
  const { data: offer, isLoading } = useQuery({
    queryKey: ['offer', offerId],
    queryFn: () => api.getOffer(offerId),
    enabled: Number.isFinite(offerId),
  })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const { data: profile } = useQuery({ queryKey: ['profile'], queryFn: api.getProfile })

  const [notes, setNotes] = useState('')
  const [lastTailor, setLastTailor] = useState<TailorResult | null>(null)
  const [cvChoice, setCvChoice] = useState<DocSource>('tailored')
  const [clChoice, setClChoice] = useState<DocSource>('tailored')
  const [assistMsg, setAssistMsg] = useState<string | null>(null)

  const updateStatus = useMutation({
    mutationFn: (status: ApplicationStatus) => api.updateApplication(offerId, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['offer', offerId] })
      qc.invalidateQueries({ queryKey: ['offers'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
  const saveNotes = useMutation({
    mutationFn: () => api.updateApplication(offerId, { notes }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['offer', offerId] }),
  })
  const tailor = useMutation({
    mutationFn: () => api.tailor(offerId),
    onSuccess: (r) => {
      setLastTailor(r)
      qc.invalidateQueries({ queryKey: ['offer', offerId] })
    },
  })
  const applyAssist = useMutation({
    mutationFn: () => api.applyAssist(offerId, { cv_source: cvChoice, cl_source: clChoice }),
    onSuccess: (r) => {
      setAssistMsg(
        r.tailoring
          ? 'Browser opened — filling your details now. Your tailored CV & cover letter are generating in the background and will attach automatically (~1–2 min).'
          : `Browser opened — filling now. CV ${r.cv_path ? '✓' : '✗ missing'} · CL ${r.cl_path ? '✓' : '✗ missing'}.`
      )
      qc.invalidateQueries({ queryKey: ['offer', offerId] })
      qc.invalidateQueries({ queryKey: ['offers'] })
    },
    onError: (e) => setAssistMsg(`Error: ${(e as Error).message}`),
  })

  if (isLoading) return <div className="p-6 text-slate-400">Loading…</div>
  if (!offer) return <div className="p-6 text-slate-400">Offer not found. <Link to="/" className="text-neon-cyan hover:underline">Back</Link></div>

  const catCls = CATEGORY_COLOR[offer.category] ?? CATEGORY_COLOR.markets
  const profileMissing = !profile?.full_name || !profile?.email
  const hasOriginalCv = !!settings?.base_cv_filename
  const hasOriginalCl = !!settings?.base_cover_letter_filename
  const hasTailoredCv = offer.has_tailored_cv
  const hasTailoredCl = offer.has_tailored_cover_letter

  return (
    <div className="max-w-5xl mx-auto px-6 py-6 space-y-4">
      <Link to="/" className="text-xs text-slate-400 hover:text-neon-cyan font-mono uppercase tracking-wider">← back to desk</Link>

      <section className="relative bg-ink-900/80 border border-ink-700 rounded-xl p-5 overflow-hidden">
        <div className="absolute -top-12 -right-12 w-48 h-48 bg-neon-cyan/10 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute -bottom-12 -left-12 w-48 h-48 bg-neon-violet/10 rounded-full blur-3xl pointer-events-none" />
        <div className="relative flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-[0.2em] text-slate-500 font-mono">{offer.bank}</span>
              <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${catCls}`}>{offer.category}</span>
            </div>
            <h1 className="text-xl font-semibold mt-1 text-slate-100">{offer.role_title}</h1>
            <div className="text-sm text-slate-400 mt-1">
              {offer.location} · {offer.program_type.replace(/_/g, ' ')}
              {offer.start_date_raw && <> · <span className="font-mono">{offer.start_date_raw}</span></>}
              {offer.duration && <> · <span className="font-mono">{offer.duration}</span></>}
            </div>
          </div>
          <StatusBadge status={offer.application_status} />
        </div>

        <div className="relative mt-4 flex flex-wrap gap-2">
          <a href={offer.apply_url} target="_blank" rel="noreferrer"
            className="btn-neon px-3 py-1.5 rounded-md bg-neon-cyan text-ink-950 text-sm font-semibold hover:bg-cyan-300">
            Apply on {offer.bank} ↗
          </a>
          {offer.source_url && (
            <a href={offer.source_url} target="_blank" rel="noreferrer"
              className="px-3 py-1.5 rounded-md border border-ink-600 text-sm text-slate-300 hover:border-neon-cyan/40 hover:text-neon-cyan">
              Source page
            </a>
          )}
        </div>

        <div className="relative mt-4 grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div className="rounded-lg border border-neon-cyan/40 bg-neon-cyan/10 px-3 py-2">
            <div className="text-[9px] uppercase tracking-[0.18em] text-neon-cyan/70 font-mono">Start date</div>
            <div className="text-base font-semibold text-neon-cyan mt-0.5 leading-tight">{offer.start_date_raw || 'See posting'}</div>
          </div>
          <KeyFact label="Duration" value={offer.duration || '—'} />
          <KeyFact label="Program" value={offer.program_type.replace(/_/g, ' ')} />
          {(() => { const p = postedInfo(offer.posted_at, offer.first_seen_at)
            return <KeyFact label={p.label} value={p.value} /> })()}
        </div>

        {offer.description && (
          <div className="relative mt-4 border-t border-ink-700 pt-4">
            <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500 font-mono mb-3">Job description</div>
            <OfferDescription text={offer.description} />
          </div>
        )}
      </section>

      <section className="bg-ink-900/80 border border-ink-700 rounded-xl p-5">
        <h2 className="font-semibold text-slate-100 mb-3 text-sm uppercase tracking-wider">Application status</h2>
        <div className="flex flex-wrap gap-2">
          {STATUSES.map(s => (
            <button key={s}
              onClick={() => updateStatus.mutate(s)}
              className={`px-3 py-1 rounded text-xs font-medium border transition-all ${
                offer.application_status === s
                  ? 'bg-neon-cyan text-ink-950 border-neon-cyan'
                  : 'border-ink-600 text-slate-300 hover:border-neon-cyan/40 hover:text-neon-cyan'
              }`}>
              {STATUS_LABEL[s]}
            </button>
          ))}
        </div>
        <div className="mt-4">
          <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1 font-mono">Notes</label>
          <textarea value={notes || offer.application_notes || ''} onChange={e => setNotes(e.target.value)}
            rows={3} className="w-full px-3 py-2 bg-ink-950 border border-ink-700 rounded text-sm focus:border-neon-cyan/60" />
          <button onClick={() => saveNotes.mutate()}
            className="mt-2 px-3 py-1 text-xs rounded border border-ink-600 hover:border-neon-cyan/40 hover:text-neon-cyan text-slate-300">
            Save notes
          </button>
        </div>
      </section>

      <section className="bg-ink-900/80 border border-ink-700 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-100 text-sm uppercase tracking-wider">CV &amp; cover letter</h2>
          <span className="text-[10px] font-mono text-slate-500">claude code cli · no api cost</span>
        </div>

        {!settings?.base_cv_filename && (
          <Banner color="amber">Upload your base CV in <Link to="/settings" className="underline text-neon-cyan">Settings</Link> first.</Banner>
        )}

        <button onClick={() => tailor.mutate()}
          disabled={tailor.isPending || !settings?.base_cv_filename}
          className="btn-neon px-3 py-1.5 rounded-md bg-neon-violet/20 border border-neon-violet/40 text-neon-violet text-sm font-semibold hover:bg-neon-violet/30 disabled:opacity-50 disabled:cursor-not-allowed">
          {tailor.isPending ? 'Generating…' : (hasTailoredCv ? 'Regenerate adapted CV + cover letter' : 'Generate adapted CV + cover letter')}
        </button>
        {tailor.error && <div className="text-neon-rose text-sm mt-2">{(tailor.error as Error).message}</div>}

        <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-4">
          <DocChoice
            label="CV"
            choice={cvChoice} setChoice={setCvChoice}
            hasTailored={hasTailoredCv} hasOriginal={hasOriginalCv}
            tailoredHref={api.downloadDocUrl(offer.id, 'cv', 'tailored')}
            originalHref={api.downloadDocUrl(offer.id, 'cv', 'original')}
          />
          <DocChoice
            label="Cover letter"
            choice={clChoice} setChoice={setClChoice}
            hasTailored={hasTailoredCl} hasOriginal={hasOriginalCl}
            tailoredHref={api.downloadDocUrl(offer.id, 'cover_letter', 'tailored')}
            originalHref={api.downloadDocUrl(offer.id, 'cover_letter', 'original')}
          />
        </div>

        {lastTailor?.cover_letter_text && (
          <div className="mt-4">
            <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 font-mono">Generated cover letter preview</div>
            <pre className="text-sm whitespace-pre-wrap bg-ink-950 border border-ink-700 rounded p-3 text-slate-300">
              {lastTailor.cover_letter_text}
            </pre>
          </div>
        )}
      </section>

      <section className="bg-ink-900/80 border border-ink-700 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-100 text-sm uppercase tracking-wider">Assisted apply</h2>
          <span className="text-[10px] font-mono text-slate-500">auto-fills the bank's form · you review &amp; submit</span>
        </div>
        {profileMissing && (
          <Banner color="amber">
            Fill out your <Link to="/profile" className="underline text-neon-cyan">profile</Link> first
            so we can pre-fill the bank's application form.
          </Banner>
        )}
        <div className="text-xs text-slate-400 mb-3">
          Opens <span className="font-mono text-neon-cyan">{offer.apply_url}</span> in a real Chromium window
          that stays logged in across applications. Every page of the flow gets auto-filled from your profile,
          and the documents selected above are uploaded into the form. Nothing is submitted without you —
          review each step, solve any CAPTCHA, and click the bank's Submit button yourself.
        </div>
        <button
          onClick={() => applyAssist.mutate()}
          disabled={applyAssist.isPending}
          className="btn-neon px-3 py-1.5 rounded-md bg-neon-cyan text-ink-950 text-sm font-semibold hover:bg-cyan-300 disabled:opacity-50">
          {applyAssist.isPending ? 'Launching browser…' : 'Apply with pre-fill assist →'}
        </button>
        {assistMsg && <div className="mt-2 text-xs text-slate-300 font-mono">{assistMsg}</div>}
      </section>
    </div>
  )
}

function DocChoice(props: {
  label: string
  choice: DocSource
  setChoice: (s: DocSource) => void
  hasTailored: boolean
  hasOriginal: boolean
  tailoredHref: string
  originalHref: string
}) {
  const opt = (val: DocSource, exists: boolean, hint: string) => (
    <button
      onClick={() => props.setChoice(val)}
      disabled={!exists}
      className={`flex-1 px-3 py-2 rounded-md text-xs font-medium border transition-all text-left ${
        props.choice === val
          ? 'bg-neon-cyan/15 text-neon-cyan border-neon-cyan/50 shadow-neon-cyan'
          : exists
            ? 'border-ink-600 text-slate-300 hover:border-neon-cyan/30'
            : 'border-ink-800 text-slate-600 cursor-not-allowed'
      }`}>
      <div className="uppercase tracking-wider text-[10px] font-mono opacity-80">{val}</div>
      <div className="mt-0.5">{hint}</div>
    </button>
  )
  const href = props.choice === 'tailored' ? props.tailoredHref : props.originalHref
  const exists = props.choice === 'tailored' ? props.hasTailored : props.hasOriginal
  return (
    <div className="bg-ink-950/60 border border-ink-700 rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-mono">{props.label}</div>
      <div className="flex gap-2">
        {opt('tailored', props.hasTailored, 'AI-adapted to this offer')}
        {opt('original', props.hasOriginal, 'Your base file')}
      </div>
      <a
        href={exists ? href : undefined}
        aria-disabled={!exists}
        onClick={(e) => { if (!exists) e.preventDefault() }}
        className={`mt-2 inline-block px-2 py-1 rounded text-[11px] font-mono ${
          exists ? 'text-neon-cyan hover:underline' : 'text-slate-600 cursor-not-allowed'
        }`}>
        ↓ download {props.choice}
      </a>
    </div>
  )
}

function KeyFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-950/50 px-3 py-2">
      <div className="text-[9px] uppercase tracking-[0.18em] text-slate-500 font-mono">{label}</div>
      <div className="text-sm text-slate-200 mt-0.5 leading-tight capitalize truncate">{value}</div>
    </div>
  )
}

function Banner({ color, children }: { color: 'amber'; children: any }) {
  const c = color === 'amber'
    ? 'bg-amber-500/10 border-amber-500/40 text-amber-200'
    : ''
  return <div className={`mb-3 p-3 border rounded text-xs ${c}`}>{children}</div>
}
