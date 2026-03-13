import { motion } from 'framer-motion'

interface VnAdvanceIndicatorProps {
  visible: boolean
}

export default function VnAdvanceIndicator({ visible }: VnAdvanceIndicatorProps) {
  if (!visible) return null

  return (
    <motion.div
      className="absolute bottom-3 right-4 text-amber-300/80"
      animate={{ y: [0, 4, 0] }}
      transition={{ repeat: Infinity, duration: 0.8, ease: 'easeInOut' }}
      aria-label="点击继续"
    >
      {/* Downward-pointing triangle */}
      <svg
        width="14"
        height="10"
        viewBox="0 0 14 10"
        fill="currentColor"
      >
        <path d="M7 10L0 0h14L7 10z" />
      </svg>
    </motion.div>
  )
}
