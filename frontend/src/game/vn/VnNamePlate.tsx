import type { VnMessage } from '../../types/game'

// Galgame-style name plate — a colored tab that "sits" on top of the text box
const TYPE_STYLE: Record<VnMessage['type'], { bg: string; text: string; border: string }> = {
  npc:        { bg: 'rgba(14, 116, 144, 0.7)',  text: 'text-sky-200',     border: 'rgba(56, 189, 248, 0.3)' },
  gm:         { bg: 'rgba(120, 90, 30, 0.7)',   text: 'text-amber-200',   border: 'rgba(200, 160, 80, 0.3)' },
  gm_comment: { bg: 'rgba(120, 90, 30, 0.7)',   text: 'text-amber-200',   border: 'rgba(200, 160, 80, 0.3)' },
  teammate:   { bg: 'rgba(22, 101, 52, 0.7)',   text: 'text-emerald-200', border: 'rgba(74, 222, 128, 0.3)' },
  player:     { bg: 'rgba(30, 58, 138, 0.7)',   text: 'text-blue-200',    border: 'rgba(96, 165, 250, 0.3)' },
}

interface VnNamePlateProps {
  type: VnMessage['type']
  speakerName?: string
}

export default function VnNamePlate({ type, speakerName }: VnNamePlateProps) {
  // GM narration and GM comments don't show a speaker badge
  if (type === 'gm' || type === 'gm_comment') return null
  if (!speakerName) return null

  const style = TYPE_STYLE[type] ?? TYPE_STYLE.npc

  return (
    <div
      className={`inline-block mb-2 px-4 py-1 rounded-md font-display font-semibold text-sm tracking-wide ${style.text}`}
      style={{
        background: style.bg,
        border: `1px solid ${style.border}`,
        backdropFilter: 'blur(4px)',
        boxShadow: `0 2px 8px rgba(0, 0, 0, 0.3)`,
        textShadow: '0 1px 3px rgba(0, 0, 0, 0.5)',
      }}
    >
      {speakerName}
    </div>
  )
}
