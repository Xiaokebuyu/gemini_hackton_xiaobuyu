import { useEffect, useRef } from 'react'
import { useDialogueStore } from '../stores/dialogueStore'
import { useStreamStore } from '../stores/streamStore'
import type { DialogueEntry } from '../types/game'

function Message({ msg, showCursor }: { msg: DialogueEntry; showCursor: boolean }) {
  const cursor = showCursor ? (
    <span className="inline-block w-0.5 h-4 bg-amber-400 animate-pulse ml-0.5 align-middle" />
  ) : null
  const isIntrospective = msg.type === 'gm_comment' && msg.tone === 'introspective'

  switch (msg.type) {
    case 'gm':
      return (
        <div className="py-1 border-l-2 border-gold-700/40 pl-3">
          <p className="italic text-parchment-200 leading-relaxed">
            {msg.content}{cursor}
          </p>
        </div>
      )

    case 'gm_comment':
      return (
        <div className="py-1 border-l-2 border-gold-700/40 pl-3">
          <p className={
            isIntrospective
              ? 'font-serif italic text-parchment-200 leading-relaxed tracking-[0.01em]'
              : 'italic text-parchment-300/90 text-sm leading-relaxed'
          }>
            {msg.content}{cursor}
          </p>
        </div>
      )

    case 'npc':
      return (
        <div className="py-1">
          <span className="font-display text-sm font-semibold text-sky-300 mr-2">{msg.speaker}</span>
          <span className="text-parchment-300">
            &ldquo;{msg.content}&rdquo;{cursor}
          </span>
        </div>
      )

    case 'emote':
      return (
        <div className="py-0.5">
          <span className="text-purple-300/90 italic text-sm mr-2">{msg.speaker}</span>
          <span className="text-purple-300/90 italic text-sm">*{msg.content}*</span>
        </div>
      )

    case 'teammate':
      return (
        <div className="py-0.5 pl-3">
          <span className="font-display text-sm font-semibold text-emerald-400 mr-2">{msg.speaker}</span>
          <span className="text-parchment-300 text-sm">{msg.content}</span>
        </div>
      )

    case 'teammate_emote':
      return (
        <div className="py-0.5 pl-3">
          <span className="text-emerald-400 text-sm italic mr-2">{msg.speaker}</span>
          <span className="text-emerald-300/75 italic text-sm">*{msg.content}*</span>
        </div>
      )

    case 'system':
      return (
        <div className="py-1.5 text-center">
          <hr className="divider-subtle" />
          <span className="text-gold-400/80 text-xs">{msg.content}</span>
          <hr className="divider-subtle" />
        </div>
      )

    case 'player':
      return (
        <div className="py-1 text-right">
          <span className="font-display text-sm font-semibold text-blue-300 mr-1">你</span>
          <span className="text-parchment-300 text-sm">{msg.content}</span>
        </div>
      )

    case 'stream':
      return (
        <div className="py-1 border-l-2 border-gold-700/40 pl-3">
          <p className="text-parchment-200 leading-relaxed">
            {msg.content}{cursor}
          </p>
        </div>
      )

    default:
      return <div className="text-gray-400 text-sm py-0.5">{msg.content}</div>
  }
}

export default function DialogueHistory() {
  const messages = useDialogueStore((s) => s.messages)
  const isStreaming = useStreamStore((s) => s.isStreaming)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="panel-inset flex-1 overflow-y-auto px-3 py-2 space-y-2.5 min-h-0">
      {messages.length === 0 ? (
        <p className="text-parchment-500 text-sm italic text-center py-4">世界在等待...</p>
      ) : (
        messages.map((msg, i) => (
          <Message
            key={msg.id}
            msg={msg}
            showCursor={isStreaming && i === messages.length - 1}
          />
        ))
      )}
      <div ref={endRef} />
    </div>
  )
}
