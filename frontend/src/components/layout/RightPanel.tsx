/**
 * Right Panel - Player Status & Party with tabs (Party/Inventory)
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useUIStore } from '../../stores';
import PanelFrame from './PanelFrame';
import PlayerStatus from '../party/PlayerStatus';
import PartyPanel from '../party/PartyPanel';
import InventoryPanel from '../inventory/InventoryPanel';

type RightTab = 'party' | 'inventory';

interface RightPanelProps {
  className?: string;
}

export const RightPanel: React.FC<RightPanelProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const { rightPanelCollapsed, toggleRightPanel } = useUIStore();
  const [activeTab, setActiveTab] = useState<RightTab>('party');

  const tabs: { key: RightTab; label: string }[] = [
    { key: 'party', label: t('tabs.party', '队伍') },
    { key: 'inventory', label: t('tabs.inventory', '背包') },
  ];

  return (
    <div className={`relative flex ${className}`}>
      {/* Collapse toggle button */}
      <button
        onClick={toggleRightPanel}
        className="
          absolute -left-3 top-1/2 -translate-y-1/2 z-20
          w-5 h-8
          bg-g-bg-surface border border-g-border
          rounded-l-lg shadow-g-sm
          flex items-center justify-center
          hover:bg-g-bg-hover transition-colors
        "
        aria-label={rightPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
      >
        {rightPanelCollapsed ? (
          <ChevronLeft className="w-4 h-4 text-g-gold" />
        ) : (
          <ChevronRight className="w-4 h-4 text-g-gold" />
        )}
      </button>

      {/* Panel content */}
      <AnimatePresence mode="wait">
        {!rightPanelCollapsed && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 300, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="w-[300px] h-full flex flex-col gap-2 p-3">
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
              {activeTab === 'party' && (
                <>
                  {/* Player Status */}
                  <PanelFrame className="flex-shrink-0">
                    <PlayerStatus />
                  </PanelFrame>

                  {/* Party Panel */}
                  <PanelFrame className="flex-1 min-h-0 overflow-hidden">
                    <PartyPanel />
                  </PanelFrame>
                </>
              )}

              {activeTab === 'inventory' && (
                <PanelFrame className="flex-1 min-h-0 overflow-hidden">
                  <InventoryPanel />
                </PanelFrame>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default RightPanel;
