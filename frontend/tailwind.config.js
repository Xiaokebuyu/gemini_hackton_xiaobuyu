/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 主色调 - 使用 CSS 变量支持主题切换
        'bg-primary': 'var(--color-bg-primary)',
        'bg-secondary': 'var(--color-bg-secondary)',
        'bg-panel': 'var(--color-bg-panel)',
        'bg-card': 'var(--color-bg-card)',

        // 强调色 - 使用 CSS 变量
        'accent-gold': 'var(--color-accent-gold)',
        'accent-cyan': 'var(--color-accent-cyan)',
        'accent-green': 'var(--color-accent-green)',
        'accent-purple': 'var(--color-accent-purple)',
        'accent-red': 'var(--color-accent-red)',

        // 队友职业色
        'role-warrior': '#ff6b6b',
        'role-healer': '#4ecdc4',
        'role-mage': '#a29bfe',
        'role-rogue': '#778beb',

        // 危险等级
        'danger-low': 'var(--sketch-danger-low)',
        'danger-medium': 'var(--sketch-danger-medium)',
        'danger-high': 'var(--sketch-danger-high)',
        'danger-extreme': 'var(--sketch-danger-extreme)',

        // 时间段
        'time-dawn': '#ffeaa7',
        'time-day': '#74b9ff',
        'time-dusk': '#fd79a8',
        'time-night': '#6c5ce7',

        // Sketch theme colors - Leather-bound parchment
        'sketch': {
          'bg': {
            'primary': '#c8b99a',
            'secondary': '#b8a888',
            'panel': '#ede5d3',
            'input': '#f5eee0',
            'glass': 'rgba(237, 229, 211, 0.92)',
            'card': '#f2ead8',
          },
          'ink': {
            'primary': '#1a1008',
            'secondary': '#3d2b1f',
            'muted': '#6b5a48',
            'faint': '#9a8b7a',
          },
          'accent': {
            'red': '#a03030',
            'blue': '#2a4a7a',
            'green': '#2d6a30',
            'gold': '#b8860b',
            'purple': '#5a3a7a',
            'cyan': '#2a7a8a',
            // Hover states
            'gold-hover': '#d4a020',
            'cyan-hover': '#1a6a7a',
            'green-hover': '#1d5a20',
            'red-hover': '#902020',
          },
          'border': {
            'dark': '#3d2b1f',
            'medium': '#6b5a48',
            'accent': '#b8860b',
            'subtle': 'rgba(61, 43, 31, 0.15)',
          },
          'bubble': {
            'gm': '#fce8b8',
            'npc': '#c8ddef',
            'player': '#c8e6c8',
            'teammate': '#d0edc8',
            'system': '#e8e8e5',
            'combat': '#f5c4c0',
          },
          'danger': {
            'low': '#7eb87e',
            'medium': '#d4a843',
            'high': '#a03030',
            'extreme': '#a03a3a',
          },
          'time': {
            'dawn': '#e8c87a',
            'day': '#7aa8c9',
            'dusk': '#c98a8a',
            'night': '#6b5b8a',
          },
        },
      },
      fontFamily: {
        'fantasy': ['Cinzel', 'Georgia', 'serif'],
        'body': ['Inter', 'system-ui', 'sans-serif'],
        'handwritten': ['Caveat', 'Ma Shan Zheng', 'cursive'],
        'sketch-title': ['Cinzel', 'Ma Shan Zheng', 'Georgia', 'serif'],
        'sketch-body': ['Inter', 'Ma Shan Zheng', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'fantasy': '0 0 20px rgba(255, 215, 0, 0.3)',
        'panel': '0 4px 20px rgba(0, 0, 0, 0.5)',
        'glow-gold': '0 0 10px rgba(255, 215, 0, 0.5)',
        'glow-cyan': '0 0 10px rgba(0, 217, 255, 0.5)',
        'glow-red': '0 0 10px rgba(255, 71, 87, 0.5)',
        // Parchment shadows - deeper
        'parchment-sm': '0 1px 4px rgba(42, 28, 15, 0.2)',
        'parchment-md': '0 4px 12px rgba(42, 28, 15, 0.25), 0 1px 3px rgba(42, 28, 15, 0.15)',
        'parchment-lg': '0 8px 24px rgba(42, 28, 15, 0.3), 0 2px 6px rgba(42, 28, 15, 0.18)',
        'parchment-glow-gold': '0 0 15px rgba(184, 134, 11, 0.4)',
      },
      backdropBlur: {
        'glass': '8px',
      },
      animation: {
        'typewriter': 'typewriter 0.05s steps(1) forwards',
        'pulse-glow': 'pulse-glow 2s ease-in-out infinite',
        'fade-in': 'fadeIn 0.3s ease-out forwards',
        'slide-up': 'slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'slide-in-right': 'slideInRight 0.3s ease-out forwards',
        'dice-roll': 'diceRoll 0.5s ease-out forwards',
        'pulse-ring': 'pulseRing 1.5s ease-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(20px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 5px rgba(255, 215, 0, 0.3)' },
          '50%': { boxShadow: '0 0 20px rgba(255, 215, 0, 0.6)' },
        },
        diceRoll: {
          '0%': { transform: 'rotate(0deg) scale(1)' },
          '25%': { transform: 'rotate(90deg) scale(1.2)' },
          '50%': { transform: 'rotate(180deg) scale(1)' },
          '75%': { transform: 'rotate(270deg) scale(1.2)' },
          '100%': { transform: 'rotate(360deg) scale(1)' },
        },
        pulseRing: {
          '0%': { boxShadow: '0 0 0 0 rgba(201, 162, 39, 0.4)' },
          '70%': { boxShadow: '0 0 0 10px rgba(201, 162, 39, 0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(201, 162, 39, 0)' },
        },
      },
    },
  },
  plugins: [],
}
