import { useEffect, useState } from 'react'
import type { PortraitSlot } from '../types/game'
import { getPortrait } from '../lib/resource-map'
import { fetchPortrait } from '../lib/api'

interface Props {
  slot: PortraitSlot
  worldId: string
  sessionId: string
}

export default function Portrait({ slot, worldId, sessionId }: Props) {
  const { characterId, isActive } = slot
  const [portraitUrl, setPortraitUrl] = useState<string>(() => getPortrait(characterId))
  const [isLoading, setIsLoading] = useState(false)

  useEffect(() => {
    const staticUrl = getPortrait(characterId)
    if (staticUrl) {
      setPortraitUrl(staticUrl)
      return
    }
    setPortraitUrl('')
    setIsLoading(true)
    fetchPortrait(worldId, sessionId, characterId)
      .then((result) => {
        if (result.image_url) {
          setPortraitUrl(result.image_url)
        }
      })
      .catch(() => {})  // 静默失败，降级到字母头像
      .finally(() => setIsLoading(false))
  }, [characterId, worldId, sessionId])

  const activeClass = isActive
    ? 'opacity-100 scale-100'
    : 'opacity-100 scale-95'

  return (
    <div
      className={`w-28 h-56 md:w-36 md:h-72 rounded-t-lg overflow-hidden transition-all duration-300 ${activeClass}`}
    >
      {isLoading ? (
        /* Loading shimmer */
        <div className="w-full h-full bg-gray-800/80 border border-gray-700/50 animate-pulse" />
      ) : portraitUrl ? (
        <img
          src={portraitUrl}
          alt={characterId}
          className="w-full h-full object-cover object-top transition-opacity duration-500 opacity-100"
          onError={() => setPortraitUrl('')}
        />
      ) : (
        <div className="w-full h-full bg-gray-800/80 border border-gray-700/50 flex items-end justify-center pb-4">
          <div className="flex flex-col items-center gap-2">
            <div className="w-16 h-16 rounded-full bg-gray-700 flex items-center justify-center">
              <span className="text-gray-300 text-2xl font-bold">
                {characterId[0]?.toUpperCase() ?? '?'}
              </span>
            </div>
            <span className="text-gray-500 text-xs text-center px-2 leading-tight">
              {characterId}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
