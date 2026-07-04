/* ════════════════════════════════════════════
 *  主布局 — 基于角色的条件渲染
 *  admin: 侧边栏 + 内容区
 *  user:  顶栏 + 全屏内容区
 * ════════════════════════════════════════════ */

import React from "react";
import {
  UserOutlined,
  LogoutOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation, Outlet } from "react-router-dom";
import { Button } from "antd";
import { useAuth } from "@/context/AuthContext";
import { SessionContext } from "@/context/SessionContext";
import { roleAtLeast } from "@/utils/role";
import { useSessions } from "@/hooks/useSessions";
import { readLastNamespaceId } from "@/hooks/useLastNamespaceId";
import SessionList from "@/components/SessionList";
import styles from "@/styles/layout.module.css";

const Layout: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const nsId = readLastNamespaceId() ?? null;
  const {
    sessions,
    activeSessionId,
    setActiveSessionId,
    createSession,
    renameSession,
    deleteSession,
    loading: sessionsLoading,
    refresh,
  } = useSessions(nsId);

  const sessionCtx = {
    sessions,
    activeSessionId,
    setActiveSessionId,
    createSession,
    renameSession,
    deleteSession,
    loading: sessionsLoading,
    refresh,
  };

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  /* ── User 布局: 无侧边栏,全屏 ── */
  if (!roleAtLeast(user?.role, "admin")) {
    return (
      <div className={styles.fullScreen}>
        <div className={styles.topBar}>
          <div className={styles.brandArea}>
            <div className={styles.logoIcon}>SE</div>
            <span className={styles.brandText}>Self-Evolution Data Agent</span>
          </div>
          <div className={styles.userMenu}>
            <span className={styles.username}>{user?.username}</span>
            <Button
              type="text"
              size="small"
              onClick={() => navigate("/profile")}
            >
              修改密码
            </Button>
            <Button
              type="text"
              size="small"
              icon={<LogoutOutlined />}
              onClick={handleLogout}
            >
              退出
            </Button>
          </div>
        </div>
        <div className={styles.fullContent}>
          <SessionContext.Provider value={sessionCtx}><Outlet /></SessionContext.Provider>
        </div>
      </div>
    );
  }

  /* ── Admin 布局: 侧边栏 + 内容区 ── */
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      {/* ── 侧边栏 ── */}
      <aside className={styles.sidebar}>
        <div className={styles.logoArea}>
          <div className={styles.logoIcon}>SE</div>
          <div className={styles.logoText}>
            Self-Evolution
            <br />
            Data Agent
          </div>
        </div>

        {/* 会话列表 — 始终展示 */}
        <SessionList
          namespaceId={nsId}
          sessions={sessions}
          activeSessionId={activeSessionId}
          loading={sessionsLoading}
          onCreate={createSession}
          onSelect={(id) => {
            setActiveSessionId(id);
            navigate("/");
          }}
          onRename={renameSession}
          onDelete={deleteSession}
        />

        <div className={styles.userArea}>
          <div className={styles.userInfo}>
            <UserOutlined />
            <span>{user?.username}</span>
          </div>
          <Button
            type="text"
            size="small"
            icon={<LogoutOutlined />}
            onClick={handleLogout}
          >
            退出
          </Button>
        </div>
      </aside>

      {/* ── 内容区 ── */}
      <main className={styles.mainContent}>
        <SessionContext.Provider value={sessionCtx}><Outlet /></SessionContext.Provider>
      </main>
    </div>
  );
};

export default Layout;
