/**
 * GM narration options card component
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
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
    <motion.div
      variants={container}
      initial="hidden"
      animate="show"
      className="mt-3 space-y-2"
    >
      <p className="text-xs text-g-text-muted uppercase tracking-wide font-body">
        {t('options.choose')}
      </p>
      {options.map((opt) => {
        const isSelected = selectedId === opt.id;
        const isDisabled = disabled || (selectedId != null && !isSelected);
        return (
          <motion.button
            key={opt.id}
            variants={item}
            whileHover={!isDisabled ? { scale: 1.01 } : undefined}
            whileTap={!isDisabled ? { scale: 0.99 } : undefined}
            onClick={() => !isDisabled && onSelect(opt)}
            disabled={isDisabled}
            className={`
              w-full text-left px-4 py-3
              bg-g-bg-surface border rounded-lg
              transition-all duration-200
              font-body text-sm
              ${isSelected
                ? 'border-g-gold bg-g-gold/10 text-g-gold'
                : isDisabled
                  ? 'border-g-border opacity-40 cursor-not-allowed'
                  : 'border-g-border hover:border-g-gold cursor-pointer'
              }
            `}
          >
            <span className="font-medium">{opt.label}</span>
            {opt.description && (
              <span className="text-g-text-secondary ml-2">{opt.description}</span>
            )}
          </motion.button>
        );
      })}
    </motion.div>
  );
};

export default GMOptions;
