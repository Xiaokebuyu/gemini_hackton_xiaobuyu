import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorlds } from '../lib/api'
import type { WorldSummary } from '../types/api'

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
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-400">
        加载中...
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <h1 className="text-3xl font-bold text-center mb-10 text-amber-400">选择世界</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-5xl mx-auto">
        {worlds.map((world) => (
          <button
            key={world.world_id}
            onClick={() => navigate(`/${world.world_id}/sessions`)}
            className="bg-gray-800 hover:bg-gray-700 rounded-lg overflow-hidden text-left transition-colors group"
          >
            <div className="h-40 bg-gradient-to-br from-gray-700 to-gray-900 relative">
              {world.cover_image && (
                <img
                  src={world.cover_image}
                  alt={world.name}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    ;(e.target as HTMLImageElement).style.display = 'none'
                  }}
                />
              )}
              <span className="absolute top-2 right-2 bg-amber-700 text-white text-xs px-2 py-0.5 rounded">
                存档 {world.player_count}
              </span>
            </div>
            <div className="p-4">
              <h2 className="text-lg font-bold text-amber-300 group-hover:text-amber-200 mb-1">
                {world.name}
              </h2>
              <p className="text-gray-400 text-sm line-clamp-3">{world.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
