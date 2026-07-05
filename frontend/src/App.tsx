import { Link, Route, Routes, useLocation } from 'react-router-dom'
import OffersPage from './pages/OffersPage'
import OfferDetailPage from './pages/OfferDetailPage'
import SettingsPage from './pages/SettingsPage'
import ProfilePage from './pages/ProfilePage'
import NotificationBell from './components/NotificationBell'
import RefreshButton from './components/RefreshButton'
import TailorProgress from './components/TailorProgress'

const NAV = [
  { to: '/', label: 'Offers' },
  { to: '/profile', label: 'Profile' },
  { to: '/settings', label: 'Settings' },
]

export default function App() {
  const loc = useLocation()
  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-20 backdrop-blur-md bg-ink-950/80 border-b border-ink-700">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <Link to="/" className="flex items-center gap-2 group">
              <LogoMark />
              <span className="text-base font-semibold tracking-tight text-slate-100">
                Nizz<span className="text-neon-cyan">Struggles</span>
              </span>
            </Link>
            <nav className="flex gap-1 text-sm">
              {NAV.map(n => {
                const active = loc.pathname === n.to ||
                  (n.to !== '/' && loc.pathname.startsWith(n.to))
                return (
                  <Link key={n.to} to={n.to}
                    className={`px-3 py-1.5 rounded-md transition-colors ${
                      active
                        ? 'text-neon-cyan bg-ink-800 shadow-[inset_0_0_0_1px_rgba(34,211,238,0.35)]'
                        : 'text-slate-400 hover:text-slate-100 hover:bg-ink-800/60'
                    }`}>
                    {n.label}
                  </Link>
                )
              })}
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <TailorProgress />
            <RefreshButton />
            <NotificationBell />
          </div>
        </div>
      </header>
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<OffersPage />} />
          <Route path="/offers/:id" element={<OfferDetailPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
      <footer className="border-t border-ink-700 py-3">
        <div className="max-w-7xl mx-auto px-6 text-[11px] text-slate-500 flex items-center justify-between font-mono">
          <span>nizzstruggles · local · {new Date().getFullYear()}</span>
          <span className="text-slate-600">trading-floor mode</span>
        </div>
      </footer>
    </div>
  )
}

function LogoMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" className="text-neon-cyan group-hover:text-neon-violet transition-colors">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="url(#lg)" strokeWidth="1.6" />
      <path d="M6 16 L10 8 L14 14 L18 6" fill="none" stroke="url(#lg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
