/* ════════════════════════════════════════════
 *  SessionContext — 跨 Layout/QueryPage 共享会话状态
 * ════════════════════════════════════════════ */

import { createContext, useContext } from "react";
import type { Session } from "@/types";

export interface SessionContextValue {
  sessions: Session[];
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
  createSession: (nsId: number) => Promise<Session>;
  renameSession: (id: string, title: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  loading: boolean;
  refresh: () => Promise<void>;
}

export const SessionContext = createContext<SessionContextValue | null>(null);

export function useSessionContext(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) {
    throw new Error("useSessionContext must be used within Layout");
  }
  return ctx;
}
