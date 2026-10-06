// Open a URL in a real browser TAB.
//
// `window.open(url, '_blank', 'noopener')` passes a features string, and Chrome
// can honour that by opening a POPUP WINDOW rather than a tab. LinkedIn's
// message composer is an overlay docked to the bottom-right of the viewport; in
// a short popup window it renders off-screen or not at all, so clicking
// "Message" looks like it does nothing at all.
//
// Clicking a synthesised anchor always yields a normal tab, respects the user's
// own tab preferences, and keeps the security property via rel="noopener".
export function openTab(url: string): void {
  const a = document.createElement('a')
  a.href = url
  a.target = '_blank'
  a.rel = 'noopener noreferrer'
  // Firefox needs the element in the document for a synthetic click to work.
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  a.remove()
}
