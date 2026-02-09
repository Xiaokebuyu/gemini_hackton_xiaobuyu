/**
 * UI State Store
 */
import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';

type ToastType = 'success' | 'error' | 'warning' | 'info';

interface Toast {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

interface UIStoreState {
  // Panels
  leftPanelCollapsed: boolean;
  rightPanelCollapsed: boolean;

  // Modals
  activeModal: string | null;
  modalData: Record<string, unknown>;

  // Toast notifications
  toasts: Toast[];

  // Loading states
  globalLoading: boolean;
  loadingMessage: string | null;

  // Theme (future use)
  theme: 'dark' | 'light';

  // Actions
  setLeftPanelCollapsed: (collapsed: boolean) => void;
  setRightPanelCollapsed: (collapsed: boolean) => void;
  toggleLeftPanel: () => void;
  toggleRightPanel: () => void;

  openModal: (modalId: string, data?: Record<string, unknown>) => void;
  closeModal: () => void;

  addToast: (toast: Omit<Toast, 'id'>) => void;
  removeToast: (id: string) => void;
  clearToasts: () => void;

  setGlobalLoading: (loading: boolean, message?: string) => void;
  setTheme: (theme: 'dark' | 'light') => void;
}

let toastId = 0;

export const useUIStore = create<UIStoreState>()(
  devtools(
    persist(
      (set, get) => ({
        // Initial state
        leftPanelCollapsed: false,
        rightPanelCollapsed: true,
        activeModal: null,
        modalData: {},
        toasts: [],
        globalLoading: false,
        loadingMessage: null,
        theme: 'dark',

        // Panel actions
        setLeftPanelCollapsed: (leftPanelCollapsed: boolean) => {
          set({ leftPanelCollapsed });
        },

        setRightPanelCollapsed: (rightPanelCollapsed: boolean) => {
          set({ rightPanelCollapsed });
        },

        toggleLeftPanel: () => {
          set((state) => ({ leftPanelCollapsed: !state.leftPanelCollapsed }));
        },

        toggleRightPanel: () => {
          set((state) => ({ rightPanelCollapsed: !state.rightPanelCollapsed }));
        },

        // Modal actions
        openModal: (modalId: string, data?: Record<string, unknown>) => {
          set({ activeModal: modalId, modalData: data || {} });
        },

        closeModal: () => {
          set({ activeModal: null, modalData: {} });
        },

        // Toast actions
        addToast: (toast) => {
          const id = `toast-${++toastId}`;
          const newToast: Toast = { ...toast, id };
          set((state) => ({ toasts: [...state.toasts, newToast] }));

          // Auto-remove after duration
          const duration = toast.duration || 5000;
          setTimeout(() => {
            get().removeToast(id);
          }, duration);
        },

        removeToast: (id: string) => {
          set((state) => ({
            toasts: state.toasts.filter((t) => t.id !== id),
          }));
        },

        clearToasts: () => {
          set({ toasts: [] });
        },

        // Loading actions
        setGlobalLoading: (globalLoading: boolean, loadingMessage?: string) => {
          set({ globalLoading, loadingMessage: loadingMessage || null });
        },

        // Theme actions
        setTheme: (theme: 'dark' | 'light') => {
          set({ theme });
        },
      }),
      {
        name: 'ui-storage',
        partialize: (state) => ({
          leftPanelCollapsed: state.leftPanelCollapsed,
          rightPanelCollapsed: state.rightPanelCollapsed,
          theme: state.theme,
        }),
      }
    ),
    { name: 'UIStore' }
  )
);

// Toast helper functions
export const toast = {
  success: (message: string, duration?: number) =>
    useUIStore.getState().addToast({ type: 'success', message, duration }),
  error: (message: string, duration?: number) =>
    useUIStore.getState().addToast({ type: 'error', message, duration }),
  warning: (message: string, duration?: number) =>
    useUIStore.getState().addToast({ type: 'warning', message, duration }),
  info: (message: string, duration?: number) =>
    useUIStore.getState().addToast({ type: 'info', message, duration }),
};
