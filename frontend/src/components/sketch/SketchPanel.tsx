/**
 * Hand-drawn style panel component
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
        p-4
        ${decorated ? 'sketch-card-decorated' : ''}
        ${className}
      `}
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
              w-3 h-3
              border-t-2 border-l-2
              border-sketch-accent-gold
            "
          />
          <div
            className="
              absolute -bottom-1 -right-1
              w-3 h-3
              border-b-2 border-r-2
              border-sketch-accent-gold
            "
          />
        </>
      )}
    </div>
  );
};

export default SketchPanel;
