import { useSceneStore } from '../stores/sceneStore'
import { useSessionStore } from '../stores/sessionStore'
import Portrait from './Portrait'
import type { PortraitSlot } from '../types/game'

const POSITION_CLASS: Record<PortraitSlot['position'], string> = {
  left: 'absolute left-2 md:left-8 bottom-[38vh] flex items-end',
  center: 'absolute left-1/2 -translate-x-1/2 bottom-[38vh] flex items-end',
  right: 'absolute right-2 md:right-8 bottom-[38vh] flex items-end',
}

export default function PortraitLayer() {
  const portraits = useSceneStore((s) => s.portraits)
  const { worldId, sessionId } = useSessionStore()

  if (portraits.length === 0 || !worldId || !sessionId) return null

  return (
    <div className="absolute inset-0 z-[5] pointer-events-none">
      {portraits.map((slot) => (
        <div key={slot.characterId} className={POSITION_CLASS[slot.position]}>
          <Portrait slot={slot} worldId={worldId} sessionId={sessionId} />
        </div>
      ))}
    </div>
  )
}
