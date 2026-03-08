import { useCombatStore } from '../../stores/combatStore'
import { useStreamStore } from '../../stores/streamStore'

interface Props {
  onEncounterAction: (choice: string, subAreaId: string) => void
}

export default function StealthResultPanel({ onEncounterAction }: Props) {
  const stealthResultData = useCombatStore((s) => s.stealthResultData)
  const encounterData = useCombatStore((s) => s.encounterData)
  const isStreaming = useStreamStore((s) => s.isStreaming)

  if (!stealthResultData || !encounterData) return null

  const { passed, roll, dc, modifier, narrative, options } = stealthResultData
  const modStr = modifier >= 0 ? `+${modifier}` : `${modifier}`
  const total = roll + modifier

  return (
    <div className="space-y-4">
      {/* 骰子结果 */}
      <div className="bg-gray-800 rounded-lg p-4 text-center">
        <div className="text-3xl mb-2">🎲</div>
        <div className="text-white text-xl font-bold">
          {roll}{' '}
          <span className="text-gray-400 text-base">{modStr}</span>
          {' '}={' '}
          <span className={passed ? 'text-green-400' : 'text-red-400'}>{total}</span>
        </div>
        <div className="text-gray-400 text-sm mt-1">vs DC {dc}</div>
        <div className={`mt-2 font-semibold ${passed ? 'text-green-400' : 'text-red-400'}`}>
          {passed ? '✓ 潜行成功' : '✗ 潜行失败'}
        </div>
      </div>

      {/* 叙述 */}
      {narrative && (
        <p className="text-gray-300 text-sm italic text-center">{narrative}</p>
      )}

      {/* 选项 */}
      {passed && options.length > 0 && (
        <div className="space-y-2">
          {options.map((opt) => (
            <button
              key={opt.action}
              onClick={() => onEncounterAction(opt.action, encounterData.sub_area_id)}
              disabled={isStreaming}
              className={`
                w-full py-2.5 rounded-lg text-sm font-medium border transition-colors
                ${isStreaming
                  ? 'bg-gray-700 border-gray-600 text-gray-500 cursor-not-allowed'
                  : 'bg-gray-700 hover:bg-gray-600 border-gray-600 text-white cursor-pointer'
                }
              `}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}

      {!passed && (
        <p className="text-center text-red-400 text-sm animate-pulse">
          ⚠️ 战斗即将开始...
        </p>
      )}
    </div>
  )
}
