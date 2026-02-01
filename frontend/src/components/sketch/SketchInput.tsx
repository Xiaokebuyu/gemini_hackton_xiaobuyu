/**
 * Hand-drawn style input component
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
          font-handwritten text-lg
          bg-sketch-bg-input
          text-sketch-ink-primary
          placeholder:text-sketch-ink-faint
          placeholder:italic
          border-2 border-sketch-ink-muted
          focus:border-sketch-accent-gold
          focus:outline-none
          resize-none
          transition-colors duration-200
          ${className}
        `}
        style={{
          borderRadius: '4px',
        }}
        {...props}
      />
    );
  }
);

SketchInput.displayName = 'SketchInput';

export default SketchInput;
