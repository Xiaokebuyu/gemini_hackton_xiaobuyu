import { AnimatePresence, motion } from 'framer-motion'
import { useVnStore } from '../../stores/vnStore'
import { useSceneStore } from '../../stores/sceneStore'

export default function VnEmoteFloat() {
  const activeEmote = useVnStore((s) => s.activeEmote)
  const activeNpcId = useSceneStore((s) => s.activeNpcId)

  // Position the emote above the corresponding portrait:
  //   speaker === activeNpcId → right portrait (NPC side)
  //   otherwise              → left portrait (companion side)
  const isRightSide = activeEmote !== null && activeEmote.speaker === activeNpcId
  const horizontalClass = isRightSide ? 'right-[15%]' : 'left-[15%]'

  return (
    <AnimatePresence>
      {activeEmote && (
        <motion.div
          key={activeEmote.id}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2, ease: 'easeOut' }}
          className={`
            absolute ${horizontalClass}
            bottom-[44vh]
            pointer-events-none
            z-20
          `}
        >
          <p className="text-purple-300/80 italic text-sm whitespace-nowrap">
            *{activeEmote.content}*
          </p>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
