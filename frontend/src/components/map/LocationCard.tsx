/**
 * Current location card component
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { MapPin, ArrowUp } from 'lucide-react';
import { useGameStore } from '../../stores';
import { useStreamGameInput } from '../../api';
import type { LocationResponse } from '../../types';

interface LocationCardProps {
  location?: LocationResponse;
  className?: string;
}

export const LocationCard: React.FC<LocationCardProps> = ({
  location,
  className = '',
}) => {
  const { t } = useTranslation();
  const { location: storeLocation, subLocation } = useGameStore();
  const { sendInput, isLoading } = useStreamGameInput();

  const currentLocation = location || storeLocation;

  const handleLeaveSubLocation = () => {
    if (subLocation && !isLoading) {
      sendInput('[离开]');
    }
  };

  if (!currentLocation) {
    return (
      <div className={`p-3 ${className}`}>
        <div className="flex items-center gap-2 text-[var(--g-text-muted)]">
          <MapPin className="w-4 h-4" />
          <span className="text-sm">{t('navigation.currentLocation')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`p-3 panel-hover ${className}`}>
      {/* Location header */}
      <div className="flex items-start gap-2 mb-2">
        <MapPin className="w-5 h-5 text-g-gold flex-shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <h3 className="font-heading text-lg text-g-gold truncate">
            {currentLocation.location_name}
          </h3>
          {subLocation && (
            <div className="flex items-center gap-2 mt-1">
              <span className="text-xs text-[var(--g-text-secondary)]">
                @ {subLocation}
              </span>
              <button
                onClick={handleLeaveSubLocation}
                disabled={isLoading}
                className="
                  flex items-center gap-1
                  px-2 py-0.5 rounded
                  text-xs text-g-cyan
                  bg-g-cyan/10 hover:bg-g-cyan/20
                  transition-all
                  disabled:opacity-50
                "
              >
                <ArrowUp className="w-3 h-3" />
                {t('navigation.leave')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Description */}
      {currentLocation.description && (
        <p className="text-sm text-[var(--g-text-secondary)] line-clamp-3">
          {currentLocation.description}
        </p>
      )}

      {/* NPCs present */}
      {currentLocation.npcs_present?.length > 0 && (
        <div className="mt-3 pt-2 border-t border-[var(--g-border-default)]">
          <span className="text-xs text-[var(--g-text-muted)]">
            Present:{' '}
          </span>
          <span className="text-xs text-g-cyan">
            {currentLocation.npcs_present.join(', ')}
          </span>
        </div>
      )}
    </div>
  );
};

export default LocationCard;
