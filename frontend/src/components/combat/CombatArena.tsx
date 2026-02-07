/**
 * Combat arena - main combat display area
 */
import React from 'react';
import { Skull, User, Sword } from 'lucide-react';
import { useCombatStore } from '../../stores';
import CombatantCard from './CombatantCard';
import DistanceBand from './DistanceBand';

interface CombatArenaProps {
  onSelectTarget: (id: string) => void;
  className?: string;
}

export const CombatArena: React.FC<CombatArenaProps> = ({
  onSelectTarget,
  className = '',
}) => {
  const { combatState, selectedTarget, getPlayer, getAllies, getEnemies } =
    useCombatStore();

  if (!combatState) return null;

  const player = getPlayer();
  const allies = getAllies();
  const enemies = getEnemies();

  return (
    <div className={`space-y-6 ${className}`}>
      {/* Enemies section */}
      <div>
        <h3 className="text-sm font-heading text-g-red mb-3 flex items-center gap-2">
          <Skull className="w-5 h-5" />
          Enemies
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {enemies.map((enemy) => (
            <CombatantCard
              key={enemy.id}
              combatant={enemy}
              isActive={combatState.active_combatant_id === enemy.id}
              isSelected={selectedTarget === enemy.id}
              onClick={() => onSelectTarget(enemy.id)}
            />
          ))}
        </div>
      </div>

      {/* Distance indicator */}
      {player && enemies.length > 0 && (
        <div className="flex justify-center py-4">
          <DistanceBand
            current={enemies[0]?.distance_band || 'NEAR'}
            className="max-w-lg"
          />
        </div>
      )}

      {/* Player and allies section */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Player */}
        {player && (
          <div>
            <h3 className="text-sm font-heading text-g-gold mb-3 flex items-center gap-2">
              <User className="w-5 h-5" />
              You
            </h3>
            <CombatantCard
              combatant={player}
              isActive={combatState.active_combatant_id === player.id}
            />
          </div>
        )}

        {/* Allies */}
        {allies.length > 0 && (
          <div>
            <h3 className="text-sm font-heading text-g-green mb-3 flex items-center gap-2">
              <Sword className="w-5 h-5" />
              Allies
            </h3>
            <div className="space-y-3">
              {allies.map((ally) => (
                <CombatantCard
                  key={ally.id}
                  combatant={ally}
                  isActive={combatState.active_combatant_id === ally.id}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default CombatArena;
