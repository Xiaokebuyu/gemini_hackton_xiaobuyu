import { AnimatePresence, motion } from 'framer-motion'
import { useVnStore } from '../../stores/vnStore'

export default function VnGmComment() {
  const activeGmComment = useVnStore((s) => s.activeGmComment)

  // No auto-dismiss — comment stays until next message advance clears it

  return (
    <AnimatePresence>
      {activeGmComment && (
        <motion.div
          key={activeGmComment.id}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.25, ease: 'easeOut' }}
          className="
            pointer-events-none
            mb-2
            max-w-[50%] w-max
            bg-stone-900/80 backdrop-blur-sm
            border border-amber-500/20
            rounded-lg
            px-5 py-2.5
            z-20
          "
        >
          <p className="italic text-amber-200/90 text-sm leading-relaxed text-center">
            {activeGmComment.content}
          </p>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
