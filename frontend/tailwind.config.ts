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
      },
    },
  },
  plugins: [],
} satisfies Config
