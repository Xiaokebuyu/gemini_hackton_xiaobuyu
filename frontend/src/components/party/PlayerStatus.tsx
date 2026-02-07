/**
 * Player status component - Golden D&D theme
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { User } from 'lucide-react';

interface PlayerStatusProps {
  className?: string;
}

export const PlayerStatus: React.FC<PlayerStatusProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();

  return (
    <div className={`p-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-3">
        {/* Avatar */}
        <div
          className="
            w-12 h-12 rounded-full
            bg-g-gold
            flex items-center justify-center
            border-2 border-g-border-strong
            shadow-g-sm
          "
        >
          <User className="w-6 h-6 text-g-bg-base" />
        </div>

        {/* Name */}
        <div className="flex-1">
          <h3 className="font-heading text-lg text-g-gold leading-tight">
            {t('status.adventurer', 'Adventurer')}
          </h3>
        </div>
      </div>
    </div>
  );
};

export default PlayerStatus;
