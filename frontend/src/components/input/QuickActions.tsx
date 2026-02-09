/**
 * Quick action shortcuts - Golden theme
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import { Eye, Clock, Search, HelpCircle } from 'lucide-react';
import { useStreamGameInput } from '../../api';
import { useChatStore } from '../../stores';
import { Button } from '../ui';

interface QuickActionsProps {
  className?: string;
}

interface QuickAction {
  id: string;
  labelKey: string;
  command: string;
  icon: React.ReactNode;
}

const quickActionsConfig: QuickAction[] = [
  {
    id: 'look',
    labelKey: 'actions.look',
    command: '[观察周围]',
    icon: <Eye className="w-4 h-4" />,
  },
  {
    id: 'wait',
    labelKey: 'actions.wait',
    command: '[等待]',
    icon: <Clock className="w-4 h-4" />,
  },
  {
    id: 'search',
    labelKey: 'actions.search',
    command: '[搜索]',
    icon: <Search className="w-4 h-4" />,
  },
  {
    id: 'help',
    labelKey: 'actions.help',
    command: '[帮助]',
    icon: <HelpCircle className="w-4 h-4" />,
  },
];

export const QuickActions: React.FC<QuickActionsProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const { sendInput, isLoading } = useStreamGameInput();
  const { isLoading: chatLoading } = useChatStore();

  const handleAction = (action: QuickAction) => {
    if (!isLoading && !chatLoading) {
      sendInput(action.command);
    }
  };

  return (
    <motion.div
      initial="hidden"
      animate="show"
      variants={{
        hidden: { opacity: 0 },
        show: { opacity: 1, transition: { staggerChildren: 0.06 } },
      }}
      className={`flex items-center gap-2 ${className}`}
    >
      <span className="text-xs text-g-text-muted mr-2 font-body">
        {t('actions.quick')}:
      </span>
      {quickActionsConfig.map((action, index) => (
        <motion.div
          key={action.id}
          variants={{
            hidden: { opacity: 0, y: 8 },
            show: { opacity: 1, y: 0 },
          }}
          whileHover={{ scale: 1.03, y: -1 }}
          whileTap={{ scale: 0.97 }}
        >
          <Button
            onClick={() => handleAction(action)}
            disabled={isLoading || chatLoading}
            variant="ghost"
            size="sm"
            title={t(action.labelKey)}
            className="flex items-center gap-1.5 btn-fantasy"
          >
            {action.icon}
            <span>{t(action.labelKey)}</span>
          </Button>
        </motion.div>
      ))}
    </motion.div>
  );
};

export default QuickActions;
