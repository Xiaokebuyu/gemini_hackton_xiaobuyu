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
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

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

  const effectiveDisplayedText = skipAnimation ? text : displayedText;
  const effectiveComplete = skipAnimation || isComplete;

  const skip = useCallback(() => {
    if (!skipAnimation && !isComplete) {
      setDisplayedText(text);
      setIsComplete(true);
      onCompleteRef.current?.();
    }
  }, [isComplete, skipAnimation, text]);

  return {
    displayedText: effectiveDisplayedText,
    isComplete: effectiveComplete,
    skip,
  };
}

export default useTypewriter;
