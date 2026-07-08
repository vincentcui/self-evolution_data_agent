/* ════════════════════════════════════════════
 *  WorkspacePage — 工作台路由页 (全屏独立布局)
 * ----------------------------------------------------------------------------
 *  脱离 Layout 会话侧边栏, 全屏占据视口. 左侧 7 项 NavLink (宽度与 Layout
 *  侧边栏一致) + 右侧 Outlet. URL /workspace/:page 随切换变化, 可分享/刷新保持.
 *  左上角返回箭头回对话页. 超链接 (去添加数据源 / 去采集 Schema …) 直跳子页.
 * ════════════════════════════════════════════ */

import React from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  ArrowLeftOutlined,
  DatabaseOutlined,
  GithubOutlined,
  BookOutlined,
  UserOutlined,
  RobotOutlined,
  ShareAltOutlined,
  ExperimentOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useActiveNamespace } from "@/hooks/useActiveNamespace";
import type { Namespace } from "@/types";

/** 子路由管理页 (数据源/Git仓库/知识库/Trace提炼…) 经 useOutletContext 取共享的当前空间,
 *  不再各自挂 NamespaceSelector 下拉框。 */
export interface WorkspaceOutletContext {
  activeNs: Namespace | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

/* ── 左侧导航宽度 — 与 Layout 侧边栏 (267px) 一致, 杜绝双菜单栏宽度错位 ── */
const SIDEBAR_WIDTH = 267;

/* ── 左侧导航项 — to 即路由 path, NavLink 自动 active ── */
const navItems = [
  { to: "/workspace/manage/datasources", icon: <DatabaseOutlined />, label: "数据源" },
  { to: "/workspace/manage/repos", icon: <GithubOutlined />, label: "Git 仓库" },
  { to: "/workspace/manage/model-management", icon: <RobotOutlined />, label: "模型管理" },
  { to: "/workspace/manage/knowledge", icon: <BookOutlined />, label: "知识库" },
  { to: "/workspace/manage/profiles", icon: <SettingOutlined />, label: "Profile 管理" },
  { to: "/workspace/manage/agent-traces", icon: <ExperimentOutlined />, label: "Trace 提炼" },
  { to: "/workspace/manage/users", icon: <UserOutlined />, label: "用户管理" },
  { to: "/workspace/manage/shares", icon: <ShareAltOutlined />, label: "分享管理" },
];

/* NavLink active 样式 */
const navLinkStyle = ({ isActive }: { isActive: boolean }): React.CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
  padding: "10px 16px",
  borderRight: isActive ? "3px solid #1677ff" : "3px solid transparent",
  fontSize: 14,
  color: isActive ? "#1677ff" : "#333",
  background: isActive ? "#e6f4ff" : "transparent",
  textDecoration: "none",
  cursor: "pointer",
});

const WorkspacePage: React.FC = () => {
  const navigate = useNavigate();
  const { activeNs, loading, refresh } = useActiveNamespace();

  return (
    <div style={{ display: "flex", height: "100vh", background: "#fff", overflow: "hidden" }}>
      {/* 左侧导航 */}
      <aside
        style={{
          width: SIDEBAR_WIDTH,
          minWidth: SIDEBAR_WIDTH,
          borderRight: "1px solid #e0e7ff",
          background: "#fff",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* 左上角返回 — 回工作台首页 */}
        <button
          onClick={() => navigate("/workspace")}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            width: "100%",
            padding: "12px 16px",
            border: "none",
            borderBottom: "1px solid #f0f0f0",
            background: "transparent",
            cursor: "pointer",
            fontSize: 14,
            color: "#555",
            textAlign: "left",
          }}
        >
          <ArrowLeftOutlined />
          返回工作台
        </button>

        {/* 当前空间标题 — 替代原固定"工作台"文案 */}
        <div
          style={{
            padding: "14px 16px",
            display: "flex",
            alignItems: "center",
            gap: 10,
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <span
            style={{
              display: "inline-flex",
              width: 32,
              height: 32,
              background: "linear-gradient(135deg, #2563eb, #3b82f6)",
              color: "#fff",
              borderRadius: 8,
              fontSize: 14,
              fontWeight: 700,
              boxShadow: "0 2px 8px rgba(37, 99, 235, 0.25)",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            SE
          </span>
          <span
            style={{
              fontSize: 18,
              fontWeight: 600,
              color: "#1a1a1a",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {loading ? "" : activeNs?.name ?? "工作台"}
          </span>
        </div>

        <nav style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} style={navLinkStyle}>
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* 右侧内容 — Outlet 渲染子路由管理页, 共享当前空间 context */}
      <div style={{ flex: 1, overflow: "auto", padding: 24, background: "#f5f7fa" }}>
        <Outlet context={{ activeNs, loading, refresh }} />
      </div>
    </div>
  );
};

export default WorkspacePage;
