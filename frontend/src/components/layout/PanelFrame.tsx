/**
 * Panel frame for golden theme
 */
import React from 'react';
import { motion } from 'framer-motion';

interface PanelFrameProps {
  children: React.ReactNode;
  className?: string;
  variant?: 'default' | 'gold' | 'cyan' | 'combat';
  animate?: boolean;
  title?: string;
}

export const PanelFrame: React.FC<PanelFrameProps> = ({
  children,
  className = '',
  variant = 'default',
  animate = false,
  title,
}) => {
  const variantBar = {
    default: '',
    gold: 'border-t-2 border-t-g-gold',
    cyan: 'border-t-2 border-t-g-cyan',
    combat: 'border-t-2 border-t-g-red',
  };

  const containerClass = `
    relative
    bg-g-bg-surface
    border border-g-border
    ${variantBar[variant]}
    rounded-xl
    shadow-g-sm
    overflow-hidden
    transition-shadow duration-200
    hover:shadow-g-md
    ${className}
  `;

  const content = (
    <div className={containerClass}>
      {/* Title */}
      {title && (
        <div className="text-sm font-heading font-semibold text-g-gold uppercase tracking-wide px-4 pt-3 pb-1">
          {title}
        </div>
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

export default PanelFrame;
