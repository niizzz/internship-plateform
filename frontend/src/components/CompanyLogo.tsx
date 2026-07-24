import { useState } from 'react'
import { bankMeta, logoSources } from '../lib/bankMeta'

// Company mark: the real logo on a light chip (so any logo reads on the dark
// theme). Tries each logo source in turn (DuckDuckGo → Google favicon), then
// falls back to a brand-colored monogram if none load.
export default function CompanyLogo({
  bank, size = 38, rounded = 11,
}: { bank: string; size?: number; rounded?: number }) {
  const m = bankMeta(bank)
  const sources = logoSources(m.domain)
  const [idx, setIdx] = useState(0)
  const showImg = idx < sources.length

  return (
    <div
      className="relative shrink-0 flex items-center justify-center overflow-hidden"
      style={{
        width: size, height: size, borderRadius: rounded,
        background: showImg ? 'rgba(248,250,252,0.96)' : `linear-gradient(135deg, ${m.color}, ${m.color}22)`,
        boxShadow: `0 0 0 1px ${m.color}55, 0 6px 16px -8px ${m.color}`,
      }}
    >
      {showImg ? (
        <img
          src={sources[idx]} alt={bank} loading="lazy"
          onError={() => setIdx(i => i + 1)}
          style={{ width: size * 0.72, height: size * 0.72, objectFit: 'contain' }}
        />
      ) : (
        <span className="font-bold tracking-tight leading-none" style={{ color: '#fff', fontSize: size * 0.3 }}>
          {m.symbol}
        </span>
      )}
    </div>
  )
}
