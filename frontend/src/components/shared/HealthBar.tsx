/**
 * Health/resource bar component
 */
import React from 'react';
import { motion } from 'framer-motion';

interface HealthBarProps {
  current: number;
  max: number;
  variant?: 'health' | 'mana' | 'experience' | 'generic';
  size?: 'sm' | 'md' | 'lg';
  showText?: boolean;
  className?: string;
}

const variantColors = {
  health: {
    high: 'bg-g-danger-low',
    medium: 'bg-g-danger-medium',
    low: 'bg-g-danger-high',
    critical: 'bg-g-danger-extreme',
  },
  mana: {
    high: 'bg-g-purple',
    medium: 'bg-g-purple',
    low: 'bg-g-purple/70',
    critical: 'bg-g-purple/50',
  },
  experience: {
    high: 'bg-g-gold',
    medium: 'bg-g-gold',
    low: 'bg-g-gold/70',
    critical: 'bg-g-gold/50',
  },
  generic: {
    high: 'bg-g-cyan',
    medium: 'bg-g-cyan',
    low: 'bg-g-cyan/70',
    critical: 'bg-g-cyan/50',
  },
};

const sizeClasses = {
  sm: 'h-1.5',
  md: 'h-2.5',
  lg: 'h-4',
};

export const HealthBar: React.FC<HealthBarProps> = ({
  current,
  max,
  variant = 'health',
  size = 'md',
  showText = false,
  className = '',
}) => {
  const percent = Math.max(0, Math.min(100, (current / max) * 100));
  const colors = variantColors[variant];

  // Determine color based on percentage (only for health variant)
  let barColor = colors.high;
  if (variant === 'health') {
    if (percent <= 25) {
      barColor = colors.critical;
    } else if (percent <= 50) {
      barColor = colors.low;
    } else if (percent <= 75) {
      barColor = colors.medium;
    }
  }

  return (
    <div className={`relative ${className}`}>
      {/* Background */}
      <div
        className={`
          w-full
          ${sizeClasses[size]}
          bg-g-bg-sidebar
          rounded-full
          overflow-hidden
          border border-[rgba(61,43,31,0.12)]
        `}
      >
        {/* Fill */}
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${percent}%` }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
          className={`
            h-full
            ${barColor}
            rounded-full
            relative
          `}
        >
          {/* Shine effect */}
          <div
            className="
              absolute inset-0
              bg-gradient-to-b from-white/20 to-transparent
              rounded-full
            "
          />
        </motion.div>
      </div>

      {/* Text overlay (for lg size) */}
      {showText && size === 'lg' && (
        <div
          className="
            absolute inset-0
            flex items-center justify-center
            text-xs font-medium text-white
            drop-shadow-md
          "
        >
          {current}/{max}
        </div>
      )}
    </div>
  );
};

export default HealthBar;
