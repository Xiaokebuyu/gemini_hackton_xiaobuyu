/**
 * Loading spinner component
 */
import React from 'react';

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const sizeClasses = {
  sm: 'w-4 h-4',
  md: 'w-8 h-8',
  lg: 'w-12 h-12',
};

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  size = 'md',
  className = '',
}) => {
  return (
    <div
      className={`
        ${sizeClasses[size]}
        border-2 border-[var(--g-border-default)]
        border-t-g-gold
        rounded-full
        animate-spin
        ${className}
      `}
    />
  );
};

export default LoadingSpinner;
