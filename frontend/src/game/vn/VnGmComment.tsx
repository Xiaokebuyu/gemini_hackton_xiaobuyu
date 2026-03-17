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
          className="pointer-events-none mb-3 max-w-[50%] w-max z-20"
          style={{
            background: 'rgba(10, 12, 20, 0.7)',
            backdropFilter: 'blur(8px)',
            border: '1px solid rgba(200, 160, 80, 0.15)',
            borderRadius: '8px',
            padding: '0.625rem 1.25rem',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.4), 0 0 12px rgba(200, 160, 80, 0.06)',
          }}
        >
          <p
            className="italic text-amber-200/90 text-sm leading-relaxed text-center"
            style={{ textShadow: '0 1px 4px rgba(0, 0, 0, 0.6)' }}
          >
            {activeGmComment.content}
          </p>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
