import { create } from 'zustand';
export type Section = 'overview' | 'deck' | 'profiles' | 'macros' | 'actions' | 'icons' | 'integrations' | 'automation' | 'diagnostics' | 'logs' | 'settings';
type UiState = { section: Section; selectedKey: string | null; sidebarOpen: boolean; setSection: (section: Section) => void; selectKey: (key: string | null) => void; toggleSidebar: () => void };
export const useUi = create<UiState>((set) => ({ section: 'overview', selectedKey: null, sidebarOpen: true, setSection: (section) => set({ section }), selectKey: (selectedKey) => set({ selectedKey }), toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })) }));
