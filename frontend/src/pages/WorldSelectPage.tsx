import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorlds } from '../lib/api'
import type { WorldSummary } from '../types/api'
import goblinSlayerCover from '../assets/goblin_slayer_cover.png'

const WORLD_COVERS: Record<string, string> = {
  goblin_slayer: goblinSlayerCover,
}

export default function WorldSelectPage() {
  const [worlds, setWorlds] = useState<WorldSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    getWorlds()
      .then(setWorlds)
      .catch((err: Error) => setError(err.message ?? '加载失败'))
  }, [])

  if (error) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-red-400">
        {error}
      </div>
    )
  }

  if (!worlds) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-parchment-400">
        加载中...
      </div>
    )
  }

  return (
    <div className="relative min-h-screen bg-gray-950 text-gray-100 p-8 vignette">
      <h1 className="font-display text-gold-300 text-3xl tracking-wide text-center mb-10">选择世界</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-5xl mx-auto">
        {worlds.map((world) => (
          <button
            key={world.world_id}
            onClick={() => navigate(`/${world.world_id}/sessions`)}
            className="panel-fantasy texture-noise hover:shadow-fantasy-glow rounded-lg overflow-hidden text-left transition-all duration-300 group"
          >
            <div className="h-40 bg-gradient-to-br from-gray-700 to-gray-900 relative">
              {(WORLD_COVERS[world.world_id] || world.cover_image) && (
                <img
                  src={WORLD_COVERS[world.world_id] || world.cover_image}
                  alt={world.name}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    ;(e.target as HTMLImageElement).style.display = 'none'
                  }}
                />
              )}
              <span className="absolute top-2 right-2 badge-fantasy">
                存档 {world.player_count}
              </span>
            </div>
            <div className="p-4">
              <h2 className="font-display text-gold-400 text-lg mb-1">
                {world.name}
              </h2>
              <p className="text-parchment-300 text-sm line-clamp-3">{world.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
