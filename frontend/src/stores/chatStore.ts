/**
 * Chat/Narrative Store
 */
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { v4 as uuidv4 } from 'uuid';
import type {
  NarrativeMessage,
  PlayerInputResponse,
  CoordinatorResponse,
  TeammateResponseResult,
} from '../types';

interface ChatStoreState {
  // Messages
  messages: NarrativeMessage[];

  // Loading state
  isLoading: boolean;

  // Actions
  addMessage: (message: Omit<NarrativeMessage, 'id' | 'timestamp'>) => void;
  addPlayerMessage: (content: string) => void;
  addGMResponse: (response: PlayerInputResponse) => void;
  addGMResponseV2: (response: CoordinatorResponse) => void;
  addSystemMessage: (content: string) => void;
  setLoading: (loading: boolean) => void;
  clearMessages: () => void;
  removeMessage: (id: string) => void;
}

export const useChatStore = create<ChatStoreState>()(
  devtools(
    (set) => ({
      // Initial state
      messages: [],
      isLoading: false,

      // Actions
      addMessage: (message) => {
        const newMessage: NarrativeMessage = {
          ...message,
          id: uuidv4(),
          timestamp: new Date(),
        };
        set((state) => ({
          messages: [...state.messages, newMessage],
        }));
      },

      addPlayerMessage: (content: string) => {
        const message: NarrativeMessage = {
          id: uuidv4(),
          speaker: 'You',
          content,
          type: 'player',
          timestamp: new Date(),
        };
        set((state) => ({
          messages: [...state.messages, message],
        }));
      },

      addGMResponse: (response: PlayerInputResponse) => {
        const newMessages: NarrativeMessage[] = [];

        // Determine message type based on response.type
        let messageType: 'gm' | 'npc' | 'combat' | 'system' = 'gm';
        if (response.type === 'dialogue' && response.npc_id) {
          messageType = 'npc';
        } else if (response.type === 'combat') {
          messageType = 'combat';
        } else if (response.type === 'system' || response.type === 'error') {
          messageType = 'system';
        }

        // Add main response
        if (response.response) {
          newMessages.push({
            id: uuidv4(),
            speaker: response.speaker || 'GM',
            content: response.response,
            type: messageType,
            timestamp: new Date(),
            metadata: {
              npc_id: response.npc_id || undefined,
            },
          });
        }

        // Add extra responses (chat room mode / teammate responses)
        if (response.responses && response.responses.length > 0) {
          response.responses.forEach((tr) => {
            const teammateResponse = tr as TeammateResponseResult;
            if (teammateResponse.response) {
              newMessages.push({
                id: uuidv4(),
                speaker: teammateResponse.name || 'Unknown',
                content: teammateResponse.response,
                type: 'teammate',
                timestamp: new Date(),
                metadata: {
                  reaction: teammateResponse.reaction,
                  character_id: teammateResponse.character_id,
                },
              });
            }
          });
        }

        set((state) => ({
          messages: [...state.messages, ...newMessages],
        }));
      },

      addGMResponseV2: (response: CoordinatorResponse) => {
        const newMessages: NarrativeMessage[] = [];

        // 1. GM 叙述
        if (response.narration) {
          newMessages.push({
            id: uuidv4(),
            speaker: response.speaker || 'GM',
            content: response.narration,
            type: 'gm',
            timestamp: new Date(),
            metadata: response.metadata,
          });
        }

        // 2. 队友响应
        if (response.teammate_responses && response.teammate_responses.length > 0) {
          for (const teammate of response.teammate_responses) {
            if (teammate.response) {
              newMessages.push({
                id: uuidv4(),
                speaker: teammate.name || 'Unknown',
                content: teammate.response,
                type: 'teammate',
                timestamp: new Date(),
                metadata: {
                  reaction: teammate.reaction,
                  character_id: teammate.character_id,
                },
              });
            }
          }
        }

        set((state) => ({
          messages: [...state.messages, ...newMessages],
        }));
      },

      addSystemMessage: (content: string) => {
        const message: NarrativeMessage = {
          id: uuidv4(),
          speaker: 'System',
          content,
          type: 'system',
          timestamp: new Date(),
        };
        set((state) => ({
          messages: [...state.messages, message],
        }));
      },

      setLoading: (isLoading: boolean) => {
        set({ isLoading });
      },

      clearMessages: () => {
        set({ messages: [] });
      },

      removeMessage: (id: string) => {
        set((state) => ({
          messages: state.messages.filter((m) => m.id !== id),
        }));
      },
    }),
    { name: 'ChatStore' }
  )
);
