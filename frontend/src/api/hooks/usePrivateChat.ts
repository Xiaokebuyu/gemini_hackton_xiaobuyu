/**
 * Private chat hook — handles SSE streaming for 1-on-1 character chat
 */
import { useCallback, useRef } from 'react';
import { streamPrivateChat } from '../sseClient';
import { useGameStore } from '../../stores/gameStore';
import { usePrivateChatStore } from '../../stores/privateChatStore';

export function usePrivateChat() {
  const { worldId, sessionId } = useGameStore();
  const {
    targetCharacterId,
    isStreaming,
    addPlayerMessage,
    startStreaming,
    appendChunk,
    finalizeMessage,
  } = usePrivateChatStore();

  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (content: string) => {
      if (!worldId || !sessionId || !targetCharacterId) return;

      addPlayerMessage(content);
      abortRef.current = new AbortController();

      let streamMsgId: string | null = null;

      try {
        await streamPrivateChat(
          worldId,
          sessionId,
          {
            target_character_id: targetCharacterId,
            input: content,
          },
          (event) => {
            switch (event.type) {
              case 'chat_start':
                streamMsgId = startStreaming();
                break;

              case 'chat_chunk':
                if (streamMsgId) {
                  appendChunk(streamMsgId, event.text as string);
                }
                break;

              case 'chat_end':
                if (streamMsgId) {
                  finalizeMessage(streamMsgId, event.full_text as string);
                  streamMsgId = null;
                }
                break;

              case 'error':
                console.error('Private chat error:', event.error);
                break;
            }
          },
          abortRef.current.signal,
        );
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          console.error('Private chat stream error:', err);
        }
      }
    },
    [
      worldId,
      sessionId,
      targetCharacterId,
      addPlayerMessage,
      startStreaming,
      appendChunk,
      finalizeMessage,
    ],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { sendMessage, isStreaming, abort };
}
