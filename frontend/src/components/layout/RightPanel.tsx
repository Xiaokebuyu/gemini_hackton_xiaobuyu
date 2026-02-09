/**
 * Right Panel - Overlay half-screen panel with 4 tabs (Party/Inventory/Quest/History)
 * Slides in from the right, covers 50% of the screen width.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { useUIStore } from '../../stores';
import { useParty } from '../../api/hooks/useParty';
import PanelFrame from './PanelFrame';
import PlayerStatus from '../party/PlayerStatus';
import PartyPanel from '../party/PartyPanel';
import InventoryPanel from '../inventory/InventoryPanel';
import QuestPanel from '../quest/QuestPanel';
import HistoryPanel from '../history/HistoryPanel';

type RightTab = 'party' | 'inventory' | 'quest' | 'history';

export const RightPanel: React.FC = () => {
  const { t } = useTranslation();
  const { rightPanelCollapsed, toggleRightPanel } = useUIStore();
  useParty();
  const [activeTab, setActiveTab] = useState<RightTab>('party');

  const isOpen = !rightPanelCollapsed;

  const tabs: { key: RightTab; label: string }[] = [
    { key: 'party', label: t('tabs.party', '队伍') },
    { key: 'inventory', label: t('tabs.inventory', '背包') },
    { key: 'quest', label: t('tabs.quest', '任务') },
    { key: 'history', label: t('tabs.history', '记录') },
  ];

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop overlay */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 top-12 bg-black/20 z-40"
            onClick={toggleRightPanel}
          />

          {/* Slide-in panel */}
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="
              fixed top-12 right-0 bottom-0 w-1/2
              bg-g-bg-surface
              border-l border-g-border
              shadow-g-lg
              z-50
              flex flex-col
            "
          >
            {/* Header with close button and tabs */}
            <div className="flex-shrink-0 border-b border-g-border px-4 pt-3 pb-0">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-heading text-g-gold">
                  {t('ui.rightPanel')}
                </h2>
                <button
                  onClick={toggleRightPanel}
                  className="
                    w-7 h-7 rounded-lg
                    flex items-center justify-center
                    text-g-text-muted hover:text-g-gold
                    hover:bg-g-bg-hover
                    transition-colors
                  "
                  aria-label="Close panel"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Tab bar */}
              <div className="flex gap-4">
                {tabs.map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`text-xs pb-2 border-b-2 transition-colors ${
                      activeTab === tab.key
                        ? 'text-g-gold border-g-gold font-semibold'
                        : 'text-g-text-muted border-transparent hover:text-g-text'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Tab content — scrollable area */}
            <div className="flex-1 min-h-0 overflow-y-auto g-scrollbar p-3">
              {activeTab === 'party' && (
                <div className="space-y-2">
                  <PanelFrame className="flex-shrink-0">
                    <PlayerStatus />
                  </PanelFrame>
                  <PanelFrame>
                    <PartyPanel />
                  </PanelFrame>
                </div>
              )}

              {activeTab === 'inventory' && (
                <PanelFrame>
                  <InventoryPanel />
                </PanelFrame>
              )}

              {activeTab === 'quest' && (
                <PanelFrame>
                  <QuestPanel />
                </PanelFrame>
              )}

              {activeTab === 'history' && (
                <PanelFrame>
                  <HistoryPanel />
                </PanelFrame>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};

export default RightPanel;
