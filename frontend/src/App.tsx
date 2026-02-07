/**
 * Main App Component
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { GameLayout } from './components/layout';
import WelcomePage from './components/landing/WelcomePage';
import { useGameStore, useChatStore, useUIStore, toast } from './stores';
import { getSessionHistory } from './api';
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
  const { worldId, sessionId, setSession, clearSession } = useGameStore();
  const { loadHistory, clearMessages } = useChatStore();
  const { toggleLeftPanel, toggleRightPanel } = useUIStore();

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

  const handleSessionCreated = async (newWorldId: string, newSessionId: string) => {
    clearMessages();
    setSession(newWorldId, newSessionId);

    // Try to load chat history for recovered sessions
    try {
      const { messages } = await getSessionHistory(newWorldId, newSessionId, 50);
      if (messages.length > 0) {
        loadHistory(messages);
        toast.info(`Session restored with ${messages.length} messages.`);
      } else {
        toast.info('Session created. Welcome, adventurer!');
      }
    } catch {
      toast.info('Session created. Welcome, adventurer!');
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
