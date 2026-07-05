/* ════════════════════════════════════════════
 *  AdminLayout — admin 角色布局
 *  侧边栏 (logo + P0 会话管理 + 工作台弹窗 + 用户区) + 内容区
 *
 *  纯展示组件，所有状态和回调由父组件 Layout 注入。
 * ════════════════════════════════════════════ */

import React from "react";
import { Outlet } from "react-router-dom";
import { SessionContext, type SessionContextValue } from "@/context/SessionContext";
import styles from "@/styles/layout.module.css";

interface Props {
  sidebarLogo: React.ReactNode;
  sidebarSessionMgmt: React.ReactNode;
  sidebarUserArea: React.ReactNode;
  workspaceModal: React.ReactNode;
  sessionCtx: SessionContextValue;
}

const AdminLayout: React.FC<Props> = ({
  sidebarLogo,
  sidebarSessionMgmt,
  sidebarUserArea,
  workspaceModal,
  sessionCtx,
}) => (
  <div style={{ display: "flex", minHeight: "100vh" }}>
    <aside className={styles.sidebar}>
      {sidebarLogo}
      {sidebarSessionMgmt}
      {workspaceModal}
      {sidebarUserArea}
    </aside>

    <main className={styles.mainContent}>
      <SessionContext.Provider value={sessionCtx}>
        <Outlet />
      </SessionContext.Provider>
    </main>
  </div>
);

export default AdminLayout;
