import type { StructuredActionRequest } from '../types/api'
import type { DonationTarget, Interactable } from '../types/game'

export interface InteractableActionMeta {
  label: string
  icon: string
}

export interface InteractableActionContext {
  sendAction: (req: StructuredActionRequest) => Promise<void> | void
  openDonation: (target: DonationTarget) => void
}

type InteractableActionHandler = (
  interactable: Interactable,
  action: StructuredActionRequest,
  context: InteractableActionContext,
) => Promise<void> | void

const ACTION_HANDLERS: Record<string, InteractableActionHandler> = {
  donate: (interactable, action, context) => {
    const targetKind = readStringParam(action.params, 'target_kind') === 'npc'
      ? 'npc'
      : 'interactable'
    const targetId = readStringParam(action.params, 'target_id') ?? interactable.id
    context.openDonation({
      targetKind,
      targetId,
      sourceName: interactable.name,
    })
  },
}

export function resolveInteractablePrimaryAction(interactable: Interactable): StructuredActionRequest {
  const actionType = String(interactable.primary_action?.action_type ?? '').trim()
  if (actionType) {
    return {
      action_type: actionType,
      params: normalizeParams(interactable.primary_action?.params),
    }
  }
  return {
    action_type: 'interact_object_v2',
    params: { interactable_id: interactable.id },
  }
}

export function describeInteractableAction(interactable: Interactable): InteractableActionMeta {
  const action = resolveInteractablePrimaryAction(interactable)
  switch (action.action_type) {
    case 'browse_board':
      return { label: `查看${interactable.name}`, icon: '📜' }
    case 'donate':
      return { label: `向${interactable.name}奉献`, icon: '🪙' }
    default:
      if (interactable.interaction_kind === 'container') {
        return { label: `检查${interactable.name}`, icon: '📦' }
      }
      return { label: interactable.name, icon: '📋' }
  }
}

export function runInteractablePrimaryAction(
  interactable: Interactable,
  context: InteractableActionContext,
): Promise<void> | void {
  const action = resolveInteractablePrimaryAction(interactable)
  const handler = ACTION_HANDLERS[action.action_type]
  if (handler) {
    return handler(interactable, action, context)
  }
  return context.sendAction(action)
}

function normalizeParams(raw: unknown): Record<string, unknown> | undefined {
  if (!raw || typeof raw !== 'object') {
    return undefined
  }
  return raw as Record<string, unknown>
}

function readStringParam(
  params: Record<string, unknown> | undefined,
  key: string,
): string | null {
  if (!params) return null
  const raw = params[key]
  if (typeof raw !== 'string') return null
  const normalized = raw.trim()
  return normalized || null
}
