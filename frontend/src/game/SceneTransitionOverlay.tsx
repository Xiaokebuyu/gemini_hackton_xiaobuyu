import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useSceneStore } from '../stores/sceneStore'

type Phase = 'fade_in' | 'hold' | 'fade_out'

export default function SceneTransitionOverlay() {
  const { transitionKey, locationName, setTransitioning } = useSceneStore()
  const [phase, setPhase] = useState<Phase | null>(null)
  const prevKey = useRef(0)
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])

  useEffect(() => {
    if (transitionKey === 0 || transitionKey === prevKey.current) return
    prevKey.current = transitionKey

    // 清除上次未完成的 timers
    timers.current.forEach(clearTimeout)
    timers.current = []

    setPhase('fade_in')
    timers.current.push(setTimeout(() => setPhase('hold'), 500))
    timers.current.push(setTimeout(() => setPhase('fade_out'), 1300))
    timers.current.push(
      setTimeout(() => {
        setPhase(null)
        setTransitioning(false)
      }, 1800),
    )

    return () => timers.current.forEach(clearTimeout)
  }, [transitionKey]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AnimatePresence>
      {phase !== null && (
        <motion.div
          key="transition-overlay"
          className="fixed inset-0 z-[25] bg-black flex items-center justify-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: phase === 'fade_out' ? 0 : 1 }}
          transition={{ duration: 0.5, ease: 'easeInOut' }}
        >
          <AnimatePresence>
            {phase === 'hold' && (
              <motion.p
                key="location-name"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
                className="text-white text-2xl font-light tracking-[0.3em] text-center px-8"
              >
                {locationName}
              </motion.p>
            )}
          </AnimatePresence>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
