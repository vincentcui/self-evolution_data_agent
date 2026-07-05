/* ════════════════════════════════════════════
 *  主布局 — 基于角色的条件渲染
 *  admin: 侧边栏 (P0 会话管理) + 内容区
 *  user:  侧边栏 (P0 会话管理) + 顶栏 + 全屏内容区
 *
 *  侧边栏用户区: 仅用户名 + 退出（pre-P0 原始，无修改密码）
 *  user 顶栏: 品牌 + 用户名 + 修改密码 + 退出（pre-P0 原始）
 * ════════════════════════════════════════════ */

import React, { useCallback, useRef, useState } from "react";
import {
  UserOutlined, LogoutOutlined, AppstoreOutlined, EditOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { Button, Modal } from "antd";
import { useAuth } from "@/context/AuthContext";
import { SessionContext } from "@/context/SessionContext";
import { roleAtLeast } from "@/utils/role";
import { useSessions } from "@/hooks/useSessions";
import { useReadiness } from "@/hooks/useReadiness";
import { readLastNamespaceId, writeLastNamespaceId } from "@/hooks/useLastNamespaceId";
import { cancelStream } from "@/api/correction";
import { getGlobalStop } from "@/hooks/useAgentStream";
import SessionList from "@/components/SessionList";
import WorkspaceModal from "@/components/WorkspaceModal";
import AdminLayout from "@/components/AdminLayout";
import UserLayout from "@/components/UserLayout";
import styles from "@/styles/layout.module.css";

const Layout: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [nsId, setNsId] = useState<number | null>(readLastNamespaceId() ?? null);
  const {
    sessions, activeSessionId, setActiveSessionId,
    createSession, renameSession, deleteSession,
    loading: sessionsLoading, refresh,
  } = useSessions(nsId);
  const { ready } = useReadiness(nsId);
  const [wsOpen, setWsOpen] = useState(false);
  const [wsPage, setWsPage] = useState<string>("namespaces");
  const [hoverWorkspace, setHoverWorkspace] = useState(false);
  const [hoverNewChat, setHoverNewChat] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const [runningTraceId, setRunningTraceId] = useState<string | null>(null);
  // ref 持有最新 traceId，避免 newChat 闭包陈旧导致 cancelStream 漏调
  const runningTraceIdRef = useRef(runningTraceId);
  runningTraceIdRef.current = runningTraceId;

  const newChat = useCallback(() => {
    if (isRunning) {
      Modal.confirm({
        title: "当前有任务正在执行",
        content: "新对话将停止当前正在执行的任务，是否继续？",
        okText: "停止并新建", cancelText: "取消",
        onOk: async () => {
          getGlobalStop()?.();
          const tid = runningTraceIdRef.current;
          if (tid) await cancelStream(tid).catch(() => {});
          setResetKey((k) => k + 1); setActiveSessionId(null); setIsRunning(false);
        },
      });
    } else {
      setResetKey((k) => k + 1);
      setActiveSessionId(null);
    }
  }, [isRunning]);

  const sessionCtx = {
    sessions, activeSessionId, setActiveSessionId,
    createSession, renameSession, deleteSession,
    loading: sessionsLoading, refresh,
    newChat, resetKey, isRunning, setIsRunning, runningTraceId, setRunningTraceId,
    wsOpen, setWsOpen, wsPage, setWsPage,
    currentNamespaceId: nsId,
    setCurrentNamespaceId: (id: number | null) => { setNsId(id); if (id) writeLastNamespaceId(id); },
  };

  const handleLogout = () => { logout(); navigate("/login"); };

  const isAdmin = roleAtLeast(user?.role, "admin");

  const btnBase = { height: 36, borderRadius: 18, fontSize: 16, fontWeight: 500 as const,
    display: "flex" as const, alignItems: "center", justifyContent: "center" };
  const ncActive = !activeSessionId;
  const ncBg = ncActive ? "#e6f4ff" : hoverNewChat ? "#f0f5ff" : "#fff";
  const ncBd = ncActive ? "1px solid #91caff" : hoverNewChat ? "1px solid #bdd7ff" : "1px solid #d9d9d9";
  const ncCl = ncActive ? "#1677ff" : hoverNewChat ? "#4096ff" : "#555";
  const wsBg = wsOpen ? "#e6f4ff" : hoverWorkspace ? "#f0f5ff" : "#fff";
  const wsBd = wsOpen ? "1px solid #91caff" : hoverWorkspace ? "1px solid #bdd7ff" : "1px solid #d9d9d9";
  const wsCl = wsOpen ? "#1677ff" : hoverWorkspace ? "#4096ff" : "#555";

  /* Logo 区 — 仅 admin（user 在顶栏已有品牌区） */
  const sidebarLogo = (
    <div className={styles.logoArea}>
      <div className={styles.logoIcon}>SE</div>
      <div className={styles.logoText}>
        Self-Evolution
        <br />
        Data Agent
      </div>
    </div>
  );

  /* P0 会话管理 — admin & user 共享 */
  const sidebarSessionMgmt = (
    <>
      {/* P0: 新对话 */}
      <div style={{ padding: "8px 12px 4px" }}>
        <Button block icon={<EditOutlined />} onClick={() => newChat()}
          onMouseEnter={() => setHoverNewChat(true)} onMouseLeave={() => setHoverNewChat(false)}
          style={{ ...btnBase, background: ncBg, border: ncBd, color: ncCl }}>
          新对话
        </Button>
      </div>

      {/* P0: 工作台 (仅 admin) */}
      {isAdmin && (
        <div style={{ padding: "4px 12px" }}>
          <Button block icon={<AppstoreOutlined />} onClick={() => setWsOpen(true)}
            onMouseEnter={() => setHoverWorkspace(true)} onMouseLeave={() => setHoverWorkspace(false)}
            style={{ ...btnBase, background: wsBg, border: wsBd, color: wsCl }}>
            工作台
          </Button>
        </div>
      )}

      {/* P0: 历史对话 + 会话列表 */}
      <div style={{ padding: "12px 16px 4px", fontSize: 12, color: "#999" }}>历史对话</div>

      <SessionList
        namespaceId={nsId} sessions={sessions} activeSessionId={activeSessionId}
        loading={sessionsLoading} ready={ready}
        onSelect={(id) => {
          if (id) {
            setActiveSessionId(id);
            navigate("/");
          } else {
            newChat();
          }
        }}
        onRename={renameSession} onDelete={deleteSession}
      />
    </>
  );

  /* 侧边栏底用户区 — 仅 admin（user 在顶栏已有用户菜单） */
  const sidebarUserArea = (
    <div style={{ marginTop: "auto" }}>
      <div className={styles.userArea}>
        <div className={styles.userInfo}>
          <UserOutlined />
          <span>{user?.username}</span>
        </div>
        <Button type="text" size="small" icon={<LogoutOutlined />} onClick={handleLogout}>
          退出
        </Button>
      </div>
    </div>
  );

  /* 工作台弹窗 — 作为 ReactNode 传入 AdminLayout */
  const workspaceModalNode = (
    <WorkspaceModal open={wsOpen} onClose={() => setWsOpen(false)} initialPage={wsPage} />
  );

  if (!isAdmin) {
    return (
      <UserLayout
        sidebarLogo={sidebarLogo}
        sidebarSessionMgmt={sidebarSessionMgmt}
        username={user?.username ?? ""}
        onLogout={handleLogout}
        onNavigateProfile={() => navigate("/profile")}
        sessionCtx={sessionCtx}
      />
    );
  }

  return (
    <AdminLayout
      sidebarLogo={sidebarLogo}
      sidebarSessionMgmt={sidebarSessionMgmt}
      sidebarUserArea={sidebarUserArea}
      workspaceModal={workspaceModalNode}
      sessionCtx={sessionCtx}
    />
  );
};

export default Layout;
