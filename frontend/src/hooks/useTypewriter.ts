/**
 * Typewriter effect hook
 */
import { useState, useEffect, useRef, useCallback } from 'react';

interface UseTypewriterOptions {
  speed?: number;
  onComplete?: () => void;
  skipAnimation?: boolean;
}

export function useTypewriter(text: string, options: UseTypewriterOptions = {}) {
  const { speed = 20, onComplete, skipAnimation = false } = options;
  const [displayedText, setDisplayedText] = useState('');
  const [isComplete, setIsComplete] = useState(skipAnimation);
  const indexRef = useRef(0);

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
      } else {
        clearInterval(interval);
        setIsComplete(true);
        onComplete?.();
      }
    }, speed);

    return () => clearInterval(interval);
  }, [text, speed, skipAnimation, onComplete]);

  const skip = useCallback(() => {
    if (!isComplete) {
      setDisplayedText(text);
      setIsComplete(true);
      onComplete?.();
    }
  }, [isComplete, text, onComplete]);

  return {
    displayedText,
    isComplete,
    skip,
  };
}

export default useTypewriter;
