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
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-72 max-h-[70vh] overflow-y-auto">
        <div className="panel-header flex items-start justify-between gap-2">
          <h2 className="font-display text-gold-300 text-lg">{name}</h2>
          <div className="flex items-center gap-2 flex-shrink-0">
            {badge && (
              <span className="badge-fantasy">{badge}</span>
            )}
            <button
              onClick={overlay.close}
              className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
            >
              ×
            </button>
          </div>
        </div>

        <div className="p-5 space-y-2 text-sm">
          {damageDice && (
            <p className="text-parchment-400">
              伤害：<span className="text-gold-400 font-mono">{damageDice}</span>
              {damageType ? <span className="text-parchment-400"> {damageType}</span> : ''}
            </p>
          )}
          {acBonus !== null && (
            <p className="text-parchment-400">
              AC 加成：<span className="text-gold-400 font-mono">+{acBonus}</span>
            </p>
          )}
          {basePrice !== null && (
            <p className="text-parchment-400 text-xs">
              基础价格：<span className="text-gold-400 font-mono">{basePrice}G</span>
            </p>
          )}
          {description && (
            <p className="text-parchment-300 text-xs mt-2 leading-relaxed">{description}</p>
          )}
          {!hasStats && !description && (
            <p className="text-parchment-500 text-xs text-center">暂无详细信息</p>
          )}
        </div>

        <div className="px-5 pb-4">
          <button
            onClick={overlay.close}
            className="btn-subtle w-full"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
