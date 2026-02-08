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
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  const effectiveDisplayedText = skipAnimation ? text : displayedText;
  const effectiveComplete = skipAnimation || isComplete;

  useEffect(() => {
    if (skipAnimation) {
      onCompleteRef.current?.();
      return;
    }

    indexRef.current = 0;
    const resetTimer = setTimeout(() => {
      setDisplayedText('');
      setIsComplete(false);
    }, 0);

    const interval = setInterval(() => {
      if (indexRef.current < text.length) {
        setDisplayedText(text.slice(0, indexRef.current + 1));
        indexRef.current++;

        // Scroll to bottom if in a scrollable container
        if (containerRef.current) {
          const parent = containerRef.current.closest('.g-scrollbar');
          if (parent) {
            parent.scrollTop = parent.scrollHeight;
          }
        }
      } else {
        clearInterval(interval);
        setIsComplete(true);
        onCompleteRef.current?.();
      }
    }, speed);

    return () => {
      clearTimeout(resetTimer);
      clearInterval(interval);
    };
  }, [text, speed, skipAnimation]);

  // Allow clicking to skip animation
  const handleClick = () => {
    if (!skipAnimation && !isComplete) {
      setDisplayedText(text);
      setIsComplete(true);
      onCompleteRef.current?.();
    }
  };

  return (
    <div
      ref={containerRef}
      className={`${className} ${!effectiveComplete ? 'cursor-pointer' : ''}`}
      onClick={handleClick}
    >
      {effectiveDisplayedText}
      {!effectiveComplete && (
        <span className="inline-block w-0.5 h-4 bg-g-gold ml-0.5 animate-pulse" />
      )}
    </div>
  );
};

export default TypewriterText;
