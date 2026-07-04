/* ════════════════════════════════════════════
 *  主布局 — 基于角色的条件渲染
 *  admin: 侧边栏 + 内容区
 *  user:  顶栏 + 全屏内容区
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
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
  AppstoreOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation, Outlet } from "react-router-dom";
import { Button, Modal, Card, Row, Col, Typography } from "antd";
import { useAuth } from "@/context/AuthContext";
import { SessionContext } from "@/context/SessionContext";
import { roleAtLeast } from "@/utils/role";
import { useSessions } from "@/hooks/useSessions";
import { readLastNamespaceId } from "@/hooks/useLastNamespaceId";
import SessionList from "@/components/SessionList";
import styles from "@/styles/layout.module.css";

const { Title, Text } = Typography;

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

const workspaceCards = [
  { path: "/", icon: <BarChartOutlined />, label: "智能查询", desc: "自然语言问数" },
  { path: "/namespaces", icon: <DatabaseOutlined />, label: "命名空间", desc: "管理空间与数据源" },
  { path: "/model-management", icon: <RobotOutlined />, label: "模型管理", desc: "API Key 配置" },
  { path: "/knowledge", icon: <BookOutlined />, label: "知识库", desc: "业务术语与口径" },
  { path: "/profiles", icon: <SettingOutlined />, label: "Profile 管理", desc: "提取器配置" },
  { path: "/admin/agent-traces", icon: <ExperimentOutlined />, label: "Trace 提炼", desc: "从执行过程沉淀知识" },
  { path: "/users", icon: <UserOutlined />, label: "用户管理", desc: "成员与权限" },
  { path: "/shares", icon: <ShareAltOutlined />, label: "分享管理", desc: "管理分享链接" },
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

  const [wsOpen, setWsOpen] = useState(false);

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

        {/* 会话列表 */}
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

        {/* 工作台入口 */}
        <div style={{ padding: "4px 16px" }}>
          <Button
            type="text"
            block
            icon={<AppstoreOutlined />}
            onClick={() => setWsOpen(true)}
            style={{ justifyContent: "flex-start", paddingLeft: 8, color: "#555" }}
          >
            <Text style={{ fontSize: 13 }}>工作台</Text>
          </Button>
        </div>

        {/* 导航菜单 — 保持原有布局 */}
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

        {/* 用户区 — 固底 */}
        <div style={{ marginTop: "auto" }}>
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
        </div>
      </aside>

      {/* 工作台浮窗 */}
      <Modal
        open={wsOpen}
        onCancel={() => setWsOpen(false)}
        footer={null}
        width={640}
        closable={false}
        styles={{
          body: { padding: 24 },
          mask: {
            backdropFilter: "blur(6px)",
            background: "rgba(0, 0, 0, 0.15)",
          },
        }}
      >
        <Title level={4} style={{ marginBottom: 20 }}>工作台</Title>
        <Row gutter={[12, 12]}>
          {workspaceCards.map((item) => (
            <Col key={item.path} xs={12} sm={8}>
              <Card
                hoverable
                size="small"
                onClick={() => { navigate(item.path); setWsOpen(false); }}
                style={{ textAlign: "center" }}
              >
                <div style={{ fontSize: 24, marginBottom: 4, color: "#1677ff" }}>
                  {item.icon}
                </div>
                <Card.Meta
                  title={<span style={{ fontSize: 13 }}>{item.label}</span>}
                  description={<span style={{ fontSize: 11 }}>{item.desc}</span>}
                />
              </Card>
            </Col>
          ))}
        </Row>
      </Modal>

      {/* ── 内容区 ── */}
      <main className={styles.mainContent}>
        <SessionContext.Provider value={sessionCtx}><Outlet /></SessionContext.Provider>
      </main>
    </div>
  );
};

export default Layout;
