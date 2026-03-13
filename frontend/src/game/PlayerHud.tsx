import { usePlayerStore } from '../stores/playerStore'
import { usePartyStore } from '../stores/partyStore'
import { useSceneStore } from '../stores/sceneStore'
import { useOverlayStore } from '../stores/overlayStore'
import { useStreamStore } from '../stores/streamStore'

const PERIOD_LABELS: Record<string, string> = {
  dawn: '黎明',
  morning: '清晨',
  noon: '正午',
  afternoon: '午后',
  dusk: '黄昏',
  evening: '傍晚',
  night: '深夜',
  midnight: '午夜',
}

const DAY_PERIODS = new Set(['dawn', 'morning', 'noon', 'afternoon'])

function getPeriodIcon(period: string): string {
  return DAY_PERIODS.has(period) ? '☀' : '🌙'
}

function getHpBarColor(pct: number): string {
  if (pct > 60) return 'bg-emerald-500'
  if (pct > 30) return 'bg-yellow-500'
  return 'bg-red-500'
}

export default function PlayerHud() {
  const { name, characterClass, level, hp, maxHp, gold, day, period } = usePlayerStore()
  const { currentArea, currentLocation } = useSceneStore()
  const { members } = usePartyStore()
  const { open } = useOverlayStore()
  const aiProcessing = useStreamStore((s) => s.aiProcessing)

  if (!name && hp <= 0 && maxHp <= 0 && gold <= 0) {
    return null
  }

  const hpPct = maxHp > 0 ? Math.max(0, Math.min(100, (hp / maxHp) * 100)) : 0
  const hpBarColor = getHpBarColor(hpPct)
  const periodLabel = PERIOD_LABELS[period] ?? period
  const periodIcon = getPeriodIcon(period)

  const partyMembers = Object.values(members)
  const locationDisplay = currentLocation
    ? `${currentArea} / ${currentLocation}`
    : currentArea

  return (
    <div className="absolute top-3 left-3 z-20 min-w-[200px] rounded-xl border border-amber-500/20 bg-stone-950/60 px-4 py-3 text-xs text-stone-200 backdrop-blur-sm shadow-[0_0_8px_rgba(245,158,11,0.1)]">
      {/* Name row */}
      <div className="font-semibold text-amber-300">
        {name || '无名旅者'}
        {characterClass ? ` · ${characterClass}` : ''}
        {level > 0 ? ` Lv.${level}` : ''}
      </div>

      {/* Location row */}
      {locationDisplay && (
        <div className="mt-0.5 text-stone-400">
          {locationDisplay}
        </div>
      )}

      {/* HP bar */}
      <div className="mt-1.5">
        <div className="mb-1 flex items-center justify-between text-stone-400">
          <span>HP</span>
          <span>
            {hp}/{maxHp}
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-stone-800">
          <div
            className={`h-full rounded-full transition-all duration-300 ${hpBarColor}`}
            style={{ width: `${hpPct}%` }}
          />
        </div>
      </div>

      {/* Stats row */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-stone-400">
        <span className="text-amber-400/80">💰 {gold}G</span>
        <span>第 {day} 天</span>
        <span>
          {periodIcon} {periodLabel}
        </span>
      </div>

      {/* AI processing indicator */}
      {aiProcessing && (
        <div className="mt-1.5 text-xs text-amber-400/60 animate-pulse">
          ● 处理中...
        </div>
      )}

      {/* Mini party portraits */}
      {partyMembers.length > 0 && (
        <div className="mt-2 flex items-center gap-1.5">
          <span className="text-stone-500 mr-0.5">队伍：</span>
          {partyMembers.map((member) => (
            <button
              key={member.id}
              title={member.name || member.id}
              onClick={() => open('party')}
              className="flex h-6 w-6 items-center justify-center rounded-full border border-stone-600 bg-stone-700 text-[10px] font-semibold uppercase text-stone-300 hover:border-amber-500/50 hover:bg-stone-600 transition-colors"
            >
              {(member.name || member.id).charAt(0)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
