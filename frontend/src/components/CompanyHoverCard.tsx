import { useRef, useState, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { bankMeta } from '../lib/bankMeta'
import CompanyLogo from './CompanyLogo'

export interface Fact { label: string; value: string; highlight?: boolean }

// Wraps any element; on hover it floats a branded company card (logo, ticker,
// blurb, quick facts) rendered in a portal so it never gets clipped by a card or
// an overflow container.
export default function CompanyHover({
  bank, facts, children, className = '',
}: { bank: string; facts?: Fact[]; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [rect, setRect] = useState<DOMRect | null>(null)

  const open = () => { if (ref.current) setRect(ref.current.getBoundingClientRect()) }
  return (
    <div ref={ref} className={className} onMouseEnter={open} onMouseLeave={() => setRect(null)}>
      {children}
      {rect && <Popover rect={rect} bank={bank} facts={facts} />}
    </div>
  )
}

function Popover({ rect, bank, facts }: { rect: DOMRect; bank: string; facts?: Fact[] }) {
  const m = bankMeta(bank)
  const width = 320
  const left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, 10), window.innerWidth - width - 10)
  const above = rect.top > 280
  const pos = above
    ? { left, top: rect.top - 12, transform: 'translateY(-100%)' }
    : { left, top: rect.bottom + 12 }

  return createPortal(
    <div className="fixed z-50 pointer-events-none animate-hovercard" style={{ width, ...pos }}>
      <div className="relative rounded-xl border border-ink-600 bg-ink-950/95 backdrop-blur-md p-4 overflow-hidden"
        style={{ boxShadow: `0 0 0 1px ${m.color}33, 0 18px 44px -12px rgba(0,0,0,0.8), 0 0 40px -18px ${m.color}` }}>
        <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: `linear-gradient(90deg, transparent, ${m.color}, transparent)` }} />
        <div className="flex items-center gap-3">
          <CompanyLogo bank={bank} size={46} rounded={12} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-100 truncate">{bank}</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                style={{ background: `${m.color}22`, color: m.color }}>{m.symbol}</span>
            </div>
            <div className="text-[11px] text-slate-400 leading-snug mt-0.5">{m.blurb}</div>
          </div>
        </div>
        {facts && facts.length > 0 && (
          <div className="mt-3 pt-3 border-t border-ink-700 grid grid-cols-2 gap-x-4 gap-y-2">
            {facts.map((ft, i) => (
              <div key={i} className={ft.highlight ? 'col-span-2' : ''}>
                <div className="text-[9px] uppercase tracking-wider text-slate-600 font-mono">{ft.label}</div>
                <div className={`truncate ${ft.highlight
                  ? 'text-sm font-semibold text-neon-cyan'
                  : 'text-xs text-slate-200 capitalize'}`}>{ft.value}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
