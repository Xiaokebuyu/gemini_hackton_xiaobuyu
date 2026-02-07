/**
 * Available destinations list component
 */
import React from 'react';
import { motion } from 'framer-motion';
import { Navigation, AlertTriangle, Lock } from 'lucide-react';
import { useGameStore } from '../../stores';
import { useGameInput } from '../../api';
import type { Destination } from '../../types';

interface DestinationListProps {
  destinations?: Destination[];
  className?: string;
}

const dangerColors = {
  low: 'text-g-danger-low border-g-danger-low',
  medium: 'text-g-danger-medium border-g-danger-medium',
  high: 'text-g-danger-high border-g-danger-high',
  extreme: 'text-g-danger-extreme border-g-danger-extreme',
};

const dangerLabels = {
  low: 'Safe',
  medium: 'Moderate',
  high: 'Dangerous',
  extreme: 'Deadly',
};

export const DestinationList: React.FC<DestinationListProps> = ({
  destinations,
  className = '',
}) => {
  const { location } = useGameStore();
  const { sendInput, isLoading } = useGameInput();

  const dests = destinations || location?.available_destinations || [];

  const handleTravel = (dest: Destination) => {
    if (dest.is_accessible && !isLoading) {
      sendInput(`[前往${dest.name}]`);
    }
  };

  if (dests.length === 0) {
    return (
      <div className={`text-sm text-[var(--g-text-muted)] ${className}`}>
        No known paths from here.
      </div>
    );
  }

  return (
    <div className={`space-y-2 ${className}`}>
      <h4 className="text-xs text-[var(--g-text-muted)] uppercase tracking-wide mb-2">
        Travel To
      </h4>
      {dests.map((dest, index) => (
        <motion.button
          key={dest.location_id}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: index * 0.05 }}
          onClick={() => handleTravel(dest)}
          disabled={!dest.is_accessible || isLoading}
          className={`
            w-full
            flex items-center gap-3
            p-2
            rounded-lg
            text-left
            transition-all duration-200
            ${
              dest.is_accessible
                ? 'bg-g-bg-surface-alt hover:bg-g-bg-surface-alt/80 border border-transparent hover:border-g-gold/50'
                : 'bg-g-bg-sidebar opacity-50 cursor-not-allowed border border-transparent'
            }
          `}
        >
          {/* Icon */}
          <div
            className={`
              w-8 h-8 rounded-lg
              flex items-center justify-center
              ${dest.is_accessible ? 'bg-g-gold/20' : 'bg-g-bg-sidebar'}
            `}
          >
            {dest.is_accessible ? (
              <Navigation className="w-4 h-4 text-g-gold" />
            ) : (
              <Lock className="w-4 h-4 text-[var(--g-text-muted)]" />
            )}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={`
                  text-sm font-medium truncate
                  ${
                    dest.is_accessible
                      ? 'text-[var(--g-text-primary)]'
                      : 'text-[var(--g-text-muted)]'
                  }
                `}
              >
                {dest.name}
              </span>
              {dest.distance && (
                <span className="text-xs text-[var(--g-text-muted)]">
                  {dest.distance}
                </span>
              )}
            </div>
            {dest.description && (
              <div className="text-xs text-[var(--g-text-muted)] truncate">
                {dest.description}
              </div>
            )}
          </div>

          {/* Danger indicator */}
          {dest.danger_level && dest.danger_level !== 'low' && (
            <div
              className={`
                flex items-center gap-1
                px-2 py-1
                rounded
                text-xs
                border
                ${dangerColors[dest.danger_level]}
              `}
              title={`Danger: ${dangerLabels[dest.danger_level]}`}
            >
              <AlertTriangle className="w-3 h-3" />
              <span>{dangerLabels[dest.danger_level]}</span>
            </div>
          )}
        </motion.button>
      ))}
    </div>
  );
};

export default DestinationList;
