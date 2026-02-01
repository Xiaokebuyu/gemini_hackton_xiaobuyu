/**
 * Hand-drawn style button component
 */
import React from 'react';
import { motion } from 'framer-motion';

interface SketchButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  className?: string;
  title?: string;
  type?: 'button' | 'submit' | 'reset';
}

const variantStyles = {
  primary: `
    bg-sketch-accent-gold text-sketch-ink-primary
    border-sketch-ink-secondary
    hover:brightness-110
  `,
  secondary: `
    bg-sketch-bg-panel text-sketch-ink-primary
    border-sketch-ink-secondary
    hover:bg-sketch-bg-secondary
  `,
  danger: `
    bg-sketch-accent-red text-white
    border-sketch-accent-red
    hover:brightness-110
  `,
  ghost: `
    bg-transparent text-sketch-ink-secondary
    border-sketch-ink-muted
    hover:bg-sketch-bg-secondary hover:text-sketch-ink-primary
  `,
};

const sizeStyles = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-base',
  lg: 'px-6 py-3 text-lg',
};

export const SketchButton: React.FC<SketchButtonProps> = ({
  children,
  onClick,
  variant = 'primary',
  size = 'md',
  disabled = false,
  className = '',
  title,
  type = 'button',
}) => {
  return (
    <motion.button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`
        font-handwritten
        border-2
        rounded-none
        transition-all duration-200
        disabled:opacity-50 disabled:cursor-not-allowed
        ${variantStyles[variant]}
        ${sizeStyles[size]}
        ${className}
      `}
      style={{
        // Irregular clip-path for hand-drawn feel
        clipPath: 'polygon(2% 0%, 98% 2%, 100% 98%, 3% 100%)',
      }}
      whileHover={disabled ? {} : { scale: 1.02, rotate: 0.5 }}
      whileTap={disabled ? {} : { scale: 0.98 }}
    >
      {children}
    </motion.button>
  );
};

export default SketchButton;
