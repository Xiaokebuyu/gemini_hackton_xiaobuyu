/**
 * Refined parchment-style panel component
 */
import React from 'react';

interface SketchPanelProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
  decorated?: boolean;
}

export const SketchPanel: React.FC<SketchPanelProps> = ({
  children,
  className = '',
  title,
  decorated = false,
}) => {
  return (
    <div
      className={`
        relative
        bg-sketch-bg-panel
        border-2 border-sketch-ink-secondary
        rounded-xl
        p-4
        shadow-parchment-md
        sketch-inner-glow
        ${decorated ? 'sketch-card-decorated' : ''}
        ${className}
      `}
    >
      {/* Title */}
      {title && (
        <div
          className="
            absolute -top-3 left-4
            px-3 py-0.5
            bg-sketch-bg-panel
            font-fantasy text-lg
            text-sketch-ink-primary
          "
        >
          {title}
        </div>
      )}

      {/* Content */}
      <div className={title ? 'mt-2' : ''}>
        {children}
      </div>

      {/* Corner decorations for decorated variant */}
      {decorated && (
        <>
          <div
            className="
              absolute -top-1 -left-1
              w-6 h-6
              border-t-2 border-l-2
              border-sketch-accent-gold
              opacity-60
              rounded-tl
            "
          />
          <div
            className="
              absolute -bottom-1 -right-1
              w-6 h-6
              border-b-2 border-r-2
              border-sketch-accent-gold
              opacity-60
              rounded-br
            "
          />
        </>
      )}
    </div>
  );
};

export default SketchPanel;
