/**
 * Refined parchment-style input component
 */
import React, { forwardRef } from 'react';

interface SketchInputProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  className?: string;
}

export const SketchInput = forwardRef<HTMLTextAreaElement, SketchInputProps>(
  ({ className = '', ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={`
          w-full
          px-4 py-3
          font-body text-base
          bg-sketch-bg-input
          text-sketch-ink-primary
          placeholder:text-sketch-ink-faint
          placeholder:italic
          border border-sketch-ink-muted
          rounded-lg
          focus:border-sketch-accent-gold
          focus:shadow-[inset_0_0_0_1px_var(--sketch-accent-gold)]
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

SketchInput.displayName = 'SketchInput';

export default SketchInput;
