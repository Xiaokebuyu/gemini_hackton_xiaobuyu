/**
 * GM narration options card component
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import type { ParsedOption } from '../../utils/narrationParser';

interface GMOptionsProps {
  options: ParsedOption[];
  onSelect: (option: ParsedOption) => void;
  disabled: boolean;
  selectedId?: string | null;
}

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.08 },
  },
};

const item = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0 },
};

export const GMOptions: React.FC<GMOptionsProps> = ({
  options,
  onSelect,
  disabled,
  selectedId,
}) => {
  const { t } = useTranslation();

  if (options.length === 0) return null;

  return (
    <div className="border-t border-b border-g-border bg-transparent py-4 px-6 mt-4">
      <p className="text-xs text-g-text-muted uppercase tracking-wide font-body mb-3">
        {t('options.choose')}
      </p>
      <motion.div
        variants={container}
        initial="hidden"
        animate="show"
      >
        <AnimatePresence>
          {options.map((opt, index) => {
            const isSelected = selectedId === opt.id;
            const isDisabled = disabled || (selectedId != null && !isSelected);
            const isUnselected = selectedId != null && !isSelected;
            return (
              <motion.button
                key={opt.id}
                variants={item}
                whileHover={!isDisabled ? { scale: 1.01 } : undefined}
                whileTap={!isDisabled ? { scale: 0.99 } : undefined}
                animate={isUnselected ? { opacity: 0, scale: 0.95 } : { opacity: 1, scale: 1 }}
                transition={{ duration: 0.2 }}
                onClick={() => !isDisabled && onSelect(opt)}
                disabled={isDisabled}
                className={`
                  w-full text-left px-6 py-3
                  border-b border-g-border/50 last:border-b-0
                  transition-all duration-200
                  font-body text-sm
                  ${isSelected
                    ? 'bg-g-gold/10 text-g-gold animate-pulse-ring'
                    : isDisabled
                      ? 'opacity-40 cursor-not-allowed'
                      : 'hover:bg-g-gold/5 cursor-pointer'
                  }
                `}
              >
                <span className="text-g-gold/70 mr-2">{index + 1}.</span>
                <span className="font-medium">{opt.label}</span>
                {opt.description && (
                  <span className="text-g-text-secondary ml-2">{opt.description}</span>
                )}
              </motion.button>
            );
          })}
        </AnimatePresence>
      </motion.div>
    </div>
  );
};

export default GMOptions;
