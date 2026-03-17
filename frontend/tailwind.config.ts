import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        warm: {
          950: '#0c0a09',
          900: '#1c1917',
          800: '#292524',
        },
        parchment: {
          50:  '#f5f0e6',
          100: '#e8dfc8',
          200: '#d4c9a8',
          300: '#c4b899',
          400: '#a89a78',
          500: '#8a7e6a',
        },
        gold: {
          300: '#e8c35a',
          400: '#d4a847',
          500: '#b8923a',
          600: '#96762e',
          700: '#6b5420',
          800: '#453614',
          900: '#2a200c',
        },
      },
      fontFamily: {
        display: ['Cinzel', 'Noto Serif SC', 'serif'],
        body:    ['Noto Serif SC', 'Source Han Serif', 'Georgia', 'serif'],
      },
      boxShadow: {
        'fantasy':       '0 2px 12px rgba(0,0,0,0.5), 0 0 1px rgba(180,140,60,0.2)',
        'fantasy-lg':    '0 4px 20px rgba(0,0,0,0.6), 0 0 20px rgba(245,180,60,0.12)',
        'fantasy-glow':  '0 0 12px rgba(245,180,60,0.2), 0 0 4px rgba(245,180,60,0.1)',
        'fantasy-inset': 'inset 0 2px 6px rgba(0,0,0,0.4), inset 0 0 1px rgba(0,0,0,0.5)',
      },
      borderColor: {
        ornate:        'rgba(180, 140, 60, 0.3)',
        'ornate-bright': 'rgba(210, 170, 80, 0.5)',
      },
      animation: {
        'fantasy-glow': 'fantasy-glow 3s ease-in-out infinite',
        'shimmer':      'shimmer 2s linear infinite',
        'fade-in-up':   'fade-in-up 0.3s ease-out both',
        'breathe':      'subtle-breathe 3s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
