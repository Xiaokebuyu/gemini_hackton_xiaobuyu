/**
 * Left Panel - Map only (Quest/History moved to RightPanel overlay)
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useUIStore } from '../../stores';
import { useLocation } from '../../api/hooks/useLocation';
import PanelFrame from './PanelFrame';
import MiniMap from '../map/MiniMap';
import LocationCard from '../map/LocationCard';
import SubLocationList from '../map/SubLocationList';
import DestinationList from '../map/DestinationList';
import GameTimeDisplay from '../map/GameTimeDisplay';

interface LeftPanelProps {
  className?: string;
}

export const LeftPanel: React.FC<LeftPanelProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const { leftPanelCollapsed, toggleLeftPanel } = useUIStore();
  const { location } = useLocation();

  return (
    <div className={`relative flex ${className}`}>
      {/* Collapse toggle button */}
      <button
        onClick={toggleLeftPanel}
        className="
          absolute -right-3 top-1/2 -translate-y-1/2 z-20
          w-5 h-8
          bg-g-bg-surface border border-g-border
          rounded-r-lg shadow-g-sm
          flex items-center justify-center
          hover:bg-g-bg-hover transition-colors
        "
        aria-label={leftPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      >
        {leftPanelCollapsed ? (
          <ChevronRight className="w-4 h-4 text-g-gold" />
        ) : (
          <ChevronLeft className="w-4 h-4 text-g-gold" />
        )}
      </button>

      {/* Panel content */}
      <AnimatePresence mode="wait">
        {!leftPanelCollapsed && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 260, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="w-[260px] h-full flex flex-col gap-2 p-3">
              {/* Mini Map */}
              <PanelFrame className="flex-shrink-0">
                <MiniMap />
              </PanelFrame>

              {/* Location Card */}
              <PanelFrame className="flex-shrink-0">
                <LocationCard />
              </PanelFrame>

              {/* Sub Locations */}
              <PanelFrame className="flex-1 min-h-0 overflow-hidden">
                <div className="h-full flex flex-col">
                  <h3 className="text-sm font-heading text-g-gold px-3 pt-3 pb-2">
                    {t('navigation.subLocations')}
                  </h3>
                  <div className="flex-1 overflow-y-auto g-scrollbar px-3 pb-3">
                    <SubLocationList subLocations={location?.available_sub_locations} />
                    <div className="mt-3 pt-3 border-t border-g-border">
                      <DestinationList />
                    </div>
                  </div>
                </div>
              </PanelFrame>

              {/* Game Time */}
              <PanelFrame className="flex-shrink-0">
                <GameTimeDisplay />
              </PanelFrame>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default LeftPanel;
