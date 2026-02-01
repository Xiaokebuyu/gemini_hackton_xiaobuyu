/**
 * Mini map component (placeholder with visual)
 */
import React from 'react';
import { Map, MapPin, Compass } from 'lucide-react';
import { useGameStore } from '../../stores';

interface MiniMapProps {
  className?: string;
}

export const MiniMap: React.FC<MiniMapProps> = ({ className = '' }) => {
  const { location } = useGameStore();

  return (
    <div className={`p-3 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Map className="w-4 h-4 text-accent-gold" />
          <h3 className="text-sm font-fantasy text-accent-gold">Map</h3>
        </div>
        <Compass className="w-4 h-4 text-[var(--color-text-muted)]" />
      </div>

      {/* Map area */}
      <div
        className="
          relative
          aspect-square
          bg-bg-secondary
          rounded-lg
          overflow-hidden
          border border-[var(--color-border-secondary)]
        "
      >
        {/* Grid pattern */}
        <div
          className="absolute inset-0 opacity-20"
          style={{
            backgroundImage: `
              linear-gradient(rgba(255, 215, 0, 0.1) 1px, transparent 1px),
              linear-gradient(90deg, rgba(255, 215, 0, 0.1) 1px, transparent 1px)
            `,
            backgroundSize: '20px 20px',
          }}
        />

        {/* Current location marker */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="relative">
            {/* Pulse effect */}
            <div
              className="
                absolute inset-0
                bg-accent-gold/30
                rounded-full
                animate-ping
              "
              style={{ width: '40px', height: '40px', margin: '-12px' }}
            />
            {/* Marker */}
            <div
              className="
                w-4 h-4
                bg-accent-gold
                rounded-full
                border-2 border-white
                shadow-glow-gold
              "
            />
          </div>
        </div>

        {/* Location name overlay */}
        {location && (
          <div
            className="
              absolute bottom-2 left-2 right-2
              bg-bg-primary/80
              backdrop-blur-sm
              rounded-md
              p-2
              text-center
            "
          >
            <div className="flex items-center justify-center gap-1">
              <MapPin className="w-3 h-3 text-accent-gold" />
              <span className="text-xs font-medium text-[var(--color-text-primary)] truncate">
                {location.location_name}
              </span>
            </div>
          </div>
        )}

        {/* Placeholder text if no location */}
        {!location && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-xs text-[var(--color-text-muted)]">
              No map data
            </span>
          </div>
        )}
      </div>

      {/* Mini legend */}
      <div className="mt-2 flex items-center justify-center gap-4 text-xs text-[var(--color-text-muted)]">
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-full bg-accent-gold" />
          <span>You</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-full bg-accent-cyan" />
          <span>POI</span>
        </div>
      </div>
    </div>
  );
};

export default MiniMap;
