/**
 * History Panel - Compact message log for current session
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { User, Shield, Users, Info } from 'lucide-react';
import { useChatStore, useGameStore } from '../../stores';
import { getSessionHistory, type HistoryMessage } from '../../api/gameApi';

const speakerIcon = (type: string) => {
  switch (type) {
    case 'player':
      return <User className="w-3 h-3 text-g-cyan flex-shrink-0" />;
    case 'gm':
      return <Shield className="w-3 h-3 text-g-gold flex-shrink-0" />;
    case 'teammate':
      return <Users className="w-3 h-3 text-green-400 flex-shrink-0" />;
    default:
      return <Info className="w-3 h-3 text-g-text-muted flex-shrink-0" />;
  }
};

const truncate = (text: string, maxLen = 50) =>
  text.length > maxLen ? text.slice(0, maxLen) + '...' : text;

const formatTime = (date: Date) => {
  const h = date.getHours().toString().padStart(2, '0');
  const m = date.getMinutes().toString().padStart(2, '0');
  return `${h}:${m}`;
};

interface DisplayMessage {
  id: string;
  speaker: string;
  type: string;
  content: string;
  timestamp: Date;
}

function mapRemoteMessage(msg: HistoryMessage, index: number): DisplayMessage | null {
  const metadata = msg.metadata || {};
  const source = typeof metadata.source === 'string' ? metadata.source : '';
  const name = typeof metadata.name === 'string' ? metadata.name : '';

  let type = 'system';
  let speaker = 'System';
  if (msg.role === 'user') {
    type = 'player';
    speaker = 'You';
  } else if (msg.role === 'assistant') {
    type = 'gm';
    speaker = 'GM';
  } else if (msg.role === 'system' && source === 'teammate') {
    type = 'teammate';
    speaker = name || 'Teammate';
  }

  const ts = msg.timestamp ? new Date(msg.timestamp) : new Date();
  if (Number.isNaN(ts.getTime())) {
    return null;
  }

  return {
    id: `remote-${index}`,
    speaker,
    type,
    content: msg.content,
    timestamp: ts,
  };
}

export const HistoryPanel: React.FC = () => {
  const { t } = useTranslation();
  const { worldId, sessionId } = useGameStore();
  const localMessages = useChatStore((s) => s.messages);

  const { data } = useQuery({
    queryKey: ['sessionHistory', worldId, sessionId],
    queryFn: () => getSessionHistory(worldId!, sessionId!, 200),
    enabled: Boolean(worldId && sessionId),
    staleTime: 30000,
  });

  const remoteMessages: DisplayMessage[] = (data?.messages || [])
    .map(mapRemoteMessage)
    .filter((msg): msg is DisplayMessage => msg != null);

  const localDisplayMessages: DisplayMessage[] = localMessages.map((msg) => ({
    id: msg.id,
    speaker: msg.speaker,
    type: msg.type,
    content: msg.content,
    timestamp: msg.timestamp,
  }));

  const dedup = new Map<string, DisplayMessage>();
  for (const msg of [...remoteMessages, ...localDisplayMessages]) {
    const key = `${msg.type}|${msg.speaker}|${msg.content}|${msg.timestamp.toISOString()}`;
    dedup.set(key, msg);
  }

  const reversed = Array.from(dedup.values()).sort(
    (a, b) => b.timestamp.getTime() - a.timestamp.getTime(),
  );

  if (reversed.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-g-text-muted">
        <Info className="w-8 h-8 mb-2" />
        <p className="text-xs">{t('history.noHistory', '暂无记录')}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto g-scrollbar">
        <div className="space-y-1 p-2">
          {reversed.map((msg) => (
            <div
              key={msg.id}
              className="flex items-start gap-2 px-2 py-1.5 rounded-lg hover:bg-g-bg-hover transition-colors"
            >
              <div className="mt-0.5">{speakerIcon(msg.type)}</div>
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline justify-between gap-1">
                  <span className="text-xs font-semibold text-g-text truncate">
                    {msg.speaker}
                  </span>
                  <span className="text-[10px] text-g-text-muted flex-shrink-0">
                    {formatTime(msg.timestamp)}
                  </span>
                </div>
                <p className="text-xs text-g-text-muted leading-snug mt-0.5">
                  {truncate(msg.content)}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default HistoryPanel;
