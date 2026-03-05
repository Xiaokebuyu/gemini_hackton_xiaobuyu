import { useEffect, useRef } from 'react'
import { useDialogueStore } from '../stores/dialogueStore'
import { useStreamStore } from '../stores/streamStore'
import type { DialogueEntry } from '../types/game'

function Message({ msg, showCursor }: { msg: DialogueEntry; showCursor: boolean }) {
  const cursor = showCursor ? (
    <span className="inline-block w-0.5 h-4 bg-amber-400 animate-pulse ml-0.5 align-middle" />
  ) : null

  switch (msg.type) {
    case 'gm':
      return (
        <div className="py-1">
          <p className="italic text-amber-200/90 leading-relaxed">
            {msg.content}{cursor}
          </p>
        </div>
      )

    case 'gm_comment':
      return (
        <div className="py-1">
          <p className="italic text-gray-400/90 text-sm leading-relaxed">
            {msg.content}{cursor}
          </p>
        </div>
      )

    case 'npc':
      return (
        <div className="py-1">
          <span className="text-sky-400 font-bold text-sm mr-2">{msg.speaker}</span>
          <span className="text-gray-100">
            &ldquo;{msg.content}&rdquo;{cursor}
          </span>
        </div>
      )

    case 'emote':
      return (
        <div className="py-0.5">
          <span className="text-purple-300 text-sm italic mr-2">{msg.speaker}</span>
          <span className="text-purple-200/80 italic text-sm">*{msg.content}*</span>
        </div>
      )

    case 'teammate':
      return (
        <div className="py-0.5 pl-3">
          <span className="text-green-400 text-sm font-medium mr-2">{msg.speaker}</span>
          <span className="text-gray-300 text-sm">{msg.content}</span>
        </div>
      )

    case 'system':
      return (
        <div className="py-1.5 text-center">
          <span className="text-gray-500 text-xs">── {msg.content} ──</span>
        </div>
      )

    case 'player':
      return (
        <div className="py-1 text-right">
          <span className="text-blue-300 text-sm">你：{msg.content}</span>
        </div>
      )

    case 'stream':
      return (
        <div className="py-1">
          <p className="text-amber-100/85 leading-relaxed">
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
    <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5 min-h-0">
      {messages.length === 0 ? (
        <p className="text-gray-600 text-sm italic text-center py-4">世界在等待...</p>
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
