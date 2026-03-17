import { useEffect, useRef } from 'react'
import { useVnStore } from '../../stores/vnStore'
import VnNamePlate from './VnNamePlate'
import VnAdvanceIndicator from './VnAdvanceIndicator'
import type { VnMessage } from '../../types/game'

// ~30 chars per second at 60fps → increment per frame = 30/60 = 0.5
// We accumulate fractional progress to handle non-integer rates cleanly.
const CHARS_PER_MS = 30 / 1000

// Role-based text color — softer, warmer tones for galgame feel
const CONTENT_COLOR: Record<VnMessage['type'], string> = {
  npc: 'text-parchment-100',
  gm: 'text-parchment-200 italic',
  gm_comment: 'text-amber-200/90 italic',
  teammate: 'text-parchment-100',
  player: 'text-blue-200',
}

interface VnTextBoxProps {
  onAdvance: () => void
}

export default function VnTextBox({ onAdvance }: VnTextBoxProps) {
  const queue = useVnStore((s) => s.queue)
  const currentIndex = useVnStore((s) => s.currentIndex)
  const displayedCharCount = useVnStore((s) => s.displayedCharCount)
  const isTypewriterComplete = useVnStore((s) => s.isTypewriterComplete)
  const tickTypewriter = useVnStore((s) => s.tickTypewriter)

  const current = queue[currentIndex]

  // Typewriter animation via requestAnimationFrame
  const rafRef = useRef<number | null>(null)
  const lastTimeRef = useRef<number | null>(null)
  const accumulatedRef = useRef<number>(0)

  useEffect(() => {
    if (!current || isTypewriterComplete) {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      lastTimeRef.current = null
      accumulatedRef.current = 0
      return
    }

    const tick = (timestamp: number) => {
      if (lastTimeRef.current === null) {
        lastTimeRef.current = timestamp
      }
      const delta = timestamp - lastTimeRef.current
      lastTimeRef.current = timestamp

      accumulatedRef.current += delta * CHARS_PER_MS
      const steps = Math.floor(accumulatedRef.current)
      if (steps > 0) {
        accumulatedRef.current -= steps
        for (let i = 0; i < steps; i++) {
          tickTypewriter()
        }
      }

      // Continue if still running (the store will flip isTypewriterComplete)
      const { isTypewriterComplete: done } = useVnStore.getState()
      if (!done) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        rafRef.current = null
      }
    }

    rafRef.current = requestAnimationFrame(tick)

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [current?.id, isTypewriterComplete, tickTypewriter]) // restart when message changes

  if (!current) {
    return null
  }

  const displayedText = current.content.substring(0, displayedCharCount)
  const contentColorClass = CONTENT_COLOR[current.type] ?? 'text-parchment-100'
  const hasMore = currentIndex + 1 < queue.length

  return (
    <div
      className="relative cursor-pointer select-none min-h-[4.5rem]"
      onClick={onAdvance}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault()
          onAdvance()
        }
      }}
    >
      <VnNamePlate type={current.type} speakerName={current.speakerName} />

      <p
        className={`text-[1.05rem] leading-[1.85] whitespace-pre-wrap ${contentColorClass}`}
        style={{ textShadow: '0 1px 4px rgba(0, 0, 0, 0.6)' }}
      >
        {displayedText}
        {/* Blinking cursor while typing */}
        {!isTypewriterComplete && (
          <span
            className="inline-block w-[2px] h-[1.1em] ml-0.5 animate-pulse align-middle"
            style={{ backgroundColor: 'rgba(200, 180, 120, 0.7)' }}
          />
        )}
      </p>

      <VnAdvanceIndicator visible={isTypewriterComplete && (hasMore || false)} />
    </div>
  )
}
