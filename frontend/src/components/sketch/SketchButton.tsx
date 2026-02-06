/**
 * Refined parchment-style button component
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
    bg-gradient-to-b from-[#d4ad2e] to-[#c9a227] text-sketch-ink-primary
    border-sketch-accent-gold
    hover:shadow-parchment-glow-gold
  `,
  secondary: `
    bg-sketch-bg-panel text-sketch-ink-primary
    border-sketch-ink-muted
    hover:bg-sketch-bg-secondary hover:shadow-parchment-md
  `,
  danger: `
    bg-sketch-accent-red text-white
    border-sketch-accent-red
    hover:brightness-110
  `,
  ghost: `
    bg-transparent text-sketch-ink-secondary
    border-sketch-ink-faint
    hover:bg-sketch-bg-secondary hover:text-sketch-ink-primary
    hover:shadow-parchment-sm
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
        font-body
        border-2
        rounded-lg
        transition-all duration-200
        disabled:opacity-50 disabled:cursor-not-allowed
        ${variantStyles[variant]}
        ${sizeStyles[size]}
        ${className}
      `}
      whileHover={disabled ? {} : { scale: 1.02, y: -1 }}
      whileTap={disabled ? {} : { scale: 0.98 }}
    >
      {children}
    </motion.button>
  );
};

export default SketchButton;
