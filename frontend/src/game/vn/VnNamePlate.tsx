import type { VnMessage } from '../../types/game'

// Role-based speaker name color mapping
const TYPE_COLOR: Record<VnMessage['type'], string> = {
  npc: 'text-sky-400',
  gm: 'text-amber-300',
  gm_comment: 'text-amber-300',
  teammate: 'text-green-400',
  player: 'text-blue-300',
}

interface VnNamePlateProps {
  type: VnMessage['type']
  speakerName?: string
}

export default function VnNamePlate({ type, speakerName }: VnNamePlateProps) {
  // GM narration and GM comments don't show a speaker badge
  if (type === 'gm' || type === 'gm_comment') return null
  if (!speakerName) return null

  const colorClass = TYPE_COLOR[type] ?? 'text-gray-300'

  return (
    <div className="mb-1 px-1">
      <span
        className={`text-sm font-semibold tracking-wide ${colorClass}`}
      >
        {speakerName}
      </span>
    </div>
  )
}
