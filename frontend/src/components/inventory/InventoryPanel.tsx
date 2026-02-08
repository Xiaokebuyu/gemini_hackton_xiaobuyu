/**
 * Inventory Panel - Placeholder for future implementation
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Package } from 'lucide-react';

export const InventoryPanel: React.FC = () => {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col items-center justify-center py-12 text-g-text-muted">
      <Package className="w-10 h-10 mb-3" />
      <p className="text-sm">{t('inventory.comingSoon', '功能开发中')}</p>
    </div>
  );
};

export default InventoryPanel;
