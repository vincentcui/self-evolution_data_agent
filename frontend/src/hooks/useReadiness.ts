/* ════════════════════════════════════════════
 *  useReadiness — 命名空间可问数状态查询
 * ----------------------------------------------------------------------------
 *  接收 namespaceId, 返回 { ready, blockers, loading, refresh }
 *  namespaceId 变化时自动重新请求.
 * ════════════════════════════════════════════ */

import { useState, useEffect, useCallback } from "react";
import { getReadiness } from "@/api";
import type { Blocker, ReadinessResult } from "@/types";

export function useReadiness(namespaceId: number | null) {
  const [ready, setReady] = useState(false);
  const [blockers, setBlockers] = useState<Blocker[]>([]);
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (namespaceId == null) {
      setReady(false);
      setBlockers([]);
      setChecks({});
      return;
    }
    setLoading(true);
    try {
      const result: ReadinessResult = await getReadiness(namespaceId);
      setReady(result.ready);
      setBlockers(result.blockers);
      setChecks(result.checks);
    } catch {
      setReady(false);
      setBlockers([]);
      setChecks({});
    } finally {
      setLoading(false);
    }
  }, [namespaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { ready, blockers, checks, loading, refresh };
}
