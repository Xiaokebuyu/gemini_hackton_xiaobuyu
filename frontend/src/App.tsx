/**
 * Main App Component
 */
import React, { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { GameLayout } from './components/layout';
import { useGameStore, useUIStore, toast } from './stores';
import { useKeyboardShortcuts } from './hooks';

// Import i18n configuration (must be before any components that use translations)
import './i18n';

// Import styles (order matters: theme.css sets base variables, sketch-theme.css overrides them)
import './styles/theme.css';
import './styles/globals.css';
import './styles/sketch-theme.css';

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
            px-4 py-3 shadow-lg
            flex items-center gap-3
            animate-slide-up
            font-handwritten
            border-2
            ${t.type === 'success' ? 'bg-sketch-accent-green text-white border-sketch-accent-green' : ''}
            ${t.type === 'error' ? 'bg-sketch-accent-red text-white border-sketch-accent-red' : ''}
            ${t.type === 'warning' ? 'bg-sketch-accent-gold text-sketch-ink-primary border-sketch-accent-gold' : ''}
            ${t.type === 'info' ? 'bg-sketch-accent-cyan text-white border-sketch-accent-cyan' : ''}
          `}
          style={{ borderRadius: '4px' }}
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
  const { toggleChatMode } = useGameStore();
  const { toggleLeftPanel, toggleRightPanel } = useUIStore();

  // Setup keyboard shortcuts
  useKeyboardShortcuts([
    {
      key: 'Tab',
      action: toggleChatMode,
    },
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

  // Set demo session on mount if none exists
  const { worldId, sessionId, setSession } = useGameStore();
  useEffect(() => {
    if (!worldId || !sessionId) {
      // Set demo session
      setSession('goblin_slayer', 'demo-session-001');
      toast.info('Demo session loaded. Connect to backend for full experience.');
    }
  }, [worldId, sessionId, setSession]);

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
