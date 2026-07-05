import { useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api } from '../api'

export default function SettingsPage() {
  const qc = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const [cvFile, setCvFile] = useState<File | null>(null)
  const [clFile, setClFile] = useState<File | null>(null)

  const uploadCV = useMutation({
    mutationFn: () => api.uploadCV(cvFile!),
    onSuccess: () => { setCvFile(null); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })
  const uploadCL = useMutation({
    mutationFn: () => api.uploadCoverLetter(clFile!),
    onSuccess: () => { setClFile(null); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })

  return (
    <div className="max-w-3xl mx-auto px-6 py-6 space-y-4">
      <PageHeader title="Settings" subtitle="Document engine, base CV & cover letter" />

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

function FileRow({ file, setFile, onUpload, uploading }: {
  file: File | null; setFile: (f: File | null) => void; onUpload: () => void; uploading: boolean
}) {
  return (
    <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
      <input type="file" accept=".pdf,.docx"
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
