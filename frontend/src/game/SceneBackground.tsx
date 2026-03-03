import { useSceneStore } from '../stores/sceneStore'
import { getBackground } from '../lib/resource-map'

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
  const { backgroundKey, currentArea, currentLocation, isTransitioning } = useSceneStore()
  const imgUrl = backgroundKey ? getBackground(currentArea, currentLocation) : ''
  const gradient = getGradient(backgroundKey || currentArea)

  return (
    <div className="absolute inset-0 z-0">
      {/* CSS 渐变兜底 */}
      <div className={`absolute inset-0 bg-gradient-to-br ${gradient}`} />

      {/* 背景图片（若有） */}
      {imgUrl && (
        <img
          src={imgUrl}
          alt=""
          className="absolute inset-0 w-full h-full object-cover"
          onError={(e) => {
            ;(e.target as HTMLImageElement).style.display = 'none'
          }}
        />
      )}

      {/* 转场遮罩 */}
      <div
        className={`absolute inset-0 bg-black transition-opacity duration-500 ${
          isTransitioning ? 'opacity-80' : 'opacity-0 pointer-events-none'
        }`}
      />
    </div>
  )
}
