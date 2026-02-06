/**
 * Left Panel - Map & Navigation
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useUIStore } from '../../stores';
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

  return (
    <div className={`relative flex ${className}`}>
      {/* Collapse toggle button */}
      <button
        onClick={toggleLeftPanel}
        className="
          absolute -right-3 top-1/2 -translate-y-1/2 z-20
          w-6 h-12
          bg-sketch-bg-panel border-2 border-sketch-ink-secondary
          rounded-r-lg shadow-parchment-sm
          flex items-center justify-center
          hover:bg-sketch-bg-secondary transition-colors
        "
        aria-label={leftPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      >
        {leftPanelCollapsed ? (
          <ChevronRight className="w-4 h-4 text-sketch-accent-gold" />
        ) : (
          <ChevronLeft className="w-4 h-4 text-sketch-accent-gold" />
        )}
      </button>

      {/* Panel content */}
      <AnimatePresence mode="wait">
        {!leftPanelCollapsed && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 280, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="w-[280px] h-full flex flex-col gap-2 p-3">
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
                  <h3 className="text-sm font-fantasy text-sketch-accent-gold px-3 pt-3 pb-2">
                    {t('navigation.subLocations')}
                  </h3>
                  <div className="flex-1 overflow-y-auto sketch-scrollbar px-3 pb-3">
                    <SubLocationList />
                    <div className="mt-3 pt-3 border-t border-sketch-ink-faint">
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
