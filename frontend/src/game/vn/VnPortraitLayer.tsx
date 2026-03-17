import { useSceneStore } from '../../stores/sceneStore'
import { useSessionStore } from '../../stores/sessionStore'
import { usePartyStore } from '../../stores/partyStore'
import VnPortrait from './VnPortrait'

export default function VnPortraitLayer() {
  const portraits = useSceneStore((s) => s.portraits)
  const activeNpcId = useSceneStore((s) => s.activeNpcId)
  const vnSpeakerId = useSceneStore((s) => s.vnSpeakerId)
  const { worldId, sessionId } = useSessionStore()
  const partyMembers = usePartyStore((s) => s.members)

  if (!worldId || !sessionId) return null

  // Left side: companion portraits (party members present in scene)
  const companionPortraits = portraits.filter((p) => p.isCompanion)

  // Right side: active NPC being talked to
  const npcPortrait = activeNpcId
    ? portraits.find((p) => p.characterId === activeNpcId) ?? { characterId: activeNpcId, isCompanion: false }
    : null

  // Companions not already shown as the NPC
  const leftPortraits = companionPortraits.filter((p) => p.characterId !== activeNpcId)

  // If there's no companion from portraits, use partyStore members that are in scene
  const leftCharacterIds: string[] = leftPortraits.length > 0
    ? leftPortraits.map((p) => p.characterId)
    : Object.keys(partyMembers).filter(
        (id) => portraits.some((p) => p.characterId === id && p.characterId !== activeNpcId)
      )

  const hasAnyPortrait = leftCharacterIds.length > 0 || npcPortrait !== null
  if (!hasAnyPortrait) return null

  return (
    <div className="absolute inset-x-0 top-0 bottom-[11rem] z-[11] pointer-events-none flex items-end justify-between">
      {/* Left side: party companions */}
      <div className="flex items-end">
        {leftCharacterIds.map((characterId) => (
          <VnPortrait
            key={characterId}
            characterId={characterId}
            worldId={worldId}
            sessionId={sessionId}
            isSpeaking={vnSpeakerId === characterId}
            side="left"
          />
        ))}
      </div>

      {/* Right side: NPC */}
      <div className="flex items-end">
        {npcPortrait && (
          <VnPortrait
            key={npcPortrait.characterId}
            characterId={npcPortrait.characterId}
            worldId={worldId}
            sessionId={sessionId}
            isSpeaking={vnSpeakerId === npcPortrait.characterId}
            side="right"
          />
        )}
      </div>
    </div>
  )
}
