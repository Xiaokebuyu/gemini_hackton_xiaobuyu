/**
 * Mini map component - shows current location and available destinations
 */
import React from 'react';
import { Map, MapPin, Compass, Navigation } from 'lucide-react';
import { useGameStore } from '../../stores';

interface MiniMapProps {
  className?: string;
}

// Position destinations around the center marker
const destinationPositions = [
  { top: '10%', left: '50%', transform: 'translateX(-50%)' },   // north
  { top: '50%', right: '8%', transform: 'translateY(-50%)' },   // east
  { bottom: '18%', left: '50%', transform: 'translateX(-50%)' }, // south
  { top: '50%', left: '8%', transform: 'translateY(-50%)' },    // west
  { top: '15%', right: '12%' },                                  // NE
  { bottom: '22%', right: '12%' },                                // SE
];

export const MiniMap: React.FC<MiniMapProps> = ({ className = '' }) => {
  const { location } = useGameStore();
  const destinations = location?.available_destinations || [];

  return (
    <div className={`p-3 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Map className="w-4 h-4 text-g-gold" />
          <h3 className="text-sm font-heading text-g-gold">Map</h3>
        </div>
        <Compass className="w-4 h-4 text-[var(--g-text-muted)]" />
      </div>

      {/* Map area */}
      <div
        className="
          relative
          aspect-square
          bg-g-bg-sidebar
          rounded-xl
          overflow-hidden
          border-2 border-g-border-strong
        "
      >
        {/* Grid pattern */}
        <div
          className="absolute inset-0 opacity-40"
          style={{
            backgroundImage: `
              linear-gradient(rgba(196, 154, 42, 0.08) 1px, transparent 1px),
              linear-gradient(90deg, rgba(196, 154, 42, 0.08) 1px, transparent 1px)
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
                bg-g-gold/30
                rounded-full
                animate-ping
              "
              style={{ width: '40px', height: '40px', margin: '-12px' }}
            />
            {/* Marker */}
            <div
              className="
                w-4 h-4
                bg-g-gold
                rounded-full
                border-2 border-white
                shadow-g-gold
              "
            />
          </div>
        </div>

        {/* Destination markers */}
        {destinations.slice(0, 6).map((dest, i) => (
          <div
            key={dest.location_id}
            className="absolute flex flex-col items-center gap-0.5"
            style={destinationPositions[i]}
            title={dest.description}
          >
            <Navigation className="w-3 h-3 text-g-cyan opacity-70" />
            <span className="text-[9px] text-g-cyan font-body leading-tight text-center max-w-[60px] truncate">
              {dest.name}
            </span>
          </div>
        ))}

        {/* Location name overlay */}
        {location && (
          <div
            className="
              absolute bottom-2 left-2 right-2
              bg-g-bg-base/80
              backdrop-blur-sm
              rounded-lg
              p-2
              text-center
            "
          >
            <div className="flex items-center justify-center gap-1">
              <MapPin className="w-3 h-3 text-g-gold" />
              <span className="text-xs font-medium text-[var(--g-text-primary)] truncate">
                {location.location_name}
              </span>
            </div>
          </div>
        )}

        {/* Placeholder text if no location */}
        {!location && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-xs text-[var(--g-text-muted)]">
              No map data
            </span>
          </div>
        )}
      </div>

      {/* Mini legend */}
      <div className="mt-2 flex items-center justify-center gap-4 text-xs text-[var(--g-text-muted)]">
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-full bg-g-gold" />
          <span>You</span>
        </div>
        {destinations.length > 0 && (
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-g-cyan" />
            <span>Destinations</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default MiniMap;
