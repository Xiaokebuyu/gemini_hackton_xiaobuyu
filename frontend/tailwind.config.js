/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Golden theme - using CSS variables
        'g': {
          'bg': {
            'base': 'var(--g-bg-base)',
            'surface': 'var(--g-bg-surface)',
            'surface-alt': 'var(--g-bg-surface-alt)',
            'input': 'var(--g-bg-input)',
            'hover': 'var(--g-bg-hover)',
            'active': 'var(--g-bg-active)',
            'sidebar': 'var(--g-bg-sidebar)',
          },
          'text': {
            'primary': 'var(--g-text-primary)',
            'secondary': 'var(--g-text-secondary)',
            'muted': 'var(--g-text-muted)',
          },
          'gold': 'var(--g-accent-gold)',
          'gold-light': 'var(--g-accent-gold-light)',
          'gold-dark': 'var(--g-accent-gold-dark)',
          'border': {
            'DEFAULT': 'var(--g-border-default)',
            'strong': 'var(--g-border-strong)',
            'subtle': 'var(--g-border-subtle)',
            'focus': 'var(--g-border-focus)',
          },
          'red': 'var(--g-red)',
          'red-bg': 'var(--g-red-bg)',
          'blue': 'var(--g-blue)',
          'blue-bg': 'var(--g-blue-bg)',
          'green': 'var(--g-green)',
          'green-bg': 'var(--g-green-bg)',
          'purple': 'var(--g-purple)',
          'purple-bg': 'var(--g-purple-bg)',
          'cyan': 'var(--g-cyan)',
          'cyan-bg': 'var(--g-cyan-bg)',
          'bubble': {
            'gm-bg': 'var(--g-bubble-gm-bg)',
            'gm-border': 'var(--g-bubble-gm-border)',
            'npc-bg': 'var(--g-bubble-npc-bg)',
            'npc-border': 'var(--g-bubble-npc-border)',
            'player-bg': 'var(--g-bubble-player-bg)',
            'player-border': 'var(--g-bubble-player-border)',
            'teammate-bg': 'var(--g-bubble-teammate-bg)',
            'teammate-border': 'var(--g-bubble-teammate-border)',
            'system-bg': 'var(--g-bubble-system-bg)',
            'system-border': 'var(--g-bubble-system-border)',
            'combat-bg': 'var(--g-bubble-combat-bg)',
            'combat-border': 'var(--g-bubble-combat-border)',
          },
          'danger': {
            'low': 'var(--g-danger-low)',
            'medium': 'var(--g-danger-medium)',
            'high': 'var(--g-danger-high)',
            'extreme': 'var(--g-danger-extreme)',
          },
          'time': {
            'dawn': 'var(--g-time-dawn)',
            'day': 'var(--g-time-day)',
            'dusk': 'var(--g-time-dusk)',
            'night': 'var(--g-time-night)',
          },
          'role': {
            'warrior': 'var(--g-role-warrior)',
            'healer': 'var(--g-role-healer)',
            'mage': 'var(--g-role-mage)',
            'rogue': 'var(--g-role-rogue)',
            'support': 'var(--g-role-support)',
            'scout': 'var(--g-role-scout)',
            'scholar': 'var(--g-role-scholar)',
          },
        },

      },
      fontFamily: {
        'heading': ['Cinzel', 'Ma Shan Zheng', 'Georgia', 'serif'],
        'body': ['Inter', 'Ma Shan Zheng', 'system-ui', 'sans-serif'],
        'mono': ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        'g-sm': 'var(--g-shadow-sm)',
        'g-md': 'var(--g-shadow-md)',
        'g-lg': 'var(--g-shadow-lg)',
        'g-gold': 'var(--g-shadow-gold)',
        'glow-red': '0 0 10px rgba(217, 79, 79, 0.3)',
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
          '0%, 100%': { boxShadow: '0 0 5px rgba(196, 154, 42, 0.2)' },
          '50%': { boxShadow: '0 0 20px rgba(196, 154, 42, 0.4)' },
        },
        diceRoll: {
          '0%': { transform: 'rotate(0deg) scale(1)' },
          '25%': { transform: 'rotate(90deg) scale(1.2)' },
          '50%': { transform: 'rotate(180deg) scale(1)' },
          '75%': { transform: 'rotate(270deg) scale(1.2)' },
          '100%': { transform: 'rotate(360deg) scale(1)' },
        },
        pulseRing: {
          '0%': { boxShadow: '0 0 0 0 rgba(196, 154, 42, 0.3)' },
          '70%': { boxShadow: '0 0 0 10px rgba(196, 154, 42, 0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(196, 154, 42, 0)' },
        },
      },
    },
  },
  plugins: [],
}
