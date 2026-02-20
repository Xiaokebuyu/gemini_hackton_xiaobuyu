/**
 * Available action buttons component - Golden D&D theme
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
import { useStreamGameInput } from '../../api';
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
    color: 'text-g-cyan border-g-cyan hover:bg-g-cyan/10',
    labelKey: 'actions.category.movement',
  },
  interaction: {
    icon: <User className="w-4 h-4" />,
    color: 'text-g-gold border-g-gold hover:bg-g-gold/10',
    labelKey: 'actions.category.interaction',
  },
  observation: {
    icon: <Eye className="w-4 h-4" />,
    color: 'text-g-purple border-g-purple hover:bg-g-purple/10',
    labelKey: 'actions.category.observation',
  },
  combat: {
    icon: <Swords className="w-4 h-4" />,
    color: 'text-g-red border-g-red hover:bg-g-red/10',
    labelKey: 'actions.category.combat',
  },
  party: {
    icon: <Users className="w-4 h-4" />,
    color: 'text-g-green border-g-green hover:bg-g-green/10',
    labelKey: 'actions.category.party',
  },
  inventory: {
    icon: <Package className="w-4 h-4" />,
    color: 'text-g-gold border-g-gold hover:bg-g-gold/10',
    labelKey: 'actions.category.inventory',
  },
  system: {
    icon: <Settings className="w-4 h-4" />,
    color: 'text-g-text-secondary border-g-border-strong hover:bg-g-bg-sidebar',
    labelKey: 'actions.category.system',
  },
};

function buildActionInput(action: GameAction): string {
  const rawText = action.display_name.trim();
  if (!rawText) return '';

  // Keep frontend clicks aligned with natural-language intents consumed by V4.
  const text = rawText.replace(/^\[(.*)\]$/, '$1').trim();
  if (action.category === 'movement') {
    const hasMoveVerb = /(前往|进入|离开|返回|去|走|travel|move|go|enter|leave)/i.test(text);
    if (!hasMoveVerb) return `前往${text}`;
  }

  return text;
}

export const ActionButtons: React.FC<ActionButtonsProps> = ({
  actions,
  className = '',
}) => {
  const { t } = useTranslation();
  const { availableActions } = useGameStore();
  const { sendInput, isLoading } = useStreamGameInput();

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
      const input = buildActionInput(action);
      if (input) {
        sendInput(input);
      }
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
            <div className="flex items-center gap-2 text-xs text-g-text-muted font-body">
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
                    bg-g-bg-surface
                    border
                    text-sm font-body
                    transition-all duration-200
                    ${action.enabled ? config.color : 'text-g-text-muted border-g-border opacity-50 cursor-not-allowed'}
                  `}
                  style={{ borderRadius: '8px' }}
                  title={action.description || action.display_name}
                >
                  {action.icon && <span>{action.icon}</span>}
                  <span>{action.display_name}</span>
                  {action.hotkey && (
                    <kbd className="ml-1 px-1.5 py-0.5 text-xs bg-g-bg-sidebar border border-g-border">
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
