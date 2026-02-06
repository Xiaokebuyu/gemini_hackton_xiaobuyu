/**
 * Refined border wrapper component
 * Replaces rough.js SVG with clean CSS borders
 */
import React from 'react';

interface SketchBorderProps {
  children: React.ReactNode;
  className?: string;
}

export const SketchBorder: React.FC<SketchBorderProps> = ({
  children,
  className = '',
}) => {
  return (
    <div
      className={`
        border-2 border-sketch-ink-secondary
        rounded-xl
        shadow-parchment-sm
        ${className}
      `}
    >
      <div className="p-4">
        {children}
      </div>
    </div>
  );
};

export default SketchBorder;
