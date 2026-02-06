/**
 * Main App Component
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { GameLayout } from './components/layout';
import WelcomePage from './components/landing/WelcomePage';
import { useGameStore, useUIStore, toast } from './stores';
import { useKeyboardShortcuts } from './hooks';

// Import i18n configuration (must be before any components that use translations)
import './i18n';

// Import styles (order matters: theme.css sets base variables, sketch-theme.css overrides them)
import './styles/theme.css';
import './styles/globals.css';
import './styles/sketch-theme.css';
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
            px-4 py-3 rounded-lg shadow-parchment-md
            flex items-center gap-3
            animate-slide-up
            font-body
            border
            ${t.type === 'success' ? 'bg-sketch-accent-green text-white border-sketch-accent-green' : ''}
            ${t.type === 'error' ? 'bg-sketch-accent-red text-white border-sketch-accent-red' : ''}
            ${t.type === 'warning' ? 'bg-sketch-accent-gold text-sketch-ink-primary border-sketch-accent-gold' : ''}
            ${t.type === 'info' ? 'bg-sketch-accent-cyan text-white border-sketch-accent-cyan' : ''}
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

  const handleSessionCreated = (newWorldId: string, newSessionId: string) => {
    setSession(newWorldId, newSessionId);
    toast.info('Session created. Welcome, adventurer!');
  };

  // 自动清理历史 demo 会话，避免直接进入失效会话。
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
