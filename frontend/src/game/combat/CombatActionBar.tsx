import { useCombatStore } from '../../stores/combatStore'
import { useStreamStore } from '../../stores/streamStore'

interface Props {
  onAction: (type: string) => void
}

const ACTIONS = [
  { type: 'attack',     label: '⚔ 攻击' },
  { type: 'defend',     label: '🛡 防御' },
  { type: 'disengage',  label: '↔ 卸离' },
  { type: 'dash',       label: '💨 冲刺' },
  { type: 'shove',      label: '↙ 推击' },
  { type: 'flee',       label: '🏃 撤退' },
]

export default function CombatActionBar({ onAction }: Props) {
  const isStreaming = useStreamStore((s) => s.isStreaming)
  const combatEndData = useCombatStore((s) => s.combatEndData)
  const disabled = isStreaming || combatEndData !== null

  return (
    <div className="absolute bottom-0 left-0 right-0 flex flex-wrap justify-center gap-2 p-3 bg-gray-950/80">
      {ACTIONS.map((a) => (
        <button
          key={a.type}
          onClick={() => onAction(a.type)}
          disabled={disabled}
          className={`
            px-2 py-1.5 sm:px-3 sm:py-2 rounded text-sm font-medium border
            ${disabled
              ? 'bg-gray-800 border-gray-700 text-gray-500 cursor-not-allowed'
              : 'bg-gray-800 hover:bg-red-900 border-red-700 text-white cursor-pointer transition-colors'
            }
          `}
        >
          {a.label}
        </button>
      ))}
    </div>
  )
}
