/**
 * Main App Component
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { GameLayout } from './components/layout';
import WelcomePage from './components/landing/WelcomePage';
import { useGameStore, useChatStore, useUIStore, toast } from './stores';
import { getSessionHistory, getLocation, getGameTime } from './api';
import type { CreateGameSessionResponse } from './types';
import { useKeyboardShortcuts } from './hooks';

// Import i18n configuration (must be before any components that use translations)
import './i18n';

// Import styles (order matters: golden-theme.css defines base variables, theme.css aliases them)
import './styles/golden-theme.css';
import './styles/theme.css';
import './styles/globals.css';
import './styles/animations.css';

// Create query client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Toast container component
const ToastContainer: React.FC = () => {
  const { toasts, removeToast } = useUIStore();

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`
            px-4 py-3 rounded-lg shadow-g-md
            flex items-center gap-3
            animate-slide-up
            font-body
            border
            ${t.type === 'success' ? 'toast-success' : ''}
            ${t.type === 'error' ? 'toast-error' : ''}
            ${t.type === 'warning' ? 'toast-warning' : ''}
            ${t.type === 'info' ? 'toast-info' : ''}
          `}
        >
          <span>{t.message}</span>
          <button
            onClick={() => removeToast(t.id)}
            className="text-current opacity-70 hover:opacity-100"
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  );
};

// App content with keyboard shortcuts
const AppContent: React.FC = () => {
  const {
    worldId,
    sessionId,
    setSession,
    clearSession,
    setLocation,
    setSubLocation,
    setGameTime,
  } = useGameStore();
  const { loadHistory, clearMessages, addMessage } = useChatStore();
  const { toggleLeftPanel, toggleRightPanel } = useUIStore();
  const initRequestRef = React.useRef(0);

  // Setup keyboard shortcuts
  useKeyboardShortcuts([
    {
      key: '[',
      ctrl: true,
      action: toggleLeftPanel,
    },
    {
      key: ']',
      ctrl: true,
      action: toggleRightPanel,
    },
  ]);

  const handleSessionCreated = async (
    newWorldId: string,
    newSessionId: string,
    createResponse?: CreateGameSessionResponse,
  ) => {
    const requestId = ++initRequestRef.current;

    const isCurrentRequest = () => {
      const state = useGameStore.getState();
      return (
        initRequestRef.current === requestId &&
        state.worldId === newWorldId &&
        state.sessionId === newSessionId
      );
    };

    clearMessages();
    setSession(newWorldId, newSessionId);

    if (createResponse) {
      if (!isCurrentRequest()) return;
      // New session: initialize from createResponse data
      if (createResponse.location) {
        setLocation(createResponse.location);
        setSubLocation(createResponse.location.sub_location_id ?? null);
      }
      const initialTime = createResponse.time ?? createResponse.location?.time;
      if (initialTime) setGameTime(initialTime);
      if (createResponse.opening_narration) {
        addMessage({ speaker: 'GM', content: createResponse.opening_narration, type: 'gm' });
      }
      toast.info('Session created. Welcome, adventurer!');
    } else {
      // Recovered session: fetch location + time + history in parallel
      try {
        const [locationResult, timeResult, historyResult] = await Promise.allSettled([
          getLocation(newWorldId, newSessionId),
          getGameTime(newWorldId, newSessionId),
          getSessionHistory(newWorldId, newSessionId, 50),
        ]);
        if (!isCurrentRequest()) return;

        if (locationResult.status === 'fulfilled') {
          setLocation(locationResult.value);
          setSubLocation(locationResult.value.sub_location_id ?? null);
        }
        if (timeResult.status === 'fulfilled') {
          setGameTime(timeResult.value);
        }

        const restoredMessages =
          historyResult.status === 'fulfilled' ? historyResult.value.messages : [];
        if (restoredMessages.length > 0) {
          loadHistory(restoredMessages);
        }

        if (locationResult.status === 'rejected' || timeResult.status === 'rejected') {
          toast.error('Session restored, but failed to load some state.');
        } else if (restoredMessages.length > 0) {
          toast.info(`Session restored with ${restoredMessages.length} messages.`);
        } else {
          toast.info('Session restored.');
        }
      } catch {
        if (!isCurrentRequest()) return;
        toast.error('Session restored, but failed to load location or time.');
      }
    }
  };

  // Auto-cleanup stale demo sessions
  React.useEffect(() => {
    if (!sessionId) return;
    if (sessionId === 'demo-session-001' || sessionId.startsWith('demo-')) {
      clearSession();
      toast.warning('Detected stale demo session. Please create or resume a real session.');
    }
  }, [sessionId, clearSession]);

  // Restore location/time after page refresh
  // zustand persists worldId/sessionId but not location/time
  React.useEffect(() => {
    if (!worldId || !sessionId) return;
    const { location } = useGameStore.getState();
    if (location !== null) return; // already have data, not a refresh

    Promise.allSettled([
      getLocation(worldId, sessionId),
      getGameTime(worldId, sessionId),
    ]).then(([locResult, timeResult]) => {
      const state = useGameStore.getState();
      if (state.worldId !== worldId || state.sessionId !== sessionId) return;
      if (locResult.status === 'fulfilled') {
        state.setLocation(locResult.value);
        state.setSubLocation(locResult.value.sub_location_id ?? null);
      }
      if (timeResult.status === 'fulfilled') {
        state.setGameTime(timeResult.value);
      }
    }).catch(() => {});
  }, [worldId, sessionId]);

  // Show welcome page if no session
  if (!worldId || !sessionId) {
    return (
      <>
        <WelcomePage onSessionCreated={handleSessionCreated} />
        <ToastContainer />
      </>
    );
  }

  return (
    <>
      <GameLayout />
      <ToastContainer />
    </>
  );
};

// Main App
function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
      {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  );
}

export default App;
