import { useCombatStore } from '../../stores/combatStore'
import { useStreamStore } from '../../stores/streamStore'
import StealthResultPanel from './StealthResultPanel'

interface Props {
  onEncounterAction: (choice: string, subAreaId: string) => void
}

const THREAT_CONFIG = {
  easy:     { label: '简单', color: 'text-green-400 border-green-700 bg-green-950/50' },
  moderate: { label: '中等', color: 'text-yellow-400 border-yellow-700 bg-yellow-950/50' },
  hard:     { label: '危险', color: 'text-orange-400 border-orange-700 bg-orange-950/50' },
  deadly:   { label: '致命', color: 'text-red-400 border-red-700 bg-red-950/50' },
}

export default function EncounterPanel({ onEncounterAction }: Props) {
  const encounterData = useCombatStore((s) => s.encounterData)
  const stealthResultData = useCombatStore((s) => s.stealthResultData)
  const isStreaming = useStreamStore((s) => s.isStreaming)

  if (!encounterData) return null

  const threat = THREAT_CONFIG[encounterData.threat_level] ?? THREAT_CONFIG.moderate

  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/50">
      <div className="bg-gray-900/95 border border-yellow-700 rounded-xl p-6 w-96 max-w-[90vw] shadow-2xl">
        {stealthResultData ? (
          // 潜行结果面板
          <StealthResultPanel onEncounterAction={onEncounterAction} />
        ) : (
          // 遭遇初始面板
          <>
            <h2 className="text-white text-xl font-bold text-center mb-4">⚠️ 遭遇！</h2>

            {/* 名称 + 威胁等级 */}
            <div className="flex items-center gap-2 justify-center mb-2">
              <span className="text-white font-semibold">{encounterData.name}</span>
              <span className={`text-xs border rounded px-2 py-0.5 font-medium ${threat.color}`}>
                {threat.label}
              </span>
            </div>

            {/* 描述 */}
            <p className="text-gray-300 text-sm text-center mb-3">
              {encounterData.description}
            </p>

            {/* 怪物数量 */}
            {encounterData.monster_count > 0 && (
              <div className="text-center text-gray-400 text-sm mb-4">
                {encounterData.monster_count} 名敌人
              </div>
            )}

            {/* 选项 */}
            <div className="space-y-2">
              {encounterData.options.map((opt) => (
                <button
                  key={opt.action}
                  onClick={() => onEncounterAction(opt.action, encounterData.sub_area_id)}
                  disabled={isStreaming}
                  className={`
                    w-full py-2.5 rounded-lg text-sm font-medium border transition-colors
                    ${isStreaming
                      ? 'bg-gray-700 border-gray-600 text-gray-500 cursor-not-allowed'
                      : 'bg-gray-800 hover:bg-gray-700 border-gray-600 text-white cursor-pointer'
                    }
                  `}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
