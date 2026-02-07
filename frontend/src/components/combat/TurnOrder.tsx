/**
 * Turn order display for combat
 */
import React from 'react';
import { motion } from 'framer-motion';
import { ChevronRight, User, Sword, Skull } from 'lucide-react';
import type { Combatant } from '../../types';

interface TurnOrderProps {
  combatants: Combatant[];
  currentTurnId: string | null;
  className?: string;
}

export const TurnOrder: React.FC<TurnOrderProps> = ({
  combatants,
  currentTurnId,
  className = '',
}) => {
  // Sort by initiative (descending)
  const sortedCombatants = [...combatants]
    .filter((c) => !c.is_dead)
    .sort((a, b) => b.initiative - a.initiative);

  return (
    <div className={`flex items-center gap-1 overflow-x-auto ${className}`}>
      {sortedCombatants.map((combatant, index) => {
        const isActive = combatant.id === currentTurnId;

        return (
          <React.Fragment key={combatant.id}>
            <motion.div
              animate={isActive ? { scale: 1.1 } : { scale: 1 }}
              className={`
                flex-shrink-0
                flex items-center gap-2
                px-3 py-2
                rounded-lg
                border-2
                ${
                  isActive
                    ? 'bg-g-gold/20 border-g-gold'
                    : combatant.is_ally
                    ? 'bg-g-bg-surface-alt border-g-green/30'
                    : 'bg-g-bg-surface-alt border-g-red/30'
                }
              `}
            >
              {/* Avatar */}
              <div
                className={`
                  w-6 h-6 rounded-full
                  flex items-center justify-center
                  text-sm
                  ${
                    combatant.is_player
                      ? 'bg-g-gold/20'
                      : combatant.is_ally
                      ? 'bg-g-green/20'
                      : 'bg-g-red/20'
                  }
                `}
              >
                {combatant.is_player
                  ? <User className="w-3 h-3" />
                  : combatant.is_ally
                  ? <Sword className="w-3 h-3" />
                  : <Skull className="w-3 h-3" />
                }
              </div>

              {/* Name */}
              <span
                className={`
                  text-sm font-medium
                  ${
                    isActive
                      ? 'text-g-gold'
                      : combatant.is_ally
                      ? 'text-g-green'
                      : 'text-g-red'
                  }
                `}
              >
                {combatant.name}
              </span>

              {/* Initiative */}
              <span className="text-xs text-[var(--g-text-muted)]">
                ({combatant.initiative})
              </span>
            </motion.div>

            {/* Arrow connector */}
            {index < sortedCombatants.length - 1 && (
              <ChevronRight className="w-4 h-4 text-[var(--g-text-muted)] flex-shrink-0" />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default TurnOrder;
