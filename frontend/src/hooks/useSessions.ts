/* ════════════════════════════════════════════
 *  useSessions — 会话 CRUD 状态管理
 * ----------------------------------------------------------------------------
 *  接收 namespaceId, 返回会话列表 + CRUD 操作 + 当前活跃会话 ID.
 *  创建/重命名/删除后自动刷新列表.
 * ════════════════════════════════════════════ */

import { useState, useEffect, useCallback } from "react";
import {
  createSession,
  deleteSession,
  listSessions,
  renameSession,
} from "@/api";
import type { Session } from "@/types";

export function useSessions(namespaceId: number | null) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (namespaceId == null) {
      setSessions([]);
      return;
    }
    setLoading(true);
    try {
      const list = await listSessions(namespaceId);
      setSessions(list);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, [namespaceId]);

  useEffect(() => {
    refresh().then(() => {
      // 初始化：无活跃会话时自动选最近会话
      setActiveSessionId((prev) => prev ?? (sessions.length > 0 ? sessions[0].id : null));
    });
  }, [refresh]);  // eslint-disable-line react-hooks/exhaustive-deps

  const create = useCallback(
    async (nsId: number) => {
      const session = await createSession(nsId);
      await refresh();
      setActiveSessionId(session.id);
      return session;
    },
    [refresh],
  );

  const rename = useCallback(
    async (id: string, title: string) => {
      await renameSession(id, title);
      await refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteSession(id);
      await refresh();
      setActiveSessionId((prev) => {
        if (prev === id) {
          return sessions.length > 1 ? sessions.find((s) => s.id !== id)?.id ?? null : null;
        }
        return prev;
      });
    },
    [refresh, sessions],
  );

  return {
    sessions,
    activeSessionId,
    setActiveSessionId,
    createSession: create,
    renameSession: rename,
    deleteSession: remove,
    loading,
    refresh,
  };
}
