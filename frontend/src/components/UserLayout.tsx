/* ════════════════════════════════════════════
 *  UserLayout — user 角色布局
 *  侧边栏 (logo + P0 会话管理) + 顶栏 (品牌/用户名/修改密码/退出) + 全屏内容区
 *
 *  纯展示组件，所有状态和回调由父组件 Layout 注入。
 * ════════════════════════════════════════════ */

import React from "react";
import { Outlet } from "react-router-dom";
import { Button } from "antd";
import { UserOutlined, LogoutOutlined } from "@ant-design/icons";
import { SessionContext, type SessionContextValue } from "@/context/SessionContext";
import styles from "@/styles/layout.module.css";

interface Props {
  sidebarLogo: React.ReactNode;
  sidebarSessionMgmt: React.ReactNode;
  username: string;
  onLogout: () => void;
  onNavigateProfile: () => void;
  sessionCtx: SessionContextValue;
}

const UserLayout: React.FC<Props> = ({
  sidebarLogo,
  sidebarSessionMgmt,
  username,
  onLogout,
  onNavigateProfile,
  sessionCtx,
}) => (
  <>
    <aside className={styles.sidebar}>
      {sidebarLogo}
      {sidebarSessionMgmt}
    </aside>

    <div className={styles.fullScreen} style={{ marginLeft: 267 }}>
      <div className={styles.topBar}>
        <div />
        <div className={styles.userMenu}>
          <span className={styles.username}>{username}</span>
          <Button type="text" size="small" onClick={onNavigateProfile}>
            修改密码
          </Button>
          <Button type="text" size="small" icon={<LogoutOutlined />} onClick={onLogout}>
            退出
          </Button>
        </div>
      </div>
      <div className={styles.fullContent}>
        <SessionContext.Provider value={sessionCtx}>
          <Outlet />
        </SessionContext.Provider>
      </div>
    </div>
  </>
);

export default UserLayout;
