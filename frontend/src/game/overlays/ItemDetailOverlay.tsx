import { useOverlayStore } from '../../stores/overlayStore'

function safeStr(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}

function safeNum(v: unknown): number | null {
  return typeof v === 'number' ? v : null
}

export default function ItemDetailOverlay() {
  const overlay = useOverlayStore()
  const data = (overlay.data ?? {}) as Record<string, unknown>

  const name = safeStr(data.name) ?? '未知物品'
  const type = safeStr(data.type)
  const rarity = safeStr(data.rarity)
  const description = safeStr(data.description)
  const basePrice = safeNum(data.base_price)
  const damageDice = safeStr(data.damage_dice)
  const damageType = safeStr(data.damage_type)
  const acBonus = safeNum(data.ac_bonus)

  const badge = [type, rarity].filter(Boolean).join('·')
  const hasStats = damageDice !== null || acBonus !== null || basePrice !== null

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-72 max-h-[70vh] overflow-y-auto">
        <div className="flex items-start justify-between gap-2 px-5 py-3 border-b border-gray-700">
          <h2 className="text-gray-100 font-semibold">{name}</h2>
          {badge && (
            <span className="text-amber-400 text-xs flex-shrink-0">[{badge}]</span>
          )}
          <button
            onClick={overlay.close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none flex-shrink-0"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-2 text-sm">
          {damageDice && (
            <p className="text-gray-300">
              伤害：{damageDice}
              {damageType ? ` ${damageType}` : ''}
            </p>
          )}
          {acBonus !== null && <p className="text-gray-300">AC 加成：+{acBonus}</p>}
          {basePrice !== null && (
            <p className="text-yellow-400 text-xs">💰 基础价格：{basePrice}G</p>
          )}
          {description && (
            <p className="text-gray-400 text-xs mt-2 leading-relaxed">{description}</p>
          )}
          {!hasStats && !description && (
            <p className="text-gray-500 text-xs text-center">暂无详细信息</p>
          )}
        </div>
      </div>
    </div>
  )
}
