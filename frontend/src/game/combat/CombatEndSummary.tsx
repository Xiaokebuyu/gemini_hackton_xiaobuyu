import { useCombatStore } from '../../stores/combatStore'
import LootOverlay from './LootOverlay'

interface Props {
  onContinue: () => void
}

const RESULT_CONFIG = {
  victory: { icon: '🏆', label: '胜利', color: 'text-yellow-400' },
  defeat:  { icon: '💀', label: '失败', color: 'text-red-400' },
  fled:    { icon: '🏃', label: '撤退', color: 'text-gray-400' },
}

export default function CombatEndSummary({ onContinue }: Props) {
  const combatEndData = useCombatStore((s) => s.combatEndData)
  const lootData = useCombatStore((s) => s.lootData)

  if (!combatEndData) return null

  const cfg = RESULT_CONFIG[combatEndData.result] ?? RESULT_CONFIG.victory

  return (
    <div className="absolute inset-0 z-60 flex items-center justify-center bg-black/60">
      <div className="bg-gray-900 border border-gray-700 rounded-xl p-8 w-80 shadow-2xl">
        {/* 结果 */}
        <div className="text-center mb-6">
          <div className="text-5xl mb-2">{cfg.icon}</div>
          <div className={`text-3xl font-bold ${cfg.color}`}>{cfg.label}</div>
        </div>

        {/* 统计 */}
        <div className="space-y-2 text-sm mb-4">
          <div className="flex justify-between">
            <span className="text-gray-400">经验值</span>
            <span className="text-blue-400">+{combatEndData.xp_gained}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">金币</span>
            <span className="text-yellow-400">+{combatEndData.gold_gained}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">战斗轮数</span>
            <span className="text-gray-300">{combatEndData.rounds_fought} 轮</span>
          </div>
        </div>

        {/* 战利品 */}
        {lootData && <LootOverlay lootData={lootData} />}

        {/* 继续按钮 */}
        <button
          onClick={onContinue}
          className="mt-6 w-full py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg font-medium transition-colors"
        >
          继续
        </button>
      </div>
    </div>
  )
}
