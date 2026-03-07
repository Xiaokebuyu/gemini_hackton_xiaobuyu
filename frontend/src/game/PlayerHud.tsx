import { usePlayerStore } from '../stores/playerStore'

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

export default function PlayerHud() {
  const {
    name,
    characterClass,
    level,
    hp,
    maxHp,
    gold,
    day,
    slot,
    period,
  } = usePlayerStore()

  if (!name && hp <= 0 && maxHp <= 0 && gold <= 0) {
    return null
  }

  return (
    <div className="absolute top-3 left-3 z-20 rounded-xl border border-amber-500/20 bg-black/55 px-3 py-2 text-xs text-gray-200 backdrop-blur-sm">
      <div className="font-semibold text-amber-300">
        {name || '无名旅者'}
        {characterClass ? ` · ${characterClass}` : ''}
        {level > 0 ? ` Lv.${level}` : ''}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-gray-300">
        <span>HP {hp}/{maxHp}</span>
        <span>{gold}G</span>
        <span>第 {day} 天</span>
        <span>第 {slot} 格</span>
        <span>{PERIOD_LABELS[period] ?? period}</span>
      </div>
    </div>
  )
}
