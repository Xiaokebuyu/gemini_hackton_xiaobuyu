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

  // Streaming state
  streamingMessageId: string | null;

  // Actions
  addMessage: (message: Omit<NarrativeMessage, 'id' | 'timestamp'>) => void;
  addPlayerMessage: (content: string) => void;
  addGMResponseV2: (response: CoordinatorResponse) => void;
  addSystemMessage: (content: string) => void;
  loadHistory: (messages: HistoryMessage[]) => void;
  setLoading: (loading: boolean) => void;
  clearMessages: () => void;
  removeMessage: (id: string) => void;

  // Streaming actions
  startStreamingMessage: (speaker: string, type: MessageType, metadata?: NarrativeMessage['metadata']) => string;
  appendToStreamingMessage: (id: string, text: string) => void;
  finalizeStreamingMessage: (id: string, fullText?: string) => void;
}

export const useChatStore = create<ChatStoreState>()(
  devtools(
    (set) => ({
      // Initial state
      messages: [],
      isLoading: false,
      streamingMessageId: null,

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

        // 3. NPC 对话响应
        if (response.npc_responses && response.npc_responses.length > 0) {
          for (const npc of response.npc_responses) {
            if (npc.dialogue) {
              newMessages.push({
                id: uuidv4(),
                speaker: npc.name || 'NPC',
                content: npc.dialogue,
                type: 'npc',
                timestamp: new Date(),
                metadata: {
                  character_id: npc.character_id,
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
          } else if (msg.role === 'system' && metadata.source === 'npc_dialogue') {
            type = 'npc';
            speaker = metadata.name || 'NPC';
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

      // --- Streaming actions ---

      startStreamingMessage: (speaker: string, type: MessageType, metadata?: NarrativeMessage['metadata']): string => {
        const id = uuidv4();
        const message: NarrativeMessage = {
          id,
          speaker,
          content: '',
          type,
          timestamp: new Date(),
          metadata,
        };
        set((state) => ({
          messages: [...state.messages, message],
          streamingMessageId: id,
        }));
        return id;
      },

      appendToStreamingMessage: (id: string, text: string) => {
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id ? { ...m, content: m.content + text } : m,
          ),
        }));
      },

      finalizeStreamingMessage: (id: string, fullText?: string) => {
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id
              ? { ...m, content: fullText !== undefined ? fullText : m.content }
              : m,
          ),
          streamingMessageId: state.streamingMessageId === id ? null : state.streamingMessageId,
        }));
      },
    }),
    { name: 'ChatStore' }
  )
);
