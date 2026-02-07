/**
 * Combatant card for combat display
 */
import React from 'react';
import { motion } from 'framer-motion';
import { Shield, Crosshair, User, Sword, Skull } from 'lucide-react';
import type { Combatant } from '../../types';
import { STATUS_EFFECT_ICONS, STATUS_EFFECT_LABELS } from '../../types';
import HealthBar from '../shared/HealthBar';

interface CombatantCardProps {
  combatant: Combatant;
  isActive?: boolean;
  isSelected?: boolean;
  onClick?: () => void;
  className?: string;
}

export const CombatantCard: React.FC<CombatantCardProps> = ({
  combatant,
  isActive = false,
  isSelected = false,
  onClick,
  className = '',
}) => {
  return (
    <motion.div
      whileHover={onClick ? { scale: 1.02 } : {}}
      whileTap={onClick ? { scale: 0.98 } : {}}
      animate={isActive ? { boxShadow: '0 0 20px rgba(196, 154, 42, 0.4)' } : {}}
      onClick={onClick}
      className={`
        relative
        bg-g-bg-surface-alt
        rounded-lg
        border-2
        p-4
        transition-all duration-200
        ${
          combatant.is_dead
            ? 'opacity-50 border-[var(--g-text-muted)]'
            : isSelected
            ? 'border-g-gold shadow-g-gold'
            : isActive
            ? 'border-g-gold'
            : combatant.is_ally
            ? 'border-g-green/50'
            : 'border-g-red/50'
        }
        ${onClick && !combatant.is_dead ? 'cursor-pointer hover:border-g-gold/70' : ''}
        ${className}
      `}
    >
      {/* Active turn indicator */}
      {isActive && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="absolute -top-3 left-1/2 -translate-x-1/2 px-2 py-0.5 bg-g-gold text-g-bg-base text-xs font-bold rounded"
        >
          CURRENT TURN
        </motion.div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        {/* Avatar and name */}
        <div className="flex items-center gap-3">
          <div
            className={`
              w-12 h-12 rounded-full
              flex items-center justify-center
              ${
                combatant.is_player
                  ? 'bg-g-gold/20 border-2 border-g-gold'
                  : combatant.is_ally
                  ? 'bg-g-green/20 border-2 border-g-green'
                  : 'bg-g-red/20 border-2 border-g-red'
              }
            `}
          >
            {combatant.is_player
              ? <User className="w-5 h-5" />
              : combatant.is_ally
              ? <Sword className="w-5 h-5" />
              : <Skull className="w-5 h-5" />
            }
          </div>
          <div>
            <h4
              className={`
                font-medium
                ${
                  combatant.is_player
                    ? 'text-g-gold'
                    : combatant.is_ally
                    ? 'text-g-green'
                    : 'text-g-red'
                }
              `}
            >
              {combatant.name}
            </h4>
            <div className="flex items-center gap-2 text-xs text-[var(--g-text-muted)]">
              <span>Init: {combatant.initiative}</span>
            </div>
          </div>
        </div>

        {/* AC badge */}
        <div
          className="
            flex items-center gap-1
            px-2 py-1
            bg-g-cyan/20
            rounded
            text-sm font-bold text-g-cyan
          "
          title="Armor Class"
        >
          <Shield className="w-3 h-3" />
          {combatant.ac}
        </div>
      </div>

      {/* Health */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="text-[var(--g-text-muted)]">HP</span>
          <span className="text-[var(--g-text-secondary)]">
            {combatant.hp}/{combatant.max_hp}
          </span>
        </div>
        <HealthBar
          current={combatant.hp}
          max={combatant.max_hp}
          variant="health"
          size="md"
        />
      </div>

      {/* Status effects */}
      {combatant.status_effects.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {combatant.status_effects.map((effect) => (
            <span
              key={effect}
              className="
                px-2 py-0.5
                bg-g-bg-sidebar
                rounded
                text-xs
                flex items-center gap-1
              "
              title={STATUS_EFFECT_LABELS[effect]}
            >
              <span>{STATUS_EFFECT_ICONS[effect]}</span>
              <span className="text-[var(--g-text-secondary)]">
                {STATUS_EFFECT_LABELS[effect]}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* Dead overlay */}
      {combatant.is_dead && (
        <div className="absolute inset-0 flex items-center justify-center bg-g-bg-base/50 rounded-lg">
          <Skull className="w-6 h-6 text-g-text-muted" />
        </div>
      )}

      {/* Target indicator */}
      {isSelected && (
        <div className="absolute -right-2 -top-2">
          <Crosshair className="w-6 h-6 text-g-gold" />
        </div>
      )}
    </motion.div>
  );
};

export default CombatantCard;
