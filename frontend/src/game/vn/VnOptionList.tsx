import { useVnStore } from '../../stores/vnStore'
import { useOptionStore } from '../../stores/optionStore'
import { audio } from '../../lib/audio'
import type { InteractRequest } from '../../types/api'

function formatSkillTag(label: string): string | null {
  // Extract [技能 DC15] from label like "[说服 DC15] 我相信你能行"
  const match = label.match(/^\[([^\]]+)\]/)
  if (!match) return null
  return match[1]
}

function stripSkillTag(label: string): string {
  return label.replace(/^\[[^\]]+\]\s*/, '')
}

interface VnOptionListProps {
  sendInteract: (req: InteractRequest) => void
}

export default function VnOptionList({ sendInteract: _sendInteract }: VnOptionListProps) {
  const options = useOptionStore((s) => s.options)
  const isLocked = useOptionStore((s) => s.isLocked)
  const setShowingOptions = useVnStore((s) => s.setShowingOptions)
  const enqueueMessage = useVnStore((s) => s.enqueueMessage)

  const handleOptionClick = (opt: { id: string; label: string; action: () => void }) => {
    if (isLocked) return
    audio.playClick()
    // Add player message to VN queue so the dialogue history shows what was chosen
    const displayLabel = stripSkillTag(opt.label)
    enqueueMessage({ type: 'player', content: displayLabel })
    // Execute the option's action (fires SSE request via the closure from applyDialogueOptions)
    opt.action()
    // Hide options overlay
    setShowingOptions(false)
  }

  return (
    <>
      {options.map((opt) => {
        const skillTag = formatSkillTag(opt.label)
        const baseLabel = skillTag ? stripSkillTag(opt.label) : opt.label
        return (
          <button
            key={opt.id}
            onClick={() => handleOptionClick(opt)}
            disabled={opt.disabled ?? isLocked}
            className="
              w-full text-left
              px-5 py-3
              rounded-md
              transition-all duration-200
              disabled:opacity-40 disabled:cursor-not-allowed
              group
            "
            style={{
              background: 'rgba(10, 12, 20, 0.65)',
              backdropFilter: 'blur(6px)',
              border: '1px solid rgba(180, 160, 120, 0.15)',
              boxShadow: '0 2px 8px rgba(0, 0, 0, 0.3)',
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget
              el.style.background = 'rgba(20, 24, 40, 0.8)'
              el.style.borderColor = 'rgba(200, 180, 120, 0.35)'
              el.style.boxShadow = '0 2px 12px rgba(0, 0, 0, 0.4), 0 0 8px rgba(200, 180, 120, 0.1)'
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget
              el.style.background = 'rgba(10, 12, 20, 0.65)'
              el.style.borderColor = 'rgba(180, 160, 120, 0.15)'
              el.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.3)'
            }}
          >
            <div className="flex items-start gap-2">
              {opt.icon && (
                <span className="text-base flex-shrink-0 opacity-80">{opt.icon}</span>
              )}
              <div className="flex-1 min-w-0">
                {skillTag && (
                  <span className="badge-fantasy mr-2 mb-0.5">
                    {skillTag}
                  </span>
                )}
                <span
                  className="text-parchment-200 text-sm leading-snug group-hover:text-parchment-50 transition-colors"
                  style={{ textShadow: '0 1px 3px rgba(0, 0, 0, 0.5)' }}
                >
                  {baseLabel}
                </span>
              </div>
            </div>
          </button>
        )
      })}
    </>
  )
}
