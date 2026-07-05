/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#070a14',
          900: '#0b1020',
          800: '#0f1730',
          700: '#15203f',
          600: '#1d2a52',
        },
        neon: {
          cyan: '#22d3ee',
          cyanDim: '#0ea5b7',
          violet: '#a855f7',
          violetDim: '#7e22ce',
          green: '#34d399',
          amber: '#fbbf24',
          rose: '#fb7185',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'neon-cyan': '0 0 0 1px rgba(34,211,238,0.4), 0 0 24px -4px rgba(34,211,238,0.45)',
        'neon-violet': '0 0 0 1px rgba(168,85,247,0.4), 0 0 24px -4px rgba(168,85,247,0.45)',
        'glow-soft': '0 8px 32px -12px rgba(0,0,0,0.6)',
      },
      backgroundImage: {
        'grid-fade': 'radial-gradient(circle at 50% -20%, rgba(34,211,238,0.10), transparent 60%), radial-gradient(circle at 80% 20%, rgba(168,85,247,0.10), transparent 50%)',
      },
    },
  },
  plugins: [],
}
