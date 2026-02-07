/**
 * Distance band indicator for combat
 */
import React from 'react';
import { motion } from 'framer-motion';
import type { DistanceBand as DistanceBandType } from '../../types';
import { DISTANCE_BAND_ORDER, DISTANCE_BAND_LABELS } from '../../types';

interface DistanceBandProps {
  current: DistanceBandType;
  className?: string;
}

const bandColors: Record<DistanceBandType, string> = {
  ENGAGED: 'bg-g-red border-g-red',
  CLOSE: 'bg-g-danger-high border-g-danger-high',
  NEAR: 'bg-g-danger-medium border-g-danger-medium',
  FAR: 'bg-g-danger-low border-g-danger-low',
  DISTANT: 'bg-g-cyan border-g-cyan',
};

export const DistanceBand: React.FC<DistanceBandProps> = ({
  current,
  className = '',
}) => {
  const currentIndex = DISTANCE_BAND_ORDER.indexOf(current);

  return (
    <div className={`${className}`}>
      <div className="flex items-center gap-1">
        {DISTANCE_BAND_ORDER.map((band, index) => {
          const isActive = index === currentIndex;
          const isPassed = index < currentIndex;

          return (
            <React.Fragment key={band}>
              {/* Band indicator */}
              <motion.div
                animate={{
                  scale: isActive ? 1.1 : 1,
                }}
                className={`
                  relative
                  px-3 py-1.5
                  rounded-md
                  text-xs font-medium
                  border-2
                  transition-all duration-200
                  ${
                    isActive
                      ? `${bandColors[band]} text-white shadow-lg`
                      : isPassed
                      ? 'bg-g-bg-sidebar border-[var(--g-border-default)] text-[var(--g-text-muted)]'
                      : 'bg-g-bg-surface-alt border-[var(--g-border-default)] text-[var(--g-text-secondary)]'
                  }
                `}
              >
                {DISTANCE_BAND_LABELS[band]}

                {/* Active indicator */}
                {isActive && (
                  <motion.div
                    layoutId="active-band"
                    className="absolute -bottom-2 left-1/2 -translate-x-1/2 w-2 h-2 bg-current rounded-full"
                  />
                )}
              </motion.div>

              {/* Connector */}
              {index < DISTANCE_BAND_ORDER.length - 1 && (
                <div
                  className={`
                    w-4 h-0.5
                    ${
                      index < currentIndex
                        ? 'bg-[var(--g-text-muted)]'
                        : 'bg-[var(--g-border-default)]'
                    }
                  `}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Description */}
      <div className="mt-2 text-center text-xs text-[var(--g-text-muted)]">
        {current === 'ENGAGED' && 'In melee range - can attack without moving'}
        {current === 'CLOSE' && 'One step away - can close with a move action'}
        {current === 'NEAR' && 'Moderate distance - requires movement'}
        {current === 'FAR' && 'Long range - significant movement needed'}
        {current === 'DISTANT' && 'Very far - ranged attacks only'}
      </div>
    </div>
  );
};

export default DistanceBand;
