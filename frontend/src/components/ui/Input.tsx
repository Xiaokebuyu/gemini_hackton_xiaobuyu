/**
 * Input component - Golden theme
 */
import React, { forwardRef } from 'react';

interface InputProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  className?: string;
}

export const Input = forwardRef<HTMLTextAreaElement, InputProps>(
  ({ className = '', ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={`
          w-full
          px-4 py-3
          font-body text-base
          bg-g-bg-input
          text-g-text-primary
          placeholder:text-g-text-muted
          placeholder:italic
          border border-g-border-strong
          rounded-lg
          focus:border-g-border-focus
          focus:ring-2 focus:ring-g-gold/20
          focus:shadow-g-gold
          focus:outline-none
          resize-none
          transition-all duration-200
          ${className}
        `}
        {...props}
      />
    );
  }
);

Input.displayName = 'Input';

export default Input;
