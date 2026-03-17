import { useEffect, useState } from 'react'
import { useSceneStore } from '../stores/sceneStore'
import { useSessionStore } from '../stores/sessionStore'
import { getBackground } from '../lib/resource-map'
import { fetchSceneImage } from '../lib/api'

// Classify location key into a base atmosphere category
function getBaseAtmosphere(key: string): 'dungeon' | 'outdoor' | 'indoor' | 'neutral' {
  const lower = key.toLowerCase()
  if (lower.includes('dungeon') || lower.includes('goblin') || lower.includes('cave')) return 'dungeon'
  if (lower.includes('field') || lower.includes('farm') || lower.includes('road') || lower.includes('outdoor')) return 'outdoor'
  if (lower.includes('guild') || lower.includes('tavern') || lower.includes('shop') || lower.includes('temple')) return 'indoor'
  return 'neutral'
}

// Derive gradient from atmosphere + danger level + area tags
// danger_level: 1.0=baseline, 2.0+=high danger, 3.0+=extreme
// tags: hostile, safe_zone, dungeon, cursed, sacred, etc.
function getGradient(key: string, dangerLevel: number, areaTags: string[]): string {
  const base = getBaseAtmosphere(key)
  const tags = new Set(areaTags.map((t) => t.toLowerCase()))

  // Tag overrides take precedence over key-based atmosphere
  const isHostile = tags.has('hostile') || tags.has('cursed')
  const isSafe = tags.has('safe_zone') || tags.has('sacred')
  const isDungeon = tags.has('dungeon') || base === 'dungeon'
  const isOutdoor = tags.has('outdoor') || base === 'outdoor'
  const isIndoor = tags.has('indoor') || base === 'indoor'

  // High danger: shift toward darker/redder palette regardless of base
  if (dangerLevel >= 3.0 || isHostile) {
    if (isDungeon) return 'from-red-950 via-gray-950 to-black'
    return 'from-red-950 via-red-900/30 to-gray-950'
  }

  if (dangerLevel >= 2.0) {
    if (isDungeon) return 'from-red-950 via-gray-950 to-gray-950'
    if (isOutdoor) return 'from-orange-950 via-gray-950 to-gray-950'
    return 'from-orange-950 via-gray-900 to-gray-950'
  }

  // Normal danger — use base atmosphere (safe zones get slightly warmer tone)
  if (isSafe) {
    if (isIndoor) return 'from-amber-900 via-gray-950 to-gray-950'
    return 'from-green-900 via-gray-950 to-gray-950'
  }

  if (isDungeon) return 'from-red-950 via-gray-950 to-gray-950'
  if (isOutdoor) return 'from-green-950 via-gray-950 to-gray-950'
  if (isIndoor) return 'from-amber-950 via-gray-950 to-gray-950'
  return 'from-gray-900 via-gray-950 to-gray-950'
}

export default function SceneBackground() {
  const { backgroundKey, dynamicBackgroundUrl, currentArea, currentLocation, currentRoom, dangerLevel, areaTags } = useSceneStore()
  const { worldId, sessionId } = useSessionStore()
  const [generatedUrl, setGeneratedUrl] = useState<string>('')
  const [isLoading, setIsLoading] = useState(false)

  // 场景变化时，若无静态图则触发 AI 背景生成
  useEffect(() => {
    if (!currentArea || !worldId || !sessionId) return
    if (getBackground(currentArea, currentLocation)) return  // 已有静态图，跳过
    if (dynamicBackgroundUrl) return  // 已有动态子地点背景 URL，跳过生成
    setGeneratedUrl('')
    setIsLoading(true)
    fetchSceneImage(worldId, sessionId, currentArea, currentLocation, currentRoom)
      .then((result) => {
        if (result.image_url) {
          setGeneratedUrl(result.image_url)
        }
      })
      .catch(() => {})  // 静默失败，保持 CSS 渐变
      .finally(() => setIsLoading(false))
  }, [currentArea, currentLocation, currentRoom, worldId, sessionId, dynamicBackgroundUrl])

  const staticUrl = backgroundKey ? getBackground(currentArea, currentLocation) : ''
  // Priority: dynamic sub-location URL > static asset > AI-generated
  const imgUrl = dynamicBackgroundUrl || staticUrl || generatedUrl
  const gradient = getGradient(backgroundKey || currentArea, dangerLevel, areaTags)

  return (
    <div className="absolute inset-0 z-0 vignette">
      {/* CSS 渐变兜底 */}
      <div className={`absolute inset-0 bg-gradient-to-br ${gradient}`} />

      {/* Loading shimmer 叠层 */}
      {isLoading && !imgUrl && (
        <div className="absolute inset-0 bg-gray-900/60 animate-pulse" />
      )}

      {/* 背景图片（若有）*/}
      {imgUrl && (
        <img
          src={imgUrl}
          alt=""
          className="absolute inset-0 w-full h-full object-cover transition-opacity duration-700 opacity-100"
          onError={(e) => {
            ;(e.target as HTMLImageElement).style.display = 'none'
          }}
        />
      )}
    </div>
  )
}
