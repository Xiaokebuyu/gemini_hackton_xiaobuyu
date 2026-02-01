/**
 * Combatant card for combat display
 */
import React from 'react';
import { motion } from 'framer-motion';
import { Shield, Crosshair } from 'lucide-react';
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
      animate={isActive ? { boxShadow: '0 0 20px rgba(255, 215, 0, 0.5)' } : {}}
      onClick={onClick}
      className={`
        relative
        bg-bg-card
        rounded-lg
        border-2
        p-4
        transition-all duration-200
        ${
          combatant.is_dead
            ? 'opacity-50 border-[var(--color-text-muted)]'
            : isSelected
            ? 'border-accent-gold shadow-glow-gold'
            : isActive
            ? 'border-accent-gold'
            : combatant.is_ally
            ? 'border-accent-green/50'
            : 'border-accent-red/50'
        }
        ${onClick && !combatant.is_dead ? 'cursor-pointer hover:border-accent-gold/70' : ''}
        ${className}
      `}
    >
      {/* Active turn indicator */}
      {isActive && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="absolute -top-3 left-1/2 -translate-x-1/2 px-2 py-0.5 bg-accent-gold text-bg-primary text-xs font-bold rounded"
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
              text-2xl
              ${
                combatant.is_player
                  ? 'bg-accent-gold/20 border-2 border-accent-gold'
                  : combatant.is_ally
                  ? 'bg-accent-green/20 border-2 border-accent-green'
                  : 'bg-accent-red/20 border-2 border-accent-red'
              }
            `}
          >
            {combatant.is_player ? '🧙' : combatant.is_ally ? '⚔️' : '👹'}
          </div>
          <div>
            <h4
              className={`
                font-medium
                ${
                  combatant.is_player
                    ? 'text-accent-gold'
                    : combatant.is_ally
                    ? 'text-accent-green'
                    : 'text-accent-red'
                }
              `}
            >
              {combatant.name}
            </h4>
            <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <span>Init: {combatant.initiative}</span>
            </div>
          </div>
        </div>

        {/* AC badge */}
        <div
          className="
            flex items-center gap-1
            px-2 py-1
            bg-accent-cyan/20
            rounded
            text-sm font-bold text-accent-cyan
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
          <span className="text-[var(--color-text-muted)]">HP</span>
          <span className="text-[var(--color-text-secondary)]">
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
                bg-bg-secondary
                rounded
                text-xs
                flex items-center gap-1
              "
              title={STATUS_EFFECT_LABELS[effect]}
            >
              <span>{STATUS_EFFECT_ICONS[effect]}</span>
              <span className="text-[var(--color-text-secondary)]">
                {STATUS_EFFECT_LABELS[effect]}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* Dead overlay */}
      {combatant.is_dead && (
        <div className="absolute inset-0 flex items-center justify-center bg-bg-primary/50 rounded-lg">
          <span className="text-2xl">💀</span>
        </div>
      )}

      {/* Target indicator */}
      {isSelected && (
        <div className="absolute -right-2 -top-2">
          <Crosshair className="w-6 h-6 text-accent-gold" />
        </div>
      )}
    </motion.div>
  );
};

export default CombatantCard;
