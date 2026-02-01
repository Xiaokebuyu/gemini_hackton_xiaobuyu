/**
 * Decorative fantasy border component
 */
import React from 'react';

interface FantasyBorderProps {
  children: React.ReactNode;
  className?: string;
  variant?: 'gold' | 'cyan' | 'red' | 'purple';
  glow?: boolean;
}

const variantStyles = {
  gold: {
    border: 'border-accent-gold',
    shadow: 'shadow-glow-gold',
    gradient: 'from-accent-gold/20',
  },
  cyan: {
    border: 'border-accent-cyan',
    shadow: 'shadow-glow-cyan',
    gradient: 'from-accent-cyan/20',
  },
  red: {
    border: 'border-accent-red',
    shadow: 'shadow-glow-red',
    gradient: 'from-accent-red/20',
  },
  purple: {
    border: 'border-accent-purple',
    shadow: '',
    gradient: 'from-accent-purple/20',
  },
};

export const FantasyBorder: React.FC<FantasyBorderProps> = ({
  children,
  className = '',
  variant = 'gold',
  glow = false,
}) => {
  const styles = variantStyles[variant];

  return (
    <div
      className={`
        relative
        border-2 ${styles.border}
        rounded-lg
        bg-gradient-to-br ${styles.gradient} to-transparent
        ${glow ? styles.shadow : ''}
        ${className}
      `}
    >
      {/* Corner decorations */}
      <svg
        className="absolute -top-1 -left-1 w-4 h-4"
        viewBox="0 0 16 16"
        fill="none"
      >
        <path
          d="M0 16V0H16"
          stroke="currentColor"
          strokeWidth="2"
          className={styles.border.replace('border-', 'text-')}
        />
      </svg>
      <svg
        className="absolute -top-1 -right-1 w-4 h-4"
        viewBox="0 0 16 16"
        fill="none"
      >
        <path
          d="M16 16V0H0"
          stroke="currentColor"
          strokeWidth="2"
          className={styles.border.replace('border-', 'text-')}
        />
      </svg>
      <svg
        className="absolute -bottom-1 -left-1 w-4 h-4"
        viewBox="0 0 16 16"
        fill="none"
      >
        <path
          d="M0 0V16H16"
          stroke="currentColor"
          strokeWidth="2"
          className={styles.border.replace('border-', 'text-')}
        />
      </svg>
      <svg
        className="absolute -bottom-1 -right-1 w-4 h-4"
        viewBox="0 0 16 16"
        fill="none"
      >
        <path
          d="M16 0V16H0"
          stroke="currentColor"
          strokeWidth="2"
          className={styles.border.replace('border-', 'text-')}
        />
      </svg>

      {/* Content */}
      <div className="relative z-10">{children}</div>
    </div>
  );
};

export default FantasyBorder;
