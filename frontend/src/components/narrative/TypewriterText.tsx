/**
 * Typewriter text effect component
 */
import React, { useState, useEffect, useRef } from 'react';

interface TypewriterTextProps {
  text: string;
  speed?: number; // ms per character
  onComplete?: () => void;
  className?: string;
  skipAnimation?: boolean;
}

export const TypewriterText: React.FC<TypewriterTextProps> = ({
  text,
  speed = 20,
  onComplete,
  className = '',
  skipAnimation = false,
}) => {
  const [displayedText, setDisplayedText] = useState('');
  const [isComplete, setIsComplete] = useState(skipAnimation);
  const indexRef = useRef(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (skipAnimation) {
      setDisplayedText(text);
      setIsComplete(true);
      onComplete?.();
      return;
    }

    setDisplayedText('');
    setIsComplete(false);
    indexRef.current = 0;

    const interval = setInterval(() => {
      if (indexRef.current < text.length) {
        setDisplayedText(text.slice(0, indexRef.current + 1));
        indexRef.current++;

        // Scroll to bottom if in a scrollable container
        if (containerRef.current) {
          const parent = containerRef.current.closest('.sketch-scrollbar');
          if (parent) {
            parent.scrollTop = parent.scrollHeight;
          }
        }
      } else {
        clearInterval(interval);
        setIsComplete(true);
        onComplete?.();
      }
    }, speed);

    return () => clearInterval(interval);
  }, [text, speed, skipAnimation, onComplete]);

  // Allow clicking to skip animation
  const handleClick = () => {
    if (!isComplete) {
      setDisplayedText(text);
      setIsComplete(true);
      onComplete?.();
    }
  };

  return (
    <div
      ref={containerRef}
      className={`${className} ${!isComplete ? 'cursor-pointer' : ''}`}
      onClick={handleClick}
    >
      {displayedText}
      {!isComplete && (
        <span className="inline-block w-0.5 h-4 bg-accent-gold ml-0.5 animate-pulse" />
      )}
    </div>
  );
};

export default TypewriterText;
