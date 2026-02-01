/**
 * Hand-drawn border component using Rough.js
 */
import React, { useEffect, useRef, useState } from 'react';
import rough from 'roughjs';

interface SketchBorderProps {
  children: React.ReactNode;
  className?: string;
  seed?: number;
  strokeColor?: string;
  strokeWidth?: number;
  roughness?: number;
  bowing?: number;
}

export const SketchBorder: React.FC<SketchBorderProps> = ({
  children,
  className = '',
  seed = 1,
  strokeColor = 'var(--sketch-border-color)',
  strokeWidth = 2,
  roughness = 1.5,
  bowing = 2,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!containerRef.current) return;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDimensions({ width, height });
      }
    });

    resizeObserver.observe(containerRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    if (!svgRef.current || dimensions.width === 0 || dimensions.height === 0) return;

    const svg = svgRef.current;
    svg.innerHTML = '';
    const rc = rough.svg(svg);

    // Draw hand-drawn rectangle border
    const rect = rc.rectangle(2, 2, dimensions.width - 4, dimensions.height - 4, {
      stroke: strokeColor,
      strokeWidth,
      roughness,
      bowing,
      seed,
      fill: 'none',
    });

    svg.appendChild(rect);
  }, [dimensions, seed, strokeColor, strokeWidth, roughness, bowing]);

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <svg
        ref={svgRef}
        className="absolute inset-0 w-full h-full pointer-events-none z-0"
        style={{ overflow: 'visible' }}
        preserveAspectRatio="none"
      />
      <div className="relative z-10 p-4">
        {children}
      </div>
    </div>
  );
};

export default SketchBorder;
