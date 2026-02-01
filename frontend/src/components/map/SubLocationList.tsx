/**
 * Sub-locations list component
 */
import React from 'react';
import { motion } from 'framer-motion';
import { DoorOpen, Lock } from 'lucide-react';
import { useGameStore } from '../../stores';
import { useGameInput } from '../../api';
import type { SubLocation } from '../../types';

interface SubLocationListProps {
  subLocations?: SubLocation[];
  className?: string;
}

export const SubLocationList: React.FC<SubLocationListProps> = ({
  subLocations,
  className = '',
}) => {
  const { subLocation: currentSubLocation } = useGameStore();
  const { sendInput, isLoading } = useGameInput();

  // Note: LocationResponse doesn't have sub_locations field
  // Sub-locations must be passed explicitly via props
  const locations = subLocations || [];

  const handleEnter = (subLoc: SubLocation) => {
    if (subLoc.is_accessible && !isLoading) {
      sendInput(`[进入${subLoc.name}]`);
    }
  };

  if (locations.length === 0) {
    return (
      <div className={`text-sm text-[var(--color-text-muted)] ${className}`}>
        No accessible areas nearby.
      </div>
    );
  }

  return (
    <div className={`space-y-2 ${className}`}>
      <h4 className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide mb-2">
        Nearby Areas
      </h4>
      {locations.map((subLoc, index) => (
        <motion.button
          key={subLoc.id}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: index * 0.05 }}
          onClick={() => handleEnter(subLoc)}
          disabled={!subLoc.is_accessible || isLoading || currentSubLocation === subLoc.id}
          className={`
            w-full
            flex items-center gap-3
            p-2
            rounded-lg
            text-left
            transition-all duration-200
            ${
              currentSubLocation === subLoc.id
                ? 'bg-accent-gold/20 border border-accent-gold'
                : subLoc.is_accessible
                ? 'bg-bg-card hover:bg-bg-card/80 border border-transparent hover:border-accent-cyan/50'
                : 'bg-bg-secondary opacity-50 cursor-not-allowed border border-transparent'
            }
          `}
        >
          {/* Icon */}
          <div
            className={`
              w-8 h-8 rounded-lg
              flex items-center justify-center
              ${subLoc.is_accessible ? 'bg-accent-cyan/20' : 'bg-bg-secondary'}
            `}
          >
            {subLoc.is_accessible ? (
              <DoorOpen className="w-4 h-4 text-accent-cyan" />
            ) : (
              <Lock className="w-4 h-4 text-[var(--color-text-muted)]" />
            )}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div
              className={`
                text-sm font-medium truncate
                ${
                  currentSubLocation === subLoc.id
                    ? 'text-accent-gold'
                    : subLoc.is_accessible
                    ? 'text-[var(--color-text-primary)]'
                    : 'text-[var(--color-text-muted)]'
                }
              `}
            >
              {subLoc.name}
              {currentSubLocation === subLoc.id && (
                <span className="ml-2 text-xs">(current)</span>
              )}
            </div>
            {subLoc.description && (
              <div className="text-xs text-[var(--color-text-muted)] truncate">
                {subLoc.description}
              </div>
            )}
          </div>
        </motion.button>
      ))}
    </div>
  );
};

export default SubLocationList;
