/* ════════════════════════════════════════════
 *  useActiveNamespace — 工作台管理页共享的"当前空间"
 * ----------------------------------------------------------------------------
 *  管理页 (数据源/Git仓库/知识库/Trace提炼) 不再各自持有命名空间下拉选择器,
 *  统一读 localStorage 记忆的空间 id (由工作台首页"进入空间"写入), 找不到
 *  记忆时兜底取列表第一个。与 NamespaceSelector 的自动选中语义一致。
 * ════════════════════════════════════════════ */

import { useCallback, useEffect, useState } from "react";
import { fetchNamespaces } from "@/api";
import { readLastNamespaceId } from "@/hooks/useLastNamespaceId";
import type { Namespace } from "@/types";

export function useActiveNamespace() {
  const [activeNs, setActiveNs] = useState<Namespace | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchNamespaces();
      const remembered = readLastNamespaceId();
      const found = list.find((n) => n.id === remembered) ?? list[0] ?? null;
      setActiveNs(found);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { activeNs, loading, refresh };
}
