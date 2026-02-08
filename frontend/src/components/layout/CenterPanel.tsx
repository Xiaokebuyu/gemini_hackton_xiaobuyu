/**
 * Center Panel - Galgame-style fixed display
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import PanelFrame from './PanelFrame';
import GalgameDisplay from '../narrative/GalgameDisplay';
import ActionButtons from '../narrative/ActionButtons';
import { useGameStore } from '../../stores';

interface CenterPanelProps {
  className?: string;
}

export const CenterPanel: React.FC<CenterPanelProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const { availableActions } = useGameStore();

  return (
    <div className={`flex flex-col h-full ${className}`}>
      {/* Galgame Display (includes ChatInput + QuickActions) */}
      <PanelFrame className="flex-1 min-h-0 overflow-hidden">
        <GalgameDisplay />
      </PanelFrame>

      {/* Available Actions */}
      {availableActions.length > 0 && (
        <div className="flex-shrink-0 mt-3">
          <PanelFrame>
            <div className="p-4">
              <h4 className="text-xs g-text-muted uppercase tracking-wide mb-3 font-body">
                {t('actions.title')}
              </h4>
              <ActionButtons actions={availableActions} />
            </div>
          </PanelFrame>
        </div>
      )}
    </div>
  );
};

export default CenterPanel;
