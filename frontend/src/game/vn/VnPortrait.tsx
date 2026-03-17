import { useEffect, useState } from 'react'
import { getPortrait } from '../../lib/resource-map'
import { fetchPortrait } from '../../lib/api'

interface Props {
  characterId: string
  worldId: string
  sessionId: string
  isSpeaking: boolean
  side: 'left' | 'right'
}

export default function VnPortrait({ characterId, worldId, sessionId, isSpeaking, side }: Props) {
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
      .catch(() => {}) // 静默失败，降级到字母头像
      .finally(() => setIsLoading(false))
  }, [characterId, worldId, sessionId])

  const brightnessClass = isSpeaking
    ? 'opacity-100 brightness-110 scale-[1.03]'
    : 'opacity-40 brightness-[0.55] scale-100'

  // Soft edge blur: use a pseudo-overlay with inset box-shadow to blur edges,
  // combined with mask-image for gentle transparency fade on top + outer side.
  // This gives a "frosted edge" effect rather than hard transparency cutoff.
  const lateralGrad = side === 'left'
    ? 'linear-gradient(to right, transparent 0%, rgba(0,0,0,0.5) 8%, black 20%, black 100%)'
    : 'linear-gradient(to left, transparent 0%, rgba(0,0,0,0.5) 8%, black 20%, black 100%)'
  const topGrad = 'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.5) 6%, black 18%, black 100%)'

  const maskStyle: React.CSSProperties = {
    maskImage: `${lateralGrad}, ${topGrad}`,
    maskComposite: 'intersect',
    WebkitMaskImage: `${lateralGrad}, ${topGrad}`,
    WebkitMaskComposite: 'destination-in',
  }

  // Inset shadow on the outer edge to simulate a soft blur halo
  const shadowSide = side === 'left'
    ? 'inset 20px 0 30px -10px rgba(0,0,0,0.8), inset 0 20px 30px -10px rgba(0,0,0,0.7)'
    : 'inset -20px 0 30px -10px rgba(0,0,0,0.8), inset 0 20px 30px -10px rgba(0,0,0,0.7)'

  const speakingGlow = isSpeaking
    ? ', 0 0 40px rgba(200, 180, 120, 0.15), 0 0 80px rgba(200, 180, 120, 0.08)'
    : ''

  return (
    <div
      className={`relative h-[42vh] w-auto flex-shrink-0 overflow-hidden transition-all duration-300 ease-out origin-bottom ${brightnessClass}`}
      style={{ ...maskStyle, boxShadow: shadowSide + speakingGlow }}
    >
      {isLoading ? (
        <div className="h-full w-32 bg-gray-800/60 animate-pulse" />
      ) : portraitUrl ? (
        <img
          src={portraitUrl}
          alt={characterId}
          className="h-full w-auto object-cover object-top"
          onError={() => setPortraitUrl('')}
        />
      ) : (
        /* Fallback: dark circle with character initial */
        <div className="h-full w-28 flex items-end justify-center pb-8">
          <div className="flex flex-col items-center gap-2">
            <div className="w-20 h-20 rounded-full bg-gray-800/80 border border-gray-700/50 flex items-center justify-center">
              <span className="text-gray-300 text-3xl font-bold">
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
