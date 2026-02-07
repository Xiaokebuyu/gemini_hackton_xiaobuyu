/**
 * Dice roll animation display
 */
import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Dices } from 'lucide-react';
import type { DiceRoll } from '../../types';

interface DiceRollDisplayProps {
  roll: DiceRoll | null;
  onComplete?: () => void;
  className?: string;
}

export const DiceRollDisplay: React.FC<DiceRollDisplayProps> = ({
  roll,
  onComplete,
  className = '',
}) => {
  const [isAnimating, setIsAnimating] = useState(false);
  const [displayValue, setDisplayValue] = useState<number | null>(null);

  useEffect(() => {
    if (roll) {
      setIsAnimating(true);
      setDisplayValue(null);

      // Animate through random numbers
      let count = 0;
      const interval = setInterval(() => {
        const max = parseInt(roll.roll_type.slice(1)) || 20;
        setDisplayValue(Math.floor(Math.random() * max) + 1);
        count++;

        if (count >= 10) {
          clearInterval(interval);
          setDisplayValue(roll.result);
          setIsAnimating(false);

          setTimeout(() => {
            onComplete?.();
          }, 1500);
        }
      }, 50);

      return () => clearInterval(interval);
    }
  }, [roll, onComplete]);

  if (!roll) return null;

  const isCrit = roll.is_critical;
  const isFumble = roll.is_fumble;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, scale: 0.5 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.5 }}
        className={`
          fixed inset-0 z-50
          flex items-center justify-center
          bg-g-bg-base/80 backdrop-blur-sm
          ${className}
        `}
      >
        <motion.div
          animate={isAnimating ? { rotate: [0, 360] } : { rotate: 0 }}
          transition={{
            duration: 0.5,
            repeat: isAnimating ? Infinity : 0,
            ease: 'linear',
          }}
          className={`
            w-32 h-32
            flex flex-col items-center justify-center
            rounded-2xl
            ${
              isCrit
                ? 'bg-g-gold/20 border-4 border-g-gold shadow-g-gold'
                : isFumble
                ? 'bg-g-red/20 border-4 border-g-red shadow-glow-red'
                : 'bg-g-bg-surface border-2 border-[var(--g-border-strong)]'
            }
          `}
        >
          {/* Dice type */}
          <div className="text-2xl mb-1">
            <Dices className="w-8 h-8 text-[var(--g-text-secondary)]" />
          </div>

          {/* Roll value */}
          <div
            className={`
              text-4xl font-bold
              ${isCrit ? 'text-g-gold' : isFumble ? 'text-g-red' : 'text-g-text-primary'}
            `}
          >
            {displayValue ?? '?'}
          </div>

          {/* Roll type */}
          <div className="text-sm text-[var(--g-text-muted)] mt-1">
            {roll.roll_type.toUpperCase()}
          </div>
        </motion.div>

        {/* Critical/Fumble label */}
        {!isAnimating && (isCrit || isFumble) && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className={`
              absolute bottom-1/3
              px-6 py-2
              rounded-full
              font-heading text-xl
              ${isCrit ? 'bg-g-gold text-g-bg-base' : 'bg-g-red text-g-text-primary'}
            `}
          >
            {isCrit ? 'CRITICAL HIT!' : 'FUMBLE!'}
          </motion.div>
        )}

        {/* Total with modifier */}
        {!isAnimating && roll.modifier !== 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="absolute bottom-1/4 text-[var(--g-text-secondary)]"
          >
            {roll.result} {roll.modifier >= 0 ? '+' : ''}{roll.modifier} = {roll.total}
          </motion.div>
        )}
      </motion.div>
    </AnimatePresence>
  );
};

export default DiceRollDisplay;
