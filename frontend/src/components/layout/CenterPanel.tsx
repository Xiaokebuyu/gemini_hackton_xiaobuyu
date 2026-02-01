/**
 * Center Panel - Narrative & Input
 */
import React from 'react';
import PanelFrame from './PanelFrame';
import NarrativeFlow from '../narrative/NarrativeFlow';
import ChatInput from '../input/ChatInput';
import QuickActions from '../input/QuickActions';

interface CenterPanelProps {
  className?: string;
}

export const CenterPanel: React.FC<CenterPanelProps> = ({ className = '' }) => {
  return (
    <div className={`flex flex-col h-full ${className}`}>
      {/* Narrative Flow */}
      <PanelFrame className="flex-1 min-h-0 overflow-hidden">
        <NarrativeFlow />
      </PanelFrame>

      {/* Input Area */}
      <div className="flex-shrink-0 mt-3">
        <PanelFrame>
          <div className="p-4">
            <ChatInput />
            <QuickActions className="mt-3" />
          </div>
        </PanelFrame>
      </div>
    </div>
  );
};

export default CenterPanel;
