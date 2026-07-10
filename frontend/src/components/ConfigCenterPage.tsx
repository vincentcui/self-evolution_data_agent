/* ════════════════════════════════════════════
 *  ConfigCenterPage — 配置中心路由页 (全屏独立布局)
 * ----------------------------------------------------------------------------
 *  与 WorkspacePage 风格一致: 左侧导航 + 右侧 Outlet。
 *  路由 /config/:page 随切换变化, 可分享/刷新保持。
 *  左上角返回箭头回对话页。
 * ════════════════════════════════════════════ */

import React from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  ArrowLeftOutlined,
  KeyOutlined,
  RobotOutlined,
} from "@ant-design/icons";

/* ── 左侧导航宽度 — 与 Layout 侧边栏 (267px) 一致, 杜绝双菜单栏宽度错位 ── */
const SIDEBAR_WIDTH = 267;

/* ── 左侧导航项 ── */
const navItems = [
  { to: "/config/model-management", icon: <RobotOutlined />, label: "模型管理" },
  { to: "/config/git-token", icon: <KeyOutlined />, label: "全局 Git Token" },
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

const ConfigCenterPage: React.FC = () => {
  const navigate = useNavigate();

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
        {/* 左上角返回 */}
        <button
          onClick={() => navigate("/")}
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
          返回对话
        </button>

        {/* 配置中心标题 */}
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
          <span style={{ fontSize: 18, fontWeight: 600, color: "#1a1a1a" }}>配置中心</span>
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

      {/* 右侧内容 */}
      <div style={{ flex: 1, overflow: "auto", padding: 24, background: "#f5f7fa" }}>
        <Outlet />
      </div>
    </div>
  );
};

export default ConfigCenterPage;
