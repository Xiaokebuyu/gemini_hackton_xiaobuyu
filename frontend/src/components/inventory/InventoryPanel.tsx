/**
 * Inventory Panel — clean item list with separators
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Package } from 'lucide-react';
import { useGameStore } from '../../stores';

export const InventoryPanel: React.FC = () => {
  const { t } = useTranslation();
  const { inventoryItems, inventoryItemCount, latestInventoryUpdate } = useGameStore();
  const normalizedItems = inventoryItems.map((item, index) => {
    const itemId = typeof item.item_id === 'string' ? item.item_id : `item_${index}`;
    const name = typeof item.name === 'string'
      ? item.name
      : (typeof item.item_name === 'string' ? item.item_name : itemId);
    const quantity = typeof item.quantity === 'number' ? item.quantity : 1;
    return {
      id: itemId,
      name,
      quantity,
      description: typeof item.description === 'string' ? item.description : '',
    };
  });

  if (inventoryItemCount <= 0 || normalizedItems.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-g-text-muted">
        <Package className="w-8 h-8 mb-3 opacity-40" />
        <p className="text-xs">{t('inventory.empty', '背包为空')}</p>
      </div>
    );
  }

  return (
    <div className="px-5 py-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-xs font-heading text-[var(--g-accent-gold)] tracking-wide uppercase">
          {t('inventory.title', '背包')}
        </h4>
        <span className="text-[11px] text-g-text-muted tabular-nums">
          {inventoryItemCount} {t('inventory.items', 'items')}
        </span>
      </div>

      {/* Latest change */}
      {latestInventoryUpdate && (
        <div className="mb-4 py-2 border-t border-b border-[var(--g-accent-gold)]/15 text-xs text-g-text-muted">
          <span className="text-[var(--g-accent-gold)] mr-1.5">
            {typeof latestInventoryUpdate.action === 'string' ? latestInventoryUpdate.action : '-'}
          </span>
          {typeof latestInventoryUpdate.item_name === 'string'
            ? latestInventoryUpdate.item_name
            : (typeof latestInventoryUpdate.item_id === 'string' ? latestInventoryUpdate.item_id : '')}
          {typeof latestInventoryUpdate.quantity === 'number' && (
            <span className="text-g-text-muted/60 ml-1">x{latestInventoryUpdate.quantity}</span>
          )}
        </div>
      )}

      {/* Item list */}
      <div>
        {normalizedItems.map((item, index) => (
          <div
            key={item.id}
            className={`
              flex items-baseline justify-between py-2.5
              ${index < normalizedItems.length - 1 ? 'border-b border-[var(--g-accent-gold)]/8' : ''}
            `}
          >
            <div className="flex-1 min-w-0">
              <span className="text-sm text-[var(--g-text-primary)]">{item.name}</span>
              {item.description && (
                <p className="text-[11px] text-g-text-muted mt-0.5 line-clamp-1">{item.description}</p>
              )}
            </div>
            <span className="text-xs text-g-text-muted tabular-nums ml-3 flex-shrink-0">
              x{item.quantity}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default InventoryPanel;
