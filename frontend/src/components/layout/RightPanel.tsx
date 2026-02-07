/**
 * Right Panel - Player Status & Party
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft, ChevronRight, Package } from 'lucide-react';
import { useUIStore } from '../../stores';
import PanelFrame from './PanelFrame';
import PlayerStatus from '../party/PlayerStatus';
import PartyPanel from '../party/PartyPanel';

interface RightPanelProps {
  className?: string;
}

export const RightPanel: React.FC<RightPanelProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const { rightPanelCollapsed, toggleRightPanel } = useUIStore();

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
              {/* Player Status */}
              <PanelFrame className="flex-shrink-0">
                <PlayerStatus />
              </PanelFrame>

              {/* Party Panel */}
              <PanelFrame className="flex-1 min-h-0 overflow-hidden">
                <PartyPanel />
              </PanelFrame>

              {/* Inventory placeholder */}
              <PanelFrame className="flex-shrink-0">
                <div className="p-3">
                  <h3 className="text-sm font-heading text-g-gold mb-2">
                    {t('status.inventory')}
                  </h3>
                  <div className="flex flex-col items-center py-4">
                    <Package className="w-8 h-8 text-g-text-muted mb-2" />
                    <p className="text-xs text-g-text-muted">
                      {t('common.loading')}
                    </p>
                  </div>
                </div>
              </PanelFrame>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default RightPanel;
