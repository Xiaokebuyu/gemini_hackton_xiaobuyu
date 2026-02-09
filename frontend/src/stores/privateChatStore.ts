/**
 * Private Chat Store — manages 1-on-1 chat state with a teammate character
 */
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { v4 as uuidv4 } from 'uuid';

export interface PrivateChatMessage {
  id: string;
  role: 'player' | 'character';
  content: string;
  timestamp: Date;
}

interface PrivateChatState {
  // State
  isOpen: boolean;
  targetCharacterId: string | null;
  targetName: string | null;
  messages: PrivateChatMessage[];
  isStreaming: boolean;
  streamingMessageId: string | null;

  // Actions
  openChat: (characterId: string, name: string) => void;
  closeChat: () => void;
  addPlayerMessage: (content: string) => void;
  startStreaming: () => string;
  appendChunk: (id: string, text: string) => void;
  finalizeMessage: (id: string, fullText: string) => void;
  clearMessages: () => void;
}

export const usePrivateChatStore = create<PrivateChatState>()(
  devtools(
    (set) => ({
      isOpen: false,
      targetCharacterId: null,
      targetName: null,
      messages: [],
      isStreaming: false,
      streamingMessageId: null,

      openChat: (characterId: string, name: string) => {
        set({
          isOpen: true,
          targetCharacterId: characterId,
          targetName: name,
          messages: [],
          isStreaming: false,
          streamingMessageId: null,
        });
      },

      closeChat: () => {
        set({
          isOpen: false,
          isStreaming: false,
          streamingMessageId: null,
        });
      },

      addPlayerMessage: (content: string) => {
        const msg: PrivateChatMessage = {
          id: uuidv4(),
          role: 'player',
          content,
          timestamp: new Date(),
        };
        set((state) => ({
          messages: [...state.messages, msg],
        }));
      },

      startStreaming: () => {
        const id = uuidv4();
        const msg: PrivateChatMessage = {
          id,
          role: 'character',
          content: '',
          timestamp: new Date(),
        };
        set((state) => ({
          messages: [...state.messages, msg],
          isStreaming: true,
          streamingMessageId: id,
        }));
        return id;
      },

      appendChunk: (id: string, text: string) => {
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id ? { ...m, content: m.content + text } : m,
          ),
        }));
      },

      finalizeMessage: (id: string, fullText: string) => {
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id ? { ...m, content: fullText } : m,
          ),
          isStreaming: false,
          streamingMessageId: null,
        }));
      },

      clearMessages: () => {
        set({ messages: [] });
      },
    }),
    { name: 'PrivateChatStore' },
  ),
);
