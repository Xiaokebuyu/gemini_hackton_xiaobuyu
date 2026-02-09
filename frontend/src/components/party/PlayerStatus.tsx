/**
 * Player status — compact RPG stat block
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Heart, Sparkles, Backpack } from 'lucide-react';
import { useGameStore } from '../../stores';
import HealthBar from '../shared/HealthBar';

interface PlayerStatusProps {
  className?: string;
}

export const PlayerStatus: React.FC<PlayerStatusProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const { playerHp, xpSnapshot, inventoryItemCount } = useGameStore();
  const hpCurrent = playerHp?.current ?? 0;
  const hpMax = playerHp?.max ?? 0;
  const hpDelta = playerHp?.delta ?? null;
  const xpCurrent = xpSnapshot?.new_xp ?? 0;
  const xpGained = xpSnapshot?.gained ?? 0;
  const level = xpSnapshot?.new_level ?? null;
  const xpBarMax = Math.max(100, Math.ceil((xpCurrent + 1) / 100) * 100);

  return (
    <div className={`px-5 py-4 ${className}`}>
      {/* Name + Level */}
      <div className="flex items-baseline gap-3 mb-4">
        <h3 className="font-heading text-lg text-[var(--g-text-primary)] tracking-wide">
          {t('status.adventurer', 'Adventurer')}
        </h3>
        {level !== null && (
          <span className="text-xs text-[var(--g-accent-gold)] font-semibold">
            Lv.{level}
          </span>
        )}
      </div>

      {/* Stats */}
      <div className="space-y-2.5">
        {/* HP */}
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-xs text-g-text-muted w-20 flex-shrink-0">
            <Heart className="w-3 h-3 text-g-danger-low" />
            {t('status.hp', '生命值')}
          </span>
          <div className="w-28 flex-shrink-0">
            <HealthBar
              current={Math.max(0, hpCurrent)}
              max={Math.max(1, hpMax || 1)}
              variant="health"
              size="sm"
            />
          </div>
          <span className="text-xs text-g-text-secondary tabular-nums whitespace-nowrap">
            {hpMax > 0 ? `${hpCurrent}/${hpMax}` : '--'}
            {hpDelta !== null && hpDelta !== 0 && (
              <span className={`ml-1 font-medium ${hpDelta > 0 ? 'text-g-green' : 'text-g-red'}`}>
                {hpDelta > 0 ? '+' : ''}{hpDelta}
              </span>
            )}
          </span>
        </div>

        {/* XP */}
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-xs text-g-text-muted w-20 flex-shrink-0">
            <Sparkles className="w-3 h-3 text-[var(--g-accent-gold)]" />
            {t('status.exp', '经验')}
          </span>
          <div className="w-28 flex-shrink-0">
            <HealthBar
              current={Math.max(0, xpCurrent)}
              max={Math.max(1, xpBarMax)}
              variant="experience"
              size="sm"
            />
          </div>
          <span className="text-xs text-g-text-secondary tabular-nums whitespace-nowrap">
            {xpCurrent}
            {xpGained > 0 && (
              <span className="text-g-green ml-1 font-medium">+{xpGained}</span>
            )}
          </span>
        </div>

        {/* Inventory count */}
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-xs text-g-text-muted w-20 flex-shrink-0">
            <Backpack className="w-3 h-3 text-[var(--g-accent-gold)]" />
            {t('status.inventory', '背包')}
          </span>
          <span className="text-xs text-g-text-secondary tabular-nums">{inventoryItemCount}</span>
        </div>
      </div>

      {/* Bottom separator */}
      <div className="mt-4 h-px bg-[var(--g-accent-gold)]/15" />
    </div>
  );
};

export default PlayerStatus;
