/**
 * Dice roll animation display
 * Global component — serves both combat rolls and ability checks.
 */
import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Dices } from 'lucide-react';
import type { DiceRoll } from '../../types';

const SKILL_LABELS: Record<string, string> = {
  stealth: '潜行', persuasion: '说服', athletics: '运动',
  perception: '感知', investigation: '调查', sleight_of_hand: '巧手',
  arcana: '奥术', intimidation: '威吓', deception: '欺骗',
  survival: '生存', medicine: '医疗', nature: '自然',
  acrobatics: '特技', insight: '洞察', animal_handling: '驯兽',
  history: '历史', religion: '宗教', performance: '表演',
};

const ABILITY_LABELS: Record<string, string> = {
  str: '力量', dex: '敏捷', con: '体质',
  int: '智力', wis: '感知', cha: '魅力',
};

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
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

          // Ability checks need more reading time
          const delay = roll.dc != null ? 2500 : 1500;
          dismissTimerRef.current = setTimeout(() => {
            onCompleteRef.current?.();
          }, delay);
        }
      }, 50);

      return () => {
        clearInterval(interval);
        if (dismissTimerRef.current) {
          clearTimeout(dismissTimerRef.current);
          dismissTimerRef.current = null;
        }
      };
    }
  }, [roll]);

  if (!roll) return null;

  const isCrit = roll.is_critical;
  const isFumble = roll.is_fumble;
  const isCheck = roll.dc != null;

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

        {/* Critical/Fumble label (non-check mode) */}
        {!isAnimating && !isCheck && (isCrit || isFumble) && (
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

        {/* Total with modifier (non-check mode) */}
        {!isAnimating && !isCheck && roll.modifier !== 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="absolute bottom-1/4 text-[var(--g-text-secondary)]"
          >
            {roll.result} {roll.modifier >= 0 ? '+' : ''}{roll.modifier} = {roll.total}
          </motion.div>
        )}

        {/* Ability check details */}
        {!isAnimating && isCheck && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="absolute bottom-1/3 text-center"
          >
            {/* Skill / ability label */}
            <div className="text-sm text-[var(--g-text-muted)] mb-1">
              {roll.skill
                ? `【${SKILL_LABELS[roll.skill] || roll.skill}检定】`
                : roll.ability
                ? `【${ABILITY_LABELS[roll.ability] || roll.ability}检定】`
                : '【检定】'}
            </div>
            {/* Calculation breakdown */}
            <div className="text-sm text-[var(--g-text-secondary)]">
              d20={roll.result}
              {roll.modifier !== 0 && (
                <> + {roll.ability ? `${ABILITY_LABELS[roll.ability] || roll.ability}` : ''}{roll.modifier >= 0 ? '+' : ''}{roll.modifier}</>
              )}
              {roll.proficiency != null && roll.proficiency !== 0 && (
                <> + 熟练+{roll.proficiency}</>
              )}
            </div>
            {/* DC comparison */}
            <div className="text-lg font-bold mt-1">
              总计 {roll.total} vs DC {roll.dc}
            </div>
            {/* Pass / fail */}
            <div className={`text-xl font-heading mt-2 ${roll.success ? 'text-g-green' : 'text-g-red'}`}>
              {isCrit ? '大成功！' : isFumble ? '大失败！' : roll.success ? '成功！' : '失败。'}
            </div>
          </motion.div>
        )}
      </motion.div>
    </AnimatePresence>
  );
};

export default DiceRollDisplay;
