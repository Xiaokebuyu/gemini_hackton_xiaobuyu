/**
 * SSE POST stream client for game input
 *
 * EventSource only supports GET — we use fetch + ReadableStream to parse SSE from a POST endpoint.
 */

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

export interface PlayerInputStreamRequest {
  input: string;
  input_type?: string | null;
  is_private?: boolean;
  private_target?: string | null;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export async function streamGameInput(
  worldId: string,
  sessionId: string,
  request: PlayerInputStreamRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${API_BASE_URL}/api/game/${worldId}/sessions/${sessionId}/input/stream`;

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`SSE request failed: ${response.status} ${text}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Parse SSE: "data: {...}\n\n"
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const part of parts) {
      const match = part.match(/^data:\s*(.+)$/m);
      if (match) {
        try {
          onEvent(JSON.parse(match[1]));
        } catch {
          // skip malformed JSON
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Private chat SSE stream
// ---------------------------------------------------------------------------

export interface PrivateChatStreamRequest {
  target_character_id: string;
  input: string;
}

export async function streamPrivateChat(
  worldId: string,
  sessionId: string,
  request: PrivateChatStreamRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${API_BASE_URL}/api/game/${worldId}/sessions/${sessionId}/private-chat/stream`;

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Private chat SSE failed: ${response.status} ${text}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const part of parts) {
      const match = part.match(/^data:\s*(.+)$/m);
      if (match) {
        try {
          onEvent(JSON.parse(match[1]));
        } catch {
          // skip malformed JSON
        }
      }
    }
  }
}
