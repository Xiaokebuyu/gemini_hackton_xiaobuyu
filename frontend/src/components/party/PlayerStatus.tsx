/**
 * Player status component - using Sketch style
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { User, Heart, Shield, Sparkles } from 'lucide-react';
import HealthBar from '../shared/HealthBar';

interface PlayerStatusProps {
  className?: string;
}

// Placeholder player stats (would come from backend)
interface PlayerStats {
  name: string;
  level: number;
  hp: number;
  maxHp: number;
  mp: number;
  maxMp: number;
  ac: number;
  experience: number;
  nextLevel: number;
}

const mockPlayerStats: PlayerStats = {
  name: 'Adventurer',
  level: 1,
  hp: 45,
  maxHp: 50,
  mp: 20,
  maxMp: 25,
  ac: 14,
  experience: 150,
  nextLevel: 300,
};

export const PlayerStatus: React.FC<PlayerStatusProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const player = mockPlayerStats;
  const expPercent = (player.experience / player.nextLevel) * 100;

  return (
    <div className={`p-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        {/* Avatar */}
        <div
          className="
            w-12 h-12 rounded-full
            bg-sketch-accent-gold
            flex items-center justify-center
            border-2 border-sketch-ink-secondary
          "
        >
          <User className="w-6 h-6 text-sketch-bg-primary" />
        </div>

        {/* Name and level */}
        <div className="flex-1">
          <h3 className="font-handwritten text-lg text-sketch-accent-gold">
            {player.name}
          </h3>
          <div className="flex items-center gap-2 text-xs text-sketch-ink-secondary font-handwritten">
            <span>{t('status.level')} {player.level}</span>
            <span className="text-sketch-ink-muted">|</span>
            <span>{player.experience}/{player.nextLevel} {t('status.exp')}</span>
          </div>
        </div>
      </div>

      {/* Experience bar */}
      <div className="mb-4">
        <div className="h-1 bg-sketch-bg-secondary rounded-full overflow-hidden">
          <div
            className="h-full bg-sketch-accent-purple transition-all duration-500"
            style={{ width: `${expPercent}%` }}
          />
        </div>
      </div>

      {/* Stats grid */}
      <div className="space-y-3">
        {/* HP */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <Heart className="w-4 h-4 text-sketch-accent-red" />
              <span className="text-sm text-sketch-ink-secondary font-handwritten">
                {t('status.hp')}
              </span>
            </div>
            <span className="text-sm font-medium text-sketch-ink-primary font-handwritten">
              {player.hp}/{player.maxHp}
            </span>
          </div>
          <HealthBar
            current={player.hp}
            max={player.maxHp}
            variant="health"
            size="md"
          />
        </div>

        {/* MP */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-sketch-accent-purple" />
              <span className="text-sm text-sketch-ink-secondary font-handwritten">
                {t('status.mp')}
              </span>
            </div>
            <span className="text-sm font-medium text-sketch-ink-primary font-handwritten">
              {player.mp}/{player.maxMp}
            </span>
          </div>
          <HealthBar
            current={player.mp}
            max={player.maxMp}
            variant="mana"
            size="md"
          />
        </div>

        {/* AC */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-sketch-accent-cyan" />
            <span className="text-sm text-sketch-ink-secondary font-handwritten">
              {t('status.ac')}
            </span>
          </div>
          <span
            className="
              px-2 py-1
              bg-sketch-accent-cyan/20
              border border-sketch-accent-cyan
              text-sm font-bold text-sketch-accent-cyan font-handwritten
            "
            style={{ borderRadius: '4px' }}
          >
            {player.ac}
          </span>
        </div>
      </div>
    </div>
  );
};

export default PlayerStatus;
