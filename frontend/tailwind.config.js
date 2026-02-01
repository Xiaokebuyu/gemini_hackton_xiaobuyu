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

        // Sketch theme colors
        'sketch': {
          'bg': {
            'primary': '#f5f0e1',
            'secondary': '#ebe5d5',
            'panel': '#faf7ed',
            'input': '#fffdf5',
          },
          'ink': {
            'primary': '#2c2416',
            'secondary': '#5c4d3a',
            'muted': '#8b7d6b',
            'faint': '#b5a898',
          },
          'accent': {
            'red': '#c75146',
            'blue': '#4a6fa5',
            'green': '#5b8a5f',
            'gold': '#c9a227',
            'purple': '#7b6b8a',
            'cyan': '#5a9aa8',
          },
          'bubble': {
            'gm': '#fff9e6',
            'npc': '#e8f0f8',
            'player': '#e8f5e9',
            'teammate': '#f0f8e8',
            'system': '#f5f5f5',
            'combat': '#fde8e6',
          },
          'danger': {
            'low': '#7eb87e',
            'medium': '#d4a843',
            'high': '#c75146',
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
        'sketch-title': ['Shadows Into Light', 'Ma Shan Zheng', 'cursive'],
        'sketch-body': ['Patrick Hand', 'Ma Shan Zheng', 'cursive'],
      },
      boxShadow: {
        'fantasy': '0 0 20px rgba(255, 215, 0, 0.3)',
        'panel': '0 4px 20px rgba(0, 0, 0, 0.5)',
        'glow-gold': '0 0 10px rgba(255, 215, 0, 0.5)',
        'glow-cyan': '0 0 10px rgba(0, 217, 255, 0.5)',
        'glow-red': '0 0 10px rgba(255, 71, 87, 0.5)',
      },
      animation: {
        'typewriter': 'typewriter 0.05s steps(1) forwards',
        'pulse-glow': 'pulse-glow 2s ease-in-out infinite',
        'fade-in': 'fadeIn 0.3s ease-out forwards',
        'slide-up': 'slideUp 0.3s ease-out forwards',
        'slide-in-right': 'slideInRight 0.3s ease-out forwards',
        'dice-roll': 'diceRoll 0.5s ease-out forwards',
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
      },
    },
  },
  plugins: [],
}
