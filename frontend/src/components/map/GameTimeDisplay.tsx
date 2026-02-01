/**
 * Game time display component
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Sun, Moon, Sunrise, Sunset } from 'lucide-react';
import { useGameStore } from '../../stores';
import type { GameTimeState } from '../../types';

interface GameTimeDisplayProps {
  time?: GameTimeState;
  className?: string;
}

const periodConfig = {
  dawn: {
    icon: <Sunrise className="w-5 h-5" />,
    labelKey: 'time.period.dawn',
    color: 'text-time-dawn',
    bgColor: 'bg-time-dawn/10',
  },
  day: {
    icon: <Sun className="w-5 h-5" />,
    labelKey: 'time.period.day',
    color: 'text-time-day',
    bgColor: 'bg-time-day/10',
  },
  dusk: {
    icon: <Sunset className="w-5 h-5" />,
    labelKey: 'time.period.dusk',
    color: 'text-time-dusk',
    bgColor: 'bg-time-dusk/10',
  },
  night: {
    icon: <Moon className="w-5 h-5" />,
    labelKey: 'time.period.night',
    color: 'text-time-night',
    bgColor: 'bg-time-night/10',
  },
};

export const GameTimeDisplay: React.FC<GameTimeDisplayProps> = ({
  time,
  className = '',
}) => {
  const { t } = useTranslation();
  const { gameTime: storeTime } = useGameStore();
  const gameTime = time || storeTime;

  const period = gameTime.period || 'day';
  const config = periodConfig[period] || periodConfig.day;

  // Format time string
  const formatTime = (hour: number, minute: number): string => {
    return `${hour.toString().padStart(2, '0')}:${minute
      .toString()
      .padStart(2, '0')}`;
  };

  return (
    <div className={`p-3 ${className}`}>
      <div className="flex items-center justify-between">
        {/* Day and time */}
        <div className="flex flex-col">
          <span className="text-xs text-[var(--color-text-muted)]">
            {t('time.day', { day: gameTime.day })}
          </span>
          <span className="text-lg font-mono font-bold text-[var(--color-text-primary)]">
            {gameTime.formatted || formatTime(gameTime.hour, gameTime.minute)}
          </span>
        </div>

        {/* Period indicator */}
        <div
          className={`
            flex items-center gap-2
            px-3 py-2
            rounded-lg
            ${config.bgColor}
          `}
        >
          <span className={config.color}>{config.icon}</span>
          <span className={`text-sm font-medium ${config.color}`}>
            {t(config.labelKey)}
          </span>
        </div>
      </div>

      {/* Visual time bar */}
      <div className="mt-3 h-1 bg-bg-secondary rounded-full overflow-hidden">
        <div
          className={`h-full ${config.bgColor.replace('/10', '')}`}
          style={{
            width: `${((gameTime.hour * 60 + gameTime.minute) / 1440) * 100}%`,
          }}
        />
      </div>
    </div>
  );
};

export default GameTimeDisplay;
