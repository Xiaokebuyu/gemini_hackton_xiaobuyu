/**
 * Chat/Narrative Store
 */
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { v4 as uuidv4 } from 'uuid';
import type {
  NarrativeMessage,
  MessageType,
  CoordinatorResponse,
} from '../types';
import type { HistoryMessage } from '../api/gameApi';

interface ChatStoreState {
  // Messages
  messages: NarrativeMessage[];

  // Loading state
  isLoading: boolean;

  // Actions
  addMessage: (message: Omit<NarrativeMessage, 'id' | 'timestamp'>) => void;
  addPlayerMessage: (content: string) => void;
  addGMResponseV2: (response: CoordinatorResponse) => void;
  addSystemMessage: (content: string) => void;
  loadHistory: (messages: HistoryMessage[]) => void;
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

      loadHistory: (historyMessages: HistoryMessage[]) => {
        const loaded: NarrativeMessage[] = historyMessages.map((msg) => {
          const metadata = (msg.metadata || {}) as Record<string, string | undefined>;
          let type: MessageType = 'system';
          let speaker = 'System';

          if (msg.role === 'user') {
            type = 'player';
            speaker = 'You';
          } else if (msg.role === 'assistant') {
            type = 'gm';
            speaker = 'GM';
          } else if (msg.role === 'system' && metadata.source === 'teammate') {
            type = 'teammate';
            speaker = metadata.name || 'Teammate';
          }

          return {
            id: uuidv4(),
            speaker,
            content: msg.content,
            type,
            timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(),
            metadata: metadata as Record<string, string | undefined>,
          };
        });

        set({ messages: loaded });
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
