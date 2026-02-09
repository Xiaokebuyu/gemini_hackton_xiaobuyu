/**
 * Sub-locations list component
 */
import React from 'react';
import { motion } from 'framer-motion';
import { DoorOpen, Lock } from 'lucide-react';
import { useGameStore } from '../../stores';
import { useStreamGameInput } from '../../api';
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
  const { sendInput, isLoading } = useStreamGameInput();

  const locations = subLocations || [];
  const isSubLocationAccessible = (subLoc: SubLocation) => subLoc.is_accessible !== false;

  const handleEnter = (subLoc: SubLocation) => {
    if (isSubLocationAccessible(subLoc) && !isLoading) {
      sendInput(`[进入${subLoc.name}]`);
    }
  };

  if (locations.length === 0) {
    return (
      <div className={`text-sm text-[var(--g-text-muted)] ${className}`}>
        No accessible areas nearby.
      </div>
    );
  }

  return (
    <div className={`space-y-2 ${className}`}>
      <h4 className="text-xs text-[var(--g-text-muted)] uppercase tracking-wide mb-2">
        Nearby Areas
      </h4>
      {locations.map((subLoc, index) => {
        const isAccessible = isSubLocationAccessible(subLoc);
        return (
          <motion.button
            key={subLoc.id || subLoc.name}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.05 }}
            onClick={() => handleEnter(subLoc)}
            disabled={!isAccessible || isLoading || currentSubLocation === subLoc.id}
            whileTap={isAccessible ? { scale: 0.97 } : undefined}
            className={`
              w-full
              flex items-center gap-3
              p-2
              rounded-lg
              text-left
              transition-all duration-200
              ${
                currentSubLocation === subLoc.id
                  ? 'bg-g-gold/20 border border-g-gold animate-pulse-glow'
                  : isAccessible
                  ? 'bg-g-bg-surface-alt hover:bg-g-bg-surface-alt/80 border border-transparent hover:border-g-cyan/50'
                  : 'bg-g-bg-sidebar opacity-50 cursor-not-allowed border border-transparent'
              }
            `}
          >
            {/* Icon */}
            <div
              className={`
                w-8 h-8 rounded-lg
                flex items-center justify-center
                ${isAccessible ? 'bg-g-cyan/20' : 'bg-g-bg-sidebar'}
              `}
            >
              {isAccessible ? (
                <DoorOpen className="w-4 h-4 text-g-cyan" />
              ) : (
                <Lock className="w-4 h-4 text-[var(--g-text-muted)]" />
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div
                className={`
                  text-sm font-medium truncate
                  ${
                    currentSubLocation === subLoc.id
                      ? 'text-g-gold'
                      : isAccessible
                      ? 'text-[var(--g-text-primary)]'
                      : 'text-[var(--g-text-muted)]'
                  }
                `}
              >
                {subLoc.name}
                {currentSubLocation === subLoc.id && (
                  <span className="ml-2 text-xs">(current)</span>
                )}
              </div>
              {subLoc.description && (
                <div className="text-xs text-[var(--g-text-muted)] truncate">
                  {subLoc.description}
                </div>
              )}
            </div>
          </motion.button>
        );
      })}
    </div>
  );
};

export default SubLocationList;
