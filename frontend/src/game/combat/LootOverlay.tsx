import type { LootDisplayData } from '../../types/sse'

interface Props {
  lootData: LootDisplayData
}

const RARITY_COLOR: Record<string, string> = {
  common: 'text-gray-300',
  uncommon: 'text-green-400',
  rare: 'text-blue-400',
  epic: 'text-purple-400',
  legendary: 'text-yellow-400',
}

export default function LootOverlay({ lootData }: Props) {
  const { items, gold } = lootData
  if (items.length === 0 && gold === 0) return null

  return (
    <div className="mt-4 border-t border-gray-700 pt-3">
      <div className="text-gray-400 text-xs uppercase tracking-wide mb-2">战利品</div>
      <div className="space-y-1">
        {items.map((item) => (
          <div key={item.item_id} className="flex justify-between text-sm">
            <span className={RARITY_COLOR[item.rarity ?? 'common'] ?? 'text-gray-300'}>
              {item.name}
            </span>
            <span className="text-gray-400">×{item.count}</span>
          </div>
        ))}
        {gold > 0 && (
          <div className="flex justify-between text-sm">
            <span className="text-yellow-400">金币</span>
            <span className="text-yellow-300">+{gold}</span>
          </div>
        )}
      </div>
    </div>
  )
}
