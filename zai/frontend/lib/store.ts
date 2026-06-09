import { create } from "zustand";

export type Tab =
  | "home"
  | "wallet"
  | "candidates"
  | "pools"
  | "token"
  | "positions"
  | "strategy";

interface AppState {
  tab: Tab;
  setTab: (tab: Tab) => void;
}

export const useAppStore = create<AppState>((set) => ({
  tab: "home",
  setTab: (tab) => set({ tab }),
}));
