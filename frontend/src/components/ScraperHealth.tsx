import { useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface HealthItem { bank: string; reason: string }

// The ⚠ badge next to Refresh. It used to be a bare count with a native title
// listing bank names, which said *that* something broke but never *why* —
// every diagnosis meant going back to the backend log. Hovering now floats the
// per-bank reason (timeout, bot wall, layout change, zero-yield hold).
export default function ScraperHealth({ failed, suspect }: { failed: HealthItem[]; suspect: HealthItem[] }) {
  const ref = useRef<HTMLSpanElement>(null)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const count = failed.length + suspect.length
  if (count === 0) return null

  const open = () => { if (ref.current) setRect(ref.current.getBoundingClientRect()) }
  return (
    <span
      ref={ref}
      onMouseEnter={open}
      onFocus={open}
      onMouseLeave={() => setRect(null)}
      onBlur={() => setRect(null)}
      tabIndex={0}
      className="px-2 py-1 rounded-md bg-neon-rose/10 border border-neon-rose/40 text-neon-rose text-[10px] font-mono font-semibold cursor-help fx-heartbeat outline-none focus:border-neon-rose"
      style={{ ['--pulse' as any]: 'rgba(251,113,133,0.3)' }}
    >
      ⚠ {count}
      {rect && <Popover rect={rect} failed={failed} suspect={suspect} />}
    </span>
  )
}

function Popover({ rect, failed, suspect }: { rect: DOMRect; failed: HealthItem[]; suspect: HealthItem[] }) {
  const width = 340
  const left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, 10), window.innerWidth - width - 10)
  // The badge lives in the top bar, so the card almost always opens downward;
  // flip it up if there genuinely is no room below.
  const below = window.innerHeight - rect.bottom > 260
  const pos = below
    ? { left, top: rect.bottom + 10 }
    : { left, top: rect.top - 10, transform: 'translateY(-100%)' }

  return createPortal(
    <div className="fixed z-50 pointer-events-none animate-hovercard" style={{ width, ...pos }}>
      <div className="relative rounded-xl border border-ink-600 bg-ink-950/95 backdrop-blur-md p-4 overflow-hidden"
        style={{ boxShadow: '0 0 0 1px rgba(251,113,133,0.2), 0 18px 44px -12px rgba(0,0,0,0.8), 0 0 40px -18px rgba(251,113,133,1)' }}>
        <div className="absolute inset-x-0 top-0 h-[2px]"
          style={{ background: 'linear-gradient(90deg, transparent, rgba(251,113,133,0.9), transparent)' }} />
        <div className="text-[9px] uppercase tracking-wider text-slate-500 font-mono">Last refresh · scraper health</div>

        <Group
          title={`Failed (${failed.length})`}
          hint="No offers came back. Existing listings for these banks were kept, not wiped."
          items={failed}
          color="#fb7185"
        />
        <Group
          title={`Zero-yield hold (${suspect.length})`}
          hint="Scraped fine but returned nothing. Held one refresh before trusting the wipe."
          items={suspect}
          color="#fbbf24"
        />

        <div className="mt-3 pt-2 border-t border-ink-700 text-[10px] text-slate-500 leading-snug">
          Nothing was deleted — hit Refresh again, and if a bank keeps failing its scraper likely needs a fix.
        </div>
      </div>
    </div>,
    document.body,
  )
}

function Group({ title, hint, items, color }: { title: string; hint: string; items: HealthItem[]; color: string }) {
  if (items.length === 0) return null
  return (
    <div className="mt-3">
      <div className="text-[10px] font-semibold font-mono" style={{ color }}>{title}</div>
      <div className="text-[10px] text-slate-500 leading-snug mt-0.5">{hint}</div>
      <div className="mt-1.5 space-y-1.5">
        {items.map((it, i) => (
          <div key={i} className="flex gap-2">
            <span className="mt-[5px] h-1 w-1 rounded-full shrink-0" style={{ background: color }} />
            <div className="min-w-0">
              <div className="text-xs font-semibold text-slate-200">{it.bank}</div>
              <div className="text-[11px] text-slate-400 leading-snug break-words">{it.reason}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
