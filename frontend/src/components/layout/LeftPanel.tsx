/**
 * Left Panel - Map & Navigation with tabs (Map/Quest/History)
 */
import React, { useState } from 'react';
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
import QuestPanel from '../quest/QuestPanel';
import HistoryPanel from '../history/HistoryPanel';

type LeftTab = 'map' | 'quest' | 'history';

interface LeftPanelProps {
  className?: string;
}

export const LeftPanel: React.FC<LeftPanelProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const { leftPanelCollapsed, toggleLeftPanel } = useUIStore();
  const [activeTab, setActiveTab] = useState<LeftTab>('map');

  const tabs: { key: LeftTab; label: string }[] = [
    { key: 'map', label: t('tabs.map', '地图') },
    { key: 'quest', label: t('tabs.quest', '任务') },
    { key: 'history', label: t('tabs.history', '记录') },
  ];

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
              {/* Tab buttons */}
              <div className="flex gap-3 px-1">
                {tabs.map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`text-xs pb-1 border-b-2 transition-colors ${
                      activeTab === tab.key
                        ? 'text-g-gold border-g-gold font-semibold'
                        : 'text-g-text-muted border-transparent hover:text-g-text'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              {activeTab === 'map' && (
                <>
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
                        <SubLocationList />
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
                </>
              )}

              {activeTab === 'quest' && (
                <PanelFrame className="flex-1 min-h-0 overflow-hidden">
                  <QuestPanel />
                </PanelFrame>
              )}

              {activeTab === 'history' && (
                <PanelFrame className="flex-1 min-h-0 overflow-hidden">
                  <HistoryPanel />
                </PanelFrame>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default LeftPanel;
