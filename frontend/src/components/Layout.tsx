/* ════════════════════════════════════════════
 *  主布局 — 基于角色的条件渲染
 *  admin: 侧边栏 + 内容区
 *  user:  顶栏 + 全屏内容区
 * ════════════════════════════════════════════ */

import React from "react";
import {
  BarChartOutlined,
  DatabaseOutlined,
  BookOutlined,
  UserOutlined,
  LogoutOutlined,
  RobotOutlined,
  ShareAltOutlined,
  ExperimentOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation, Outlet } from "react-router-dom";
import { Button } from "antd";
import { useAuth } from "@/context/AuthContext";
import { roleAtLeast } from "@/utils/role";
import { useSessions } from "@/hooks/useSessions";
import { readLastNamespaceId } from "@/hooks/useLastNamespaceId";
import SessionList from "@/components/SessionList";
import styles from "@/styles/layout.module.css";

const adminNavItems = [
  { path: "/", icon: <BarChartOutlined />, label: "智能查询" },
  { path: "/namespaces", icon: <DatabaseOutlined />, label: "命名空间" },
  { path: "/model-management", icon: <RobotOutlined />, label: "模型管理" },
  { path: "/knowledge", icon: <BookOutlined />, label: "知识库" },
  { path: "/profiles", icon: <SettingOutlined />, label: "Profile 管理" },
  { path: "/admin/agent-traces", icon: <ExperimentOutlined />, label: "Trace 提炼" },
  { path: "/users", icon: <UserOutlined />, label: "用户管理" },
  { path: "/shares", icon: <ShareAltOutlined />, label: "分享管理" },
];

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
  } = useSessions(nsId);

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
          <Outlet />
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

        {/* SessionList: 首页对话页展示 */}
        {location.pathname === "/" && (
          <SessionList
            namespaceId={nsId}
            sessions={sessions}
            activeSessionId={activeSessionId}
            loading={false}
            onCreate={createSession}
            onSelect={(id) => {
              setActiveSessionId(id);
              navigate("/");
            }}
            onRename={renameSession}
            onDelete={deleteSession}
          />
        )}

        <nav className={styles.navList}>
          {adminNavItems.map((item) => (
            <button
              key={item.path}
              className={
                location.pathname === item.path
                  ? styles.navItemActive
                  : styles.navItem
              }
              onClick={() => navigate(item.path)}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>

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
        <Outlet />
      </main>
    </div>
  );
};

export default Layout;
