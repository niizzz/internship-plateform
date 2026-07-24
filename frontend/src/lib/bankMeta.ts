// Per-bank branding: accent color, logo domain, ticker symbol and a one-line
// blurb. Powers the company logos + hover cards. Keyed by a normalized bank name
// so accents/spacing never break the lookup.

export interface BankMeta {
  symbol: string       // short ticker, e.g. "GS", "JPM"
  color: string        // brand accent (hex) — drives glow + monogram
  domain: string       // for the logo (logo.clearbit.com/<domain>)
  blurb: string        // one-line desk description
}

const M: Record<string, BankMeta> = {
  'Goldman Sachs':        { symbol: 'GS',   color: '#7BA0D9', domain: 'goldmansachs.com',   blurb: 'US bulge-bracket. Global Markets — FICC & Equities flow, structuring.' },
  'JPMorgan':             { symbol: 'JPM',  color: '#3E6FB0', domain: 'jpmorgan.com',       blurb: 'Largest US bank. Markets across rates, credit, equities and FX.' },
  'Morgan Stanley':       { symbol: 'MS',   color: '#3FA7DC', domain: 'morganstanley.com',  blurb: 'US bulge-bracket. Institutional Securities — FID & Equity structuring.' },
  'Citi':                 { symbol: 'C',    color: '#1E77CC', domain: 'citi.com',           blurb: 'Global markets powerhouse. FX, rates, EM and securitized products.' },
  'Bank of America':      { symbol: 'BAC',  color: '#D64860', domain: 'bankofamerica.com',  blurb: 'US bulge-bracket. Global Markets sales & trading.' },
  'UBS':                  { symbol: 'UBS',  color: '#EC5B62', domain: 'ubs.com',            blurb: 'Swiss bank. Global Markets — equity derivatives, FX, rates.' },
  'HSBC':                 { symbol: 'HSBA', color: '#E8515F', domain: 'hsbc.com',           blurb: 'Global markets across EMEA & Asia. Rates, credit, FX.' },
  'BNP Paribas':          { symbol: 'BNP',  color: '#3AAE7C', domain: 'bnpparibas.com',     blurb: 'European leader. Global Markets — rates, credit, equity & prime.' },
  'Deutsche Bank':        { symbol: 'DBK',  color: '#5B8DEF', domain: 'db.com',             blurb: 'German flow leader. Rates, credit, FX and financing.' },
  'Barclays':             { symbol: 'BARC', color: '#37B6E9', domain: 'barclays.com',       blurb: 'UK bank. Markets — macro, credit, equities, securitized.' },
  'Société Générale':     { symbol: 'GLE',  color: '#E5556B', domain: 'societegenerale.com',blurb: 'French leader in equity derivatives & structured products.' },
  'Santander':            { symbol: 'SAN',  color: '#E85361', domain: 'santander.com',      blurb: 'Spanish bank. Rates, FX, credit and structured solutions.' },
  'Crédit Agricole CIB':  { symbol: 'ACA',  color: '#3EA97A', domain: 'ca-cib.com',         blurb: 'French CIB. FIC, structuring, cross-currency and financing.' },
  'Natixis':              { symbol: 'KN',   color: '#A46BC4', domain: 'natixis.groupebpce.com', blurb: 'BPCE CIB. Structured products, equity derivatives, financing.' },
  'Lazard':               { symbol: 'LAZ',  color: '#C4A968', domain: 'lazard.com',         blurb: 'Advisory & asset-management boutique.' },
  'Lazard Frères Gestion':{ symbol: 'LFG',  color: '#B79A58', domain: 'lazardfreresgestion.fr', blurb: "Lazard's French AM arm. Fixed income, cross-asset, structured." },
  'Rothschild & Co':      { symbol: 'ROT',  color: '#4B7BA8', domain: 'rothschildandco.com',blurb: 'Advisory house with wealth & asset management.' },
  'Commerzbank':          { symbol: 'CBK',  color: '#E0B341', domain: 'commerzbank.com',    blurb: 'German bank. Rates, FX, credit and structured.' },
  'Kepler Cheuvreux':     { symbol: 'KECH', color: '#4C8BD0', domain: 'keplercheuvreux.com',blurb: 'European equities broker. Structured products, execution.' },
  'Nomura':               { symbol: 'NMR',  color: '#D65167', domain: 'nomura.com',         blurb: 'Japanese bank. Global Markets & structured solutions.' },
  'BBVA':                 { symbol: 'BBVA', color: '#3D8FD4', domain: 'bbva.com',           blurb: 'Spanish bank. Global Markets, quant and structuring.' },
  'Euronext':             { symbol: 'ENX',  color: '#9B6BE0', domain: 'euronext.com',       blurb: 'Pan-European exchange. Index structuring, trading, quant.' },
  'RBC':                  { symbol: 'RY',   color: '#5A8FE0', domain: 'rbc.com',            blurb: 'Canadian bank. Global Markets & credit sales.' },
  'CMC Markets':          { symbol: 'CMCX', color: '#E8556B', domain: 'cmcmarkets.com',     blurb: 'CFD & spread-betting broker. Trading technology.' },
  'Amundi':               { symbol: 'AMUN', color: '#3F9BDC', domain: 'amundi.com',         blurb: "Europe's largest asset manager. Product structuring." },
  'Maven Securities':     { symbol: 'MAVN', color: '#2ECC9B', domain: 'mavensecurities.com',blurb: 'Prop trading & market making. Options and volatility.' },
  'UniCredit':            { symbol: 'UCG',  color: '#E4002B', domain: 'unicreditgroup.eu',  blurb: 'Pan-European bank. Client Solutions markets desks in Milan & Munich.' },
  'Mizuho':               { symbol: 'MZH',  color: '#2B4B9B', domain: 'mizuhogroup.com',    blurb: 'Japanese bank, London EMEA hub. Securitised products & structured trading.' },
}

const NORM: Record<string, BankMeta> = {}
const norm = (s: string) =>
  (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '')
for (const [k, v] of Object.entries(M)) NORM[norm(k)] = v

export function bankMeta(bank: string): BankMeta {
  const hit = NORM[norm(bank)]
  if (hit) return hit
  // Fallback: initials + a deterministic accent so unknown firms still look intentional.
  const initials = (bank || '?').split(/\s+/).map(w => w[0]).join('').slice(0, 3).toUpperCase()
  const palette = ['#4C8BD0', '#9B6BE0', '#3EA97A', '#E0B341', '#E5556B', '#3FA7DC']
  let h = 0
  for (let i = 0; i < bank.length; i++) h = (h * 31 + bank.charCodeAt(i)) >>> 0
  return { symbol: initials || '—', color: palette[h % palette.length], domain: '', blurb: 'S&T / markets desk.' }
}

// Ordered logo sources tried in <CompanyLogo> (each falls through on error to
// the next, then to a brand monogram). DuckDuckGo has the best-quality icons but
// 404s some domains (e.g. natixis.com, lazardfreresgestion.fr); Google's favicon
// service covers most of the rest. Clearbit's logo API is dead (DNS gone).
export function logoSources(domain: string): string[] {
  if (!domain) return []
  return [
    `https://icons.duckduckgo.com/ip3/${domain}.ico`,
    `https://www.google.com/s2/favicons?domain=${domain}&sz=128`,
  ]
}
