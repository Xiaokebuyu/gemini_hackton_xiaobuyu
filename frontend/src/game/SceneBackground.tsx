import { useEffect, useState } from 'react'
import { useSceneStore } from '../stores/sceneStore'
import { useSessionStore } from '../stores/sessionStore'
import { getBackground } from '../lib/resource-map'
import { fetchSceneImage } from '../lib/api'

function getGradient(key: string): string {
  const lower = key.toLowerCase()
  if (lower.includes('dungeon') || lower.includes('goblin') || lower.includes('cave')) {
    return 'from-red-950 via-gray-950 to-gray-950'
  }
  if (lower.includes('field') || lower.includes('farm') || lower.includes('road') || lower.includes('outdoor')) {
    return 'from-green-950 via-gray-950 to-gray-950'
  }
  if (lower.includes('guild') || lower.includes('tavern') || lower.includes('shop') || lower.includes('temple')) {
    return 'from-amber-950 via-gray-950 to-gray-950'
  }
  return 'from-gray-900 via-gray-950 to-gray-950'
}

export default function SceneBackground() {
  const { backgroundKey, dynamicBackgroundUrl, currentArea, currentLocation, currentRoom } = useSceneStore()
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
  const gradient = getGradient(backgroundKey || currentArea)

  return (
    <div className="absolute inset-0 z-0">
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
