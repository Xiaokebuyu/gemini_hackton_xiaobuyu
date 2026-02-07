/**
 * Button component - Golden theme
 */
import React from 'react';
import { motion } from 'framer-motion';

interface ButtonProps {
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
    bg-g-gold text-white
    border-g-gold-dark
    hover:bg-g-gold-dark
    shadow-g-sm hover:shadow-g-gold
  `,
  secondary: `
    bg-g-bg-surface text-g-text-primary
    border-g-border-strong
    hover:bg-g-bg-hover hover:shadow-g-sm
  `,
  danger: `
    bg-g-red text-white
    border-g-red
    hover:brightness-110
  `,
  ghost: `
    bg-transparent text-g-text-secondary
    border-transparent
    hover:bg-g-bg-hover hover:text-g-text-primary
  `,
};

const sizeStyles = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-base',
  lg: 'px-6 py-3 text-lg',
};

export const Button: React.FC<ButtonProps> = ({
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
        border
        rounded-lg
        transition-all duration-200
        disabled:opacity-50 disabled:cursor-not-allowed
        ${variantStyles[variant]}
        ${sizeStyles[size]}
        ${className}
      `}
      whileHover={disabled ? {} : { scale: 1.01, y: -1 }}
      whileTap={disabled ? {} : { scale: 0.98 }}
    >
      {children}
    </motion.button>
  );
};

export default Button;
