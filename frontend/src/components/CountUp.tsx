import { useEffect, useRef, useState } from 'react'

// Animated number that eases from its previous value to the new one — the little
// bit of motion that makes the stat cards feel live.
export default function CountUp({
  value, duration = 850, format = (n: number) => String(Math.round(n)), className,
}: {
  value: number
  duration?: number
  format?: (n: number) => string
  className?: string
}) {
  const [display, setDisplay] = useState(value)
  const from = useRef(value)
  const raf = useRef(0)

  useEffect(() => {
    const start = performance.now()
    const startVal = from.current
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3) // easeOutCubic
      setDisplay(startVal + (value - startVal) * eased)
      if (p < 1) raf.current = requestAnimationFrame(tick)
      else from.current = value
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [value, duration])

  return <span className={className}>{format(display)}</span>
}
