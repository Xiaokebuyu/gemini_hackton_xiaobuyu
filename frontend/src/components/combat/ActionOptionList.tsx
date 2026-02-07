/**
 * Combat action options list - using Golden theme style
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import {
  Sword,
  Shield,
  Zap,
  Move,
  Package,
  LogOut,
  ChevronRight,
} from 'lucide-react';
import type { CombatActionType, CombatActionOption } from '../../types';

interface ActionOptionListProps {
  actions: CombatActionOption[];
  onSelectAction: (action: CombatActionType) => void;
  selectedAction: CombatActionType | null;
  className?: string;
}

const actionIcons: Record<CombatActionType, React.ReactNode> = {
  ATTACK: <Sword className="w-4 h-4" />,
  OFFHAND: <Sword className="w-4 h-4" />,
  THROW: <Zap className="w-4 h-4" />,
  SHOVE: <Move className="w-4 h-4" />,
  SPELL: <Zap className="w-4 h-4" />,
  DEFEND: <Shield className="w-4 h-4" />,
  MOVE: <Move className="w-4 h-4" />,
  DASH: <Move className="w-4 h-4" />,
  DISENGAGE: <LogOut className="w-4 h-4" />,
  USE_ITEM: <Package className="w-4 h-4" />,
  FLEE: <LogOut className="w-4 h-4" />,
  END_TURN: <ChevronRight className="w-4 h-4" />,
};

const actionColors: Record<CombatActionType, string> = {
  ATTACK: 'border-g-red hover:bg-g-red/10',
  OFFHAND: 'border-g-danger-high hover:bg-g-danger-high/10',
  THROW: 'border-g-danger-medium hover:bg-g-danger-medium/10',
  SHOVE: 'border-g-purple hover:bg-g-purple/10',
  SPELL: 'border-g-purple hover:bg-g-purple/10',
  DEFEND: 'border-g-cyan hover:bg-g-cyan/10',
  MOVE: 'border-g-gold hover:bg-g-gold/10',
  DASH: 'border-g-gold hover:bg-g-gold/10',
  DISENGAGE: 'border-g-text-secondary hover:bg-g-bg-sidebar',
  USE_ITEM: 'border-g-green hover:bg-g-green/10',
  FLEE: 'border-g-danger-extreme hover:bg-g-danger-extreme/10',
  END_TURN: 'border-g-text-muted hover:bg-g-bg-sidebar',
};

export const ActionOptionList: React.FC<ActionOptionListProps> = ({
  actions,
  onSelectAction,
  selectedAction,
  className = '',
}) => {
  const { t } = useTranslation();

  // Group actions by category
  const attackActions = actions.filter((a) =>
    ['ATTACK', 'OFFHAND', 'THROW', 'SHOVE', 'SPELL'].includes(a.action_type)
  );
  const defenseActions = actions.filter((a) =>
    ['DEFEND', 'DISENGAGE'].includes(a.action_type)
  );
  const moveActions = actions.filter((a) =>
    ['MOVE', 'DASH'].includes(a.action_type)
  );
  const otherActions = actions.filter((a) =>
    ['USE_ITEM', 'FLEE', 'END_TURN'].includes(a.action_type)
  );

  const renderActionGroup = (
    titleKey: string,
    groupActions: CombatActionOption[]
  ) => {
    if (groupActions.length === 0) return null;

    return (
      <div className="mb-4">
        <h4 className="text-xs text-g-text-muted uppercase tracking-wide mb-2 font-body">
          {t(titleKey)}
        </h4>
        <div className="grid grid-cols-2 gap-2">
          {groupActions.map((action, index) => (
            <motion.button
              key={action.action_type}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.05 }}
              onClick={() => onSelectAction(action.action_type)}
              disabled={!action.enabled}
              className={`
                flex items-center gap-2
                px-3 py-2
                bg-g-bg-surface
                border
                text-left font-body
                transition-all duration-200
                ${
                  selectedAction === action.action_type
                    ? 'border-g-gold bg-g-gold/10'
                    : actionColors[action.action_type]
                }
                ${
                  !action.enabled
                    ? 'opacity-50 cursor-not-allowed'
                    : 'cursor-pointer'
                }
              `}
              style={{ borderRadius: '8px' }}
            >
              <span
                className={
                  selectedAction === action.action_type
                    ? 'text-g-gold'
                    : ''
                }
              >
                {actionIcons[action.action_type]}
              </span>
              <div className="flex-1 min-w-0">
                <div
                  className={`
                    text-sm font-medium truncate
                    ${
                      selectedAction === action.action_type
                        ? 'text-g-gold'
                        : 'text-g-text-primary'
                    }
                  `}
                >
                  {action.display_name}
                </div>
                {action.requires && (
                  <div className="text-xs text-g-text-muted truncate">
                    {t('combat.requires')}: {action.requires}
                  </div>
                )}
              </div>
            </motion.button>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className={className}>
      {renderActionGroup('combat.attack', attackActions)}
      {renderActionGroup('combat.defense', defenseActions)}
      {renderActionGroup('combat.movement', moveActions)}
      {renderActionGroup('combat.other', otherActions)}
    </div>
  );
};

export default ActionOptionList;
