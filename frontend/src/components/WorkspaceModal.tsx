/* ════════════════════════════════════════════
 *  WorkspaceModal — 工作台浮窗，毛玻璃背景
 *  左侧导航 + 右侧内容，和原来首页布局一样
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Modal, Space } from "antd";
import {
  BarChartOutlined,
  DatabaseOutlined,
  BookOutlined,
  UserOutlined,
  RobotOutlined,
  ShareAltOutlined,
  ExperimentOutlined,
  SettingOutlined,
} from "@ant-design/icons";

/* ── 把每个管理页面 import 进来，在右侧渲染 ── */
import NamespacePage from "@/pages/NamespacePage";
import KnowledgePage from "@/pages/KnowledgePage";
import ModelManagement from "@/pages/ModelManagement";
import ProfileManagement from "@/pages/ProfileManagement";
import AgentTracesPage from "@/pages/AgentTracesPage";
import UserManagePage from "@/pages/UserManagePage";
import ShareManagePage from "@/pages/ShareManagePage";

const navItems = [
  { key: "namespaces", icon: <DatabaseOutlined />, label: "命名空间" },
  { key: "model-management", icon: <RobotOutlined />, label: "模型管理" },
  { key: "knowledge", icon: <BookOutlined />, label: "知识库" },
  { key: "profiles", icon: <SettingOutlined />, label: "Profile 管理" },
  { key: "agent-traces", icon: <ExperimentOutlined />, label: "Trace 提炼" },
  { key: "users", icon: <UserOutlined />, label: "用户管理" },
  { key: "shares", icon: <ShareAltOutlined />, label: "分享管理" },
];

const pageMap: Record<string, React.FC> = {
  namespaces: NamespacePage,
  knowledge: KnowledgePage,
  "model-management": ModelManagement,
  profiles: ProfileManagement,
  "agent-traces": AgentTracesPage,
  users: UserManagePage,
  shares: ShareManagePage,
};

interface Props {
  open: boolean;
  onClose: () => void;
  initialPage?: string;
}

const WorkspaceModal: React.FC<Props> = ({ open, onClose, initialPage }) => {
  const [active, setActive] = useState(initialPage || "namespaces");

  React.useEffect(() => {
    if (open && initialPage) setActive(initialPage);
  }, [open, initialPage]);
  const Page = pageMap[active] ?? (() => null);

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width="calc(100vw - 480px)"
      title={
        <Space size={10} align="center">
          <span style={{
            display: "inline-flex",
            width: 32, height: 32,
            background: "linear-gradient(135deg, #2563eb, #3b82f6)",
            color: "#fff",
            borderRadius: 8,
            fontSize: 14,
            fontWeight: 700,
            boxShadow: "0 2px 8px rgba(37, 99, 235, 0.25)",
            alignItems: "center",
            justifyContent: "center",
          }}>SE</span>
          <span style={{ fontSize: 22, fontWeight: 600, color: "#1a1a1a", lineHeight: "28px" }}>工作台</span>
        </Space>
      }
      closable={true}
      styles={{
        header: { padding: "0 24px 0 16px", borderBottom: "none" },
        body: { padding: 0, height: "78vh", overflow: "hidden", background: "#fff" },
        mask: {
          backdropFilter: "blur(6px)",
          WebkitBackdropFilter: "blur(6px)",
          background: "rgba(0, 0, 0, 0.12)",
        },
      }}
    >
      <div style={{ display: "flex", height: "100%" }}>
        {/* 左侧导航 */}
        <div style={{ width: 175, minWidth: 175, borderRight: "1px solid #e0e7ff", background: "#fff", display: "flex", flexDirection: "column" }}>
          <nav style={{ flex: 1, overflowY: "auto" }}>
            {navItems.map((item) => (
              <button
                key={item.key}
                onClick={() => setActive(item.key)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  width: "100%",
                  padding: "10px 16px",
                  border: "none",
                  background: active === item.key ? "#e6f4ff" : "transparent",
                  borderRight: active === item.key ? "3px solid #1677ff" : "3px solid transparent",
                  cursor: "pointer",
                  fontSize: 14,
                  color: active === item.key ? "#1677ff" : "#333",
                }}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </nav>
        </div>
        {/* 右侧内容 */}
        <div style={{ flex: 1, overflow: "auto", padding: 24, background: "#f5f7fa" }}>
          <Page />
        </div>
      </div>
    </Modal>
  );
};

export default WorkspaceModal;
