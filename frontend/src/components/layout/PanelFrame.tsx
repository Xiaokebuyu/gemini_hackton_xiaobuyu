/**
 * Hand-drawn style panel frame for Sketch theme
 */
import React from 'react';
import { motion } from 'framer-motion';

interface PanelFrameProps {
  children: React.ReactNode;
  className?: string;
  variant?: 'default' | 'gold' | 'cyan' | 'combat';
  showCorners?: boolean;
  animate?: boolean;
  title?: string;
}

export const PanelFrame: React.FC<PanelFrameProps> = ({
  children,
  className = '',
  variant = 'default',
  showCorners = true,
  animate = false,
  title,
}) => {
  const variantStyles = {
    default: 'border-sketch-ink-secondary',
    gold: 'border-sketch-accent-gold',
    cyan: 'border-sketch-accent-cyan',
    combat: 'border-sketch-accent-red',
  };

  const containerClass = `
    relative
    bg-sketch-bg-panel
    border-2 ${variantStyles[variant]}
    overflow-hidden
    ${className}
  `;

  const content = (
    <div
      className={containerClass}
      style={{
        boxShadow: '3px 3px 8px rgba(44, 36, 22, 0.15)',
        borderRadius: '4px',
      }}
    >
      {/* Title */}
      {title && (
        <div
          className="
            absolute -top-3 left-4
            px-3 py-0.5
            bg-sketch-bg-panel
            font-sketch-title text-lg
            text-sketch-ink-primary
          "
        >
          {title}
        </div>
      )}

      {/* Corner decorations */}
      {showCorners && (
        <>
          <CornerDecoration position="top-left" variant={variant} />
          <CornerDecoration position="bottom-right" variant={variant} />
        </>
      )}

      {/* Content */}
      <div className={`relative z-10 ${title ? 'mt-2' : ''}`}>{children}</div>
    </div>
  );

  if (animate) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {content}
      </motion.div>
    );
  }

  return content;
};

interface CornerDecorationProps {
  position: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
  variant: 'default' | 'gold' | 'cyan' | 'combat';
}

const CornerDecoration: React.FC<CornerDecorationProps> = ({
  position,
  variant,
}) => {
  const colorMap = {
    default: 'border-sketch-accent-gold',
    gold: 'border-sketch-accent-gold',
    cyan: 'border-sketch-accent-cyan',
    combat: 'border-sketch-accent-red',
  };

  const positionStyles = {
    'top-left': '-top-1 -left-1 border-t-2 border-l-2',
    'top-right': '-top-1 -right-1 border-t-2 border-r-2',
    'bottom-left': '-bottom-1 -left-1 border-b-2 border-l-2',
    'bottom-right': '-bottom-1 -right-1 border-b-2 border-r-2',
  };

  return (
    <div
      className={`
        absolute
        w-3 h-3
        ${positionStyles[position]}
        ${colorMap[variant]}
        pointer-events-none
      `}
    />
  );
};

export default PanelFrame;
