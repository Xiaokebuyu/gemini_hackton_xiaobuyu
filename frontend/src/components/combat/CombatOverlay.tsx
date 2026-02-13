/**
 * Full-screen combat overlay
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Swords, Trophy, Skull } from 'lucide-react';
import { useCombatStore, useGameStore } from '../../stores';
import { useCombatAction, useEndCombat } from '../../api';
import CombatArena from './CombatArena';
import ActionOptionList from './ActionOptionList';
import CombatLog from './CombatLog';
import TurnOrder from './TurnOrder';
import { PanelFrame } from '../layout';

interface CombatOverlayProps {
  className?: string;
}

export const CombatOverlay: React.FC<CombatOverlayProps> = ({
  className = '',
}) => {
  const {
    combatState,
    selectedAction,
    selectedTarget,
    selectAction,
    selectTarget,
    clearSelection,
    canAct,
  } = useCombatStore();
  const { setCombatId } = useGameStore();
  const { executeAction, isLoading } = useCombatAction();
  const { endCombat } = useEndCombat();

  // Get real actions from combat state
  const actions = combatState?.player_actions || [];

  // Handle action execution
  const handleExecuteAction = () => {
    if (!selectedAction || isLoading) return;

    // Check if target is needed
    const actionDef = actions.find((a) => a.action_type === selectedAction);
    if (actionDef?.requires_target && !selectedTarget) {
      return;
    }

    executeAction({
      action_id: selectedAction,
    });

    clearSelection();
  };

  // Handle flee/end combat
  const handleFlee = () => {
    endCombat(undefined);
  };

  if (!combatState) return null;

  const playerCanAct = canAct();

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className={`
          fixed inset-0 z-40
          bg-g-bg-base/95 backdrop-blur-sm
          overflow-hidden
          ${className}
        `}
      >
        {/* Header */}
        <div className="h-16 border-b border-[var(--g-border-default)] bg-g-bg-sidebar/50">
          <div className="h-full max-w-7xl mx-auto px-4 flex items-center justify-between">
            {/* Title */}
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-g-red/20 flex items-center justify-center">
                <Swords className="w-5 h-5 text-g-red" />
              </div>
              <div>
                <h1 className="font-heading text-xl text-g-red">
                  Combat
                </h1>
                <p className="text-xs text-[var(--g-text-muted)]">
                  Round {combatState.current_round}
                </p>
              </div>
            </div>

            {/* Turn order */}
            <div className="flex-1 mx-8 overflow-hidden">
              <TurnOrder
                combatants={combatState.combatants}
                currentTurnId={combatState.active_combatant_id}
              />
            </div>

            {/* Close/flee button */}
            <button
              onClick={handleFlee}
              className="
                p-2 rounded-lg
                text-[var(--g-text-muted)]
                hover:text-g-red hover:bg-g-red/10
                transition-colors
              "
              title="Attempt to flee"
            >
              <X className="w-6 h-6" />
            </button>
          </div>
        </div>

        {/* Main content */}
        <div className="h-[calc(100vh-4rem)] flex">
          {/* Combat arena */}
          <div className="flex-1 overflow-y-auto p-6">
            <CombatArena
              onSelectTarget={(id) => selectTarget(id === selectedTarget ? null : id)}
            />
          </div>

          {/* Right sidebar */}
          <div className="w-80 border-l border-[var(--g-border-default)] flex flex-col">
            {/* Actions panel */}
            <PanelFrame className="flex-1 m-3 mb-0 overflow-hidden">
              <div className="h-full flex flex-col">
                <div className="p-3 border-b border-[var(--g-border-default)]">
                  <h3 className="text-sm font-heading text-g-gold">
                    {playerCanAct ? 'Your Actions' : 'Waiting...'}
                  </h3>
                </div>
                <div className="flex-1 overflow-y-auto g-scrollbar p-3">
                  {playerCanAct && actions.length > 0 ? (
                    <ActionOptionList
                      actions={actions}
                      onSelectAction={selectAction}
                      selectedAction={selectedAction}
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-[var(--g-text-muted)]">
                      {playerCanAct ? 'Waiting for actions...' : 'Waiting for other combatants...'}
                    </div>
                  )}
                </div>

                {/* Execute button */}
                {playerCanAct && selectedAction && (
                  <div className="p-3 border-t border-[var(--g-border-default)]">
                    <button
                      onClick={handleExecuteAction}
                      disabled={isLoading || (actions.find(a => a.action_type === selectedAction)?.requires_target && !selectedTarget)}
                      className="
                        w-full
                        py-3
                        bg-g-gold text-g-bg-base
                        rounded-lg
                        font-bold
                        hover:shadow-g-gold
                        disabled:opacity-50 disabled:cursor-not-allowed
                        transition-all
                      "
                    >
                      {isLoading ? 'Executing...' : 'Execute Action'}
                    </button>
                    {actions.find(a => a.action_type === selectedAction)?.requires_target && !selectedTarget && (
                      <p className="text-xs text-g-red mt-2 text-center">
                        Select a target first
                      </p>
                    )}
                  </div>
                )}
              </div>
            </PanelFrame>

            {/* Combat log */}
            <PanelFrame className="h-64 m-3 overflow-hidden">
              <CombatLog entries={combatState.combat_log} />
            </PanelFrame>
          </div>
        </div>

        {/* Victory/defeat overlay */}
        {combatState.is_victory !== null && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="
              absolute inset-0
              flex items-center justify-center
              bg-g-bg-base/80 backdrop-blur-sm
            "
          >
            <div className="text-center">
              <div className="mb-4 flex justify-center">
                {combatState.is_victory
                  ? <Trophy className="w-12 h-12 text-g-gold" />
                  : <Skull className="w-12 h-12 text-g-red" />
                }
              </div>
              <h2
                className={`
                  font-heading text-4xl mb-4
                  ${combatState.is_victory ? 'text-g-gold' : 'text-g-red'}
                `}
              >
                {combatState.is_victory ? 'Victory!' : 'Defeat'}
              </h2>
              {combatState.rewards && combatState.is_victory && (
                <div className="text-[var(--g-text-secondary)] mb-6">
                  <p>+{combatState.rewards.experience} XP</p>
                  <p>+{combatState.rewards.gold} Gold</p>
                </div>
              )}
              <button
                onClick={() => setCombatId(null)}
                className="btn-primary"
              >
                Continue
              </button>
            </div>
          </motion.div>
        )}
      </motion.div>
    </AnimatePresence>
  );
};

export default CombatOverlay;
