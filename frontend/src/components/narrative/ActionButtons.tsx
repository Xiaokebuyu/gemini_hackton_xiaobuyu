/**
 * Available action buttons component - using Sketch style
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import {
  MapPin,
  User,
  Eye,
  Swords,
  Users,
  Package,
  Settings,
} from 'lucide-react';
import { useGameStore } from '../../stores';
import { useGameInput } from '../../api';
import type { GameAction, ActionCategory } from '../../types';

interface ActionButtonsProps {
  actions?: GameAction[];
  className?: string;
}

const categoryConfig: Record<
  ActionCategory,
  { icon: React.ReactNode; color: string; labelKey: string }
> = {
  movement: {
    icon: <MapPin className="w-4 h-4" />,
    color: 'text-sketch-accent-cyan border-sketch-accent-cyan hover:bg-sketch-accent-cyan/10',
    labelKey: 'actions.category.movement',
  },
  interaction: {
    icon: <User className="w-4 h-4" />,
    color: 'text-sketch-accent-gold border-sketch-accent-gold hover:bg-sketch-accent-gold/10',
    labelKey: 'actions.category.interaction',
  },
  observation: {
    icon: <Eye className="w-4 h-4" />,
    color: 'text-sketch-accent-purple border-sketch-accent-purple hover:bg-sketch-accent-purple/10',
    labelKey: 'actions.category.observation',
  },
  combat: {
    icon: <Swords className="w-4 h-4" />,
    color: 'text-sketch-accent-red border-sketch-accent-red hover:bg-sketch-accent-red/10',
    labelKey: 'actions.category.combat',
  },
  party: {
    icon: <Users className="w-4 h-4" />,
    color: 'text-sketch-accent-green border-sketch-accent-green hover:bg-sketch-accent-green/10',
    labelKey: 'actions.category.party',
  },
  inventory: {
    icon: <Package className="w-4 h-4" />,
    color: 'text-sketch-accent-gold border-sketch-accent-gold hover:bg-sketch-accent-gold/10',
    labelKey: 'actions.category.inventory',
  },
  system: {
    icon: <Settings className="w-4 h-4" />,
    color: 'text-sketch-ink-secondary border-sketch-ink-secondary hover:bg-sketch-bg-secondary',
    labelKey: 'actions.category.system',
  },
};

export const ActionButtons: React.FC<ActionButtonsProps> = ({
  actions,
  className = '',
}) => {
  const { t } = useTranslation();
  const { availableActions } = useGameStore();
  const { sendInput, isLoading } = useGameInput();

  const actionsToShow = actions || availableActions;

  if (!actionsToShow || actionsToShow.length === 0) {
    return null;
  }

  // Group actions by category
  const groupedActions = actionsToShow.reduce((acc, action) => {
    const category = action.category;
    if (!acc[category]) {
      acc[category] = [];
    }
    acc[category].push(action);
    return acc;
  }, {} as Record<ActionCategory, GameAction[]>);

  const handleActionClick = (action: GameAction) => {
    if (action.enabled && !isLoading) {
      // Send the action as a command
      sendInput(`[${action.display_name}]`);
    }
  };

  return (
    <div className={`space-y-3 ${className}`}>
      {Object.entries(groupedActions).map(([category, categoryActions]) => {
        const config = categoryConfig[category as ActionCategory];
        if (!config) {
          console.warn(`Unknown action category: ${category}`);
          return null;
        }
        return (
          <motion.div
            key={category}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-2"
          >
            {/* Category label */}
            <div className="flex items-center gap-2 text-xs text-sketch-ink-muted font-body">
              {config.icon}
              <span className="uppercase tracking-wide">
                {t(config.labelKey)}
              </span>
            </div>

            {/* Action buttons */}
            <div className="flex flex-wrap gap-2">
              {categoryActions.map((action) => (
                <motion.button
                  key={action.action_id}
                  whileHover={{ scale: action.enabled ? 1.02 : 1 }}
                  whileTap={{ scale: action.enabled ? 0.98 : 1 }}
                  onClick={() => handleActionClick(action)}
                  disabled={!action.enabled || isLoading}
                  className={`
                    flex items-center gap-2
                    px-3 py-2
                    bg-sketch-bg-panel
                    border
                    text-sm font-body
                    transition-all duration-200
                    ${action.enabled ? config.color : 'text-sketch-ink-muted border-sketch-ink-faint opacity-50 cursor-not-allowed'}
                  `}
                  style={{ borderRadius: '8px' }}
                  title={action.description || action.display_name}
                >
                  {action.icon && <span>{action.icon}</span>}
                  <span>{action.display_name}</span>
                  {action.hotkey && (
                    <kbd className="ml-1 px-1.5 py-0.5 text-xs bg-sketch-bg-secondary border border-sketch-ink-faint">
                      {action.hotkey}
                    </kbd>
                  )}
                </motion.button>
              ))}
            </div>
          </motion.div>
        );
      })}
    </div>
  );
};

export default ActionButtons;
