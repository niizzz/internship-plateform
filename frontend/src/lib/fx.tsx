import { useCallback, useEffect, useRef, useState } from 'react'

// ---------------------------------------------------------------------------
// Shared: respect the user's reduced-motion preference everywhere.
// ---------------------------------------------------------------------------
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

// ---------------------------------------------------------------------------
// <Particles /> — ambient starfield with constellation links + cursor gravity.
// Fixed, behind all content, pauses when the tab is hidden. ~90 dots max.
// ---------------------------------------------------------------------------
export function Particles() {
  const ref = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    if (prefersReducedMotion()) return
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let W = 0, H = 0, raf = 0, running = true
    const DPR = Math.min(2, window.devicePixelRatio || 1)
    const mouse = { x: -9999, y: -9999 }
    const COLORS = ['#22d3ee', '#a855f7', '#34d399']

    type P = { x: number; y: number; vx: number; vy: number; r: number; c: string; tw: number }
    let pts: P[] = []

    const resize = () => {
      W = window.innerWidth; H = window.innerHeight
      canvas.width = W * DPR; canvas.height = H * DPR
      canvas.style.width = `${W}px`; canvas.style.height = `${H}px`
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0)
      const n = Math.min(90, Math.floor((W * H) / 22000))
      pts = Array.from({ length: n }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.22, vy: (Math.random() - 0.5) * 0.22,
        r: 0.6 + Math.random() * 1.5,
        c: COLORS[(Math.random() * COLORS.length) | 0],
        tw: Math.random() * Math.PI * 2,
      }))
    }

    const step = (t: number) => {
      if (!running) return
      ctx.clearRect(0, 0, W, H)
      for (const p of pts) {
        // Gentle drift + soft cursor attraction inside 160px.
        const dx = mouse.x - p.x, dy = mouse.y - p.y
        const d2 = dx * dx + dy * dy
        if (d2 < 160 * 160 && d2 > 1) {
          const d = Math.sqrt(d2)
          p.vx += (dx / d) * 0.012
          p.vy += (dy / d) * 0.012
        }
        p.vx *= 0.985; p.vy *= 0.985
        p.x += p.vx; p.y += p.vy
        if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20
        if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20
        const glow = 0.35 + 0.3 * Math.sin(t / 900 + p.tw)
        ctx.globalAlpha = glow
        ctx.fillStyle = p.c
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
        ctx.fill()
      }
      // Constellation lines between close pairs (cheap n² on <=90 points).
      ctx.lineWidth = 0.5
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const a = pts[i], b = pts[j]
          const dx = a.x - b.x, dy = a.y - b.y
          const d2 = dx * dx + dy * dy
          if (d2 < 120 * 120) {
            ctx.globalAlpha = 0.05 * (1 - d2 / (120 * 120))
            ctx.strokeStyle = a.c
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
          }
        }
      }
      ctx.globalAlpha = 1
      raf = requestAnimationFrame(step)
    }

    const onMouse = (e: MouseEvent) => { mouse.x = e.clientX; mouse.y = e.clientY }
    const onLeave = () => { mouse.x = -9999; mouse.y = -9999 }
    const onVis = () => {
      running = document.visibilityState === 'visible'
      if (running) raf = requestAnimationFrame(step)
      else cancelAnimationFrame(raf)
    }

    resize()
    raf = requestAnimationFrame(step)
    window.addEventListener('resize', resize)
    window.addEventListener('mousemove', onMouse)
    window.addEventListener('mouseout', onLeave)
    document.addEventListener('visibilitychange', onVis)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      window.removeEventListener('mousemove', onMouse)
      window.removeEventListener('mouseout', onLeave)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  if (prefersReducedMotion()) return null
  return <canvas ref={ref} aria-hidden
    className="fixed inset-0 z-0 pointer-events-none opacity-60" />
}

// ---------------------------------------------------------------------------
// useTilt — 3D card tilt + glare position. Attach the returned handlers and
// style to any element with class `fx-tilt` (CSS supplies perspective/glare).
// ---------------------------------------------------------------------------
export function useTilt(maxDeg = 7) {
  const ref = useRef<HTMLDivElement | null>(null)
  const frame = useRef(0)

  const onMove = useCallback((e: React.MouseEvent) => {
    const el = ref.current
    if (!el || prefersReducedMotion()) return
    const rect = el.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width
    const py = (e.clientY - rect.top) / rect.height
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => {
      el.style.setProperty('--rx', `${(0.5 - py) * maxDeg}deg`)
      el.style.setProperty('--ry', `${(px - 0.5) * maxDeg}deg`)
      el.style.setProperty('--gx', `${px * 100}%`)
      el.style.setProperty('--gy', `${py * 100}%`)
    })
  }, [maxDeg])

  const onLeave = useCallback(() => {
    const el = ref.current
    if (!el) return
    cancelAnimationFrame(frame.current)
    el.style.setProperty('--rx', '0deg')
    el.style.setProperty('--ry', '0deg')
  }, [])

  return { ref, onMouseMove: onMove, onMouseLeave: onLeave }
}

// ---------------------------------------------------------------------------
// burstConfetti — celebratory particle burst at (x, y) in viewport coords.
// Creates a throwaway canvas, animates ~70 shards for 1.4s, removes itself.
// ---------------------------------------------------------------------------
export function burstConfetti(x: number, y: number, colors?: string[]) {
  if (prefersReducedMotion()) return
  const palette = colors ?? ['#22d3ee', '#a855f7', '#34d399', '#fbbf24', '#fb7185']
  const canvas = document.createElement('canvas')
  const DPR = Math.min(2, window.devicePixelRatio || 1)
  canvas.width = window.innerWidth * DPR
  canvas.height = window.innerHeight * DPR
  canvas.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:9999'
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')!
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0)

  const shards = Array.from({ length: 70 }, () => {
    const ang = Math.random() * Math.PI * 2
    const v = 3 + Math.random() * 7
    return {
      x, y,
      vx: Math.cos(ang) * v, vy: Math.sin(ang) * v - 3,
      w: 3 + Math.random() * 5, h: 2 + Math.random() * 3,
      rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
      c: palette[(Math.random() * palette.length) | 0],
    }
  })
  const t0 = performance.now()
  const tick = (t: number) => {
    const age = (t - t0) / 1400
    if (age >= 1) { canvas.remove(); return }
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight)
    ctx.globalAlpha = 1 - age
    for (const s of shards) {
      s.vy += 0.18; s.x += s.vx; s.y += s.vy; s.rot += s.vr
      ctx.save()
      ctx.translate(s.x, s.y)
      ctx.rotate(s.rot)
      ctx.fillStyle = s.c
      ctx.fillRect(-s.w / 2, -s.h / 2, s.w, s.h)
      ctx.restore()
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

// ---------------------------------------------------------------------------
// <LiveClock /> — ticking HH:MM:SS, the heartbeat in the footer.
// ---------------------------------------------------------------------------
export function LiveClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    <span className="tabular-nums">
      {pad(now.getHours())}:{pad(now.getMinutes())}
      <span className="opacity-50">:{pad(now.getSeconds())}</span>
    </span>
  )
}
