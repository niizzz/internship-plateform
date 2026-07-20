import { ReactNode, useMemo } from 'react'

/** Rich renderer for offer descriptions.
 *
 *  Scrapers store descriptions as "markdown-lite": `## ` heading lines,
 *  `**bold**` spans and `• ` bullet lines (see backend html_to_text).
 *  Legacy rows are plain text, so a few conservative heuristics also promote
 *  ALL-CAPS lines / short "Something:" lines to headings.
 */

type Block =
  | { kind: 'heading'; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'bullets'; items: string[] }
  | { kind: 'numbered'; items: { n: string; text: string }[] }

const BULLET_RE = /^[•·▪◦‣►▸*]\s+|^[-–—]\s+|^o\s+/
const NUMBERED_RE = /^(\d{1,2})[.)]\s+/

function stripMarkers(s: string): string {
  return s.replace(/\*\*/g, '').trim()
}

function isHeuristicHeading(line: string): boolean {
  const t = stripMarkers(line)
  if (!t || t.length > 64) return false
  if (BULLET_RE.test(t) || NUMBERED_RE.test(t)) return false
  if (/[:：]$/.test(t)) return true
  const letters = t.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, '')
  if (letters.length >= 3 && letters === letters.toUpperCase()) return true
  // A short fully-bold line that isn't a sentence is a section title.
  if (/^\*\*[^*]+\*\*$/.test(line) && !/[.!?]$/.test(t)) return true
  return false
}

function parse(text: string): Block[] {
  const blocks: Block[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const last = blocks[blocks.length - 1]

    if (line.startsWith('## ')) {
      const t = stripMarkers(line.slice(3)).replace(/[:：]\s*$/, '')
      if (t) blocks.push({ kind: 'heading', text: t })
    } else if (BULLET_RE.test(line)) {
      const item = line.replace(BULLET_RE, '')
      if (last?.kind === 'bullets') last.items.push(item)
      else blocks.push({ kind: 'bullets', items: [item] })
    } else if (NUMBERED_RE.test(line)) {
      const n = line.match(NUMBERED_RE)![1]
      const item = { n, text: line.replace(NUMBERED_RE, '') }
      if (last?.kind === 'numbered') last.items.push(item)
      else blocks.push({ kind: 'numbered', items: [item] })
    } else if (isHeuristicHeading(line)) {
      blocks.push({ kind: 'heading', text: stripMarkers(line).replace(/[:：]\s*$/, '') })
    } else {
      blocks.push({ kind: 'para', text: line })
    }
  }
  return blocks
}

/** Render `**bold**` spans; tolerant of unbalanced markers. */
function inline(text: string): ReactNode {
  const parts = text.split('**')
  if (parts.length === 1) return text
  if (parts.length % 2 === 0) {
    // Unbalanced — glue the trailing piece back together literally.
    const tail = parts.pop()!
    parts[parts.length - 1] += '**' + tail
  }
  return parts.map((p, i) =>
    i % 2 === 1 ? <strong key={i} className="font-semibold text-slate-100">{p}</strong> : p
  )
}

export default function OfferDescription({ text }: { text: string }) {
  const blocks = useMemo(() => parse(text), [text])

  return (
    <div className="text-sm text-slate-300 leading-relaxed space-y-2.5">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case 'heading':
            return (
              <h3 key={i}
                className={`${i === 0 ? 'mt-0' : 'mt-5'} mb-1.5 flex items-center gap-2 text-slate-100 font-semibold text-xs uppercase tracking-[0.15em]`}>
                <span className="w-1 h-3.5 rounded-full bg-gradient-to-b from-neon-cyan to-neon-violet shrink-0" />
                {b.text}
              </h3>
            )
          case 'bullets':
            return (
              <ul key={i} className="space-y-1.5">
                {b.items.map((it, j) => (
                  <li key={j} className="flex gap-2.5">
                    <span className="text-neon-cyan/80 mt-px shrink-0 text-xs leading-relaxed">▸</span>
                    <span>{inline(it)}</span>
                  </li>
                ))}
              </ul>
            )
          case 'numbered':
            return (
              <ol key={i} className="space-y-1.5">
                {b.items.map((it, j) => (
                  <li key={j} className="flex gap-2.5">
                    <span className="text-neon-cyan/80 font-mono text-xs mt-0.5 shrink-0 w-4 text-right">{it.n}.</span>
                    <span>{inline(it.text)}</span>
                  </li>
                ))}
              </ol>
            )
          case 'para':
            return <p key={i}>{inline(b.text)}</p>
        }
      })}
    </div>
  )
}
