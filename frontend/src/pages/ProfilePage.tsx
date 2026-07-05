import { useEffect, useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api, Profile } from '../api'

const EMPTY: Profile = {}

const SECTIONS: { title: string; fields: Array<[keyof Profile, string, string?]> }[] = [
  {
    title: 'Identity',
    fields: [
      ['full_name', 'Full name'],
      ['email', 'Email'],
      ['phone', 'Phone (with country code)'],
      ['gender', 'Gender'],
      ['nationality', 'Nationality'],
      ['date_of_birth', 'Date of birth (YYYY-MM-DD)'],
    ],
  },
  {
    title: 'Address',
    fields: [
      ['address', 'Street address'],
      ['city', 'City'],
      ['postcode', 'Postcode'],
      ['country', 'Country'],
    ],
  },
  {
    title: 'Links',
    fields: [
      ['linkedin_url', 'LinkedIn URL'],
      ['github_url', 'GitHub URL'],
      ['portfolio_url', 'Portfolio / personal site URL'],
    ],
  },
  {
    title: 'Education',
    fields: [
      ['university', 'University'],
      ['degree', 'Degree / programme'],
      ['graduation_year', 'Expected graduation (year or YYYY-MM)'],
      ['gpa', 'GPA / grade'],
    ],
  },
  {
    title: 'Eligibility & languages',
    fields: [
      ['work_authorization', 'Work authorization (e.g. UK citizen, EU national, Tier 4)'],
      ['languages', 'Languages (e.g. French (native), English (C2))'],
    ],
  },
]

export default function ProfilePage() {
  const qc = useQueryClient()
  const { data: server } = useQuery({ queryKey: ['profile'], queryFn: api.getProfile })
  const [form, setForm] = useState<Profile>(EMPTY)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  useEffect(() => {
    if (server) setForm(server)
  }, [server])

  const save = useMutation({
    mutationFn: () => api.updateProfile(form),
    onSuccess: () => {
      setSavedAt(new Date().toLocaleTimeString())
      qc.invalidateQueries({ queryKey: ['profile'] })
    },
  })

  const set = (k: keyof Profile) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value })

  const completed = Object.values(form).filter(v => typeof v === 'string' && v.trim().length > 0).length
  const total = SECTIONS.flatMap(s => s.fields).length

  return (
    <div className="max-w-4xl mx-auto px-6 py-6 space-y-4">
      <div className="mb-2 flex items-end justify-between flex-wrap gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-slate-500 font-mono">Operator</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Profile</h1>
          <div className="text-sm text-slate-400">
            Used by <span className="text-neon-cyan">Pre-fill assist</span> to auto-populate bank application forms.
          </div>
        </div>
        <div className="text-xs text-slate-400 font-mono">
          <span className="text-neon-cyan tabular-nums">{completed}</span> / {total} fields filled
          {savedAt && <span className="ml-3 text-neon-green">✓ saved {savedAt}</span>}
        </div>
      </div>

      {SECTIONS.map(sec => (
        <section key={sec.title} className="bg-ink-900/80 border border-ink-700 rounded-xl p-5">
          <h2 className="font-semibold text-slate-100 mb-3 text-sm uppercase tracking-wider">{sec.title}</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {sec.fields.map(([key, label]) => (
              <div key={key as string}>
                <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1 font-mono">{label}</label>
                <input
                  value={(form[key] as string) || ''}
                  onChange={set(key)}
                  className="w-full px-3 py-2 bg-ink-950 border border-ink-700 rounded text-sm focus:border-neon-cyan/60"
                />
              </div>
            ))}
          </div>
        </section>
      ))}

      <div className="sticky bottom-4 flex justify-end">
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="btn-neon px-4 py-2 rounded-md bg-neon-cyan text-ink-950 text-sm font-semibold hover:bg-cyan-300 shadow-neon-cyan disabled:opacity-50">
          {save.isPending ? 'Saving…' : 'Save profile'}
        </button>
      </div>
      {save.error && <div className="text-neon-rose text-sm">{(save.error as Error).message}</div>}
    </div>
  )
}
